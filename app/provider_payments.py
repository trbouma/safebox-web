"""Durable communication boundary for Lightning-to-Acorn delivery."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import uuid

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.models import ClaimedHandle, ProviderPayment, utc_now


logger = logging.getLogger("safebox_web.provider_payments")


def enqueue_provider_payment(
    engine: Engine,
    *,
    registration: ClaimedHandle,
    amount_msat: int,
    comment: str | None,
    metadata: str,
    mint: str,
) -> str:
    payment_id = uuid.uuid4().hex
    with Session(engine) as session:
        session.add(
            ProviderPayment(
                payment_id=payment_id,
                claimed_handle=registration.claimed_handle,
                recipient_npub=registration.npub,
                recipient_relay=registration.home_relay,
                amount_msat=amount_msat,
                amount_sat=amount_msat // 1000,
                comment=comment,
                lnurl_metadata=metadata,
                mint=mint,
            )
        )
        session.commit()
    return payment_id


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
        if payment.status == "INVOICE_PENDING" and payment.invoice:
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
            .order_by(ProviderPayment.id)
        )
        for payment in session.exec(statement):
            if payment.next_check_at is None or payment.next_check_at <= now:
                return payment
    return None


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


async def process_provider_payments_once(engine: Engine, acorn) -> bool:
    """Process at most one item from each safe payment transition."""

    changed = False
    quote_request = next_provider_payment(engine, "QUOTE_PENDING")
    if quote_request is not None:
        try:
            quote = await asyncio.to_thread(
                acorn.deposit,
                amount=quote_request.amount_sat,
                mint=quote_request.mint,
            )
            update_provider_payment(
                engine,
                quote_request.payment_id,
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
            update_provider_payment(
                engine,
                quote_request.payment_id,
                status="FAILED",
                error=f"Invoice creation failed: {type(exc).__name__}",
            )
        changed = True

    invoice = next_provider_payment(engine, "INVOICE_PENDING")
    if invoice is not None and invoice.mint_quote:
        try:
            paid, _ = await acorn.check_quote(
                quote=invoice.mint_quote,
                amount=invoice.amount_sat,
                mint=(
                    invoice.mint.removeprefix("https://").removeprefix("http://")
                ),
            )
        except Exception as exc:
            logger.warning(
                "provider settlement check failed payment_id=%s error=%s",
                invoice.payment_id,
                type(exc).__name__,
            )
            paid = False
        update_provider_payment(
            engine,
            invoice.payment_id,
            status="SETTLED" if paid else "INVOICE_PENDING",
            attempts=invoice.attempts + 1,
            next_check_at=None if paid else utc_now() + timedelta(seconds=2),
        )
        if paid:
            logger.info(
                "provider invoice settled payment_id=%s amount_sat=%s",
                invoice.payment_id,
                invoice.amount_sat,
            )
        changed = True

    settled = next_provider_payment(engine, "SETTLED")
    if settled is not None:
        # Mark before external publication. An interrupted/ambiguous publish is
        # deliberately not retried automatically because that could duplicate
        # the recipient payment.
        update_provider_payment(engine, settled.payment_id, status="DELIVERING")
        try:
            delivery = await acorn.send_ecash_transfer(
                amount=settled.amount_sat,
                recipient=settled.recipient_npub,
                relay=settled.recipient_relay,
                comment=(
                    settled.comment
                    or f"Lightning payment to {settled.claimed_handle}"
                ),
            )
            update_provider_payment(
                engine,
                settled.payment_id,
                status="DELIVERED",
                delivery_event_id=(
                    str(delivery.get("event_id") or delivery.get("event") or "")
                    or None
                ),
                error=None,
            )
            logger.info(
                "provider ecash delivered payment_id=%s event_id=%s relay=%s",
                settled.payment_id,
                delivery.get("event_id") or delivery.get("event"),
                settled.recipient_relay,
            )
        except Exception as exc:
            logger.exception(
                "provider ecash delivery requires review payment_id=%s",
                settled.payment_id,
            )
            update_provider_payment(
                engine,
                settled.payment_id,
                status="DELIVERY_FAILED",
                error=f"Delivery outcome requires review: {type(exc).__name__}",
            )
        changed = True

    return changed
