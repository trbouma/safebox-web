import asyncio
from datetime import timedelta
from email.utils import formatdate
from time import time
from types import SimpleNamespace

import httpx
import pytest
from sqlmodel import Session, select

from app.models import ClaimedHandle, utc_now
from app import provider_payments as provider
from tests.test_lnurl_pay import queued_payment, FakeProviderAcorn


@pytest.mark.parametrize("status", [429, 503])
def test_http_failure_defers_mint_preserves_quotes_and_other_mint_progress(tmp_path, monkeypatch, status):
    engine, first = queued_payment(tmp_path)
    logs = []
    monkeypatch.setattr(provider.logger, "warning", lambda template, *args: logs.append(template % args))
    with Session(engine) as session:
        registration = session.exec(select(ClaimedHandle)).one()
    ids = [first]
    for mint in ("https://mint.example.com", "https://other.example.com"):
        ids.append(provider.enqueue_provider_payment(
            engine, registration=registration, amount_msat=21000,
            comment=None, metadata="[]", mint=mint,
        ))
    for i, payment_id in enumerate(ids):
        provider.update_provider_payment(engine, payment_id,
            status="INVOICE_PENDING", mint_quote=f"quote-{i}", next_check_at=utc_now())

    class Acorn(FakeProviderAcorn):
        async def get_quote_state(self, **kwargs):
            self.probe_calls.append(kwargs)
            if kwargs["mint"] == "https://mint.example.com":
                request = httpx.Request("GET", kwargs["mint"])
                response = httpx.Response(status, request=request,
                    headers={"Retry-After": "120"},
                    json={"detail": "too many requests secret=do-not-log", "request": "lnbc-secret"})
                response.raise_for_status()
            return SimpleNamespace(state="UNPAID", paid=False)

    acorn = Acorn(quote_paid=False)
    asyncio.run(provider.process_provider_payments_once(engine, acorn))
    assert len(acorn.probe_calls) == 2
    assert not acorn.check_calls
    for i, payment_id in enumerate(ids[:2]):
        row = provider.get_provider_payment(engine, payment_id)
        assert row.status == "INVOICE_PENDING"
        assert row.mint_quote == f"quote-{i}"
        assert row.delivery_event_id is None
        assert row.attempts == (1 if i == 0 else 0)
        assert (row.next_check_at - utc_now()).total_seconds() > 115
    text = " ".join(logs)
    assert f"HTTP {status}" in text
    assert "do-not-log" not in text
    assert "lnbc-secret" not in text
    assert provider.get_provider_payment(engine, ids[2]).attempts == 1
    # Existing deadlines survive a new worker instance; no request or issuance
    # is attempted early after restart.
    restarted = FakeProviderAcorn(quote_paid=True)
    asyncio.run(provider.process_provider_payments_once(engine, restarted))
    assert restarted.probe_calls == []
    assert restarted.check_calls == []
    # Once due, resume the original quote, deliver once, and clear its error.
    provider.update_provider_payment(engine, first, next_check_at=utc_now())
    asyncio.run(provider.process_provider_payments_once(engine, restarted))
    row = provider.get_provider_payment(engine, first)
    assert row.status == "DELIVERED"
    assert row.error is None
    assert row.mint_quote == "quote-0"
    assert len(restarted.delivery_calls) == 1
    asyncio.run(provider.process_provider_payments_once(engine, restarted))
    assert len(restarted.delivery_calls) == 1
    engine.dispose()


def test_timeout_backoff_increases_and_resets_after_success(monkeypatch):
    monkeypatch.setattr(provider.random, "uniform", lambda *args: 1)
    invoice = SimpleNamespace(mint="https://mint.example", mint_quote="q")
    class Acorn:
        async def get_quote_state(self, **kwargs):
            raise httpx.ConnectTimeout("sensitive URL")
    acorn = Acorn()
    for expected in (5, 10, 20):
        states = provider._discovery_state(acorn)
        if invoice.mint in states:
            states[invoice.mint]["until"] = 0
        asyncio.run(provider._discover_provider_settlements(acorn, [invoice]))
        assert expected - 1 < states[invoice.mint]["until"] - time() <= expected
    async def success(**kwargs):
        return SimpleNamespace(state="PAID")
    acorn.get_quote_state = success
    states[invoice.mint]["until"] = 0
    asyncio.run(provider._discover_provider_settlements(acorn, [invoice]))
    assert states[invoice.mint]["failures"] == 0


def test_retry_after_date_and_unpaid_age():
    response = httpx.Response(429, headers={"Retry-After": formatdate(time() + 120, usegmt=True)})
    assert 118 < provider._retry_after_seconds(response) <= 120
    response.headers["Retry-After"] = "invalid"
    assert provider._retry_after_seconds(response) == 0
    for age, delay in ((0, 5), (600, 30), (7200, 300), (172800, 1800)):
        invoice = SimpleNamespace(created_at=utc_now() - timedelta(seconds=age))
        assert provider._unpaid_recheck_seconds(invoice) == delay


def test_not_found_http_response_does_not_block_other_quotes():
    class Acorn:
        async def get_quote_state(self, **kwargs):
            if kwargs["quote"] == "missing":
                request = httpx.Request("GET", "https://mint.example")
                httpx.Response(404, request=request).raise_for_status()
            return SimpleNamespace(state="PAID")
    invoices = [SimpleNamespace(mint="https://mint.example", mint_quote=q)
                for q in ("missing", "paid")]
    results = asyncio.run(provider._discover_provider_settlements(Acorn(), invoices))
    assert isinstance(results[0][1], httpx.HTTPStatusError)
    assert results[1][1].state == "PAID"


def test_diagnostics_reports_deferred_discovery_separately(tmp_path):
    from app.payment_diagnostics import _payment_issues
    engine, payment_id = queued_payment(tmp_path)
    provider.update_provider_payment(engine, payment_id, status="INVOICE_PENDING",
        error="Settlement discovery deferred: HTTP 429", next_check_at=utc_now())
    row = provider.get_provider_payment(engine, payment_id)
    issues = _payment_issues(row, None, {"state": "UNPAID"}, None)
    assert any(item["code"] == "SETTLEMENT_DISCOVERY_DEFERRED" for item in issues)
    engine.dispose()
