"""NFC credential issuance, owner read-back and PIN-gated login."""
from __future__ import annotations

import base64
import hashlib
import secrets
import os
import re
from dataclasses import replace
from time import time

import cbor2
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session
from stroma import Keys

from app.dependencies import CredentialsDependency
from app.models import NfcCardPolicy
from app.security import CsrfProtector, SessionCredentials, normalize_bootstrap_relay
from app.templating import render_template

router = APIRouter()
MIME = "application/vnd.safebox.card+cbor"
DOMAIN = b"safebox-web/nfc-card/v1\x00"
MAX_ENVELOPE = 400  # bounded so a single record fits the 496-byte design target
MAX_LIFETIME = 365 * 86400
PENDING_TTL = 1800
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def now():
    return int(time())


def cipher(settings):
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=DOMAIN).derive(
        base64.urlsafe_b64decode(settings.cookie_key))
    return AESGCM(key)


def owner(credentials):
    return Keys(priv_k=credentials.nsec).public_key_bech32()


def encode_card(settings, credentials, pin, lifetime_days):
    relay = normalize_bootstrap_relay(credentials.bootstrap_relay,
                                     allowed_ws_relays=settings.allowed_ws_relays)
    if len(relay.encode()) > 200:
        raise ValueError("Bootstrap relay is too long for this card format")
    generation = os.urandom(16)
    issued = now()
    private = {0: bytes.fromhex(Keys(priv_k=credentials.nsec).private_key_hex()),
               1: relay, 2: pin, 3: generation, 4: issued, 5: issued + lifetime_days * 86400}
    header = {0: 1, 1: 1, 2: settings.nfc_issuer_origin, 3: 1}
    nonce = os.urandom(12)
    encrypted = cipher(settings).encrypt(nonce, cbor2.dumps(private, canonical=True),
                                         DOMAIN + cbor2.dumps(header, canonical=True))
    payload = cbor2.dumps({**header, 4: nonce, 5: encrypted}, canonical=True)
    if len(payload) > MAX_ENVELOPE:
        raise ValueError("Credential exceeds this card format's size limit")
    return payload, generation.hex(), private[5]


def _decrypt_card(settings, payload):
    """Internal boundary: callers must not render or log the returned secrets."""
    try:
        if not 1 <= len(payload) <= MAX_ENVELOPE:
            raise ValueError()
        outer = cbor2.loads(payload)
        if (not isinstance(outer, dict) or set(outer) != set(range(6))
                or any(type(k) is not int for k in outer)
                or cbor2.dumps(outer, canonical=True) != payload
                or type(outer[0]) is not int or outer[0] != 1
                or type(outer[1]) is not int or outer[1] != 1
                or outer[2] != settings.nfc_issuer_origin
                or type(outer[3]) is not int or outer[3] != 1
                or type(outer[4]) is not bytes or len(outer[4]) != 12
                or type(outer[5]) is not bytes):
            raise ValueError()
        header = {k: outer[k] for k in range(4)}
        plaintext = cipher(settings).decrypt(outer[4], outer[5], DOMAIN + cbor2.dumps(header, canonical=True))
        data = cbor2.loads(plaintext)
        if (not isinstance(data, dict) or set(data) != set(range(6))
                or any(type(k) is not int for k in data)
                or cbor2.dumps(data, canonical=True) != plaintext
                or type(data[0]) is not bytes or len(data[0]) != 32
                or type(data[1]) is not str or len(data[1].encode()) > 200
                or type(data[2]) is not str or not re.fullmatch(r"(?:[0-9]{4}|[0-9]{6,12})", data[2])
                or type(data[3]) is not bytes or len(data[3]) != 16
                or type(data[4]) is not int or type(data[5]) is not int
                or data[4] > now() + 60 or not 0 < data[5] - data[4] <= MAX_LIFETIME
                or not 0 < int.from_bytes(data[0], "big") < 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141):
            raise ValueError()
        keys = Keys(priv_k=data[0].hex())
        relay = normalize_bootstrap_relay(data[1], allowed_ws_relays=settings.allowed_ws_relays)
        metadata = {"generation": data[3].hex(), "issued_at": data[4], "expires_at": data[5],
                "bootstrap_relay": relay, "issuer": outer[2], "npub": keys.public_key_bech32(),
                "expired": data[5] <= now(), "bytes": len(payload)}
        credentials = SessionCredentials(nsec=keys.private_key_bech32(), bootstrap_relay=relay)
        return metadata, credentials, data[2]
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError, InvalidTag, cbor2.CBORError):
        raise ValueError("Card could not be verified for this account and issuer") from None


def decode_card(settings, payload, expected_npub):
    """Owner read-back never returns the private key or PIN."""
    metadata, _, _ = _decrypt_card(settings, payload)
    if metadata["npub"] != expected_npub:
        raise ValueError("Card could not be verified for this account and issuer")
    return metadata


def unpack(value):
    try:
        if len(value) > 536:
            raise ValueError()
        payload = base64.b64decode(value, validate=True)
        if len(payload) > MAX_ENVELOPE:
            raise ValueError()
        return payload
    except (ValueError, UnicodeError):
        raise HTTPException(400, "Invalid or oversized card encoding") from None


def require_origin(request):
    settings = request.app.state.settings
    # Scheme is resolved by the ASGI server's trusted-proxy handling. Never
    # independently trust raw Forwarded or X-Forwarded-* headers here.
    authority = request.url.netloc.lower()
    origin = f"{request.url.scheme}://{authority}"
    if request.url.scheme != "https":
        raise HTTPException(403, "NFC requires HTTPS; check trusted proxy configuration")
    if settings.nfc_issuer_origin:
        if origin != settings.nfc_issuer_origin:
            raise HTTPException(403, "Open this page on the configured NFC issuer HTTPS origin")
        return settings
    if not settings.nfc_allowed_hosts:
        raise HTTPException(503, "Configure SAFEBOX_NFC_ALLOWED_HOSTS or SAFEBOX_NFC_ISSUER_ORIGIN")
    if authority not in settings.nfc_allowed_hosts:
        raise HTTPException(403, "Request host is not allowed for NFC issuance")
    try:
        return replace(settings, nfc_issuer_origin=origin)
    except ValueError:
        raise HTTPException(403, "Open this page on the configured NFC issuer HTTPS origin")


def require_csrf(request, token):
    if not CsrfProtector(request.app.state.settings).verify(token):
        raise HTTPException(403, "The form token is invalid or expired")


def policy_for(engine, npub):
    with Session(engine) as session:
        return session.get(NfcCardPolicy, npub)


def ensure_policy(engine, npub):
    with Session(engine) as session:
        if session.get(NfcCardPolicy, npub) is None:
            session.add(NfcCardPolicy(npub=npub))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()


def compare_update(engine, npub, revision, **values):
    with Session(engine) as session:
        result = session.execute(update(NfcCardPolicy).where(
            NfcCardPolicy.npub == npub, NfcCardPolicy.revision == revision
        ).values(revision=revision + 1, **values))
        if result.rowcount != 1:
            session.rollback()
            raise HTTPException(409, "Card policy changed. Reload the NFC page before continuing")
        session.commit()


def page(request, credentials, **context):
    settings = request.app.state.settings
    try:
        issuer = require_origin(request).nfc_issuer_origin
    except HTTPException:
        issuer = ""
    return HTMLResponse(render_template("nfc_card.html", title="NFC Cards",
        csrf_token=CsrfProtector(settings).issue(), issuer=issuer,
        policy=policy_for(request.app.state.database_engine, owner(credentials)),
        mime=MIME, **context), headers=HEADERS)


@router.get("/nfc", response_class=HTMLResponse)
async def nfc_page(request: Request, credentials: CredentialsDependency):
    return page(request, credentials)


@router.post("/nfc/prepare", response_class=HTMLResponse)
async def prepare(request: Request, credentials: CredentialsDependency,
                  csrf_token: str = Form(""),
                  lifetime: str = Form("90"), confirmed: str = Form("")):
    require_csrf(request, csrf_token)
    settings = require_origin(request)
    if confirmed != "yes":
        raise HTTPException(400, "Confirm card issuance")
    if lifetime not in {"30", "90", "365"}:
        raise HTTPException(400, "Choose a supported card lifetime")
    pin = f"{secrets.randbelow(10000):04d}"
    try:
        payload, generation, expires = encode_card(settings, credentials, pin, int(lifetime))
    except ValueError:
        raise HTTPException(400, "Unable to encode this wallet's bootstrap information within card limits") from None
    engine, npub = request.app.state.database_engine, owner(credentials)
    ensure_policy(engine, npub)
    current = policy_for(engine, npub)
    compare_update(engine, npub, current.revision, pending_generation=generation,
                   pending_digest=hashlib.sha256(payload).hexdigest(), pending_deadline=now() + PENDING_TTL)
    return page(request, credentials, card_payload=base64.b64encode(payload).decode(),
                byte_count=len(payload), expires=expires, generated_pin=pin,
                notice="Credential prepared for 30 minutes. Existing active card remains valid until replacement activation.")


@router.post("/nfc/read", response_class=HTMLResponse)
async def read_card(request: Request, credentials: CredentialsDependency,
                    csrf_token: str = Form(""), card_payload: str = Form("")):
    require_csrf(request, csrf_token)
    settings = require_origin(request)
    payload = unpack(card_payload)
    try:
        metadata = decode_card(settings, payload, owner(credentials))
    except ValueError:
        raise HTTPException(400, "Card could not be verified for this account and issuer") from None
    policy = policy_for(request.app.state.database_engine, owner(credentials))
    digest = hashlib.sha256(payload).hexdigest()
    pending = bool(policy and policy.pending_generation == metadata["generation"]
                   and policy.pending_digest == digest and (policy.pending_deadline or 0) > now()
                   and not metadata["expired"])
    metadata["state"] = ("Expired" if metadata["expired"] else "Pending activation" if pending else
        "Locked — replace card" if policy and policy.active_generation == metadata["generation"] and policy.login_failures >= 5 else
        "Active" if policy and policy.active_generation == metadata["generation"] else "Inactive / revoked / superseded")
    return page(request, credentials, metadata=metadata, activation_payload=card_payload if pending else None,
                activation_revision=policy.revision if pending else None)


@router.post("/nfc/activate")
async def activate(request: Request, credentials: CredentialsDependency,
                   csrf_token: str = Form(""), card_payload: str = Form(""),
                   revision: str = Form(""), confirmed: str = Form("")):
    require_csrf(request, csrf_token)
    settings = require_origin(request)
    if confirmed != "yes" or not revision.isascii() or not revision.isdigit() or len(revision) > 12:
        raise HTTPException(400, "Explicit activation confirmation is required")
    payload = unpack(card_payload)
    npub, engine = owner(credentials), request.app.state.database_engine
    try:
        metadata = decode_card(settings, payload, npub)
    except ValueError:
        raise HTTPException(400, "Card could not be verified for this account and issuer") from None
    policy = policy_for(engine, npub)
    if (not policy or metadata["expired"] or policy.pending_generation != metadata["generation"]
            or policy.pending_digest != hashlib.sha256(payload).hexdigest()
            or (policy.pending_deadline or 0) <= now() or policy.revision != int(revision)):
        raise HTTPException(409, "Pending card expired or policy changed. Read the card and review again")
    compare_update(engine, npub, policy.revision, active_generation=metadata["generation"],
                   active_expires_at=metadata["expires_at"], pending_generation=None,
                   pending_digest=None, pending_deadline=None, login_failures=0)
    return RedirectResponse("/nfc", status_code=303, headers=HEADERS)


@router.post("/nfc/revoke")
async def revoke(request: Request, credentials: CredentialsDependency,
                 csrf_token: str = Form(""), revision: str = Form(""), confirmed: str = Form("")):
    require_csrf(request, csrf_token)
    if confirmed != "yes" or not revision.isascii() or not revision.isdigit() or len(revision) > 12:
        raise HTTPException(400, "Explicit revocation confirmation is required")
    engine, npub = request.app.state.database_engine, owner(credentials)
    compare_update(engine, npub, int(revision), active_generation=None, active_expires_at=None,
                   pending_generation=None, pending_digest=None, pending_deadline=None)
    return RedirectResponse("/nfc", status_code=303, headers=HEADERS)
