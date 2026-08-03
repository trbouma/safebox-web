"""Stateless authentication and transport helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import ipaddress
import json
from urllib.parse import urlsplit, urlunsplit

from cryptography.fernet import Fernet, InvalidToken
from mnemonic import Mnemonic
from monstr.encrypt import Keys
from starlette.requests import Request

from acorn.func_utils import recover_nsec_from_seed

from app.config import Settings


SECURE_COOKIE_NAME = "__Host-safebox_session"
LOOPBACK_COOKIE_NAME = "safebox_session"


@dataclass(frozen=True)
class SessionCredentials:
    """The complete client-held Acorn bootstrap session."""

    nsec: str
    bootstrap_relay: str
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


class SessionCipher:
    """Encrypt and authenticate browser-held session credentials."""

    def __init__(self, settings: Settings) -> None:
        self._fernet = Fernet(settings.cookie_key.encode("ascii"))
        self._ttl = settings.session_ttl_seconds

    def encode(self, credentials: SessionCredentials) -> str:
        payload = json.dumps(
            asdict(credentials), separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return self._fernet.encrypt(payload).decode("ascii")

    def decode(self, token: str) -> SessionCredentials:
        try:
            raw = self._fernet.decrypt(token.encode("ascii"), ttl=self._ttl)
            payload = json.loads(raw)
            credentials = SessionCredentials(**payload)
        except (InvalidToken, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("session cookie is invalid or expired") from exc
        if credentials.version != 1:
            raise ValueError("session cookie version is unsupported")
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


def normalize_bootstrap_relay(value: str) -> str:
    """Normalize and validate a secure relay URL.

    Plain ``ws://`` is accepted only for an IPv4 loopback relay.
    """

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
    if parsed.scheme == "ws":
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as exc:
            raise ValueError("unencrypted ws:// relays are allowed only on loopback") from exc
        if not address.is_loopback:
            raise ValueError("unencrypted ws:// relays are allowed only on loopback")

    normalized_path = parsed.path or ""
    return urlunsplit((parsed.scheme, parsed.netloc, normalized_path, parsed.query, ""))


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
        raise ValueError("enter a valid 12- or 24-word BIP39 offline mnemonic")
    return canonical_nsec(recover_nsec_from_seed(phrase))


def credentials_from_login(
    *, secret_type: str, secret: str, bootstrap_relay: str
) -> SessionCredentials:
    if secret_type == "nsec":
        nsec = canonical_nsec(secret)
    elif secret_type == "mnemonic":
        nsec = nsec_from_offline_mnemonic(secret)
    else:
        raise ValueError("select nsec or offline mnemonic")
    return SessionCredentials(
        nsec=nsec,
        bootstrap_relay=normalize_bootstrap_relay(bootstrap_relay),
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
    return request.url.scheme == "https" or is_loopback_http_request(request)


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
    return LOOPBACK_COOKIE_NAME if is_loopback_http_request(request) else SECURE_COOKIE_NAME
