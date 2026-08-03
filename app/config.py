"""Environment-backed application configuration."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """Runtime settings that do not contain wallet state."""

    cookie_key: str
    session_ttl_seconds: int = 8 * 60 * 60
    wallet_load_timeout_seconds: float = 20.0
    default_bootstrap_relay: str = "wss://relay.getsafebox.app"

    def __post_init__(self) -> None:
        try:
            Fernet(self.cookie_key.encode("ascii"))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "SAFEBOX_COOKIE_KEY must be a valid URL-safe Fernet key"
            ) from exc
        if self.session_ttl_seconds < 60:
            raise ValueError("SAFEBOX_SESSION_TTL_SECONDS must be at least 60")
        if self.wallet_load_timeout_seconds <= 0:
            raise ValueError("SAFEBOX_WALLET_LOAD_TIMEOUT_SECONDS must be positive")

    @classmethod
    def from_env(cls) -> "Settings":
        env_file = Path.cwd() / ".env"
        if env_file.is_file():
            load_dotenv(env_file, override=False)

        cookie_key = os.getenv("SAFEBOX_COOKIE_KEY", "").strip()
        if not cookie_key:
            raise RuntimeError(
                "SAFEBOX_COOKIE_KEY is required; generate one with "
                "cryptography.fernet.Fernet.generate_key()"
            )
        try:
            ttl = int(os.getenv("SAFEBOX_SESSION_TTL_SECONDS", str(8 * 60 * 60)))
        except ValueError as exc:
            raise RuntimeError("SAFEBOX_SESSION_TTL_SECONDS must be an integer") from exc
        try:
            load_timeout = float(os.getenv("SAFEBOX_WALLET_LOAD_TIMEOUT_SECONDS", "20"))
        except ValueError as exc:
            raise RuntimeError(
                "SAFEBOX_WALLET_LOAD_TIMEOUT_SECONDS must be a number"
            ) from exc
        return cls(
            cookie_key=cookie_key,
            session_ttl_seconds=ttl,
            wallet_load_timeout_seconds=load_timeout,
            default_bootstrap_relay=os.getenv(
                "SAFEBOX_DEFAULT_BOOTSTRAP_RELAY",
                "wss://relay.getsafebox.app",
            ).strip(),
        )
