"""Background finalization of relay-backed attached-Acorn deposit quotes."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import hashlib
import logging
import secrets
from time import monotonic
from typing import Any, Callable

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.models import DepositFinalizationJob, WebWorkerHeartbeat, utc_now
from app.worker_liveness import WORKER_STALE_SECONDS, worker_is_live


logger = logging.getLogger("safebox_web.deposit_finalization")
JOB_LEASE_SECONDS = 15 * 60
JOB_HEARTBEAT_SECONDS = 30
POLL_SECONDS = 3
MAX_MONITOR_SECONDS = 30


def deposit_quote_hash(quote: str) -> str:
    return hashlib.sha256(str(quote).encode("utf-8")).hexdigest()


def _job_values(job: DepositFinalizationJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {
        key: getattr(job, key)
        for key in (
            "npub", "quote_hash", "amount", "mint", "status", "phase",
            "error", "started_at", "updated_at", "lease_expires_at",
            "owner_worker_id",
        )
    }


def get_deposit_finalization_job(
    engine: Engine,
    npub: str,
    *,
    quote_hash: str | None = None,
) -> dict[str, Any] | None:
    with Session(engine) as session:
        statement = select(DepositFinalizationJob).where(
            DepositFinalizationJob.npub == npub
        )
        if quote_hash is not None:
            statement = statement.where(
                DepositFinalizationJob.quote_hash == quote_hash
            )
        else:
            statement = statement.order_by(
                DepositFinalizationJob.updated_at.desc()
            ).limit(1)
        values = _job_values(session.exec(statement).scalars().first())
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
            values["phase"] = "RESUMABLE"
            values["error"] = (
                "The previous worker stopped. Reconnect this Acorn to resume "
                "from its relay-backed deposit quote."
            )
    return values


def claim_deposit_finalization_job(
    engine: Engine,
    npub: str,
    *,
    quote_hash: str,
    amount: int,
    mint: str,
    worker_id: str | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    now = utc_now()
    owner_token = secrets.token_urlsafe(24)
    lease_expires_at = now + timedelta(seconds=JOB_LEASE_SECONDS)
    worker_cutoff = now - timedelta(seconds=WORKER_STALE_SECONDS)
    values = dict(
        quote_hash=quote_hash,
        owner_token=owner_token,
        owner_worker_id=worker_id,
        amount=int(amount),
        mint=str(mint).rstrip("/"),
        status="RUNNING",
        phase="STARTING",
        error=None,
        started_at=now,
        updated_at=now,
        lease_expires_at=lease_expires_at,
    )
    with Session(engine) as session:
        if session.get(DepositFinalizationJob, quote_hash) is None:
            session.add(DepositFinalizationJob(npub=npub, **values))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
            else:
                return True, owner_token, _job_values(
                    session.get(DepositFinalizationJob, quote_hash)
                ) or {}

        live_owner = exists(
            select(WebWorkerHeartbeat.worker_id)
            .where(WebWorkerHeartbeat.worker_id == DepositFinalizationJob.owner_worker_id)
            .where(WebWorkerHeartbeat.heartbeat_at > worker_cutoff)
        )
        result = session.exec(
            update(DepositFinalizationJob)
            .where(DepositFinalizationJob.quote_hash == quote_hash)
            .where(DepositFinalizationJob.npub == npub)
            .where(
                or_(
                    DepositFinalizationJob.status != "RUNNING",
                    and_(
                        DepositFinalizationJob.owner_worker_id.is_(None),
                        DepositFinalizationJob.lease_expires_at <= now,
                    ),
                    and_(
                        DepositFinalizationJob.owner_worker_id.is_not(None),
                        ~live_owner,
                    ),
                )
            )
            .values(**values)
        )
        session.commit()
        claimed = bool(result.rowcount)
        return claimed, owner_token if claimed else "", _job_values(
            session.get(DepositFinalizationJob, quote_hash)
        ) or {}


def update_deposit_finalization_job(
    engine: Engine,
    npub: str,
    quote_hash: str,
    owner_token: str,
    **changes: Any,
) -> bool:
    changes["updated_at"] = utc_now()
    with Session(engine) as session:
        result = session.exec(
            update(DepositFinalizationJob)
            .where(DepositFinalizationJob.quote_hash == quote_hash)
            .where(DepositFinalizationJob.npub == npub)
            .where(DepositFinalizationJob.owner_token == owner_token)
            .values(**changes)
        )
        session.commit()
        return bool(result.rowcount)


async def run_deposit_finalization_job(
    *,
    engine: Engine,
    acorn,
    npub: str,
    quote: str,
    quote_hash: str,
    owner_token: str,
    wait_seconds: float,
) -> None:
    async def maintain_lease() -> None:
        while True:
            await asyncio.sleep(JOB_HEARTBEAT_SECONDS)
            if not update_deposit_finalization_job(
                engine,
                npub,
                quote_hash,
                owner_token,
                lease_expires_at=utc_now() + timedelta(seconds=JOB_LEASE_SECONDS),
            ):
                raise RuntimeError("Deposit finalization lease ownership was lost")

    heartbeat = asyncio.create_task(maintain_lease())
    try:
        update_deposit_finalization_job(
            engine, npub, quote_hash, owner_token, phase="AWAITING_PAYMENT"
        )
        deadline = monotonic() + min(
            MAX_MONITOR_SECONDS,
            max(0.1, float(wait_seconds)),
        )
        while monotonic() < deadline:
            result = await acorn.finalize_pending_deposit(quote)
            status = str(result.get("status") or "").upper()
            if status in {"COMPLETE", "NOT_FOUND"}:
                update_deposit_finalization_job(
                    engine,
                    npub,
                    quote_hash,
                    owner_token,
                    status="COMPLETE",
                    phase="COMPLETE",
                    error=None,
                )
                return
            await asyncio.sleep(min(POLL_SECONDS, max(0.0, deadline - monotonic())))
        update_deposit_finalization_job(
            engine,
            npub,
            quote_hash,
            owner_token,
            status="PENDING",
            phase="AWAITING_PAYMENT",
            error=None,
        )
    except asyncio.CancelledError:
        update_deposit_finalization_job(
            engine,
            npub,
            quote_hash,
            owner_token,
            status="INTERRUPTED",
            phase="RESUMABLE",
            error="Deposit monitoring was interrupted and can be resumed safely.",
        )
        raise
    except Exception as exc:
        logger.exception("background deposit finalization failed npub=%s", npub)
        update_deposit_finalization_job(
            engine,
            npub,
            quote_hash,
            owner_token,
            status="FAILED",
            phase="REVIEW",
            error=(
                "Safebox could not finalize this deposit. The quote remains "
                f"encrypted on the Acorn relay for recovery ({type(exc).__name__})."
            ),
        )
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)


def run_deposit_finalization_job_in_thread(
    *,
    engine: Engine,
    acorn_factory: Callable[[], Any],
    npub: str,
    quote: str,
    quote_hash: str,
    owner_token: str,
    wait_seconds: float,
    load_timeout_seconds: float,
) -> None:
    async def execute() -> None:
        acorn = acorn_factory()
        await asyncio.wait_for(acorn.load_data(), timeout=load_timeout_seconds)
        await run_deposit_finalization_job(
            engine=engine,
            acorn=acorn,
            npub=npub,
            quote=quote,
            quote_hash=quote_hash,
            owner_token=owner_token,
            wait_seconds=wait_seconds,
        )

    try:
        asyncio.run(execute())
    except Exception as exc:
        logger.exception("deposit finalization thread failed npub=%s", npub)
        update_deposit_finalization_job(
            engine,
            npub,
            quote_hash,
            owner_token,
            status="FAILED",
            phase="REVIEW",
            error=(
                "Safebox could not load this Acorn to finalize its deposit "
                f"({type(exc).__name__}). Reconnect to resume."
            ),
        )
