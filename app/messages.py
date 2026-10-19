"""Explicit, server-rendered private messaging; no automatic message actions."""
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import select

from app.dependencies import AcornDependency, SettingsDependency, DatabaseSessionDependency
from app.models import ClaimedHandle
from app.lnurl_pay import public_request_host
from app.security import CsrfProtector
from app.templating import render_template
from app.private_messages import encode_message, present_messages
from app.payment_discovery import resolve_wallet_message_recipient
from acorn.func_utils import npub_to_hex
from acorn.safebox_discovery import SafeboxDiscoveryError

router = APIRouter()
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def page(settings, **context):
    return HTMLResponse(render_template(
        "messages.html", title="Private Messages",
        csrf_token=CsrfProtector(settings).issue(), **context), headers=HEADERS)


def local_wallet_resolver(request, session):
    def resolve(address):
        if not isinstance(address, str) or address.count('@') != 1:
            return False, None
        name, domain = address.strip().lower().split('@')
        if domain != public_request_host(request).lower():
            return False, None
        registration = session.exec(select(ClaimedHandle).where(
            ClaimedHandle.claimed_handle == name)).first()
        if registration is None:
            return True, None
        return True, {'npub': registration.npub, 'pubkey': npub_to_hex(registration.npub),
                      'relay_hints': [registration.home_relay], 'discovery_source': 'local-wallet'}
    return resolve


@router.get("/messages", response_class=HTMLResponse)
async def inbox(request: Request, acorn: AcornDependency, settings: SettingsDependency,
                session: DatabaseSessionDependency, sent: bool = False):
    if not callable(getattr(acorn, "get_private_messages", None)):
        return page(settings, error="Private messaging requires the updated Safebox Acorn component. Ask the operator to update it.")
    try:
        messages = await asyncio.wait_for(acorn.get_private_messages(), timeout=25)
        messages = await present_messages(messages, legacy_domains=settings.safebox_legacy_domains,
                                          local_resolver=local_wallet_resolver(request, session))
        for message in messages:
            message["date"] = datetime.fromtimestamp(message["created_at"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        error = None
    except Exception:
        messages = []
        error = "The inbox could not be loaded. Please refresh to try again."
    return page(settings, messages=messages, error=error, sent=sent)


@router.post("/messages", response_class=HTMLResponse)
async def send(request: Request, acorn: AcornDependency, settings: SettingsDependency,
               session: DatabaseSessionDependency,
               recipient: str = Form(..., max_length=320),
               message: str = Form(..., max_length=4000),
               csrf_token: str = Form(...)):
    if not CsrfProtector(settings).verify(csrf_token):
        raise HTTPException(403, "Invalid form token")
    if not callable(getattr(acorn, "get_private_messages", None)):
        raise HTTPException(503, "Update Safebox Acorn before using private messaging")
    if not recipient.strip() or not message.strip():
        return page(settings, error="Enter a recipient and a message.", recipient=recipient, draft=message)
    registration = session.exec(
        select(ClaimedHandle).where(ClaimedHandle.npub == acorn.pubkey_bech32)
    ).first()
    sender = (
        f"{registration.claimed_handle}@{public_request_host(request)}".lower()
        if registration is not None else None
    )
    outgoing_message = encode_message(message, sender)
    destination_key, relay_options = recipient.strip(), {}
    if '@' in destination_key:
        try:
            is_local, destination = local_wallet_resolver(request, session)(destination_key)
            if not is_local:
                destination = await resolve_wallet_message_recipient(
                    destination_key, legacy_domains=settings.safebox_legacy_domains)
            if destination is None:
                return page(settings, error="This address does not advertise wallet messaging. No message was sent.", recipient=recipient, draft=message)
            destination_key = destination['npub']
            relay_options = {'dm_relays': destination['relay_hints']}
        except (SafeboxDiscoveryError, ValueError):
            return page(settings, error="Wallet discovery is unavailable or invalid. No message was sent. Try again later.", recipient=recipient, draft=message)
    try:
        # Pass an explicit key after wallet discovery: secure_dm must never
        # reinterpret the address through its legacy/social NIP-05 resolver.
        await asyncio.wait_for(acorn.secure_dm(destination_key, outgoing_message, **relay_options), timeout=30)
    except Exception:
        # Never echo arbitrary library exceptions: they can contain private data.
        return page(settings, error="Delivery could not be confirmed. The message may have reached a relay. Check with the recipient before sending again.", recipient=recipient, draft=message)
    return RedirectResponse("/messages?sent=true", status_code=303, headers=HEADERS)
