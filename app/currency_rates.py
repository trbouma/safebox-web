"""Last-known-good exchange-rate cache for informational wallet estimates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import math
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.models import CurrencyRate, utc_now


CURRENCY_METADATA = {
    "CAD": ("$", "Canadian dollar"),
    "USD": ("$", "United States dollar"),
    "EUR": ("€", "Euro"),
    "GBP": ("£", "British pound"),
    "JPY": ("¥", "Japanese yen"),
    "INR": ("₹", "Indian rupee"),
}


def _normalize_codes(currencies: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(code).strip().upper()
            for code in currencies
            if str(code).strip()
        )
    )


def _public_source_label(source_url: str) -> str:
    """Discard query strings and fragments that may contain provider secrets."""

    parsed = urlsplit(source_url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


async def fetch_currency_rates(
    source_url: str,
    currencies: Iterable[str],
    *,
    timeout_seconds: float = 10.0,
    client: httpx.AsyncClient | None = None,
) -> dict[str, float]:
    """Fetch and validate fiat-per-BTC values without mutating cached data."""

    requested = _normalize_codes(currencies)
    owns_client = client is None
    http_client = client or httpx.AsyncClient(timeout=timeout_seconds)
    try:
        response = await http_client.get(source_url)
        response.raise_for_status()
        payload = response.json()
    finally:
        if owns_client:
            await http_client.aclose()
    if not isinstance(payload, dict):
        raise ValueError("currency rate response must be a JSON object")

    rates: dict[str, float] = {}
    for code in requested:
        item = payload.get(code)
        if not isinstance(item, dict):
            continue
        try:
            value = float(item["15m"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            rates[code] = value
    if not rates:
        raise ValueError("currency rate response contained no requested valid rates")
    return rates


async def refresh_currency_rates(
    engine: Engine,
    *,
    source_url: str,
    currencies: Iterable[str],
    timeout_seconds: float = 10.0,
    client: httpx.AsyncClient | None = None,
    fetched_at: datetime | None = None,
) -> dict:
    """Fetch rates, then atomically replace only successfully returned values."""

    requested = _normalize_codes(currencies)
    rates = await fetch_currency_rates(
        source_url,
        requested,
        timeout_seconds=timeout_seconds,
        client=client,
    )
    observed_at = (fetched_at or utc_now()).replace(tzinfo=None)
    source_label = _public_source_label(source_url)
    with Session(engine) as session:
        for code, value in rates.items():
            symbol, description = CURRENCY_METADATA.get(code, (code, code))
            row = session.get(CurrencyRate, code)
            if row is None:
                row = CurrencyRate(
                    currency_code=code,
                    fiat_per_btc=value,
                    currency_symbol=symbol,
                    currency_description=description,
                    source=source_label,
                    fetched_at=observed_at,
                    updated_at=observed_at,
                )
                session.add(row)
            else:
                row.fiat_per_btc = value
                row.currency_symbol = symbol
                row.currency_description = description
                row.source = source_label
                row.fetched_at = observed_at
                row.updated_at = observed_at
        session.commit()
    return {
        "updated": len(rates),
        "currencies": sorted(rates),
        "missing": sorted(set(requested) - set(rates)),
        "fetched_at": observed_at,
        "source": source_label,
    }


def currency_balance_estimate(
    session: Session,
    *,
    sats: int,
    currency_code: str,
    stale_seconds: int,
    now: datetime | None = None,
) -> dict | None:
    """Return a display-only fiat estimate from the cached rate."""

    code = str(currency_code).strip().upper()
    row = session.get(CurrencyRate, code)
    if row is None or row.fiat_per_btc <= 0:
        return None
    current_time = (now or datetime.now(UTC)).replace(tzinfo=None)
    stale = current_time - row.fetched_at > timedelta(seconds=stale_seconds)
    return {
        "currency_code": code,
        "currency_symbol": row.currency_symbol,
        "amount": int(sats) * row.fiat_per_btc / 100_000_000,
        "fiat_per_btc": row.fiat_per_btc,
        "fetched_at": row.fetched_at,
        "source": row.source,
        "stale": stale,
    }
