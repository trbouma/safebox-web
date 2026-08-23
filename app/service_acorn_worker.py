"""Standalone single-owner process for the provider Acorn."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from typing import Sequence

from acorn import Acorn
import qrcode

from app.config import ServiceAcornSettings
from app.currency_rates import refresh_currency_rates
from app.database import create_database_engine, run_migrations
from app.provider_payments import process_provider_payments_once, set_provider_identity
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
        set_provider_identity(engine, runtime.acorn.pubkey_hex)
        service_acorn_runtime = runtime
        service_acorn = runtime.acorn
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


async def retire_worker(settings: ServiceAcornSettings) -> dict:
    """Explicitly sweep, burn, and remove an existing service Acorn."""

    _require_enabled(settings)
    state_path = service_acorn_state_path(settings)
    if not state_path.is_file():
        raise RuntimeError(f"No service Acorn recovery state exists at {state_path}")
    runtime = await start_service_acorn(settings)
    return await stop_service_acorn(runtime, settings)


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

    runtime = await start_service_acorn(settings)
    effective_mint = mint or runtime.acorn.home_mint
    quote = await asyncio.to_thread(
        runtime.acorn.deposit,
        amount,
        effective_mint,
    )

    print(f"Service Acorn funding amount: {amount} sats", flush=True)
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
    commands.add_parser("retire", help="sweep and burn the service Acorn")
    fund_parser = commands.add_parser(
        "fund",
        help="deposit an operating reserve into the service Acorn",
    )
    fund_parser.add_argument("amount", type=int, help="reserve amount in sats")
    fund_parser.add_argument(
        "--mint",
        default=None,
        help="optional mint override; defaults to the service Acorn home mint",
    )
    parser.set_defaults(command="run")
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
        elif args.command == "fund":
            result = asyncio.run(fund_worker(settings, args.amount, mint=args.mint))
            print(
                "Service Acorn funding confirmed: "
                f"{result['amount']} sats deposited; "
                f"balance={result['balance']} sats",
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
