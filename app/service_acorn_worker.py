"""Standalone single-owner process for the provider Acorn."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import timedelta
import fcntl
import json
import logging
import os
from pathlib import Path
import signal
from time import time
from typing import Sequence

from acorn import Acorn
import qrcode

from app.config import ServiceAcornSettings
from app.currency_rates import refresh_currency_rates
from app.database import create_database_engine, run_migrations
from app.provider_payments import (
    quarantine_abandoned_provider_claims,
    process_provider_payments_once,
    reconcile_legacy_settlement_timeouts,
    set_provider_identity,
)
from app.service_acorn import (
    ServiceAcornRuntime,
    service_acorn_state_path,
    start_service_acorn,
    stop_service_acorn,
)


logger = logging.getLogger("safebox_web.service_acorn_worker")

# These globals belong only to this worker process. Web workers do not import or
# own the provider wallet.
service_acorn_runtime: ServiceAcornRuntime | None = None
service_acorn: Acorn | None = None
SERVICE_WORKER_HEARTBEAT_SECONDS = 10.0
SERVICE_WORKER_STALE_SECONDS = 45.0


class ServiceAcornWorkerAlreadyRunning(RuntimeError):
    """Raised when another process owns the persisted service Acorn."""


def _worker_operational_path(settings: ServiceAcornSettings, suffix: str) -> Path:
    state_path = service_acorn_state_path(settings)
    # Queue ownership is global to the shared data volume, not to one recovery
    # filename. Two differently configured state files must not create two
    # independent owners of the same provider-payment queue.
    return state_path.parent / f".service-acorn-{suffix}"


def service_acorn_reserve_snapshot_path(settings: ServiceAcornSettings) -> Path:
    return Path(settings.service_acorn_reserve_snapshot_file).expanduser().resolve()


def write_service_acorn_reserve_snapshot(
    settings: ServiceAcornSettings,
    acorn: Acorn,
) -> dict:
    """Persist a read-only operating-reserve snapshot for management surfaces."""

    snapshot = {
        "status": "OK",
        "balance": int(acorn.get_balance()),
        "unit": "sat",
        "mint": acorn.home_mint,
        "npub": acorn.pubkey_bech32,
        "updated_at": int(time()),
    }
    snapshot_path = service_acorn_reserve_snapshot_path(settings)
    snapshot_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_path = snapshot_path.with_name(f".{snapshot_path.name}.tmp")
    temporary_path.write_text(json.dumps(snapshot, sort_keys=True) + "\n")
    os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, snapshot_path)
    os.chmod(snapshot_path, 0o600)
    return snapshot


@contextmanager
def service_acorn_worker_lock(settings: ServiceAcornSettings):
    """Hold an exclusive process lock beside the service-Acorn state file."""

    lock_path = _worker_operational_path(settings, "worker.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+", encoding="utf-8")
    os.chmod(lock_path, 0o600)
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ServiceAcornWorkerAlreadyRunning(
                f"Another service Acorn worker owns {lock_path}"
            ) from exc
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"{os.getpid()}\n")
        lock_file.flush()
        yield lock_path
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()


async def run_service_worker_heartbeat(
    settings: ServiceAcornSettings,
    stop_event: asyncio.Event,
) -> None:
    """Prove that the worker event loop remains able to make progress."""

    heartbeat_path = _worker_operational_path(settings, "worker.heartbeat")
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        while not stop_event.is_set():
            heartbeat_path.touch(mode=0o600)
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=SERVICE_WORKER_HEARTBEAT_SECONDS
                )
            except TimeoutError:
                pass
    finally:
        heartbeat_path.unlink(missing_ok=True)


def service_worker_health(settings: ServiceAcornSettings) -> dict:
    """Return health only while the singleton worker loop is responsive."""

    heartbeat_path = _worker_operational_path(settings, "worker.heartbeat")
    if not heartbeat_path.is_file():
        raise RuntimeError("Service Acorn worker heartbeat is missing")
    age_seconds = max(0.0, time() - heartbeat_path.stat().st_mtime)
    if age_seconds > SERVICE_WORKER_STALE_SECONDS:
        raise RuntimeError(
            f"Service Acorn worker heartbeat is stale ({age_seconds:.1f}s)"
        )
    return {"status": "OK", "heartbeat_age_seconds": round(age_seconds, 3)}


def _configure_operational_logging() -> None:
    """Keep worker INFO logs visible after Alembic configures logging."""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )


def _require_enabled(settings: ServiceAcornSettings) -> None:
    if not settings.service_acorn_enabled:
        raise RuntimeError(
            "Set SAFEBOX_SERVICE_ACORN_ENABLED=true before starting the "
            "standalone service Acorn worker"
        )


def _install_stop_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_number, stop_event.set)
        except (NotImplementedError, RuntimeError):
            # Signal handlers are unavailable on some event loops. Keyboard
            # interruption still terminates the command through asyncio.run().
            pass


async def run_currency_rate_refresh_loop(
    engine,
    settings: ServiceAcornSettings,
    stop_event: asyncio.Event,
) -> None:
    """Refresh display-only rates without delaying provider payments."""

    delay_seconds = 0.0
    while not stop_event.is_set():
        if delay_seconds:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay_seconds)
                return
            except TimeoutError:
                pass
        try:
            refresh_result = await refresh_currency_rates(
                engine,
                source_url=settings.currency_rate_source_url,
                currencies=settings.currency_rate_currencies,
            )
            logger.info(
                "currency rates refreshed updated=%s missing=%s",
                refresh_result["updated"],
                ",".join(refresh_result["missing"]) or "none",
            )
            delay_seconds = settings.currency_rate_interval_seconds
        except Exception as exc:
            # Rates are display-only. Keep prior valid rows and retry sooner
            # than the normal refresh interval without blocking settlement.
            logger.warning(
                "currency rate refresh failed; retaining cached values "
                "error_type=%s",
                type(exc).__name__,
            )
            delay_seconds = min(60.0, settings.currency_rate_interval_seconds)


async def _run_worker_locked(
    settings: ServiceAcornSettings,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Own one Acorn until stopped, retaining it for the next process."""

    global service_acorn_runtime, service_acorn

    _require_enabled(settings)
    worker_stop = stop_event or asyncio.Event()
    if stop_event is None:
        _install_stop_handlers(worker_stop)

    run_migrations(settings.database_url)
    # Alembic's logging configuration intentionally keeps its root logger at
    # WARN. Restore the standalone worker's operational INFO logs after the
    # migration completes so readiness and queue progress remain observable.
    _configure_operational_logging()
    engine = create_database_engine(settings.database_url)
    try:
        runtime = await start_service_acorn(settings)
        set_provider_identity(engine, runtime.acorn.pubkey_hex)
        recovered_timeouts = reconcile_legacy_settlement_timeouts(engine)
        if recovered_timeouts:
            logger.warning(
                "resumed periodic settlement checks for legacy timed-out "
                "provider payments count=%s",
                recovered_timeouts,
            )
        # The process lock proves that no prior worker can still own these
        # claims. Any in-progress state found at startup is therefore abandoned,
        # regardless of its age.
        abandoned = quarantine_abandoned_provider_claims(
            engine, stale_after=timedelta(0)
        )
        if any(abandoned.values()):
            logger.warning(
                "quarantined interrupted provider-payment operations counts=%s",
                abandoned,
            )
        service_acorn_runtime = runtime
        service_acorn = runtime.acorn
        write_service_acorn_reserve_snapshot(settings, runtime.acorn)
        logger.info(
            "standalone service Acorn worker ready npub=%s recovered=%s",
            runtime.acorn.pubkey_bech32,
            runtime.recovered,
        )
        currency_rate_task = (
            asyncio.create_task(
                run_currency_rate_refresh_loop(engine, settings, worker_stop)
            )
            if settings.currency_rates_enabled
            else None
        )
        heartbeat_task = asyncio.create_task(
            run_service_worker_heartbeat(settings, worker_stop)
        )
        try:
            while not worker_stop.is_set():
                try:
                    changed = await process_provider_payments_once(
                        engine,
                        runtime.acorn,
                        gift_wrap_retention_seconds=(
                            settings.service_acorn_gift_wrap_retention_seconds
                        ),
                        nip57_require_description_hash=(
                            settings.nip57_require_description_hash
                        ),
                        delivery_retry_attempts=(
                            settings.service_acorn_delivery_retry_attempts
                        ),
                        delivery_retry_base_seconds=(
                            settings.service_acorn_delivery_retry_base_seconds
                        ),
                        delivery_retry_max_seconds=(
                            settings.service_acorn_delivery_retry_max_seconds
                        ),
                    )
                except Exception:
                    logger.exception("service Acorn provider-payment cycle failed")
                    changed = False
                try:
                    write_service_acorn_reserve_snapshot(settings, runtime.acorn)
                except Exception:
                    logger.exception("service Acorn reserve snapshot update failed")
                if changed:
                    continue
                try:
                    await asyncio.wait_for(
                        worker_stop.wait(),
                        timeout=settings.service_acorn_poll_seconds,
                    )
                except TimeoutError:
                    pass
        finally:
            worker_stop.set()
            await heartbeat_task
            if currency_rate_task is not None:
                await currency_rate_task
            # A routine deploy or restart must not destroy an operational wallet.
            # The mode-0600 state file lets the next singleton worker recover it.
            logger.info(
                "standalone service Acorn worker stopped; recovery retained path=%s",
                runtime.state_path,
            )
            service_acorn_runtime = None
            service_acorn = None
    finally:
        engine.dispose()


async def run_worker(
    settings: ServiceAcornSettings,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run exactly one process against a persisted service Acorn."""

    with service_acorn_worker_lock(settings) as lock_path:
        logger.info("service Acorn singleton lock acquired path=%s", lock_path)
        await _run_worker_locked(settings, stop_event=stop_event)


async def retire_worker(settings: ServiceAcornSettings) -> dict:
    """Explicitly sweep, burn, and remove an existing service Acorn."""

    _require_enabled(settings)
    state_path = service_acorn_state_path(settings)
    if not state_path.is_file():
        raise RuntimeError(f"No service Acorn recovery state exists at {state_path}")
    with service_acorn_worker_lock(settings):
        runtime = await start_service_acorn(settings)
        return await stop_service_acorn(runtime, settings)


async def balance_worker(settings: ServiceAcornSettings) -> dict:
    """Read the persisted service Acorn operating balance."""

    _require_enabled(settings)
    state_path = service_acorn_state_path(settings)
    if not state_path.is_file():
        raise RuntimeError(
            "No service Acorn recovery state exists. Start the worker once "
            "before checking its balance."
        )
    with service_acorn_worker_lock(settings):
        runtime = await start_service_acorn(settings)
        return write_service_acorn_reserve_snapshot(settings, runtime.acorn)


async def fund_worker(
    settings: ServiceAcornSettings,
    amount: int,
    *,
    mint: str | None = None,
    poll_interval_seconds: float = 3.0,
) -> dict:
    """Fund the persisted service Acorn without exposing its private key."""

    _require_enabled(settings)
    if amount <= 0:
        raise ValueError("Service Acorn funding amount must be greater than zero")
    if not service_acorn_state_path(settings).is_file():
        raise RuntimeError(
            "No service Acorn recovery state exists. Start the worker once "
            "before funding it."
        )

    with service_acorn_worker_lock(settings):
        runtime = await start_service_acorn(settings)
        effective_mint = mint or runtime.acorn.home_mint
        quote = await asyncio.to_thread(
            runtime.acorn.deposit,
            amount,
            effective_mint,
        )

        print(f"Service Acorn funding amount: ₿{amount}", flush=True)
        print(f"Mint: {effective_mint}", flush=True)
        print(f"Quote: {quote.quote}", flush=True)
        print(f"Invoice:\n{quote.invoice}\n", flush=True)
        qr = qrcode.QRCode()
        qr.add_data(quote.invoice)
        qr.make(fit=True)
        qr.print_ascii()
        print(
            "Waiting for payment confirmation. Keep this command running...",
            flush=True,
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.payment_timeout_seconds
        while True:
            paid, _ = await runtime.acorn.check_quote(
                quote=quote.quote,
                amount=amount,
                mint=effective_mint,
            )
            if paid:
                await runtime.acorn.add_tx_history(
                    tx_type="C",
                    amount=amount,
                    comment="service Acorn operating reserve deposit",
                )
                balance = int(runtime.acorn.get_balance())
                logger.info(
                    "service Acorn funding confirmed amount=%s balance=%s mint=%s",
                    amount,
                    balance,
                    effective_mint,
                )
                return {
                    "status": "CONFIRMED",
                    "amount": amount,
                    "balance": balance,
                    "mint": effective_mint,
                }
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise RuntimeError(
                    "Service Acorn funding was not confirmed before timeout. "
                    f"Preserve quote {quote.quote} and inspect the wallet before "
                    "requesting another invoice."
                )
            await asyncio.sleep(min(poll_interval_seconds, remaining))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="safebox-service-acorn",
        description=(
            "Run, fund, or explicitly retire the singleton Safebox service Acorn."
        ),
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("run", help="run the singleton provider worker")
    commands.add_parser("health", help="check singleton worker event-loop health")
    commands.add_parser("retire", help="sweep and burn the service Acorn")
    balance_parser = commands.add_parser(
        "balance",
        help="show the service Acorn operating reserve balance",
    )
    balance_parser.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-readable result",
    )
    fund_parser = commands.add_parser(
        "fund",
        help="deposit an operating reserve into the service Acorn",
    )
    fund_parser.add_argument("amount", type=int, help="reserve amount in ₿")
    fund_parser.add_argument(
        "--mint",
        default=None,
        help="optional mint override; defaults to the service Acorn home mint",
    )
    parser.set_defaults(command="run")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _configure_operational_logging()
    settings = ServiceAcornSettings.from_env()
    try:
        if args.command == "health":
            print(json.dumps(service_worker_health(settings)), flush=True)
        elif args.command == "retire":
            asyncio.run(retire_worker(settings))
        elif args.command == "balance":
            result = asyncio.run(balance_worker(settings))
            if args.json:
                print(json.dumps(result), flush=True)
            else:
                print(
                    f"Service Acorn reserve: ₿{result['balance']}",
                    flush=True,
                )
        elif args.command == "fund":
            result = asyncio.run(fund_worker(settings, args.amount, mint=args.mint))
            print(
                "Service Acorn funding confirmed: "
                f"₿{result['amount']} deposited; "
                f"balance=₿{result['balance']}",
                flush=True,
            )
        else:
            asyncio.run(run_worker(settings))
    except KeyboardInterrupt:
        return 0
    except Exception:
        logger.exception("service Acorn worker command failed command=%s", args.command)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
