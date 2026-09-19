from __future__ import annotations

from datetime import timedelta
import json

from sqlmodel import Session, SQLModel

from app.config import ServiceAcornSettings
from app.database import create_database_engine
from app.models import ClaimedHandle, ProviderPayment, utc_now
import app.payment_diagnostics as diagnostics


class QuoteResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self.payload


def diagnostic_database(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'diagnostics.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            ClaimedHandle(
                claimed_handle="alice",
                npub="npub1alice",
                home_relay="wss://relay.example.com",
            )
        )
        session.commit()
    return engine


def add_payment(engine, **changes) -> ProviderPayment:
    values = {
        "payment_id": "payment-1",
        "claimed_handle": "alice",
        "recipient_npub": "npub1alice",
        "recipient_relay": "wss://relay.example.com",
        "amount_msat": 100_000,
        "amount_sat": 100,
        "comment": None,
        "lnurl_metadata": '[["text/plain","test"]]',
        "status": "INVOICE_PENDING",
        "mint": "https://mint.example.com",
        "mint_quote": "quote-1",
        "invoice": "redacted-by-diagnostics",
        "next_check_at": utc_now(),
    }
    values.update(changes)
    payment = ProviderPayment(**values)
    with Session(engine) as session:
        session.add(payment)
        session.commit()
        session.refresh(payment)
        session.expunge(payment)
    return payment


def healthy_worker(monkeypatch) -> None:
    monkeypatch.setattr(
        diagnostics,
        "service_worker_health",
        lambda settings: {"status": "OK", "heartbeat_age_seconds": 1.25},
    )


def test_diagnoses_paid_quote_that_worker_never_polled(
    tmp_path,
    monkeypatch,
) -> None:
    engine = diagnostic_database(tmp_path)
    add_payment(engine, attempts=0)
    healthy_worker(monkeypatch)

    def mint_get(url, *, timeout):
        assert url.endswith("/v1/mint/quote/bolt11/quote-1")
        assert timeout == 5
        return QuoteResponse(
            {
                "state": "PAID",
                "amount": 100,
                "amount_paid": 100,
                "amount_issued": 0,
                "request": "lnbc-secret-not-for-report",
            }
        )

    report = diagnostics.diagnose_handle(
        engine,
        ServiceAcornSettings(),
        "Alice",
        timeout=5,
        http_get=mint_get,
    )

    assert report["status"] == "PROBLEMS_FOUND"
    assert report["problem_count"] == 1
    assert report["queue"]["paid_unissued_amount_sat"] == 100
    assert report["payments"][0]["issues"][0]["code"] == "PAID_NOT_POLLED"
    assert report["payments"][0]["mint_quote"] == {
        "state": "PAID",
        "amount": 100,
        "amount_paid": 100,
        "amount_issued": 0,
        "updated_at": None,
    }
    assert "lnbc-secret" not in json.dumps(report)
    engine.dispose()


def test_problems_only_omits_normal_unpaid_invoice(tmp_path, monkeypatch) -> None:
    engine = diagnostic_database(tmp_path)
    add_payment(engine)
    healthy_worker(monkeypatch)

    report = diagnostics.diagnose_handle(
        engine,
        ServiceAcornSettings(),
        "alice",
        problems_only=True,
        http_get=lambda url, timeout: QuoteResponse(
            {
                "state": "UNPAID",
                "amount": 100,
                "amount_paid": 0,
                "amount_issued": 0,
            }
        ),
    )

    assert report["status"] == "OK"
    assert report["queue"]["examined"] == 1
    assert report["queue"]["status_counts"] == {"INVOICE_PENDING": 1}
    assert report["queue"]["paid_unissued_amount_sat"] == 0
    assert report["payments"] == []
    engine.dispose()


def test_reports_stale_delivery_and_registration_mismatch(
    tmp_path,
    monkeypatch,
) -> None:
    engine = diagnostic_database(tmp_path)
    add_payment(
        engine,
        status="DELIVERING",
        recipient_npub="npub1wrong",
        recipient_relay="wss://wrong.example.com",
        updated_at=utc_now() - timedelta(hours=1),
    )
    healthy_worker(monkeypatch)

    report = diagnostics.diagnose_handle(
        engine,
        ServiceAcornSettings(),
        "alice",
        http_get=lambda url, timeout: QuoteResponse(
            {
                "state": "PAID",
                "amount": 100,
                "amount_paid": 100,
                "amount_issued": 100,
            }
        ),
    )

    codes = {issue["code"] for issue in report["payments"][0]["issues"]}
    assert codes == {
        "RECIPIENT_IDENTITY_MISMATCH",
        "RECIPIENT_RELAY_MISMATCH",
        "STALE_IN_PROGRESS_STATE",
    }
    rendered = diagnostics.format_diagnostic_report(report)
    assert "No wallet or payment state was changed." in rendered
    assert "npub1wrong" not in rendered
    engine.dispose()


def test_worker_health_failure_is_a_global_finding(tmp_path, monkeypatch) -> None:
    engine = diagnostic_database(tmp_path)

    def unhealthy(settings):
        raise RuntimeError("heartbeat is stale")

    monkeypatch.setattr(diagnostics, "service_worker_health", unhealthy)
    report = diagnostics.diagnose_handle(
        engine,
        ServiceAcornSettings(),
        "alice",
        http_get=lambda url, timeout: QuoteResponse({}),
    )

    assert report["worker"]["status"] == "UNHEALTHY"
    assert report["issues"][0]["code"] == "SERVICE_WORKER_UNHEALTHY"
    engine.dispose()


def test_parser_accepts_operator_options() -> None:
    args = diagnostics._parser().parse_args(
        ["alice", "--limit", "50", "--problems-only", "--json", "--timeout", "3"]
    )

    assert args.handle == "alice"
    assert args.limit == 50
    assert args.problems_only is True
    assert args.json is True
    assert args.timeout == 3
