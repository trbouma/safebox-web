from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import httpx
import pytest
from sqlmodel import Session

from app.currency_rates import (
    CURRENCY_METADATA,
    currency_balance_estimate,
    refresh_currency_rates,
)
from app.database import create_database_engine, run_migrations
from app.models import CurrencyRate


@pytest.mark.parametrize(
    ("currency", "symbol", "description"),
    (
        ("CNY", "CN¥", "Chinese yuan (renminbi)"),
        ("AUD", "A$", "Australian dollar"),
        ("CHF", "Fr", "Swiss franc"),
        ("SGD", "S$", "Singapore dollar"),
        ("HKD", "HK$", "Hong Kong dollar"),
        ("BRL", "R$", "Brazilian real"),
    ),
)
def test_additional_currency_metadata_is_unambiguous(
    currency: str,
    symbol: str,
    description: str,
) -> None:
    assert CURRENCY_METADATA[currency] == (symbol, description)


def test_refresh_persists_only_valid_requested_rates(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'rates.db'}"
    run_migrations(database_url)
    engine = create_database_engine(database_url)
    fetched_at = datetime(2026, 8, 12, 12, 0, 0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "CAD": {"15m": 140_000.0},
                "USD": {"15m": "100000.5"},
                "EUR": {"15m": -1},
                "AUD": {"15m": 150_000},
            },
        )

    async def scenario() -> dict:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await refresh_currency_rates(
                engine,
                source_url="https://rates.example/ticker?api_key=secret",
                currencies=("CAD", "USD", "EUR"),
                client=client,
                fetched_at=fetched_at,
            )

    result = asyncio.run(scenario())
    with Session(engine) as session:
        cad = session.get(CurrencyRate, "CAD")
        usd = session.get(CurrencyRate, "USD")
        eur = session.get(CurrencyRate, "EUR")

    assert result["updated"] == 2
    assert result["missing"] == ["EUR"]
    assert cad is not None and cad.fiat_per_btc == 140_000.0
    assert cad.currency_symbol == "$"
    assert cad.fetched_at == fetched_at
    assert cad.source == "https://rates.example/ticker"
    assert usd is not None and usd.fiat_per_btc == 100_000.5
    assert eur is None
    engine.dispose()


def test_failed_refresh_retains_last_known_good_rate(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'rates.db'}"
    run_migrations(database_url)
    engine = create_database_engine(database_url)
    with Session(engine) as session:
        session.add(
            CurrencyRate(
                currency_code="CAD",
                fiat_per_btc=123_456.0,
                currency_symbol="$",
                currency_description="Canadian dollar",
                source="existing",
                fetched_at=datetime(2026, 8, 12, 10, 0, 0),
                updated_at=datetime(2026, 8, 12, 10, 0, 0),
            )
        )
        session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "unavailable"})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await refresh_currency_rates(
                engine,
                source_url="https://rates.example/ticker",
                currencies=("CAD",),
                client=client,
            )

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(scenario())

    with Session(engine) as session:
        cad = session.get(CurrencyRate, "CAD")
        assert cad is not None
        assert cad.fiat_per_btc == 123_456.0
        assert cad.source == "existing"
    engine.dispose()


def test_balance_estimate_marks_old_cache_as_stale(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'rates.db'}"
    run_migrations(database_url)
    engine = create_database_engine(database_url)
    now = datetime(2026, 8, 12, 12, 0, 0)
    with Session(engine) as session:
        session.add(
            CurrencyRate(
                currency_code="CAD",
                fiat_per_btc=200_000.0,
                currency_symbol="$",
                currency_description="Canadian dollar",
                source="test",
                fetched_at=now - timedelta(hours=25),
                updated_at=now - timedelta(hours=25),
            )
        )
        session.commit()
        estimate = currency_balance_estimate(
            session,
            sats=50_000,
            currency_code="CAD",
            stale_seconds=86_400,
            now=now,
        )

    assert estimate is not None
    assert estimate["amount"] == 100.0
    assert estimate["stale"] is True
    engine.dispose()
