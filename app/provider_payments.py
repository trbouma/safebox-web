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
from monstr.client.client import ClientPool
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


def enqueue_provider_payment(
    engine: Engine,
    *,
    registration: ClaimedHandle,
    amount_msat: int,
    comment: str | None,
    metadata: str,
    mint: str,
    zap_request: ValidatedZapRequest | None = None,
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


async def process_provider_payments_once(
    engine: Engine,
    acorn,
    *,
    gift_wrap_retention_seconds: int | None = None,
    nip57_require_description_hash: bool = False,
) -> bool:
    """Process at most one item from each safe payment transition."""

    changed = False
    quote_request = next_provider_payment(engine, "QUOTE_PENDING")
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
            update_provider_payment(
                engine,
                settled.payment_id,
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
            )
            logger.info(
                "provider ecash delivered payment_id=%s event_id=%s relay=%s expiration=%s",
                settled.payment_id,
                delivery.get("event_id") or delivery.get("event"),
                settled.recipient_relay,
                expiration,
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

    receipt_pending = next_provider_payment(engine, "RECEIPT_PENDING")
    if receipt_pending is not None:
        zap = get_provider_zap(engine, receipt_pending.payment_id)
        if zap is None:
            update_provider_payment(
                engine,
                receipt_pending.payment_id,
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
                update_provider_payment(
                    engine,
                    receipt_pending.payment_id,
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
                update_provider_payment(
                    engine,
                    receipt_pending.payment_id,
                    status="RECEIPT_FAILED",
                    error="Ecash delivered; NIP-57 receipt publication requires review",
                )
        changed = True

    return changed
