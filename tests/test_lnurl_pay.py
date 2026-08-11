from __future__ import annotations

import asyncio
import hashlib
import json
from time import time
from types import SimpleNamespace

from bech32 import bech32_decode, convertbits
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from monstr.encrypt import Keys
from monstr.event.event import Event
from sqlmodel import Session, select

from app.config import Settings
from app.database import create_database_engine, run_migrations
import app.lnurl_pay as lnurl_module
from app.main import create_app
from app.models import ClaimedHandle, ProviderPayment, ProviderZap
from app.nip57 import build_zap_receipt, validate_zap_request
from app.provider_payments import (
    enqueue_provider_payment,
    get_provider_payment,
    get_provider_zap,
    process_provider_payments_once,
    set_provider_identity,
    update_provider_payment,
)
import app.provider_payments as provider_module


def test_encode_lnurl_round_trips_to_payment_endpoint() -> None:
    endpoint = "https://safebox.example/.well-known/lnurlp/alice"

    encoded = lnurl_module.encode_lnurl(endpoint)
    hrp, words = bech32_decode(encoded)
    decoded = convertbits(words, 5, 8, False)

    assert encoded.startswith("LNURL1")
    assert encoded == encoded.upper()
    assert hrp == "lnurl"
    assert bytes(decoded).decode("utf-8") == endpoint


def test_encode_lnurl_rejects_non_url_payload() -> None:
    try:
        lnurl_module.encode_lnurl("alice@safebox.example")
    except ValueError as exc:
        assert "absolute HTTP(S) URL" in str(exc)
    else:
        raise AssertionError("invalid LNURL input was accepted")


def payment_settings(tmp_path) -> Settings:
    return Settings(
        cookie_key=Fernet.generate_key().decode("ascii"),
        database_url=f"sqlite:///{tmp_path / 'payments.db'}",
        service_acorn_home_mint="https://mint.example.com",
        provider_invoice_wait_seconds=1,
        lnurl_min_sendable_msat=1000,
        lnurl_max_sendable_msat=100_000,
    )


RECIPIENT_KEYS = Keys(priv_k="11" * 32)
SENDER_KEYS = Keys(priv_k="22" * 32)
PROVIDER_KEYS = Keys(priv_k="33" * 32)


def add_registration(engine, *, real_key: bool = False) -> ClaimedHandle:
    with Session(engine) as session:
        registration = ClaimedHandle(
            claimed_handle="alice",
            npub=(
                RECIPIENT_KEYS.public_key_bech32()
                if real_key
                else "npub1alice"
            ),
            home_relay="wss://relay.example.com",
        )
        session.add(registration)
        session.commit()
        session.refresh(registration)
        return registration


def signed_zap_request(*, lnurl: str, amount_msat: int = 21_000) -> str:
    event = Event(
        kind=9734,
        content="pytest zap",
        pub_key=SENDER_KEYS.public_key_hex(),
        tags=[
            ["relays", "wss://relay.example.com"],
            ["amount", str(amount_msat)],
            ["lnurl", lnurl],
            ["p", RECIPIENT_KEYS.public_key_hex()],
        ],
    )
    event.sign(SENDER_KEYS.private_key_hex())
    return json.dumps(event.data(), separators=(",", ":"), sort_keys=True)


def validated_zap_request():
    lnurl = lnurl_module.encode_lnurl(
        "https://pay.example/.well-known/lnurlp/alice"
    )
    return validate_zap_request(
        signed_zap_request(lnurl=lnurl),
        amount_msat=21_000,
        provider_pubkey=PROVIDER_KEYS.public_key_hex(),
        expected_lnurl=lnurl,
    )


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
        rejected_zap = client.get(
            "/lnpay/alice", params={"amount": "21000", "nostr": "event"}
        ).json()
        assert rejected_zap["status"] == "ERROR"
        assert rejected_zap["reason"] == "Nostr zap service is not ready"
        with Session(app.state.database_engine) as session:
            assert session.exec(select(ProviderPayment)).all() == []


def test_lnurl_discovery_accepts_and_persists_valid_zap(tmp_path, monkeypatch) -> None:
    settings = payment_settings(tmp_path)
    app = create_app(settings)

    quote_calls: list[str] = []

    def fake_quote(payment, zap, *, require_description_hash):
        quote_calls.append(payment.payment_id)
        return SimpleNamespace(
            quote="quote-zap",
            invoice="lnbc21-zap",
            description_hash_bound=False,
        )

    monkeypatch.setattr(provider_module, "_request_zap_mint_quote", fake_quote)

    with TestClient(app, base_url="https://pay.example") as client:
        add_registration(app.state.database_engine, real_key=True)
        set_provider_identity(
            app.state.database_engine,
            PROVIDER_KEYS.public_key_hex(),
        )
        discovery = client.get("/.well-known/lnurlp/alice").json()
        assert discovery["allowsNostr"] is True
        assert discovery["nostrPubkey"] == PROVIDER_KEYS.public_key_hex()

        lnurl = lnurl_module.encode_lnurl(
            "https://pay.example/.well-known/lnurlp/alice"
        )
        zap_json = signed_zap_request(lnurl=lnurl)
        callback = client.get(
            "/lnpay/alice",
            params={"amount": "21000", "nostr": zap_json},
        )
        assert callback.json()["pr"] == "lnbc21-zap"

        repeated = client.get(
            "/lnpay/alice",
            params={"amount": "21000", "nostr": zap_json},
        )
        assert repeated.json()["pr"] == "lnbc21-zap"

        with Session(app.state.database_engine) as session:
            payment = session.exec(select(ProviderPayment)).one()
            zap = session.exec(select(ProviderZap)).one()
        assert payment.comment == "pytest zap"
        assert payment.status == "INVOICE_PENDING"
        assert zap.payment_id == payment.payment_id
        assert zap.request_json == zap_json
        assert json.loads(zap.receipt_relays_json) == [
            "wss://relay.example.com"
        ]
        assert quote_calls == [payment.payment_id]


def test_zap_callback_returns_clean_error_when_mint_quote_fails(
    tmp_path, monkeypatch
) -> None:
    settings = payment_settings(tmp_path)
    app = create_app(settings)

    def fail_quote(payment, zap, *, require_description_hash):
        raise RuntimeError("mint unavailable")

    monkeypatch.setattr(provider_module, "_request_zap_mint_quote", fail_quote)

    with TestClient(app, base_url="https://pay.example") as client:
        add_registration(app.state.database_engine, real_key=True)
        set_provider_identity(app.state.database_engine, PROVIDER_KEYS.public_key_hex())
        lnurl = lnurl_module.encode_lnurl(
            "https://pay.example/.well-known/lnurlp/alice"
        )
        response = client.get(
            "/lnpay/alice",
            params={"amount": "21000", "nostr": signed_zap_request(lnurl=lnurl)},
        )

        assert response.status_code == 200
        assert response.json() == {
            "status": "ERROR",
            "reason": "Unable to create a Lightning invoice",
        }
        with Session(app.state.database_engine) as session:
            payment = session.exec(select(ProviderPayment)).one()
        assert payment.status == "FAILED"
        assert payment.invoice is None


def test_zap_validation_rejects_amount_and_preserves_social_recipient() -> None:
    lnurl = lnurl_module.encode_lnurl(
        "https://pay.example/.well-known/lnurlp/alice"
    )
    zap_json = signed_zap_request(lnurl=lnurl)
    try:
        validate_zap_request(
            zap_json,
            amount_msat=22_000,
            provider_pubkey=PROVIDER_KEYS.public_key_hex(),
            expected_lnurl=lnurl,
        )
    except ValueError as exc:
        assert "amount does not match" in str(exc)
    else:
        raise AssertionError("mismatched zap amount was accepted")

    validated = validate_zap_request(
        zap_json,
        amount_msat=21_000,
        provider_pubkey=PROVIDER_KEYS.public_key_hex(),
        expected_lnurl=lnurl,
    )
    assert validated.recipient_pubkey == RECIPIENT_KEYS.public_key_hex()


def test_zap_validation_ignores_unsafe_relay_hints_when_wss_remains() -> None:
    lnurl = lnurl_module.encode_lnurl(
        "https://pay.example/.well-known/lnurlp/alice"
    )
    payload = json.loads(signed_zap_request(lnurl=lnurl))
    payload["tags"][0] = [
        "relays",
        "ws://relay.example.com",
        "wss://localhost:7447",
        "wss://relay.example.com",
    ]
    event = Event(
        kind=payload["kind"],
        content=payload["content"],
        pub_key=SENDER_KEYS.public_key_hex(),
        tags=payload["tags"],
        created_at=payload["created_at"],
    )
    event.sign(SENDER_KEYS.private_key_hex())

    validated = validate_zap_request(
        json.dumps(event.data(), separators=(",", ":"), sort_keys=True),
        amount_msat=21_000,
        provider_pubkey=PROVIDER_KEYS.public_key_hex(),
        expected_lnurl=lnurl,
    )

    assert validated.relays == ("wss://relay.example.com",)


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


def queued_payment(tmp_path, *, zap: bool = False):
    database_url = f"sqlite:///{tmp_path / 'worker-payments.db'}"
    run_migrations(database_url)
    engine = create_database_engine(database_url)
    with Session(engine) as session:
        registration = ClaimedHandle(
            claimed_handle="alice",
            npub=(
                RECIPIENT_KEYS.public_key_bech32() if zap else "npub1alice"
            ),
            home_relay="wss://relay.example.com",
        )
        session.add(registration)
        session.commit()
        session.refresh(registration)
    payment_id = enqueue_provider_payment(
        engine,
        registration=registration,
        amount_msat=21_000,
        comment="pytest zap" if zap else "provider test",
        metadata='[["text/plain","test"]]',
        mint="https://mint.example.com",
        zap_request=validated_zap_request() if zap else None,
    )
    return engine, payment_id


def test_worker_creates_invoice_settles_and_delivers_ecash(tmp_path) -> None:
    engine, payment_id = queued_payment(tmp_path)
    acorn = FakeProviderAcorn()

    before = int(time())
    assert asyncio.run(
        process_provider_payments_once(
            engine,
            acorn,
            gift_wrap_retention_seconds=3600,
        )
    ) is True

    payment = get_provider_payment(engine, payment_id)
    assert payment.status == "DELIVERED"
    assert payment.delivery_event_id == "event-1"
    assert acorn.deposit_calls == [
        {"amount": 21, "mint": "https://mint.example.com"}
    ]
    assert acorn.check_calls[0]["mint"] == "mint.example.com"
    assert acorn.delivery_calls[0]["recipient"] == "npub1alice"
    assert acorn.delivery_calls[0]["relay"] == "wss://relay.example.com"
    assert before + 3600 <= acorn.delivery_calls[0]["expiration"] <= int(time()) + 3600
    engine.dispose()


def test_ambiguous_delivery_is_not_automatically_retried(tmp_path) -> None:
    engine, payment_id = queued_payment(tmp_path)
    acorn = FakeProviderAcorn(fail_delivery=True)

    asyncio.run(process_provider_payments_once(engine, acorn))
    assert get_provider_payment(engine, payment_id).status == "DELIVERY_FAILED"
    asyncio.run(process_provider_payments_once(engine, acorn))
    assert len(acorn.delivery_calls) == 1
    engine.dispose()


def test_zap_worker_delivers_ecash_then_publishes_receipt(
    tmp_path, monkeypatch
) -> None:
    engine, payment_id = queued_payment(tmp_path, zap=True)
    acorn = FakeProviderAcorn()

    monkeypatch.setattr(
        provider_module,
        "_request_zap_mint_quote",
        lambda payment, zap, **kwargs: SimpleNamespace(
            quote="quote-zap", invoice="lnbc21-zap"
        ),
    )

    async def fake_publish(acorn, payment, zap):
        return SimpleNamespace(
            id="receipt-event-1",
            data=lambda: {"id": "receipt-event-1", "kind": 9735},
        )

    monkeypatch.setattr(
        provider_module,
        "_publish_provider_zap_receipt",
        fake_publish,
    )

    assert asyncio.run(process_provider_payments_once(engine, acorn)) is True
    payment = get_provider_payment(engine, payment_id)
    zap = get_provider_zap(engine, payment_id)
    assert payment.status == "DELIVERED"
    assert payment.delivery_event_id == "event-1"
    assert zap.receipt_event_id == "receipt-event-1"
    assert acorn.deposit_calls == []
    assert acorn.delivery_calls[0]["comment"] == "pytest zap"
    engine.dispose()


def test_zap_receipt_failure_does_not_reclassify_ecash_delivery(
    tmp_path, monkeypatch
) -> None:
    engine, payment_id = queued_payment(tmp_path, zap=True)
    acorn = FakeProviderAcorn()
    monkeypatch.setattr(
        provider_module,
        "_request_zap_mint_quote",
        lambda payment, zap, **kwargs: SimpleNamespace(
            quote="quote-zap", invoice="lnbc21-zap"
        ),
    )

    async def fail_publish(acorn, payment, zap):
        raise RuntimeError("relay unavailable")

    monkeypatch.setattr(
        provider_module,
        "_publish_provider_zap_receipt",
        fail_publish,
    )

    asyncio.run(process_provider_payments_once(engine, acorn))
    payment = get_provider_payment(engine, payment_id)
    assert payment.status == "RECEIPT_FAILED"
    assert payment.delivery_event_id == "event-1"
    assert len(acorn.delivery_calls) == 1
    asyncio.run(process_provider_payments_once(engine, acorn))
    assert len(acorn.delivery_calls) == 1
    engine.dispose()


def test_zap_mint_quote_requires_matching_description_hash(
    tmp_path, monkeypatch
) -> None:
    engine, payment_id = queued_payment(tmp_path, zap=True)
    payment = get_provider_payment(engine, payment_id)
    zap = get_provider_zap(engine, payment_id)
    captured: dict = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"quote": "quote-zap", "request": "lnbc21-zap"}

    def fake_post(url, *, json, timeout):
        captured.update({"url": url, "payload": json})
        return Response()

    monkeypatch.setattr(provider_module.httpx, "post", fake_post)
    monkeypatch.setattr(
        provider_module.bolt11,
        "decode",
        lambda invoice: SimpleNamespace(
            description_hash=hashlib.sha256(zap.request_json.encode()).hexdigest()
        ),
    )
    quote = provider_module._request_zap_mint_quote(payment, zap)
    assert quote.quote == "quote-zap"
    assert captured["payload"]["description"] == zap.request_json

    monkeypatch.setattr(
        provider_module.bolt11,
        "decode",
        lambda invoice: SimpleNamespace(description_hash="00" * 32),
    )
    try:
        provider_module._request_zap_mint_quote(payment, zap)
    except RuntimeError as exc:
        assert "does not commit" in str(exc)
    else:
        raise AssertionError("noncompliant zap invoice was accepted")
    engine.dispose()


def test_zap_mint_quote_compatibility_mode_accepts_unbound_invoice(
    tmp_path, monkeypatch
) -> None:
    engine, payment_id = queued_payment(tmp_path, zap=True)
    payment = get_provider_payment(engine, payment_id)
    zap = get_provider_zap(engine, payment_id)
    captured: dict = {}
    warnings: list[str] = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"quote": "quote-compatible", "request": "lnbc21-compatible"}

    def fake_post(url, *, json, timeout):
        captured.update({"url": url, "payload": json})
        return Response()

    monkeypatch.setattr(provider_module.httpx, "post", fake_post)
    monkeypatch.setattr(
        provider_module.logger,
        "warning",
        lambda message, *args: warnings.append(message % args),
    )
    monkeypatch.setattr(
        provider_module.bolt11,
        "decode",
        lambda invoice: SimpleNamespace(description_hash=None),
    )

    quote = provider_module._request_zap_mint_quote(
        payment,
        zap,
        require_description_hash=False,
    )

    assert quote.quote == "quote-compatible"
    assert quote.description_hash_bound is False
    assert "description" not in captured["payload"]
    assert "strict NIP-57 clients may reject" in warnings[0]
    engine.dispose()


def test_zap_receipt_copies_target_tags_and_is_signed() -> None:
    zap = validated_zap_request()
    acorn = SimpleNamespace(
        pubkey_hex=PROVIDER_KEYS.public_key_hex(),
        privkey_hex=PROVIDER_KEYS.private_key_hex(),
    )
    receipt = build_zap_receipt(
        zap_request_json=zap.raw,
        invoice="lnbc21-zap",
        acorn=acorn,
    )
    tags = list(receipt.tags)
    assert receipt.kind == 9735
    assert receipt.content == ""
    assert receipt.is_valid()
    assert ["p", RECIPIENT_KEYS.public_key_hex()] in tags
    assert ["P", SENDER_KEYS.public_key_hex()] in tags
    assert ["bolt11", "lnbc21-zap"] in tags
    assert ["description", zap.raw] in tags
