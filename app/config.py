"""Environment-backed application configuration."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false")


DEFAULT_GIFT_WRAP_RETENTION_SECONDS = 7 * 24 * 60 * 60
MIN_GIFT_WRAP_RETENTION_SECONDS = 60 * 60
MAX_GIFT_WRAP_RETENTION_SECONDS = 30 * 24 * 60 * 60
DEFAULT_SESSION_TTL_HOURS = 30 * 24
DEFAULT_SESSION_TTL_SECONDS = DEFAULT_SESSION_TTL_HOURS * 60 * 60
DEFAULT_MAX_BLOB_BYTES = 10 * 1024 * 1024


def _allowed_ws_relays_from_env() -> tuple[str, ...]:
    """Return the operator's comma-delimited exact ws:// relay allowlist."""

    return tuple(
        relay.strip()
        for relay in os.getenv("SAFEBOX_ALLOWED_WS_RELAYS", "").split(",")
        if relay.strip()
    )


def _session_ttl_seconds_from_env() -> int:
    """Read the public hours setting, with seconds kept as a legacy fallback."""

    hours_value = os.getenv("SAFEBOX_SESSION_TTL_HOURS", "").strip()
    if hours_value:
        try:
            hours = int(hours_value)
        except ValueError as exc:
            raise RuntimeError(
                "SAFEBOX_SESSION_TTL_HOURS must be an integer"
            ) from exc
        if hours < 1:
            raise RuntimeError("SAFEBOX_SESSION_TTL_HOURS must be at least 1")
        return hours * 60 * 60

    legacy_seconds = os.getenv("SAFEBOX_SESSION_TTL_SECONDS", "").strip()
    if legacy_seconds:
        try:
            return int(legacy_seconds)
        except ValueError as exc:
            raise RuntimeError(
                "SAFEBOX_SESSION_TTL_SECONDS must be an integer"
            ) from exc

    return DEFAULT_SESSION_TTL_SECONDS


def _gift_wrap_retention_from_env() -> int | None:
    raw_value = os.getenv(
        "SAFEBOX_SERVICE_ACORN_GIFT_WRAP_RETENTION_SECONDS",
        str(DEFAULT_GIFT_WRAP_RETENTION_SECONDS),
    ).strip().lower()
    try:
        return None if raw_value in {"", "0", "none", "off"} else int(raw_value)
    except ValueError as exc:
        raise RuntimeError(
            "SAFEBOX_SERVICE_ACORN_GIFT_WRAP_RETENTION_SECONDS must be an "
            "integer number of seconds, or 0 to disable"
        ) from exc


def _validate_gift_wrap_retention(value: int | None) -> None:
    if value is not None and not (
        MIN_GIFT_WRAP_RETENTION_SECONDS
        <= value
        <= MAX_GIFT_WRAP_RETENTION_SECONDS
    ):
        raise ValueError(
            "SAFEBOX_SERVICE_ACORN_GIFT_WRAP_RETENTION_SECONDS must be "
            "between 3600 and 2592000, or 0 to disable"
        )


@dataclass(frozen=True)
class ServiceAcornSettings:
    """Configuration for the standalone provider-wallet worker."""

    wallet_load_timeout_seconds: float = 20.0
    payment_timeout_seconds: float = 90.0
    database_url: str = "sqlite:///data/database.db"
    service_acorn_poll_seconds: float = 0.5
    allowed_ws_relays: tuple[str, ...] = ()
    service_acorn_enabled: bool = False
    service_acorn_home_relay: str = "wss://relay.getsafebox.app"
    service_acorn_home_mint: str = "https://mint.getsafebox.app"
    service_acorn_state_file: str = "data/service-acorn.json"
    service_acorn_gift_wrap_retention_seconds: int | None = (
        DEFAULT_GIFT_WRAP_RETENTION_SECONDS
    )
    nip57_require_description_hash: bool = False
    service_acorn_shutdown_recipient: str | None = None
    service_acorn_shutdown_relay: str | None = None

    def __post_init__(self) -> None:
        if self.wallet_load_timeout_seconds <= 0:
            raise ValueError("SAFEBOX_WALLET_LOAD_TIMEOUT_SECONDS must be positive")
        if self.payment_timeout_seconds <= 0:
            raise ValueError("SAFEBOX_PAYMENT_TIMEOUT_SECONDS must be positive")
        if self.service_acorn_poll_seconds <= 0:
            raise ValueError("SAFEBOX_SERVICE_ACORN_POLL_SECONDS must be positive")
        _validate_gift_wrap_retention(
            self.service_acorn_gift_wrap_retention_seconds
        )
        if self.service_acorn_enabled:
            if not self.service_acorn_home_relay.strip():
                raise ValueError("SAFEBOX_SERVICE_ACORN_HOME_RELAY is required")
            if not self.service_acorn_home_mint.strip():
                raise ValueError("SAFEBOX_SERVICE_ACORN_HOME_MINT is required")
            if not self.service_acorn_state_file.strip():
                raise ValueError("SAFEBOX_SERVICE_ACORN_STATE_FILE is required")

    @classmethod
    def from_env(cls) -> "ServiceAcornSettings":
        """Load worker settings without requiring the web cookie secret."""

        env_file = Path.cwd() / ".env"
        if env_file.is_file():
            load_dotenv(env_file, override=False)
        try:
            load_timeout = float(os.getenv("SAFEBOX_WALLET_LOAD_TIMEOUT_SECONDS", "20"))
            payment_timeout = float(os.getenv("SAFEBOX_PAYMENT_TIMEOUT_SECONDS", "90"))
            poll_seconds = float(os.getenv("SAFEBOX_SERVICE_ACORN_POLL_SECONDS", "0.5"))
        except ValueError as exc:
            raise RuntimeError("Safebox Acorn timeout settings must be numbers") from exc
        retention_seconds = _gift_wrap_retention_from_env()
        shutdown_recipient = os.getenv(
            "SAFEBOX_SERVICE_ACORN_SHUTDOWN_RECIPIENT", ""
        ).strip()
        shutdown_relay = os.getenv(
            "SAFEBOX_SERVICE_ACORN_SHUTDOWN_RELAY", ""
        ).strip()
        return cls(
            wallet_load_timeout_seconds=load_timeout,
            payment_timeout_seconds=payment_timeout,
            database_url=os.getenv(
                "SAFEBOX_DATABASE_URL", "sqlite:///data/database.db"
            ).strip(),
            service_acorn_poll_seconds=poll_seconds,
            allowed_ws_relays=_allowed_ws_relays_from_env(),
            service_acorn_enabled=_env_bool("SAFEBOX_SERVICE_ACORN_ENABLED", False),
            service_acorn_home_relay=os.getenv(
                "SAFEBOX_SERVICE_ACORN_HOME_RELAY",
                "wss://relay.getsafebox.app",
            ).strip(),
            service_acorn_home_mint=os.getenv(
                "SAFEBOX_SERVICE_ACORN_HOME_MINT",
                "https://mint.getsafebox.app",
            ).strip(),
            service_acorn_state_file=os.getenv(
                "SAFEBOX_SERVICE_ACORN_STATE_FILE",
                "data/service-acorn.json",
            ).strip(),
            service_acorn_gift_wrap_retention_seconds=retention_seconds,
            nip57_require_description_hash=_env_bool(
                "SAFEBOX_NIP57_REQUIRE_DESCRIPTION_HASH",
                False,
            ),
            service_acorn_shutdown_recipient=shutdown_recipient or None,
            service_acorn_shutdown_relay=shutdown_relay or None,
        )


@dataclass(frozen=True)
class Settings:
    """Runtime settings that do not contain wallet state."""

    cookie_key: str
    session_ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS
    wallet_load_timeout_seconds: float = 20.0
    payment_timeout_seconds: float = 90.0
    allowed_ws_relays: tuple[str, ...] = ()
    default_bootstrap_relay: str = "wss://relay.getsafebox.app"
    default_home_mint: str = "https://mint.getsafebox.app"
    blossom_home_server: str = "https://blossom.getsafebox.app"
    max_blob_bytes: int = DEFAULT_MAX_BLOB_BYTES
    database_url: str = "sqlite:///data/database.db"
    provider_invoice_wait_seconds: float = 10.0
    lnurl_min_sendable_msat: int = 1000
    lnurl_max_sendable_msat: int = 100_000_000
    lnurl_comment_allowed: int = 256
    service_acorn_enabled: bool = False
    service_acorn_home_relay: str = "wss://relay.getsafebox.app"
    service_acorn_home_mint: str = "https://mint.getsafebox.app"
    service_acorn_state_file: str = "data/service-acorn.json"
    service_acorn_gift_wrap_retention_seconds: int | None = (
        DEFAULT_GIFT_WRAP_RETENTION_SECONDS
    )
    service_acorn_shutdown_recipient: str | None = None
    service_acorn_shutdown_relay: str | None = None

    def __post_init__(self) -> None:
        try:
            Fernet(self.cookie_key.encode("ascii"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "SAFEBOX_COOKIE_KEY must be a valid URL-safe 32-byte application key"
            ) from exc
        if self.session_ttl_seconds < 60:
            raise ValueError("session lifetime must be at least 60 seconds")
        if self.wallet_load_timeout_seconds <= 0:
            raise ValueError("SAFEBOX_WALLET_LOAD_TIMEOUT_SECONDS must be positive")
        if self.payment_timeout_seconds <= 0:
            raise ValueError("SAFEBOX_PAYMENT_TIMEOUT_SECONDS must be positive")
        if self.max_blob_bytes <= 0:
            raise ValueError("SAFEBOX_MAX_BLOB_BYTES must be positive")
        if not self.blossom_home_server.strip():
            raise ValueError("SAFEBOX_BLOSSOM_HOME_SERVER is required")
        if self.provider_invoice_wait_seconds <= 0:
            raise ValueError("SAFEBOX_PROVIDER_INVOICE_WAIT_SECONDS must be positive")
        if self.lnurl_min_sendable_msat < 1000:
            raise ValueError("SAFEBOX_LNURL_MIN_SENDABLE_MSAT must be at least 1000")
        if self.lnurl_max_sendable_msat < self.lnurl_min_sendable_msat:
            raise ValueError(
                "SAFEBOX_LNURL_MAX_SENDABLE_MSAT must not be below the minimum"
            )
        if not 0 <= self.lnurl_comment_allowed <= 1000:
            raise ValueError("SAFEBOX_LNURL_COMMENT_ALLOWED must be between 0 and 1000")
        _validate_gift_wrap_retention(
            self.service_acorn_gift_wrap_retention_seconds
        )
        if self.service_acorn_enabled:
            if not self.service_acorn_home_relay.strip():
                raise ValueError("SAFEBOX_SERVICE_ACORN_HOME_RELAY is required")
            if not self.service_acorn_home_mint.strip():
                raise ValueError("SAFEBOX_SERVICE_ACORN_HOME_MINT is required")
            if not self.service_acorn_state_file.strip():
                raise ValueError("SAFEBOX_SERVICE_ACORN_STATE_FILE is required")

    @classmethod
    def from_env(cls) -> "Settings":
        env_file = Path.cwd() / ".env"
        if env_file.is_file():
            load_dotenv(env_file, override=False)

        cookie_key = os.getenv("SAFEBOX_COOKIE_KEY", "").strip()
        if not cookie_key:
            raise RuntimeError(
                "SAFEBOX_COOKIE_KEY is required; generate a URL-safe "
                "32-byte application key"
            )
        ttl = _session_ttl_seconds_from_env()
        try:
            load_timeout = float(os.getenv("SAFEBOX_WALLET_LOAD_TIMEOUT_SECONDS", "20"))
        except ValueError as exc:
            raise RuntimeError(
                "SAFEBOX_WALLET_LOAD_TIMEOUT_SECONDS must be a number"
            ) from exc
        try:
            payment_timeout = float(os.getenv("SAFEBOX_PAYMENT_TIMEOUT_SECONDS", "90"))
        except ValueError as exc:
            raise RuntimeError("SAFEBOX_PAYMENT_TIMEOUT_SECONDS must be a number") from exc
        try:
            provider_wait = float(os.getenv("SAFEBOX_PROVIDER_INVOICE_WAIT_SECONDS", "10"))
            lnurl_min = int(os.getenv("SAFEBOX_LNURL_MIN_SENDABLE_MSAT", "1000"))
            lnurl_max = int(os.getenv("SAFEBOX_LNURL_MAX_SENDABLE_MSAT", "100000000"))
            comment_allowed = int(os.getenv("SAFEBOX_LNURL_COMMENT_ALLOWED", "256"))
            max_blob_bytes = int(
                os.getenv("SAFEBOX_MAX_BLOB_BYTES", str(DEFAULT_MAX_BLOB_BYTES))
            )
        except ValueError as exc:
            raise RuntimeError("Safebox numeric application settings are invalid") from exc
        default_relay = os.getenv(
            "SAFEBOX_DEFAULT_BOOTSTRAP_RELAY",
            "wss://relay.getsafebox.app",
        ).strip()
        default_mint = os.getenv(
            "SAFEBOX_DEFAULT_HOME_MINT",
            "https://mint.getsafebox.app",
        ).strip()
        shutdown_recipient = os.getenv(
            "SAFEBOX_SERVICE_ACORN_SHUTDOWN_RECIPIENT",
            "",
        ).strip()
        shutdown_relay = os.getenv(
            "SAFEBOX_SERVICE_ACORN_SHUTDOWN_RELAY",
            "",
        ).strip()
        return cls(
            cookie_key=cookie_key,
            session_ttl_seconds=ttl,
            wallet_load_timeout_seconds=load_timeout,
            payment_timeout_seconds=payment_timeout,
            allowed_ws_relays=_allowed_ws_relays_from_env(),
            default_bootstrap_relay=default_relay,
            default_home_mint=default_mint,
            blossom_home_server=os.getenv(
                "SAFEBOX_BLOSSOM_HOME_SERVER",
                "https://blossom.getsafebox.app",
            ).strip(),
            max_blob_bytes=max_blob_bytes,
            database_url=os.getenv(
                "SAFEBOX_DATABASE_URL",
                "sqlite:///data/database.db",
            ).strip(),
            provider_invoice_wait_seconds=provider_wait,
            lnurl_min_sendable_msat=lnurl_min,
            lnurl_max_sendable_msat=lnurl_max,
            lnurl_comment_allowed=comment_allowed,
            service_acorn_enabled=_env_bool(
                "SAFEBOX_SERVICE_ACORN_ENABLED",
                False,
            ),
            service_acorn_home_relay=os.getenv(
                "SAFEBOX_SERVICE_ACORN_HOME_RELAY",
                default_relay,
            ).strip(),
            service_acorn_home_mint=os.getenv(
                "SAFEBOX_SERVICE_ACORN_HOME_MINT",
                default_mint,
            ).strip(),
            service_acorn_state_file=os.getenv(
                "SAFEBOX_SERVICE_ACORN_STATE_FILE",
                "data/service-acorn.json",
            ).strip(),
            service_acorn_gift_wrap_retention_seconds=(
                _gift_wrap_retention_from_env()
            ),
            service_acorn_shutdown_recipient=shutdown_recipient or None,
            service_acorn_shutdown_relay=shutdown_relay or None,
        )
