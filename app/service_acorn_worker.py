"""Standalone single-owner process for the provider Acorn."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from typing import Sequence

from acorn import Acorn

from app.config import ServiceAcornSettings
from app.database import create_database_engine, run_migrations
from app.provider_payments import process_provider_payments_once
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


async def run_worker(
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
    engine = create_database_engine(settings.database_url)
    try:
        runtime = await start_service_acorn(settings)
        service_acorn_runtime = runtime
        service_acorn = runtime.acorn
        logger.info(
            "standalone service Acorn worker ready npub=%s recovered=%s",
            runtime.acorn.pubkey_bech32,
            runtime.recovered,
        )
        try:
            while not worker_stop.is_set():
                try:
                    changed = await process_provider_payments_once(engine, runtime.acorn)
                except Exception:
                    logger.exception("service Acorn provider-payment cycle failed")
                    changed = False
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


async def retire_worker(settings: ServiceAcornSettings) -> dict:
    """Explicitly sweep, burn, and remove an existing service Acorn."""

    _require_enabled(settings)
    state_path = service_acorn_state_path(settings)
    if not state_path.is_file():
        raise RuntimeError(f"No service Acorn recovery state exists at {state_path}")
    runtime = await start_service_acorn(settings)
    return await stop_service_acorn(runtime, settings)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="safebox-service-acorn",
        description="Run or explicitly retire the singleton Safebox service Acorn.",
    )
    parser.add_argument(
        "command",
        choices=("run", "retire"),
        nargs="?",
        default="run",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = ServiceAcornSettings.from_env()
    try:
        if args.command == "retire":
            asyncio.run(retire_worker(settings))
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
