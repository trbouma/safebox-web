"""Durable communication boundary for Lightning-to-Acorn delivery."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import hashlib
import hmac
import json
import logging
from time import time
from types import SimpleNamespace
import uuid

import bolt11
import httpx
from acorn import RetryablePreSwapError
from sqlalchemy import case, or_, update
from stroma import ClientPool
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.models import (
    ClaimedHandle,
    ProviderIdentity,
    ProviderPayment,
    ProviderZap,
    utc_now,
)
from app.nip57 import ValidatedZapRequest, build_zap_receipt
from app.security import normalize_home_mint


logger = logging.getLogger("safebox_web.provider_payments")
MAX_PROVIDER_SETTLEMENT_ATTEMPTS = 60
PROVIDER_SETTLEMENT_DISCOVERY_BATCH_SIZE = 64
PROVIDER_SETTLEMENT_DISCOVERY_CONCURRENCY = 8
PROVIDER_RECIPIENT_QUEUE_STATUSES = (
    "PAID_RECONCILIATION_PENDING",
    "SETTLED",
    "DELIVERING",
    "DELIVERY_FAILED",
)
PROVIDER_SETTLEMENT_RECHECK_SECONDS = 5.0
PROVIDER_STALE_SETTLEMENT_RECHECK_SECONDS = 60.0
LEGACY_SETTLEMENT_TIMEOUT_ERROR = "Invoice settlement timed out"


def _is_quote_not_found_error(exc: Exception) -> bool:
    text = " ".join(
        part
        for part in (type(exc).__name__, str(exc), repr(exc.__cause__))
        if part
    ).lower()
    return "quote not found" in text


def enqueue_provider_payment(
    engine: Engine,
    *,
    registration: ClaimedHandle,
    amount_msat: int,
    comment: str | None,
    metadata: str,
    mint: str,
    zap_request: ValidatedZapRequest | None = None,
    initial_status: str = "QUOTE_PENDING",
) -> str:
    payment_id = uuid.uuid4().hex
    with Session(engine) as session:
        payment = ProviderPayment(
            payment_id=payment_id,
            claimed_handle=registration.claimed_handle,
            recipient_npub=registration.npub,
            recipient_relay=registration.home_relay,
            amount_msat=amount_msat,
            amount_sat=amount_msat // 1000,
            comment=comment,
            lnurl_metadata=metadata,
            status=initial_status,
            mint=mint,
        )
        session.add(payment)
        if zap_request is not None:
            session.add(
                ProviderZap(
                    payment_id=payment_id,
                    request_event_id=zap_request.event_id,
                    request_json=zap_request.raw,
                    receipt_relays_json=json.dumps(list(zap_request.relays)),
                )
            )
        session.commit()
    return payment_id


def set_provider_identity(engine: Engine, nostr_pubkey: str) -> None:
    with Session(engine) as session:
        identity = session.get(ProviderIdentity, "service-acorn")
        if identity is None:
            identity = ProviderIdentity(
                name="service-acorn",
                nostr_pubkey=nostr_pubkey,
            )
        else:
            identity.nostr_pubkey = nostr_pubkey
            identity.updated_at = utc_now()
        session.add(identity)
        session.commit()


def get_provider_identity(engine: Engine) -> ProviderIdentity | None:
    with Session(engine) as session:
        return session.get(ProviderIdentity, "service-acorn")


def get_provider_zap(engine: Engine, payment_id: str) -> ProviderZap | None:
    with Session(engine) as session:
        return session.exec(
            select(ProviderZap).where(ProviderZap.payment_id == payment_id)
        ).first()


def get_payment_for_zap_request(
    engine: Engine, request_event_id: str
) -> ProviderPayment | None:
    with Session(engine) as session:
        zap = session.exec(
            select(ProviderZap).where(
                ProviderZap.request_event_id == request_event_id
            )
        ).first()
        if zap is None:
            return None
        return session.exec(
            select(ProviderPayment).where(
                ProviderPayment.payment_id == zap.payment_id
            )
        ).first()


def get_provider_payment(engine: Engine, payment_id: str) -> ProviderPayment | None:
    with Session(engine) as session:
        return session.exec(
            select(ProviderPayment).where(ProviderPayment.payment_id == payment_id)
        ).first()


async def wait_for_provider_invoice(
    engine: Engine,
    payment_id: str,
    *,
    timeout: float,
    interval: float = 0.05,
) -> ProviderPayment:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        payment = get_provider_payment(engine, payment_id)
        if payment is None:
            raise RuntimeError("Provider payment disappeared from the durable queue")
        if payment.invoice and payment.status in {
            "INVOICE_PENDING",
            "SETTLEMENT_UNCONFIRMED",
            "SETTLED",
            "DELIVERING",
            "RECEIPT_PENDING",
            "RECEIPT_FAILED",
            "DELIVERED",
            "DELIVERY_FAILED",
        }:
            return payment
        if payment.status == "FAILED":
            raise RuntimeError(payment.error or "Provider invoice creation failed")
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("Service Acorn did not create the invoice in time")
        await asyncio.sleep(interval)


def next_provider_payment(engine: Engine, status: str) -> ProviderPayment | None:
    now = utc_now()
    with Session(engine) as session:
        statement = (
            select(ProviderPayment)
            .where(ProviderPayment.status == status)
            .where(
                or_(
                    ProviderPayment.next_check_at.is_(None),
                    ProviderPayment.next_check_at <= now,
                )
            )
            .order_by(
                case(
                    (ProviderPayment.next_check_at.is_(None), 0),
                    else_=1,
                ),
                ProviderPayment.next_check_at,
                ProviderPayment.id,
            )
            .limit(1)
        )
        return session.exec(statement).first()


def due_provider_settlements(
    engine: Engine,
    *,
    limit: int = PROVIDER_SETTLEMENT_DISCOVERY_BATCH_SIZE,
) -> list[ProviderPayment]:
    """Return a bounded priority batch of invoices due for mint discovery.

    A newly created invoice must receive its first mint check before older
    unpaid quotes consume another retry.  After every due invoice has been
    checked at least once, the queue falls back to its chronological retry
    order.  This keeps fresh payments responsive without abandoning durable
    polling of older quotes.
    """

    now = utc_now()
    with Session(engine) as session:
        statement = (
            select(ProviderPayment)
            .where(
                ProviderPayment.status.in_(
                    ("INVOICE_PENDING", "SETTLEMENT_UNCONFIRMED")
                )
            )
            .where(
                or_(
                    ProviderPayment.next_check_at.is_(None),
                    ProviderPayment.next_check_at <= now,
                )
            )
            .order_by(
                case(
                    (ProviderPayment.attempts == 0, 0),
                    else_=1,
                ),
                case(
                    (ProviderPayment.next_check_at.is_(None), 0),
                    else_=1,
                ),
                ProviderPayment.next_check_at,
                ProviderPayment.id,
            )
            .limit(max(1, int(limit)))
        )
        return list(session.exec(statement).all())


def next_provider_settlement(engine: Engine) -> ProviderPayment | None:
    """Return the first invoice in the current settlement-discovery batch."""

    due = due_provider_settlements(engine, limit=1)
    return due[0] if due else None


def provider_recipient_queue(
    engine: Engine,
    recipient_npub: str,
    *,
    limit: int = 50,
) -> list[ProviderPayment]:
    """Return provider payments awaiting recipient-visible completion."""

    with Session(engine) as session:
        statement = (
            select(ProviderPayment)
            .where(ProviderPayment.recipient_npub == recipient_npub)
            .where(ProviderPayment.status.in_(PROVIDER_RECIPIENT_QUEUE_STATUSES))
            .order_by(ProviderPayment.id.desc())
            .limit(max(1, int(limit)))
        )
        return list(session.exec(statement).all())


def reconcile_legacy_settlement_timeouts(engine: Engine) -> int:
    """Resume mint polling for invoices made terminal by the former timeout."""

    recovered = 0
    with Session(engine) as session:
        statement = select(ProviderPayment).where(
            ProviderPayment.status == "FAILED",
            ProviderPayment.error == LEGACY_SETTLEMENT_TIMEOUT_ERROR,
            ProviderPayment.mint_quote.is_not(None),
            ProviderPayment.invoice.is_not(None),
        )
        for payment in session.exec(statement):
            payment.status = "SETTLEMENT_UNCONFIRMED"
            payment.error = (
                "Settlement remains unconfirmed; continuing periodic mint checks"
            )
            payment.next_check_at = utc_now()
            payment.updated_at = utc_now()
            session.add(payment)
            recovered += 1
        session.commit()
    return recovered


def quarantine_abandoned_provider_claims(
    engine: Engine,
    *,
    stale_after: timedelta = timedelta(minutes=15),
) -> dict[str, int]:
    """Move crash-abandoned claims to explicit manual-review states.

    Retrying either invoice creation or ecash publication after an unknown
    outcome can duplicate a financial operation. Startup recovery therefore
    makes ambiguity visible instead of guessing that the operation failed.
    """

    cutoff = utc_now() - stale_after
    transitions = {
        "WORKER_QUOTE_CREATING": (
            "FAILED",
            "Invoice creation was interrupted; verify mint state before retrying",
        ),
        "DELIVERING": (
            "DELIVERY_FAILED",
            "Delivery was interrupted; verify recipient and service Acorn state",
        ),
        "RECEIPT_PUBLISHING": (
            "RECEIPT_FAILED",
            "Ecash delivered; zap receipt publication was interrupted",
        ),
    }
    recovered: dict[str, int] = {}
    with Session(engine) as session:
        for source, (target, error) in transitions.items():
            result = session.exec(
                update(ProviderPayment)
                .where(ProviderPayment.status == source)
                .where(ProviderPayment.updated_at < cutoff)
                .values(status=target, error=error, next_check_at=None, updated_at=utc_now())
            )
            recovered[source] = int(result.rowcount or 0)
        session.commit()
    return recovered


def update_provider_payment(engine: Engine, payment_id: str, **changes) -> None:
    with Session(engine) as session:
        payment = session.exec(
            select(ProviderPayment).where(ProviderPayment.payment_id == payment_id)
        ).first()
        if payment is None:
            raise RuntimeError(f"Provider payment not found: {payment_id}")
        for name, value in changes.items():
            setattr(payment, name, value)
        payment.updated_at = utc_now()
        session.add(payment)
        session.commit()


def transition_provider_payment(
    engine: Engine,
    payment_id: str,
    *,
    expected_status: str | tuple[str, ...],
    **changes,
) -> ProviderPayment | None:
    """Apply one compare-and-swap transition and return the refreshed row.

    External operations can complete after another actor has advanced or
    quarantined a payment. Requiring the state observed by the caller prevents
    stale results from overwriting that newer decision.
    """

    expected = (
        (expected_status,)
        if isinstance(expected_status, str)
        else tuple(expected_status)
    )
    if not expected:
        raise ValueError("At least one expected provider-payment status is required")
    values = {**changes, "updated_at": utc_now()}
    with Session(engine) as session:
        result = session.exec(
            update(ProviderPayment)
            .where(ProviderPayment.payment_id == payment_id)
            .where(ProviderPayment.status.in_(expected))
            .values(**values)
        )
        session.commit()
        if not result.rowcount:
            return None
    return get_provider_payment(engine, payment_id)


def claim_next_provider_payment(
    engine: Engine,
    status: str,
    *,
    claimed_status: str,
    **changes,
) -> ProviderPayment | None:
    """Atomically claim the oldest due row in ``status``.

    Candidate selection and claiming are separate database statements, so a
    competing worker may see the same candidate. The conditional UPDATE is the
    serialization point: only one actor can change the expected status.
    """

    while candidate := next_provider_payment(engine, status):
        claimed = transition_provider_payment(
            engine,
            candidate.payment_id,
            expected_status=status,
            status=claimed_status,
            **changes,
        )
        if claimed is not None:
            return claimed
    return None


def update_provider_zap(engine: Engine, payment_id: str, **changes) -> None:
    with Session(engine) as session:
        zap = session.exec(
            select(ProviderZap).where(ProviderZap.payment_id == payment_id)
        ).first()
        if zap is None:
            raise RuntimeError(f"Provider zap not found: {payment_id}")
        for name, value in changes.items():
            setattr(zap, name, value)
        session.add(zap)
        session.commit()


def _request_zap_mint_quote(
    payment: ProviderPayment,
    zap: ProviderZap,
    *,
    require_description_hash: bool = True,
):
    mint = normalize_home_mint(payment.mint)
    request_body: dict[str, object] = {
        "amount": payment.amount_sat,
        "unit": "sat",
    }
    if require_description_hash:
        request_body["description"] = zap.request_json
    response = httpx.post(
        f"{mint}/v1/mint/quote/bolt11",
        json=request_body,
        timeout=httpx.Timeout(10.0, connect=5.0),
    )
    response.raise_for_status()
    payload = response.json()
    quote = str(payload["quote"])
    invoice = str(payload["request"])
    decoded = bolt11.decode(invoice)
    actual_hash = str(getattr(decoded, "description_hash", "") or "").lower()
    expected_hash = hashlib.sha256(zap.request_json.encode("utf-8")).hexdigest()
    description_hash_bound = bool(
        actual_hash and hmac.compare_digest(actual_hash, expected_hash)
    )
    if require_description_hash and not description_hash_bound:
        raise RuntimeError(
            "Mint invoice does not commit to the NIP-57 zap request description"
        )
    if not require_description_hash and not description_hash_bound:
        logger.warning(
            "provider zap invoice is not description-hash bound; "
            "compatibility mode permits payment but strict NIP-57 clients may "
            "reject the receipt payment_id=%s mint=%s",
            payment.payment_id,
            mint,
        )
    return SimpleNamespace(
        quote=quote,
        invoice=invoice,
        description_hash_bound=description_hash_bound,
    )


async def create_zap_invoice(
    engine: Engine,
    payment_id: str,
    *,
    require_description_hash: bool = False,
) -> ProviderPayment:
    """Create a zap invoice in the callback while preserving durable state.

    Social clients expect an LNURL callback to return promptly. Zap rows enter
    ``QUOTE_CREATING`` so the background worker cannot race the callback for a
    second mint quote; settlement and delivery remain worker-owned.
    """

    payment = get_provider_payment(engine, payment_id)
    zap = get_provider_zap(engine, payment_id)
    if payment is None or zap is None:
        raise RuntimeError("Durable zap payment state is missing")
    if payment.invoice:
        return payment
    if payment.status != "QUOTE_CREATING":
        raise RuntimeError(
            payment.error or f"Zap invoice is not creatable from {payment.status}"
        )
    try:
        quote = await asyncio.to_thread(
            _request_zap_mint_quote,
            payment,
            zap,
            require_description_hash=require_description_hash,
        )
        transitioned = transition_provider_payment(
            engine,
            payment_id,
            expected_status="QUOTE_CREATING",
            status="INVOICE_PENDING",
            mint_quote=quote.quote,
            invoice=quote.invoice,
            error=None,
            next_check_at=utc_now(),
        )
        logger.info(
            "provider zap invoice ready payment_id=%s handle=%s amount_sat=%s "
            "description_hash_bound=%s",
            payment_id,
            payment.claimed_handle,
            payment.amount_sat,
            quote.description_hash_bound,
        )
        if transitioned is None:
            raise RuntimeError("Zap invoice state changed while creating the quote")
        return transitioned
    except Exception as exc:
        logger.exception("provider zap invoice creation failed payment_id=%s", payment_id)
        transition_provider_payment(
            engine,
            payment_id,
            expected_status="QUOTE_CREATING",
            status="FAILED",
            error=f"Zap invoice creation failed: {type(exc).__name__}",
        )
        raise RuntimeError("Unable to create a Lightning invoice") from exc


async def _publish_provider_zap_receipt(acorn, payment: ProviderPayment, zap: ProviderZap):
    receipt = build_zap_receipt(
        zap_request_json=zap.request_json,
        invoice=str(payment.invoice),
        acorn=acorn,
    )
    relays = json.loads(zap.receipt_relays_json)
    async with ClientPool(relays) as clients:
        clients.publish(receipt)
    return receipt


async def _discover_provider_settlements(acorn, invoices):
    """Read mint quote states concurrently without mutating wallet proofs."""

    semaphore = asyncio.Semaphore(PROVIDER_SETTLEMENT_DISCOVERY_CONCURRENCY)

    async def discover(invoice):
        if not invoice.mint_quote:
            return invoice, RuntimeError("Mint quote is missing")
        try:
            async with semaphore:
                quote = await acorn.get_quote_state(
                    quote=invoice.mint_quote,
                    mint=invoice.mint,
                )
            return invoice, quote
        except Exception as exc:
            return invoice, exc

    return await asyncio.gather(*(discover(invoice) for invoice in invoices))


async def process_provider_payments_once(
    engine: Engine,
    acorn,
    *,
    gift_wrap_retention_seconds: int | None = None,
    nip57_require_description_hash: bool = False,
    delivery_retry_attempts: int = 4,
    delivery_retry_base_seconds: float = 2.0,
    delivery_retry_max_seconds: float = 60.0,
) -> bool:
    """Process at most one item from each safe payment transition."""

    changed = False
    quote_request = claim_next_provider_payment(
        engine,
        "QUOTE_PENDING",
        claimed_status="WORKER_QUOTE_CREATING",
        error=None,
        next_check_at=None,
    )
    if quote_request is not None:
        try:
            zap = get_provider_zap(engine, quote_request.payment_id)
            if zap is None:
                quote = await asyncio.to_thread(
                    acorn.deposit,
                    amount=quote_request.amount_sat,
                    mint=quote_request.mint,
                )
            else:
                quote = await asyncio.to_thread(
                    _request_zap_mint_quote,
                    quote_request,
                    zap,
                    require_description_hash=nip57_require_description_hash,
                )
            transition_provider_payment(
                engine,
                quote_request.payment_id,
                expected_status="WORKER_QUOTE_CREATING",
                status="INVOICE_PENDING",
                mint_quote=quote.quote,
                invoice=quote.invoice,
                error=None,
                next_check_at=utc_now(),
            )
            logger.info(
                "provider invoice ready payment_id=%s handle=%s amount_sat=%s",
                quote_request.payment_id,
                quote_request.claimed_handle,
                quote_request.amount_sat,
            )
        except Exception as exc:
            logger.exception(
                "provider invoice creation failed payment_id=%s",
                quote_request.payment_id,
            )
            transition_provider_payment(
                engine,
                quote_request.payment_id,
                expected_status="WORKER_QUOTE_CREATING",
                status="FAILED",
                error=f"Invoice creation failed: {type(exc).__name__}",
            )
        changed = True

    due_invoices = due_provider_settlements(engine)
    if due_invoices:
        discoveries = await _discover_provider_settlements(acorn, due_invoices)
        for discovered_invoice, result in discoveries:
            if isinstance(result, Exception):
                terminal_error = (
                    "Mint quote not found"
                    if _is_quote_not_found_error(result)
                    else None
                )
                logger.warning(
                    "provider settlement discovery failed payment_id=%s error=%s",
                    discovered_invoice.payment_id,
                    type(result).__name__,
                )
                paid = False
            else:
                terminal_error = None
                paid = bool(getattr(result, "paid", False)) or str(
                    getattr(result, "state", "")
                ).upper() == "PAID"
            if paid:
                transition_provider_payment(
                    engine,
                    discovered_invoice.payment_id,
                    expected_status=discovered_invoice.status,
                    status="PAID_RECONCILIATION_PENDING",
                    attempts=discovered_invoice.attempts + 1,
                    error=None,
                    next_check_at=None,
                )
                continue

            attempts = discovered_invoice.attempts + 1
            settlement_stale = (
                terminal_error is None
                and (
                    discovered_invoice.status == "SETTLEMENT_UNCONFIRMED"
                    or attempts >= MAX_PROVIDER_SETTLEMENT_ATTEMPTS
                )
            )
            transition_provider_payment(
                engine,
                discovered_invoice.payment_id,
                expected_status=discovered_invoice.status,
                status=(
                    "FAILED"
                    if terminal_error
                    else "SETTLEMENT_UNCONFIRMED"
                    if settlement_stale
                    else "INVOICE_PENDING"
                ),
                attempts=attempts,
                error=(
                    terminal_error
                    or (
                        "Settlement remains unconfirmed; continuing periodic mint checks"
                        if settlement_stale
                        else None
                    )
                ),
                next_check_at=(
                    None
                    if terminal_error
                    else utc_now()
                    + timedelta(
                        seconds=(
                            PROVIDER_STALE_SETTLEMENT_RECHECK_SECONDS
                            if settlement_stale
                            else PROVIDER_SETTLEMENT_RECHECK_SECONDS
                        )
                    )
                ),
            )
            if terminal_error:
                logger.warning(
                    "provider invoice failed payment_id=%s reason=%s",
                    discovered_invoice.payment_id,
                    terminal_error,
                )
            elif (
                settlement_stale
                and discovered_invoice.status != "SETTLEMENT_UNCONFIRMED"
            ):
                logger.warning(
                    "provider invoice settlement remains unconfirmed; continuing "
                    "periodic checks payment_id=%s attempts=%s",
                    discovered_invoice.payment_id,
                    attempts,
                )
        changed = True

    # Quote discovery above is read-only and safely concurrent. Proof minting
    # remains serialized through the single service Acorn owner.
    invoice = next_provider_payment(engine, "PAID_RECONCILIATION_PENDING")
    if invoice is not None and invoice.mint_quote:
        terminal_error: str | None = None
        try:
            paid, _ = await acorn.check_quote(
                quote=invoice.mint_quote,
                amount=invoice.amount_sat,
                mint=(
                    invoice.mint.removeprefix("https://").removeprefix("http://")
                ),
            )
        except Exception as exc:
            if _is_quote_not_found_error(exc):
                terminal_error = "Mint quote not found"
            logger.warning(
                "provider settlement check failed payment_id=%s error=%s",
                invoice.payment_id,
                type(exc).__name__,
            )
            paid = False
        attempts = invoice.attempts + 1
        transitioned = transition_provider_payment(
            engine,
            invoice.payment_id,
            expected_status=invoice.status,
            status=(
                "SETTLED"
                if paid
                else "FAILED"
                if terminal_error
                else "PAID_RECONCILIATION_PENDING"
            ),
            attempts=attempts,
            error=(
                terminal_error
                or (
                    "Lightning payment confirmed; ecash issuance remains pending"
                    if not paid
                    else None
                )
            ),
            next_check_at=(
                None
                if paid or terminal_error
                else utc_now()
                + timedelta(
                    seconds=(
                        PROVIDER_SETTLEMENT_RECHECK_SECONDS
                    )
                )
            ),
        )
        if transitioned is None:
            logger.warning(
                "provider settlement result discarded after concurrent state "
                "change payment_id=%s expected_status=%s",
                invoice.payment_id,
                invoice.status,
            )
        if paid:
            logger.info(
                "provider invoice settled payment_id=%s amount_sat=%s",
                invoice.payment_id,
                invoice.amount_sat,
            )
        elif terminal_error:
            logger.warning(
                "provider invoice failed payment_id=%s reason=%s",
                invoice.payment_id,
                terminal_error,
            )
        elif not paid:
            logger.warning(
                "provider paid invoice reconciliation remains pending "
                "payment_id=%s attempts=%s",
                invoice.payment_id,
                attempts,
            )
        changed = True

    settled_candidate = next_provider_payment(engine, "SETTLED")
    settled = None
    if settled_candidate is not None:
        settled = transition_provider_payment(
            engine,
            settled_candidate.payment_id,
            expected_status="SETTLED",
            status="DELIVERING",
            delivery_attempts=int(settled_candidate.delivery_attempts) + 1,
            next_check_at=None,
        )
    if settled is not None:
        # Mark before external publication. An interrupted/ambiguous publish is
        # deliberately not retried automatically because that could duplicate
        # the recipient payment.
        delivery_attempt = int(settled.delivery_attempts)
        try:
            expiration = (
                int(time()) + gift_wrap_retention_seconds
                if gift_wrap_retention_seconds is not None
                else None
            )
            delivery = await acorn.send_ecash_transfer(
                amount=settled.amount_sat,
                recipient=settled.recipient_npub,
                relay=settled.recipient_relay,
                comment=(
                    settled.comment
                    or f"Lightning payment to {settled.claimed_handle}"
                ),
                expiration=expiration,
            )
            transition_provider_payment(
                engine,
                settled.payment_id,
                expected_status="DELIVERING",
                status=(
                    "RECEIPT_PENDING"
                    if get_provider_zap(engine, settled.payment_id) is not None
                    else "DELIVERED"
                ),
                delivery_event_id=(
                    str(delivery.get("event_id") or delivery.get("event") or "")
                    or None
                ),
                error=None,
                next_check_at=None,
            )
            logger.info(
                "provider ecash delivered payment_id=%s event_id=%s relay=%s expiration=%s",
                settled.payment_id,
                delivery.get("event_id") or delivery.get("event"),
                settled.recipient_relay,
                expiration,
            )
        except RetryablePreSwapError as exc:
            detail = str(exc).strip() or repr(exc)
            if delivery_attempt < max(1, int(delivery_retry_attempts)):
                retry_delay = min(
                    float(delivery_retry_max_seconds),
                    float(delivery_retry_base_seconds)
                    * (2 ** max(0, delivery_attempt - 1)),
                )
                transition_provider_payment(
                    engine,
                    settled.payment_id,
                    expected_status="DELIVERING",
                    status="SETTLED",
                    error=(
                        f"Retryable pre-swap delivery failure: {type(exc).__name__}: "
                        f"{detail}"
                    )[:500],
                    next_check_at=utc_now() + timedelta(seconds=retry_delay),
                )
                logger.warning(
                    "provider ecash delivery scheduled for safe retry "
                    "payment_id=%s attempt=%s max_attempts=%s delay_seconds=%.1f "
                    "error_type=%s error=%r",
                    settled.payment_id,
                    delivery_attempt,
                    delivery_retry_attempts,
                    retry_delay,
                    type(exc).__name__,
                    exc,
                )
            else:
                transition_provider_payment(
                    engine,
                    settled.payment_id,
                    expected_status="DELIVERING",
                    status="DELIVERY_FAILED",
                    error=(
                        "Safe delivery retries exhausted: "
                        f"{type(exc).__name__}: {detail}"
                    )[:500],
                    next_check_at=None,
                )
                logger.error(
                    "provider ecash safe delivery retries exhausted "
                    "payment_id=%s attempts=%s error_type=%s error=%r",
                    settled.payment_id,
                    delivery_attempt,
                    type(exc).__name__,
                    exc,
                )
        except Exception as exc:
            detail = str(exc).strip() or repr(exc)
            logger.exception(
                "provider ecash delivery requires review payment_id=%s error_type=%s error=%r",
                settled.payment_id,
                type(exc).__name__,
                exc,
            )
            transition_provider_payment(
                engine,
                settled.payment_id,
                expected_status="DELIVERING",
                status="DELIVERY_FAILED",
                error=(
                    f"Delivery outcome requires review: {type(exc).__name__}: "
                    f"{detail}"
                )[:500],
                next_check_at=None,
            )
        changed = True

    receipt_pending = claim_next_provider_payment(
        engine,
        "RECEIPT_PENDING",
        claimed_status="RECEIPT_PUBLISHING",
    )
    if receipt_pending is not None:
        zap = get_provider_zap(engine, receipt_pending.payment_id)
        if zap is None:
            transition_provider_payment(
                engine,
                receipt_pending.payment_id,
                expected_status="RECEIPT_PUBLISHING",
                status="DELIVERED",
            )
        else:
            try:
                receipt = await _publish_provider_zap_receipt(
                    acorn,
                    receipt_pending,
                    zap,
                )
                update_provider_zap(
                    engine,
                    receipt_pending.payment_id,
                    receipt_event_id=str(receipt.id),
                    receipt_json=json.dumps(
                        receipt.data(), separators=(",", ":"), sort_keys=True
                    ),
                    receipt_error=None,
                )
                transition_provider_payment(
                    engine,
                    receipt_pending.payment_id,
                    expected_status="RECEIPT_PUBLISHING",
                    status="DELIVERED",
                    error=None,
                )
                logger.info(
                    "provider zap receipt published payment_id=%s event_id=%s",
                    receipt_pending.payment_id,
                    receipt.id,
                )
            except Exception as exc:
                logger.exception(
                    "provider zap receipt publication failed payment_id=%s",
                    receipt_pending.payment_id,
                )
                update_provider_zap(
                    engine,
                    receipt_pending.payment_id,
                    receipt_error=f"Receipt publication failed: {type(exc).__name__}",
                )
                transition_provider_payment(
                    engine,
                    receipt_pending.payment_id,
                    expected_status="RECEIPT_PUBLISHING",
                    status="RECEIPT_FAILED",
                    error="Ecash delivered; NIP-57 receipt publication requires review",
                )
        changed = True

    return changed
