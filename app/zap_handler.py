"""Compatibility-oriented NIP-57 handling for the public LNURL callback."""

from __future__ import annotations

import logging

from sqlalchemy.engine import Engine

from app.models import ClaimedHandle, ProviderPayment
from app.nip57 import validate_zap_request
from app.provider_payments import (
    create_zap_invoice,
    enqueue_provider_payment,
    get_payment_for_zap_request,
    get_provider_identity,
    wait_for_provider_invoice,
)


logger = logging.getLogger("safebox_web.zap_handler")


async def handle_zap_invoice_request(
    engine: Engine,
    *,
    registration: ClaimedHandle,
    amount_msat: int,
    comment: str | None,
    metadata: str,
    mint: str,
    nostr: str,
    expected_lnurl: str,
    invoice_wait_seconds: float,
    require_description_hash: bool = False,
) -> ProviderPayment:
    """Validate, persist, and synchronously prepare one idempotent zap invoice.

    This mirrors Safebox 2's reliable behavior of returning a mint invoice
    directly from the zap callback. Settlement, ecash delivery, and kind-9735
    publication remain durable service-worker responsibilities.
    """

    provider_identity = get_provider_identity(engine)
    if provider_identity is None:
        raise RuntimeError("Nostr zap service is not ready")
    zap_request = validate_zap_request(
        nostr,
        amount_msat=amount_msat,
        provider_pubkey=provider_identity.nostr_pubkey,
        expected_lnurl=expected_lnurl,
    )
    existing = get_payment_for_zap_request(engine, zap_request.event_id)
    if existing is not None:
        if existing.invoice:
            logger.info(
                "reusing provider zap invoice payment_id=%s request_event_id=%s",
                existing.payment_id,
                zap_request.event_id,
            )
            return existing
        return await wait_for_provider_invoice(
            engine,
            existing.payment_id,
            timeout=invoice_wait_seconds,
        )

    payment_id = enqueue_provider_payment(
        engine,
        registration=registration,
        amount_msat=amount_msat,
        comment=zap_request.content or comment,
        metadata=metadata,
        mint=mint,
        zap_request=zap_request,
        initial_status="QUOTE_CREATING",
    )
    return await create_zap_invoice(
        engine,
        payment_id,
        require_description_hash=require_description_hash,
    )
