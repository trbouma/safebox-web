"""Application-scoped Acorn used for provider payment operations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import stat
import tempfile
from typing import Callable

from acorn import Acorn
from acorn.func_utils import generate_seed_phrase_and_nsec

from app.config import ServiceAcornSettings, Settings
from app.security import normalize_bootstrap_relay, normalize_home_mint


logger = logging.getLogger("safebox_web.service_acorn")

ServiceSettings = Settings | ServiceAcornSettings


@dataclass
class ServiceAcornRuntime:
    """One process-wide service Acorn and its mutation lock."""

    acorn: Acorn
    state_path: Path
    recovered: bool
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def service_acorn_state_path(settings: ServiceSettings) -> Path:
    return Path(settings.service_acorn_state_file).expanduser().resolve()


def _write_private_state(path: Path, payload: dict) -> None:
    """Atomically persist the minimum recovery material with mode 0600."""

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


def _read_private_state(path: Path) -> dict:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise RuntimeError(
            f"Service Acorn recovery file must be owner-only (0600): {path}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to read service Acorn recovery file: {path}") from exc
    required = {"nsec", "home_relay", "home_mint", "initialized"}
    if not isinstance(payload, dict) or not required.issubset(payload):
        raise RuntimeError(f"Service Acorn recovery file is incomplete: {path}")
    return payload


async def start_service_acorn(
    settings: ServiceSettings,
    *,
    acorn_factory: Callable[..., Acorn] = Acorn,
    key_generator: Callable[[], tuple[str, str]] = generate_seed_phrase_and_nsec,
) -> ServiceAcornRuntime:
    """Create a fresh service Acorn or recover one after an unclean stop."""

    path = service_acorn_state_path(settings)
    recovered = path.exists()
    seed_phrase: str | None = None

    if recovered:
        state = _read_private_state(path)
        nsec = str(state["nsec"])
        home_relay = normalize_bootstrap_relay(
            str(state["home_relay"]),
            settings.allowed_ws_relays,
        )
        home_mint = normalize_home_mint(str(state["home_mint"]))
        initialized = bool(state["initialized"])
    else:
        home_relay = normalize_bootstrap_relay(
            settings.service_acorn_home_relay,
            settings.allowed_ws_relays,
        )
        home_mint = normalize_home_mint(settings.service_acorn_home_mint)
        seed_phrase, nsec = key_generator()
        initialized = False
        _write_private_state(
            path,
            {
                "nsec": nsec,
                "home_relay": home_relay,
                "home_mint": home_mint,
                "initialized": False,
            },
        )

    acorn = acorn_factory(
        nsec=nsec,
        home_relay=home_relay,
        relays=[home_relay],
        mints=[home_mint],
    )

    try:
        if not initialized:
            await asyncio.wait_for(
                acorn.create_instance(
                    keepkey=seed_phrase is None,
                    seed_phrase=seed_phrase,
                ),
                timeout=settings.wallet_load_timeout_seconds,
            )
            _write_private_state(
                path,
                {
                    "nsec": nsec,
                    "home_relay": home_relay,
                    "home_mint": home_mint,
                    "initialized": True,
                },
            )
        await asyncio.wait_for(
            acorn.load_data(),
            timeout=settings.wallet_load_timeout_seconds,
        )
    except Exception:
        logger.exception(
            "service Acorn startup failed; recovery file retained path=%s",
            path,
        )
        raise

    logger.info(
        "service Acorn ready recovered=%s npub=%s relay=%s mint=%s",
        recovered,
        acorn.pubkey_bech32,
        home_relay,
        home_mint,
    )
    return ServiceAcornRuntime(acorn=acorn, state_path=path, recovered=recovered)


async def stop_service_acorn(
    runtime: ServiceAcornRuntime,
    settings: ServiceSettings,
) -> dict:
    """Sweep any balance, burn relay state, then remove local recovery state."""

    async with runtime.lock:
        acorn = runtime.acorn
        try:
            await asyncio.wait_for(
                acorn.load_data(),
                timeout=settings.wallet_load_timeout_seconds,
            )
            balance = int(acorn.get_balance())
            if balance > 0 and not settings.service_acorn_shutdown_recipient:
                raise RuntimeError(
                    "Service Acorn still holds funds and no shutdown sweep "
                    "recipient is configured"
                )
            result = await asyncio.wait_for(
                acorn.burn_wallet(
                    send_to=(
                        settings.service_acorn_shutdown_recipient
                        if balance > 0
                        else None
                    ),
                    send_relay=settings.service_acorn_shutdown_relay,
                    relays=[acorn.home_relay],
                    allow_funded=False,
                ),
                timeout=settings.payment_timeout_seconds,
            )
        except Exception:
            logger.exception(
                "service Acorn shutdown incomplete; recovery file retained path=%s",
                runtime.state_path,
            )
            raise

        runtime.state_path.unlink(missing_ok=True)
        logger.info(
            "service Acorn burned and recovery file removed npub=%s",
            acorn.pubkey_bech32,
        )
        return result
