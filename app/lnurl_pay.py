"""Public LNURL-pay path backed by the standalone service Acorn worker."""

from __future__ import annotations

import json
import logging
from urllib.parse import urlsplit

from bech32 import bech32_encode, convertbits
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from sqlmodel import Session, select

from app.dependencies import SettingsDependency
from app.models import ClaimedHandle
from app.nip57 import validate_zap_request
from app.provider_payments import (
    enqueue_provider_payment,
    get_payment_for_zap_request,
    get_provider_identity,
    get_provider_payment,
    get_provider_zap,
    wait_for_provider_invoice,
)


logger = logging.getLogger("safebox_web.lnurl_pay")
router = APIRouter()


def encode_lnurl(url: str) -> str:
    """Encode an HTTP(S) LNURL-pay endpoint as an uppercase Bech32 LNURL."""

    normalized = str(url or "").strip()
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("LNURL must encode an absolute HTTP(S) URL")
    data = convertbits(list(normalized.encode("utf-8")), 8, 5, True)
    if data is None:
        raise ValueError("Unable to convert the LNURL endpoint to Bech32 data")
    encoded = bech32_encode("lnurl", data)
    if not encoded:
        raise ValueError("Unable to encode the LNURL endpoint")
    return encoded.upper()


def _cors_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Accept, Content-Type, Origin, User-Agent",
    }


def _error(reason: str) -> JSONResponse:
    return JSONResponse(
        {"status": "ERROR", "reason": reason},
        headers=_cors_headers(),
    )


def _registration(engine, handle: str) -> ClaimedHandle | None:
    with Session(engine) as session:
        return session.exec(
            select(ClaimedHandle).where(
                ClaimedHandle.claimed_handle == str(handle or "").strip().lower()
            )
        ).first()


def _metadata(handle: str, host: str) -> str:
    address = f"{handle}@{host}"
    return json.dumps(
        [
            ["text/plain", f"Payment to {address}"],
            ["text/identifier", address],
        ],
        separators=(",", ":"),
    )


@router.get("/.well-known/lnurlp/{handle}", name="lnurl_pay_resolve")
async def lnurl_pay_resolve(
    request: Request,
    handle: str,
    settings: SettingsDependency,
):
    registration = _registration(request.app.state.database_engine, handle)
    if registration is None:
        return _error("Lightning address not found")
    callback = str(
        request.url_for(
            "lnurl_pay_callback",
            handle=registration.claimed_handle,
        )
    )
    host = request.url.hostname or "localhost"
    provider_identity = get_provider_identity(request.app.state.database_engine)
    payload = {
        "callback": callback,
        "minSendable": settings.lnurl_min_sendable_msat,
        "maxSendable": settings.lnurl_max_sendable_msat,
        "metadata": _metadata(registration.claimed_handle, host),
        "commentAllowed": settings.lnurl_comment_allowed,
        "tag": "payRequest",
    }
    if provider_identity is not None:
        payload.update(
            {
                "allowsNostr": True,
                "nostrPubkey": provider_identity.nostr_pubkey,
            }
        )
    return JSONResponse(
        payload,
        headers=_cors_headers(),
    )


@router.options("/.well-known/lnurlp/{handle}")
async def lnurl_pay_resolve_options(handle: str):
    return Response(status_code=200, headers=_cors_headers())


@router.get("/lnpay/{handle}", name="lnurl_pay_callback")
async def lnurl_pay_callback(
    request: Request,
    handle: str,
    settings: SettingsDependency,
    amount: str = "",
    comment: str | None = None,
    nostr: str | None = None,
):
    registration = _registration(request.app.state.database_engine, handle)
    if registration is None:
        return _error("Lightning address not found")
    try:
        amount_msat = int(amount)
    except (TypeError, ValueError):
        return _error("Amount must be an integer number of millisatoshis")
    if not (
        settings.lnurl_min_sendable_msat
        <= amount_msat
        <= settings.lnurl_max_sendable_msat
    ):
        return _error("Amount is outside the supported range")
    if amount_msat % 1000:
        return _error("This service currently accepts whole-satoshi amounts only")
    if comment is not None and len(comment) > settings.lnurl_comment_allowed:
        return _error("Comment is longer than commentAllowed")

    host = request.url.hostname or "localhost"
    metadata = _metadata(registration.claimed_handle, host)
    zap_request = None
    if nostr:
        provider_identity = get_provider_identity(request.app.state.database_engine)
        if provider_identity is None:
            return _error("Nostr zap service is not ready")
        try:
            from acorn.func_utils import npub_to_hex

            expected_lnurl = encode_lnurl(
                str(
                    request.url_for(
                        "lnurl_pay_resolve",
                        handle=registration.claimed_handle,
                    )
                )
            )
            zap_request = validate_zap_request(
                nostr,
                amount_msat=amount_msat,
                recipient_pubkey=npub_to_hex(registration.npub),
                provider_pubkey=provider_identity.nostr_pubkey,
                expected_lnurl=expected_lnurl,
            )
        except (RuntimeError, ValueError) as exc:
            logger.warning("zap request rejected handle=%s error=%s", handle, exc)
            return _error(str(exc))
        existing = get_payment_for_zap_request(
            request.app.state.database_engine,
            zap_request.event_id,
        )
        if existing is not None:
            payment_id = existing.payment_id
        else:
            payment_id = enqueue_provider_payment(
                request.app.state.database_engine,
                registration=registration,
                amount_msat=amount_msat,
                comment=zap_request.content or comment,
                metadata=metadata,
                mint=settings.service_acorn_home_mint,
                zap_request=zap_request,
            )
    else:
        payment_id = enqueue_provider_payment(
            request.app.state.database_engine,
            registration=registration,
            amount_msat=amount_msat,
            comment=comment,
            metadata=metadata,
            mint=settings.service_acorn_home_mint,
        )
    try:
        payment = await wait_for_provider_invoice(
            request.app.state.database_engine,
            payment_id,
            timeout=settings.provider_invoice_wait_seconds,
        )
    except TimeoutError:
        logger.warning("provider invoice wait timed out payment_id=%s", payment_id)
        return _error("Lightning invoice service timed out; please try again")
    except RuntimeError:
        logger.exception("provider invoice request failed payment_id=%s", payment_id)
        return _error("Unable to create a Lightning invoice")

    return JSONResponse(
        {
            "pr": payment.invoice,
            "routes": [],
            "successAction": {
                "tag": "message",
                "message": f"Payment will be delivered to {registration.claimed_handle}",
            },
        },
        headers=_cors_headers(),
    )


@router.options("/lnpay/{handle}")
async def lnurl_pay_callback_options(handle: str):
    return Response(status_code=200, headers=_cors_headers())


@router.get("/lnpay/status/{payment_id}")
async def provider_payment_status(request: Request, payment_id: str):
    payment = get_provider_payment(request.app.state.database_engine, payment_id)
    if payment is None:
        return _error("Provider payment not found")
    zap = get_provider_zap(request.app.state.database_engine, payment.payment_id)
    return JSONResponse(
        {
            "status": "OK",
            "paymentId": payment.payment_id,
            "paymentStatus": payment.status,
            "handle": payment.claimed_handle,
            "amount": payment.amount_sat,
            "unit": "sat",
            "deliveryEventId": payment.delivery_event_id,
            "zapReceiptEventId": zap.receipt_event_id if zap else None,
            "error": payment.error,
        },
        headers=_cors_headers(),
    )
