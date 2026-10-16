"""NFC/QR login: bounded input, persistent limits and browser-bound attempts."""
import hashlib
import base64
import hmac
import re
import secrets
from contextlib import contextmanager
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session

from app.models import NfcCardPolicy, NfcLoginChallenge, NfcLoginRate
from app import nfc_qr
from app.nfc_cards import HEADERS, _decrypt_card, now, require_csrf, require_origin, unpack
from app.security import CsrfProtector, set_session_cookie
from app.templating import render_template

router = APIRouter(prefix="/nfc/login")
BROWSER_COOKIE = "__Host-safebox_nfc_login"
TTL = 300
ATTEMPTS = 5
SOURCE_LIMIT = 30  # all login requests per source per five-minute window
FAILURE = "NFC login was not authorized. Check your PIN and active card. After five failed PIN attempts, connect with recovery material and replace the card."


def digest(value):
    return hashlib.sha256(value.encode("ascii")).hexdigest()


@contextmanager
def database_boundary():
    try:
        yield
    except SQLAlchemyError:
        raise HTTPException(503, "NFC login is temporarily unavailable. No session was issued.", headers=HEADERS) from None


def throttle(request):
    """Atomically count all public login traffic, before parsing/decryption."""
    settings = request.app.state.settings
    clock = now()
    source = request.client.host if request.client else "unknown"
    bucket = hmac.new(settings.cookie_key.encode(),
        f"nfc-login-rate/v1:{clock // TTL}:{source}".encode(), hashlib.sha256).hexdigest()
    engine = request.app.state.database_engine
    with Session(engine) as session:
        session.execute(delete(NfcLoginRate).where(NfcLoginRate.expires_at <= clock))
        session.execute(delete(NfcLoginChallenge).where(NfcLoginChallenge.expires_at <= clock))
        session.commit()
        if session.get(NfcLoginRate, bucket) is None:
            session.add(NfcLoginRate(bucket=bucket, expires_at=(clock // TTL + 1) * TTL))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
        result = session.execute(update(NfcLoginRate).where(
            NfcLoginRate.bucket == bucket, NfcLoginRate.requests < SOURCE_LIMIT
        ).values(requests=NfcLoginRate.requests + 1))
        session.commit()
        if result.rowcount != 1:
            raise HTTPException(429, "Too many NFC login requests. Wait five minutes before retrying.",
                                headers={**HEADERS, "Retry-After": str(TTL)})


async def read_form(request):
    """No unbounded form parsing or validation errors containing secret inputs."""
    if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
        raise HTTPException(400, "Invalid NFC login form", headers=HEADERS)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 4096:
            raise HTTPException(413, "NFC login form is too large", headers=HEADERS)
        body.extend(chunk)
    try:
        values = parse_qs(body.decode("ascii"), keep_blank_values=True, max_num_fields=8,
                          encoding="utf-8", errors="strict")
        if any(len(v) != 1 for v in values.values()):
            raise ValueError()
        return {k: v[0] for k, v in values.items()}
    except (ValueError, UnicodeError):
        raise HTTPException(400, "Invalid NFC login form", headers=HEADERS) from None


def challenge_filter(request, settings, token):
    browser = request.cookies.get(BROWSER_COOKIE, "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", browser):
        raise HTTPException(400, "NFC login expired. Start again.", headers=HEADERS)
    return (NfcLoginChallenge.token_hash == digest(token),
            NfcLoginChallenge.browser_hash == digest(browser),
            NfcLoginChallenge.issuer == settings.nfc_issuer_origin,
            NfcLoginChallenge.expires_at > now())


def render(settings, **context):
    return HTMLResponse(render_template("nfc_login.html", title="Login with NFC or QR",
        csrf_token=CsrfProtector(settings).issue(), **context), headers=HEADERS)


@router.get("")
async def login_page(request: Request):
    settings = require_origin(request)
    with database_boundary():
        throttle(request)
        token, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with Session(request.app.state.database_engine) as session:
            session.add(NfcLoginChallenge(token_hash=digest(token), browser_hash=digest(browser),
                issuer=settings.nfc_issuer_origin, expires_at=now() + TTL))
            session.commit()
    response = render(settings, challenge=token)
    response.set_cookie(BROWSER_COOKIE, browser, max_age=TTL, secure=True,
                        httponly=True, samesite="strict", path="/")
    return response


@router.post("/read")
async def read_for_login(request: Request):
    settings = require_origin(request)
    with database_boundary():
        throttle(request)
        form = await read_form(request)
        require_csrf(request, form.get("csrf_token", ""))
        token, encoded = form.get("challenge", ""), form.get("card_payload", "")
        qr_text = form.get("qr_payload", "")
        if qr_text:
            if encoded:
                raise HTTPException(400, "Submit one login credential at a time", headers=HEADERS)
            try:
                payload = nfc_qr.decode(qr_text)
            except ValueError:
                raise HTTPException(400, "Not a supported login QR credential", headers=HEADERS) from None
            encoded = base64.b64encode(payload).decode("ascii")
        else:
            payload = unpack(encoded)
        if not payload:
            raise HTTPException(400, "No NFC credential was read", headers=HEADERS)
        with Session(request.app.state.database_engine) as session:
            result = session.execute(update(NfcLoginChallenge).where(
                *challenge_filter(request, settings, token), NfcLoginChallenge.payload_digest.is_(None)
            ).values(payload_digest=hashlib.sha256(payload).hexdigest()))
            session.commit()
            if result.rowcount != 1:
                raise HTTPException(400, "NFC login expired. Start again.", headers=HEADERS)
    # No decryption or identity oracle at the read stage. Only encrypted bytes return.
    return render(settings, challenge=token, card_payload=encoded)


def authorize(engine, metadata, correct_pin):
    """CAS is the authorization point, serialized with activation/revocation."""
    with Session(engine) as session:
        policy = session.get(NfcCardPolicy, metadata["npub"])
        if (not policy or metadata["expired"] or metadata["expires_at"] <= now()
                or policy.active_generation != metadata["generation"]
                or (policy.active_expires_at or 0) <= now() or policy.login_failures >= ATTEMPTS):
            return False
        result = session.execute(update(NfcCardPolicy).where(
            NfcCardPolicy.npub == metadata["npub"], NfcCardPolicy.revision == policy.revision,
            NfcCardPolicy.active_generation == metadata["generation"],
            NfcCardPolicy.active_expires_at > now(),
            NfcCardPolicy.login_failures == policy.login_failures,
            NfcCardPolicy.login_failures < ATTEMPTS,
        ).values(login_failures=0 if correct_pin else NfcCardPolicy.login_failures + 1))
        session.commit()
        return result.rowcount == 1 and correct_pin


@router.post("")
async def login(request: Request):
    settings = require_origin(request)
    with database_boundary():
        throttle(request)
        form = await read_form(request)
        require_csrf(request, form.get("csrf_token", ""))
        token = form.get("challenge", "")
        payload = unpack(form.get("card_payload", ""))
        with Session(request.app.state.database_engine) as session:
            result = session.execute(delete(NfcLoginChallenge).where(
                *challenge_filter(request, settings, token),
                NfcLoginChallenge.payload_digest == hashlib.sha256(payload).hexdigest()))
            session.commit()
            if result.rowcount != 1:
                raise HTTPException(400, "NFC login expired or already used. Start again.", headers=HEADERS)
        try:
            metadata, credentials, expected_pin = _decrypt_card(settings, payload)
        except ValueError:
            response = render(settings, error=FAILURE)
            response.status_code = 400
            return response
        supplied = form.get("pin", "")
        valid = bool(re.fullmatch(r"(?:[0-9]{4}|[0-9]{6,12})", supplied))
        correct = hmac.compare_digest(supplied.encode(), expected_pin.encode()) and valid
        if not authorize(request.app.state.database_engine, metadata, correct):
            response = render(settings, error=FAILURE)
            response.status_code = 400
            return response
    response = RedirectResponse("/wallet", status_code=303, headers=HEADERS)
    set_session_cookie(response, request=request, settings=settings, credentials=credentials)
    response.delete_cookie(BROWSER_COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return response
