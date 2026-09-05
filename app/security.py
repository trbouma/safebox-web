"""Stateless authentication and transport helpers."""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import os
import re
from time import time
from urllib.parse import urlsplit, urlunsplit

from cryptography.fernet import Fernet, InvalidToken
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from mnemonic import Mnemonic
from monstr.encrypt import Keys
from starlette.requests import Request
from starlette.responses import Response

from acorn import validate_record_protection_key
from acorn.func_utils import recover_nsec_from_seed

from app.config import Settings
from app.localization import normalize_language_tag


SECURE_COOKIE_NAME = "__Host-safebox_session"
LOOPBACK_COOKIE_NAME = "safebox_session"


@dataclass(frozen=True)
class SessionCredentials:
    """The complete client-held Acorn bootstrap session."""

    nsec: str
    bootstrap_relay: str
    home_mint: str | None = None
    deferred_acorn_mnemonic: str | None = None
    record_protection_key: str | None = None
    record_protection_backup_confirmed: bool = False
    currency: str = "USD"
    language: str = "en"
    version: int = 1


@dataclass(frozen=True)
class DepositQuoteState:
    """Client-held state for one Lightning deposit quote."""

    quote: str
    amount: int
    mint: str
    invoice: str
    purpose: str = "safebox-web-deposit-quote"
    version: int = 1


@dataclass(frozen=True)
class InvoicePaymentState:
    """Client-held state for one reviewed Lightning invoice payment."""

    invoice: str
    amount: int
    purpose: str = "safebox-web-invoice-payment"
    version: int = 1


class SessionCipher:
    """Encrypt and authenticate browser-held session credentials.

    Version 2 tokens use AES-256-GCM. Unprefixed version 1 Fernet tokens remain
    readable only so sessions issued before the migration can expire normally.
    """

    _PREFIX = "v2."
    _PURPOSE = "safebox-web-session"
    _AAD = b"safebox-web/session-cookie/v2"
    _NONCE_BYTES = 12
    _CLOCK_SKEW_SECONDS = 60

    def __init__(self, settings: Settings) -> None:
        encoded_key = settings.cookie_key.encode("ascii")
        self._legacy_fernet = Fernet(encoded_key)
        master_key = base64.urlsafe_b64decode(encoded_key)
        session_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=self._AAD,
        ).derive(master_key)
        self._aesgcm = AESGCM(session_key)
        self._ttl = settings.session_ttl_seconds

    def encode(self, credentials: SessionCredentials) -> str:
        credentials = self._validate_credentials(credentials)
        payload = json.dumps(
            {
                "credentials": asdict(credentials),
                "issued_at": int(time()),
                "purpose": self._PURPOSE,
                "version": 2,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        nonce = os.urandom(self._NONCE_BYTES)
        ciphertext = self._aesgcm.encrypt(nonce, payload, self._AAD)
        token = base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii").rstrip("=")
        return self._PREFIX + token

    def decode(self, token: str) -> SessionCredentials:
        normalized = str(token).strip()
        if len(normalized) >= 2 and normalized[0] == normalized[-1] == '"':
            normalized = normalized[1:-1]
        if normalized.startswith(self._PREFIX):
            return self._decode_v2(normalized[len(self._PREFIX) :])
        return self._decode_legacy(normalized)

    def _decode_v2(self, token: str) -> SessionCredentials:
        try:
            padded_token = token + "=" * (-len(token) % 4)
            sealed = base64.b64decode(
                padded_token.encode("ascii"),
                altchars=b"-_",
                validate=True,
            )
            if len(sealed) <= self._NONCE_BYTES:
                raise ValueError("session cookie is truncated")
            nonce = sealed[: self._NONCE_BYTES]
            ciphertext = sealed[self._NONCE_BYTES :]
            raw = self._aesgcm.decrypt(nonce, ciphertext, self._AAD)
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("session cookie payload is invalid")
            if (
                payload.get("version") != 2
                or payload.get("purpose") != self._PURPOSE
            ):
                raise ValueError("session cookie version or purpose is unsupported")
            issued_at = payload.get("issued_at")
            if not isinstance(issued_at, int) or isinstance(issued_at, bool):
                raise ValueError("session cookie timestamp is invalid")
            now = int(time())
            if issued_at > now + self._CLOCK_SKEW_SECONDS or now - issued_at > self._ttl:
                raise ValueError("session cookie is expired")
            credentials = SessionCredentials(**payload["credentials"])
        except (
            InvalidTag,
            UnicodeError,
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError("session cookie is invalid or expired") from exc
        return self._validate_credentials(credentials)

    def _decode_legacy(self, token: str) -> SessionCredentials:
        try:
            raw = self._legacy_fernet.decrypt(token.encode("ascii"), ttl=self._ttl)
            credentials = SessionCredentials(**json.loads(raw))
        except (
            InvalidToken,
            UnicodeError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError("session cookie is invalid or expired") from exc
        return self._validate_credentials(credentials)

    @staticmethod
    def _validate_credentials(credentials: SessionCredentials) -> SessionCredentials:
        if credentials.version != 1:
            raise ValueError("session cookie version is unsupported")
        if credentials.deferred_acorn_mnemonic is not None:
            phrase = " ".join(credentials.deferred_acorn_mnemonic.strip().split())
            if not Mnemonic("english").check(phrase):
                raise ValueError("session cookie deferred mnemonic is invalid")
            if recover_nsec_from_seed(phrase) != credentials.nsec:
                raise ValueError(
                    "session cookie deferred mnemonic does not match the Acorn key"
                )
        if credentials.record_protection_key is not None:
            try:
                validate_record_protection_key(credentials.record_protection_key)
            except ValueError as exc:
                raise ValueError("session cookie record protection key is invalid") from exc
        if not isinstance(credentials.record_protection_backup_confirmed, bool):
            raise ValueError("session cookie record protection status is invalid")
        if not isinstance(credentials.currency, str) or not re.fullmatch(
            r"[A-Z]{3}", credentials.currency
        ):
            raise ValueError("session cookie currency preference is invalid")
        try:
            canonical_language = normalize_language_tag(credentials.language)
        except ValueError as exc:
            raise ValueError("session cookie language preference is invalid") from exc
        if canonical_language != credentials.language:
            credentials = replace(credentials, language=canonical_language)
        if (
            credentials.record_protection_backup_confirmed
            and credentials.record_protection_key is None
        ):
            raise ValueError(
                "session cookie cannot confirm a missing record protection key"
            )
        return credentials


class DepositQuoteCipher:
    """Encrypt and authenticate short-lived deposit quote state."""

    def __init__(self, settings: Settings) -> None:
        self._fernet = Fernet(settings.cookie_key.encode("ascii"))
        self._ttl = min(settings.session_ttl_seconds, 60 * 60)

    def encode(self, state: DepositQuoteState) -> str:
        payload = json.dumps(
            asdict(state), separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return self._fernet.encrypt(payload).decode("ascii")

    def decode(self, token: str) -> DepositQuoteState:
        try:
            raw = self._fernet.decrypt(str(token).encode("ascii"), ttl=self._ttl)
            payload = json.loads(raw)
            state = DepositQuoteState(**payload)
        except (InvalidToken, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("deposit quote is invalid or expired") from exc
        if state.version != 1 or state.purpose != "safebox-web-deposit-quote":
            raise ValueError("deposit quote version or purpose is unsupported")
        if state.amount <= 0 or not state.quote or not state.mint or not state.invoice:
            raise ValueError("deposit quote is incomplete")
        if len(state.quote) > 512 or len(state.mint) > 2048 or len(state.invoice) > 2048:
            raise ValueError("deposit quote contains an oversized field")
        return state


class InvoicePaymentCipher:
    """Encrypt and authenticate a short-lived reviewed Lightning invoice."""

    def __init__(self, settings: Settings) -> None:
        self._fernet = Fernet(settings.cookie_key.encode("ascii"))
        self._ttl = min(settings.session_ttl_seconds, 15 * 60)

    def encode(self, state: InvoicePaymentState) -> str:
        payload = json.dumps(
            asdict(state), separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return self._fernet.encrypt(payload).decode("ascii")

    def decode(self, token: str) -> InvoicePaymentState:
        try:
            raw = self._fernet.decrypt(str(token).encode("ascii"), ttl=self._ttl)
            payload = json.loads(raw)
            state = InvoicePaymentState(**payload)
        except (InvalidToken, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invoice payment review is invalid or expired") from exc
        if state.version != 1 or state.purpose != "safebox-web-invoice-payment":
            raise ValueError("invoice payment version or purpose is unsupported")
        if state.amount <= 0 or not state.invoice or len(state.invoice) > 4096:
            raise ValueError("invoice payment state is incomplete")
        return state


class CsrfProtector:
    """Issue and verify short-lived, server-authenticated form tokens."""

    _PAYLOAD = b"safebox-web-csrf-v1"

    def __init__(self, settings: Settings) -> None:
        self._fernet = Fernet(settings.cookie_key.encode("ascii"))
        self._ttl = min(settings.session_ttl_seconds, 60 * 60)

    def issue(self) -> str:
        return self._fernet.encrypt(self._PAYLOAD).decode("ascii")

    def verify(self, token: str) -> bool:
        try:
            payload = self._fernet.decrypt(
                str(token).encode("ascii"), ttl=self._ttl
            )
        except (InvalidToken, UnicodeError, ValueError, TypeError):
            return False
        return payload == self._PAYLOAD


def _normalize_relay_url(value: str, *, require_ws_port: bool = False) -> str:
    """Return a canonical WebSocket relay URL without applying access policy."""

    relay = str(value).strip()
    if not relay:
        raise ValueError("bootstrap relay is required")
    if not relay.startswith(("wss://", "ws://")):
        relay = f"wss://{relay}"

    parsed = urlsplit(relay)
    if parsed.scheme not in {"wss", "ws"} or not parsed.hostname:
        raise ValueError("bootstrap relay must be a ws:// or wss:// URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("bootstrap relay must not contain credentials or a fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("bootstrap relay port is invalid") from exc
    if require_ws_port and parsed.scheme == "ws" and port is None:
        raise ValueError("allowed ws:// relays must include an explicit port")

    hostname = parsed.hostname.lower()
    canonical_host = f"[{hostname}]" if ":" in hostname else hostname
    canonical_netloc = canonical_host if port is None else f"{canonical_host}:{port}"
    normalized_path = parsed.path or ""
    return urlunsplit(
        (parsed.scheme.lower(), canonical_netloc, normalized_path, parsed.query, "")
    )


def normalize_bootstrap_relay(
    value: str,
    allowed_ws_relays: tuple[str, ...] | list[str] = (),
) -> str:
    """Normalize a relay URL and require explicit authorization for ws://."""

    normalized = _normalize_relay_url(value, require_ws_port=True)
    if urlsplit(normalized).scheme != "ws":
        return normalized

    allowed: set[str] = set()
    for configured in allowed_ws_relays:
        candidate = _normalize_relay_url(configured, require_ws_port=True)
        if urlsplit(candidate).scheme != "ws":
            raise ValueError("SAFEBOX_ALLOWED_WS_RELAYS may contain only ws:// URLs")
        allowed.add(candidate)
    if normalized not in allowed:
        raise ValueError(
            "ws:// relay is not listed in SAFEBOX_ALLOWED_WS_RELAYS"
        )
    return normalized


def normalize_home_mint(value: str) -> str:
    """Normalize and validate an HTTPS mint URL.

    Plain HTTP is accepted only for an IPv4 loopback mint used in local
    development.
    """

    mint = str(value).strip().rstrip("/")
    if not mint:
        raise ValueError("home mint is required")
    if not mint.startswith(("https://", "http://")):
        mint = f"https://{mint}"

    parsed = urlsplit(mint)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("home mint must be an http:// or https:// URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "home mint must not contain credentials, a query, or a fragment"
        )
    if parsed.scheme == "http":
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as exc:
            raise ValueError(
                "unencrypted http:// mints are allowed only on loopback"
            ) from exc
        if not address.is_loopback:
            raise ValueError("unencrypted http:// mints are allowed only on loopback")

    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def canonical_nsec(value: str) -> str:
    """Validate and return the canonical Bech32 private key."""

    candidate = str(value).strip()
    if not candidate.startswith("nsec1"):
        raise ValueError("enter a valid nsec private key")
    try:
        return Keys(priv_k=candidate).private_key_bech32()
    except (TypeError, ValueError, IndexError) as exc:
        raise ValueError("enter a valid nsec private key") from exc


def nsec_from_offline_mnemonic(value: str) -> str:
    """Derive an Acorn nsec without retaining the submitted mnemonic."""

    phrase = " ".join(str(value).strip().split())
    if not Mnemonic("english").check(phrase):
        raise ValueError("enter a valid 12- or 24-word Safebox Acorn mnemonic")
    return canonical_nsec(recover_nsec_from_seed(phrase))


def credentials_from_connection(
    *,
    secret_type: str,
    secret: str,
    bootstrap_relay: str,
    home_mint: str | None = None,
    deferred_acorn_mnemonic: str | None = None,
    record_protection_key: str | None = None,
    record_protection_backup_confirmed: bool = False,
    allowed_ws_relays: tuple[str, ...] | list[str] = (),
) -> SessionCredentials:
    if secret_type == "nsec":
        nsec = canonical_nsec(secret)
    elif secret_type == "mnemonic":
        nsec = nsec_from_offline_mnemonic(secret)
    else:
        raise ValueError("select nsec or offline mnemonic")
    if record_protection_key is not None:
        record_protection_key = validate_record_protection_key(
            record_protection_key
        )
    return SessionCredentials(
        nsec=nsec,
        bootstrap_relay=normalize_bootstrap_relay(
            bootstrap_relay,
            allowed_ws_relays,
        ),
        home_mint=(
            (str(home_mint).strip() or None)
            if home_mint is not None
            else None
        ),
        deferred_acorn_mnemonic=deferred_acorn_mnemonic,
        record_protection_key=record_protection_key,
        record_protection_backup_confirmed=record_protection_backup_confirmed,
    )


def is_loopback_http_request(request: Request) -> bool:
    """Return whether this is direct HTTP development on 127.0.0.1."""

    if request.url.scheme != "http" or request.url.hostname != "127.0.0.1":
        return False
    if request.url.port is None:
        return False
    if request.client is None:
        return False
    try:
        return ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        return False


def is_allowed_transport(request: Request) -> bool:
    return (
        request.url.scheme == "https"
        or is_loopback_http_request(request)
        or _allows_insecure_http(request)
    )


def _allows_insecure_http(request: Request) -> bool:
    settings = getattr(request.app.state, "settings", None)
    return bool(
        request.url.scheme == "http"
        and settings is not None
        and settings.allow_insecure_http
    )


def is_same_origin(request: Request, origin: str) -> bool:
    """Compare an Origin header by scheme, hostname, and effective port."""

    try:
        parsed = urlsplit(str(origin).strip())
        if parsed.username or parsed.password or parsed.path not in {"", "/"}:
            return False
        if parsed.query or parsed.fragment or not parsed.hostname:
            return False
        origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        request_port = request.url.port or (
            443 if request.url.scheme == "https" else 80
        )
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == request.url.scheme.lower()
        and parsed.hostname.lower() == str(request.url.hostname).lower()
        and origin_port == request_port
    )


def cookie_name_for_request(request: Request) -> str:
    if is_loopback_http_request(request) or _allows_insecure_http(request):
        return LOOPBACK_COOKIE_NAME
    return SECURE_COOKIE_NAME


def set_session_cookie(
    response: Response,
    *,
    request: Request,
    settings: Settings,
    credentials: SessionCredentials,
) -> None:
    """Issue a persistent browser cookie aligned with the encrypted session TTL."""

    response.set_cookie(
        key=cookie_name_for_request(request),
        value=SessionCipher(settings).encode(credentials),
        max_age=settings.session_ttl_seconds,
        expires=datetime.now(timezone.utc)
        + timedelta(seconds=settings.session_ttl_seconds),
        httponly=True,
        secure=not (
            is_loopback_http_request(request) or _allows_insecure_http(request)
        ),
        samesite="strict",
        path="/",
    )
