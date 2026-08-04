from __future__ import annotations

import asyncio
from types import SimpleNamespace

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import Settings
from app.database import create_database_engine, run_migrations
import app.lnurl_pay as lnurl_module
from app.main import create_app
from app.models import ClaimedHandle, ProviderPayment
from app.provider_payments import (
    enqueue_provider_payment,
    get_provider_payment,
    process_provider_payments_once,
    update_provider_payment,
)


def payment_settings(tmp_path) -> Settings:
    return Settings(
        cookie_key=Fernet.generate_key().decode("ascii"),
        database_url=f"sqlite:///{tmp_path / 'payments.db'}",
        service_acorn_home_mint="https://mint.example.com",
        provider_invoice_wait_seconds=1,
        lnurl_min_sendable_msat=1000,
        lnurl_max_sendable_msat=100_000,
    )


def add_registration(engine) -> ClaimedHandle:
    with Session(engine) as session:
        registration = ClaimedHandle(
            claimed_handle="alice",
            npub="npub1alice",
            home_relay="wss://relay.example.com",
        )
        session.add(registration)
        session.commit()
        session.refresh(registration)
        return registration


def test_lnurl_discovery_and_callback_queue_invoice(tmp_path, monkeypatch) -> None:
    settings = payment_settings(tmp_path)
    app = create_app(settings)

    async def fake_wait(engine, payment_id, *, timeout, interval=0.05):
        update_provider_payment(
            engine,
            payment_id,
            status="INVOICE_PENDING",
            mint_quote="quote-1",
            invoice="lnbc21-test",
        )
        return get_provider_payment(engine, payment_id)

    monkeypatch.setattr(lnurl_module, "wait_for_provider_invoice", fake_wait)

    with TestClient(app, base_url="https://pay.example") as client:
        add_registration(app.state.database_engine)
        discovery = client.get("/.well-known/lnurlp/alice")
        assert discovery.status_code == 200
        assert discovery.headers["access-control-allow-origin"] == "*"
        payload = discovery.json()
        assert payload["tag"] == "payRequest"
        assert payload["callback"] == "https://pay.example/lnpay/alice"
        assert payload["minSendable"] == 1000
        assert payload["maxSendable"] == 100_000
        assert "alice@pay.example" in payload["metadata"]

        callback = client.get(
            "/lnpay/alice",
            params={"amount": "21000", "comment": "hello"},
        )
        assert callback.status_code == 200
        assert callback.json()["pr"] == "lnbc21-test"

        with Session(app.state.database_engine) as session:
            payment = session.exec(select(ProviderPayment)).one()
        assert payment.status == "INVOICE_PENDING"
        assert payment.amount_sat == 21
        assert payment.recipient_npub == "npub1alice"
        assert payment.recipient_relay == "wss://relay.example.com"
        assert payment.comment == "hello"


def test_lnurl_callback_rejects_invalid_or_unsupported_requests(tmp_path) -> None:
    settings = payment_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app, base_url="https://pay.example") as client:
        add_registration(app.state.database_engine)
        assert client.get("/.well-known/lnurlp/missing").json() == {
            "status": "ERROR",
            "reason": "Lightning address not found",
        }
        assert client.get("/lnpay/alice", params={"amount": "1001"}).json()[
            "status"
        ] == "ERROR"
        assert client.get(
            "/lnpay/alice", params={"amount": "21000", "nostr": "event"}
        ).json() == {
            "status": "ERROR",
            "reason": "Nostr zap requests are not supported yet",
        }
        with Session(app.state.database_engine) as session:
            assert session.exec(select(ProviderPayment)).all() == []


class FakeProviderAcorn:
    def __init__(self, *, fail_delivery: bool = False) -> None:
        self.fail_delivery = fail_delivery
        self.deposit_calls: list[dict] = []
        self.check_calls: list[dict] = []
        self.delivery_calls: list[dict] = []

    def deposit(self, **kwargs):
        self.deposit_calls.append(kwargs)
        return SimpleNamespace(quote="quote-1", invoice="lnbc21-test")

    async def check_quote(self, **kwargs):
        self.check_calls.append(kwargs)
        return True, "lnbc21-test"

    async def send_ecash_transfer(self, **kwargs):
        self.delivery_calls.append(kwargs)
        if self.fail_delivery:
            raise RuntimeError("ambiguous relay publish")
        return {"event_id": "event-1"}


def queued_payment(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'worker-payments.db'}"
    run_migrations(database_url)
    engine = create_database_engine(database_url)
    with Session(engine) as session:
        registration = ClaimedHandle(
            claimed_handle="alice",
            npub="npub1alice",
            home_relay="wss://relay.example.com",
        )
        session.add(registration)
        session.commit()
        session.refresh(registration)
    payment_id = enqueue_provider_payment(
        engine,
        registration=registration,
        amount_msat=21_000,
        comment="provider test",
        metadata='[["text/plain","test"]]',
        mint="https://mint.example.com",
    )
    return engine, payment_id


def test_worker_creates_invoice_settles_and_delivers_ecash(tmp_path) -> None:
    engine, payment_id = queued_payment(tmp_path)
    acorn = FakeProviderAcorn()

    assert asyncio.run(process_provider_payments_once(engine, acorn)) is True

    payment = get_provider_payment(engine, payment_id)
    assert payment.status == "DELIVERED"
    assert payment.delivery_event_id == "event-1"
    assert acorn.deposit_calls == [
        {"amount": 21, "mint": "https://mint.example.com"}
    ]
    assert acorn.check_calls[0]["mint"] == "mint.example.com"
    assert acorn.delivery_calls[0]["recipient"] == "npub1alice"
    assert acorn.delivery_calls[0]["relay"] == "wss://relay.example.com"
    engine.dispose()


def test_ambiguous_delivery_is_not_automatically_retried(tmp_path) -> None:
    engine, payment_id = queued_payment(tmp_path)
    acorn = FakeProviderAcorn(fail_delivery=True)

    asyncio.run(process_provider_payments_once(engine, acorn))
    assert get_provider_payment(engine, payment_id).status == "DELIVERY_FAILED"
    asyncio.run(process_provider_payments_once(engine, acorn))
    assert len(acorn.delivery_calls) == 1
    engine.dispose()
