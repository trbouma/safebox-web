"""Session-bound outgoing Lightning payments executed outside request workers."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import inspect
import logging
import secrets
from typing import Any, Callable

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.models import OutgoingPaymentJob, WebWorkerHeartbeat, utc_now
from app.worker_liveness import WORKER_STALE_SECONDS, worker_is_live


logger = logging.getLogger("safebox_web.outgoing_payment")
JOB_LEASE_SECONDS = 15 * 60
JOB_HEARTBEAT_SECONDS = 30

INVALID_LIGHTNING_ADDRESS_MESSAGE = (
    "Not a valid Lightning address. Check the address and try again."
)


def _public_payment_error(exc: Exception, payment_kind: str) -> str:
    """Hide component and protocol internals from payment status pages."""

    detail = str(exc).strip() or type(exc).__name__
    normalized = detail.lower()
    invalid_address_markers = (
        "not a valid lightning address",
        "invalid lightning address",
        "lightning address does not exist",
        "lighting address does not exist",
        "not enough values to unpack",
    )
    if payment_kind == "address" and any(
        marker in normalized for marker in invalid_address_markers
    ):
        return INVALID_LIGHTNING_ADDRESS_MESSAGE
    return f"{type(exc).__name__}: {detail}"


def _payment_error_code(
    exc: Exception,
    payment_kind: str,
    *,
    outcome_uncertain: bool,
) -> str:
    """Return a stable journal code without persisting exception prose."""

    detail = str(exc).strip().lower()
    if payment_kind == "address" and any(
        marker in detail
        for marker in (
            "not a valid lightning address",
            "invalid lightning address",
            "lightning address does not exist",
            "lighting address does not exist",
            "not enough values to unpack",
        )
    ):
        return "invalid_lightning_address"
    if outcome_uncertain:
        return "payment_outcome_uncertain"
    if "stale proof" in detail or "already spent" in detail:
        return "stale_proofs"
    if "insufficient balance" in detail or "exceeds the available balance" in detail:
        return "insufficient_balance"
    if any(
        marker in detail
        for marker in (
            "mint is unavailable",
            "mint unavailable",
            "connecttimeout",
            "connection refused",
            "mint endpoint unreachable",
        )
    ):
        return "mint_unavailable"
    return "payment_failed"


def _tender_kwargs(
    payment_method: Callable[..., Any],
    tendered_amount: float | None,
    tendered_currency: str,
) -> dict[str, Any]:
    """Pass tender metadata only to Acorn versions that support it."""

    try:
        parameters = inspect.signature(payment_method).parameters
    except (TypeError, ValueError):
        return {}
    if "tendered_amount" not in parameters or "tendered_currency" not in parameters:
        return {}
    return {
        "tendered_amount": tendered_amount,
        "tendered_currency": str(tendered_currency or "SAT"),
    }


def _job_values(job: OutgoingPaymentJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {
        column: getattr(job, column)
        for column in (
            "npub", "status", "phase", "payment_kind", "recipient", "amount",
            "tendered_amount", "tendered_currency",
            "total_fees", "mint_fees", "lightning_fee",
            "lightning_fee_reserve", "lightning_fee_return", "message", "error",
            "started_at", "updated_at", "lease_expires_at", "owner_worker_id",
        )
    }


def get_outgoing_payment_job(engine: Engine, npub: str) -> dict[str, Any] | None:
    with Session(engine) as session:
        values = _job_values(session.get(OutgoingPaymentJob, npub))
    if values is not None and values["status"] == "RUNNING":
        owner_stopped = bool(
            values["owner_worker_id"]
            and not worker_is_live(engine, values["owner_worker_id"])
        )
        lease_expired = bool(
            not values["owner_worker_id"]
            and values["lease_expires_at"] <= utc_now()
        )
        if owner_stopped or lease_expired:
            values["status"] = "INTERRUPTED"
            values["phase"] = "REVIEW"
            values["error"] = (
                "The payment worker stopped before reporting a final result. "
                "Review transaction history and reconcile pending payments; do not retry blindly."
            )
    return values


def claim_outgoing_payment_job(
    engine: Engine,
    npub: str,
    *,
    payment_kind: str,
    recipient: str,
    amount: int,
    tendered_amount: float | None = None,
    tendered_currency: str = "SAT",
    worker_id: str | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    now = utc_now()
    worker_cutoff = now - timedelta(seconds=WORKER_STALE_SECONDS)
    lease_expires_at = now + timedelta(seconds=JOB_LEASE_SECONDS)
    owner_token = secrets.token_urlsafe(24)
    values = dict(
        owner_token=owner_token,
        owner_worker_id=worker_id,
        payment_kind=payment_kind,
        recipient=recipient,
        amount=int(amount),
        tendered_amount=tendered_amount,
        tendered_currency=str(tendered_currency or "SAT"),
        status="RUNNING",
        phase="STARTING",
        total_fees=None,
        mint_fees=None,
        lightning_fee=None,
        lightning_fee_reserve=None,
        lightning_fee_return=None,
        message=None,
        error=None,
        started_at=now,
        updated_at=now,
        lease_expires_at=lease_expires_at,
    )
    with Session(engine) as session:
        if session.get(OutgoingPaymentJob, npub) is None:
            session.add(OutgoingPaymentJob(npub=npub, **values))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
            else:
                return True, owner_token, _job_values(
                    session.get(OutgoingPaymentJob, npub)
                ) or {}

        live_owner = exists(
            select(WebWorkerHeartbeat.worker_id)
            .where(WebWorkerHeartbeat.worker_id == OutgoingPaymentJob.owner_worker_id)
            .where(WebWorkerHeartbeat.heartbeat_at > worker_cutoff)
        )
        statement = (
            update(OutgoingPaymentJob)
            .where(OutgoingPaymentJob.npub == npub)
            .where(
                or_(
                    OutgoingPaymentJob.status != "RUNNING",
                    and_(
                        OutgoingPaymentJob.owner_worker_id.is_(None),
                        OutgoingPaymentJob.lease_expires_at <= now,
                    ),
                    and_(
                        OutgoingPaymentJob.owner_worker_id.is_not(None),
                        ~live_owner,
                    ),
                )
            )
            .values(**values)
        )
        result = session.exec(statement)
        session.commit()
        return bool(result.rowcount), owner_token if result.rowcount else "", (
            _job_values(session.get(OutgoingPaymentJob, npub)) or {}
        )


def update_outgoing_payment_job(
    engine: Engine,
    npub: str,
    owner_token: str,
    **changes: Any,
) -> bool:
    changes["updated_at"] = utc_now()
    with Session(engine) as session:
        result = session.exec(
            update(OutgoingPaymentJob)
            .where(OutgoingPaymentJob.npub == npub)
            .where(OutgoingPaymentJob.owner_token == owner_token)
            .values(**changes)
        )
        session.commit()
        return bool(result.rowcount)


async def run_outgoing_payment_job(
    *,
    engine: Engine,
    acorn,
    npub: str,
    owner_token: str,
    payment_kind: str,
    recipient: str,
    amount: int,
    comment: str,
    tendered_amount: float | None = None,
    tendered_currency: str = "SAT",
) -> None:
    async def maintain_lease() -> None:
        while True:
            await asyncio.sleep(JOB_HEARTBEAT_SECONDS)
            if not update_outgoing_payment_job(
                engine,
                npub,
                owner_token,
                lease_expires_at=utc_now() + timedelta(seconds=JOB_LEASE_SECONDS),
            ):
                raise RuntimeError("Outgoing payment lease ownership was lost")

    heartbeat = asyncio.create_task(maintain_lease())
    try:
        update_outgoing_payment_job(engine, npub, owner_token, phase="PAYING")
        if payment_kind == "invoice":
            message, fees, *_details = await acorn.pay_multi_invoice(
                lninvoice=recipient,
                comment=comment,
                **_tender_kwargs(
                    acorn.pay_multi_invoice,
                    tendered_amount,
                    tendered_currency,
                ),
            )
        else:
            message, fees = await acorn.pay_multi(
                amount=int(amount),
                lnaddress=recipient,
                comment=comment,
                **_tender_kwargs(
                    acorn.pay_multi,
                    tendered_amount,
                    tendered_currency,
                ),
            )
        update_outgoing_payment_job(
            engine,
            npub,
            owner_token,
            status="COMPLETE",
            phase="COMPLETE",
            total_fees=int(fees),
            mint_fees=getattr(fees, "mint_fees", None),
            lightning_fee=getattr(fees, "lightning_fee", None),
            lightning_fee_reserve=getattr(fees, "lightning_fee_reserve", None),
            lightning_fee_return=getattr(fees, "lightning_fee_return", None),
            message=str(message).splitlines()[0],
        )
    except asyncio.CancelledError:
        update_outgoing_payment_job(
            engine, npub, owner_token, status="INTERRUPTED", phase="REVIEW",
            error="Payment processing was interrupted. Reconcile before retrying.",
        )
        raise
    except Exception as exc:
        logger.exception("background outgoing payment failed npub=%s", npub)
        fees = getattr(exc, "fees", 0)
        total_fees = max(0, int(fees or 0))
        mint_fees = getattr(fees, "mint_fees", None)
        lightning_fee = getattr(fees, "lightning_fee", None)
        lightning_fee_reserve = getattr(fees, "lightning_fee_reserve", None)
        lightning_fee_return = getattr(fees, "lightning_fee_return", None)
        error_text = str(exc).strip() or type(exc).__name__
        public_error = _public_payment_error(exc, payment_kind)
        outcome_uncertain = (
            "unknown" in type(exc).__name__.lower()
            or "finalization" in type(exc).__name__.lower()
            or "unresolved" in error_text.lower()
            or "do not retry" in error_text.lower()
        )
        error_code = _payment_error_code(
            exc,
            payment_kind,
            outcome_uncertain=outcome_uncertain,
        )
        history_recorded = bool(getattr(exc, "history_recorded", False))
        history_error = None
        if not history_recorded:
            try:
                await acorn.add_tx_history(
                    tx_type="X",
                    amount=int(amount),
                    comment=str(comment or "").strip(),
                    tendered_amount=tendered_amount,
                    tendered_currency=tendered_currency,
                    fees=total_fees,
                    error_code=error_code,
                )
            except Exception as history_exc:
                history_error = type(history_exc).__name__
                logger.exception(
                    "outgoing payment failure history publish failed npub=%s",
                    npub,
                )
        if history_error:
            public_error += (
                " Transaction history could not be updated; review the "
                f"relay-backed payment journal ({history_error})."
            )
        update_outgoing_payment_job(
            engine, npub, owner_token, status="FAILED", phase="REVIEW",
            total_fees=total_fees,
            mint_fees=mint_fees,
            lightning_fee=lightning_fee,
            lightning_fee_reserve=lightning_fee_reserve,
            lightning_fee_return=lightning_fee_return,
            error=public_error,
        )
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)


def run_outgoing_payment_job_in_thread(
    *,
    engine: Engine,
    acorn_factory: Callable[[], Any],
    npub: str,
    owner_token: str,
    payment_kind: str,
    recipient: str,
    amount: int,
    comment: str,
    tendered_amount: float | None,
    tendered_currency: str,
    load_timeout_seconds: float,
) -> None:
    async def execute() -> None:
        acorn = acorn_factory()
        await asyncio.wait_for(acorn.load_data(), timeout=load_timeout_seconds)
        await run_outgoing_payment_job(
            engine=engine, acorn=acorn, npub=npub, owner_token=owner_token,
            payment_kind=payment_kind, recipient=recipient, amount=amount,
            comment=comment,
            tendered_amount=tendered_amount,
            tendered_currency=tendered_currency,
        )

    try:
        asyncio.run(execute())
    except Exception as exc:
        logger.exception("outgoing payment thread failed npub=%s", npub)
        update_outgoing_payment_job(
            engine, npub, owner_token, status="FAILED", phase="REVIEW",
            error=f"{type(exc).__name__}: {exc}",
        )
