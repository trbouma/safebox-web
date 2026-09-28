"""Explicit, server-rendered private messaging; no automatic message actions."""
import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import AcornDependency, SettingsDependency
from app.security import CsrfProtector
from app.templating import render_template

router = APIRouter()
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def page(settings, **context):
    return HTMLResponse(render_template(
        "messages.html", title="Private Messages",
        csrf_token=CsrfProtector(settings).issue(), **context), headers=HEADERS)


@router.get("/messages", response_class=HTMLResponse)
async def inbox(acorn: AcornDependency, settings: SettingsDependency, sent: bool = False):
    if not callable(getattr(acorn, "get_private_messages", None)):
        return page(settings, error="Private messaging requires the updated Safebox Acorn component. Ask the operator to update it.")
    try:
        messages = await asyncio.wait_for(acorn.get_private_messages(), timeout=25)
        for message in messages:
            message["date"] = datetime.fromtimestamp(message["created_at"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        error = None
    except Exception:
        messages = []
        error = "The inbox could not be loaded. Please refresh to try again."
    return page(settings, messages=messages, error=error, sent=sent)


@router.post("/messages", response_class=HTMLResponse)
async def send(acorn: AcornDependency, settings: SettingsDependency,
               recipient: str = Form(..., max_length=320),
               message: str = Form(..., max_length=4000),
               csrf_token: str = Form(...)):
    if not CsrfProtector(settings).verify(csrf_token):
        raise HTTPException(403, "Invalid form token")
    if not callable(getattr(acorn, "get_private_messages", None)):
        raise HTTPException(503, "Update Safebox Acorn before using private messaging")
    if not recipient.strip() or not message.strip():
        return page(settings, error="Enter a recipient and a message.", recipient=recipient, draft=message)
    try:
        await asyncio.wait_for(acorn.secure_dm(recipient.strip(), message), timeout=30)
    except Exception:
        # Never echo arbitrary library exceptions: they can contain private data.
        return page(settings, error="Delivery could not be confirmed. The message may have reached a relay. Check with the recipient before sending again.", recipient=recipient, draft=message)
    return RedirectResponse("/messages?sent=true", status_code=303, headers=HEADERS)
