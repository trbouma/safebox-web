from __future__ import annotations

import asyncio
from dataclasses import replace
from html import unescape
import json
import os
import re
import sqlite3
import time
from types import SimpleNamespace

from cryptography.fernet import Fernet
import pytest


# app.main deliberately refuses to import without an explicit server-held key.
os.environ.setdefault("SAFEBOX_COOKIE_KEY", Fernet.generate_key().decode("ascii"))

from fastapi.testclient import TestClient

from acorn import (
    record_protection_key_from_entropy,
    record_protection_recovery_phrase,
)
import app.main as main_module
import app.security as security_module
from app.config import (
    DEFAULT_SESSION_TTL_HOURS,
    DEFAULT_SESSION_TTL_SECONDS,
    Settings,
)
from app.dependencies import (
    get_acorn,
    get_deposit_acorn,
    get_loaded_acorn,
    get_payment_acorn,
    get_receive_acorn,
    get_session_credentials,
)
from app.main import create_app
from app.security import (
    CsrfProtector,
    DepositQuoteCipher,
    DepositQuoteState,
    InvoicePaymentCipher,
    InvoicePaymentState,
    SECURE_COOKIE_NAME,
    SessionCipher,
    SessionCredentials,
    normalize_bootstrap_relay,
)


TEST_KEY = Fernet.generate_key().decode("ascii")
TEST_SETTINGS = Settings(cookie_key=TEST_KEY, session_ttl_seconds=3600)
TEST_MNEMONIC = (
    "abandon abandon abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon abandon abandon abandon abandon art"
)
TEST_NSEC = "nsec1yddnfntunakhu3v4ll56ujcuk4sxm79v526j05s2qly026ergt6q682r8v"
TEST_NPUB = "npub1"
TEST_RPK = "a5" * 32
TEST_RPK_PHRASE = record_protection_recovery_phrase(TEST_RPK)


def make_https_client() -> TestClient:
    return TestClient(create_app(TEST_SETTINGS), base_url="https://safebox.example")


def valid_csrf_token() -> str:
    return CsrfProtector(TEST_SETTINGS).issue()


def test_ecash_retention_duration_uses_readable_units() -> None:
    assert main_module._humanize_retention(3_600) == "1 hour"
    assert main_module._humanize_retention(7_200) == "2 hours"
    assert main_module._humanize_retention(86_400) == "1 day"
    assert main_module._humanize_retention(604_800) == "1 week"
    assert main_module._humanize_retention(1_209_600) == "2 weeks"
    assert main_module._humanize_retention(2_592_000) == "1 month"


def test_ecash_retention_notice_explains_disabled_expiration() -> None:
    settings = replace(
        TEST_SETTINGS,
        service_acorn_enabled=True,
        service_acorn_gift_wrap_retention_seconds=None,
    )

    notice = main_module._ecash_retention_notice(settings)

    assert "Ecash message retention" in notice
    assert "does not request automatic expiration" in notice
    assert "own policy" in notice


def database_settings(tmp_path) -> Settings:
    return replace(
        TEST_SETTINGS,
        database_url=f"sqlite:///{tmp_path / 'database.db'}",
    )


class FakeLoadedAcorn:
    pubkey_bech32 = (
        "npub10xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqpkge6d"
    )
    home_relay = "wss://relay.example.com"
    home_mint = "https://mint.example.com"

    def __init__(
        self,
        balance: int = 321,
        deposit_paid: bool = True,
        verified_balance: int | None = None,
        verification_status: str = "clean",
        transaction_history: list[dict] | None = None,
        receive_result: dict | None = None,
    ) -> None:
        self.balance = balance
        self.proofs = [object()] if balance else []
        self.deposit_paid = deposit_paid
        self.verified_balance = balance if verified_balance is None else verified_balance
        self.verification_status = verification_status
        self.loaded = False
        self.payments: list[dict] = []
        self.invoice_payments: list[dict] = []
        self.deposit_calls: list[int] = []
        self.quote_checks: list[tuple[str, int]] = []
        self.record_put_calls: list[dict] = []
        self.record_delete_calls: list[dict] = []
        self.record_delete_result = {
            "status": "DELETE_REQUESTED",
            "hidden_on": [self.home_relay],
            "blob_cleanup": None,
            "index_error": None,
        }
        self.history_entries: list[dict] = list(transaction_history or [])
        self.receive_result = receive_result or {
            "queried": 0,
            "accepted_count": 0,
            "accepted_amount": 0,
        }
        self.receive_calls = 0

    async def load_data(self) -> None:
        self.loaded = True

    async def check_proofs(self) -> dict:
        return {
            "status": self.verification_status,
            "recommendation": "Review the proof state.",
            "mint_confirmed_unspent": {
                "amount": self.verified_balance,
                "proof_count": len(self.proofs) if self.verified_balance else 0,
            },
        }

    def get_balance(self) -> int:
        return self.balance

    async def get_user_record_labels(self) -> list[str]:
        return ["Field Notes", "Travel/2026", "A & B"]

    async def get_record_safebox(self, record_name: str):
        class Record:
            type = "generic"
            payload = {
                "label": record_name,
                "unsafe": "<script>alert('no')</script>",
            }

        return Record()

    async def put_record(self, **kwargs):
        self.record_put_calls.append(kwargs)
        return {
            "status": "OK",
            "label": kwargs["record_name"],
            "event_id": "record-event-1",
            "verified": True,
        }

    async def delete_record(self, label: str, **kwargs):
        self.record_delete_calls.append({"label": label, **kwargs})
        return self.record_delete_result

    async def pay_multi(self, amount: int, lnaddress: str, comment: str):
        self.payments.append(
            {"amount": amount, "lnaddress": lnaddress, "comment": comment}
        )
        self.balance -= amount + 1
        return f"Payment of {amount} sats successful!", 1

    async def pay_multi_invoice(self, lninvoice: str, comment: str):
        self.invoice_payments.append(
            {"invoice": lninvoice, "comment": comment}
        )
        self.balance -= 22
        return "Paid 21 sats with fees 1 sats successful!", 1, "hash", "preimage", None

    def deposit(self, amount: int):
        self.deposit_calls.append(amount)
        return SimpleNamespace(
            invoice="lnbc21n1pytestinvoice",
            quote="pytest-deposit-quote",
        )

    async def check_quote(self, quote: str, amount: int):
        self.quote_checks.append((quote, amount))
        if self.deposit_paid:
            self.balance += amount
            return True, "lnbc21n1pytestinvoice"
        return False, None

    async def add_tx_history(self, **entry) -> None:
        self.history_entries.append(entry)

    async def get_tx_history(self) -> list[dict]:
        return self.history_entries

    async def sweep_ecash_transfers(self) -> dict:
        self.receive_calls += 1
        return self.receive_result


class FakeCreatedAcorn:
    instances: list["FakeCreatedAcorn"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.pubkey_bech32 = "npub1newcomponent"
        self.created_seed_phrase: str | None = None
        self.loaded = False
        self.__class__.instances.append(self)

    async def create_instance(self, seed_phrase: str) -> str:
        self.created_seed_phrase = seed_phrase
        return TEST_NSEC

    async def load_data(self) -> None:
        self.loaded = True


class FakeBlobAcorn(FakeLoadedAcorn):
    def __init__(
        self,
        *,
        existing_labels: set[str] | None = None,
        downloaded_type: str | None = "text/plain",
        downloaded_data: bytes | None = b"private blob contents",
        record_lookup_error: ValueError | None = None,
        blob_type: str | None = "text/plain",
        orig_sha256: str | None = "1ea23f2b" + "0" * 56,
    ) -> None:
        super().__init__()
        self.existing_labels = set(existing_labels or set())
        self.downloaded_type = downloaded_type
        self.downloaded_data = downloaded_data
        self.record_lookup_error = record_lookup_error
        self.blob_type = blob_type
        self.orig_sha256 = orig_sha256
        self.blob_reads: list[str] = []

    async def get_record_safebox(self, record_name: str):
        if self.record_lookup_error is not None:
            raise self.record_lookup_error
        if record_name not in self.existing_labels:
            raise ValueError("record not found")
        return SimpleNamespace(
            type="blob",
            payload={"filename": "notes.txt", "description": "Private notes"},
            blobref="https://blossom.example/encrypted-sha256",
            blobtype=self.blob_type,
            origsha256=self.orig_sha256,
        )

    async def get_user_record_labels(self) -> list[str]:
        return sorted(self.existing_labels)

    async def get_record_blobdata(self, record_name: str):
        self.blob_reads.append(record_name)
        return self.downloaded_type, self.downloaded_data


def test_settings_load_cookie_key_from_working_directory_env_file(
    tmp_path, monkeypatch
) -> None:
    env_key = Fernet.generate_key().decode("ascii")
    (tmp_path / ".env").write_text(
        f"SAFEBOX_COOKIE_KEY={env_key}\nSAFEBOX_SESSION_TTL_HOURS=2\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SAFEBOX_COOKIE_KEY", raising=False)
    monkeypatch.delenv("SAFEBOX_SESSION_TTL_HOURS", raising=False)
    monkeypatch.delenv("SAFEBOX_SESSION_TTL_SECONDS", raising=False)

    settings = Settings.from_env()

    assert settings.cookie_key == env_key
    assert settings.session_ttl_seconds == 7200


def test_default_session_lifetime_is_30_days() -> None:
    settings = Settings(cookie_key=TEST_KEY)

    assert DEFAULT_SESSION_TTL_HOURS == 720
    assert DEFAULT_SESSION_TTL_SECONDS == 2_592_000
    assert settings.session_ttl_seconds == DEFAULT_SESSION_TTL_SECONDS


def test_settings_load_comma_delimited_ws_relay_allowlist(
    tmp_path, monkeypatch
) -> None:
    env_key = Fernet.generate_key().decode("ascii")
    (tmp_path / ".env").write_text(
        f"SAFEBOX_COOKIE_KEY={env_key}\n"
        "SAFEBOX_ALLOWED_WS_RELAYS=ws://localhost:8735, ws://beelink:7777\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SAFEBOX_COOKIE_KEY", raising=False)
    monkeypatch.delenv("SAFEBOX_ALLOWED_WS_RELAYS", raising=False)

    settings = Settings.from_env()

    assert settings.allowed_ws_relays == (
        "ws://localhost:8735",
        "ws://beelink:7777",
    )


def test_ws_relay_requires_exact_allowlist_entry() -> None:
    with pytest.raises(ValueError, match="SAFEBOX_ALLOWED_WS_RELAYS"):
        normalize_bootstrap_relay("ws://localhost:8735")

    assert normalize_bootstrap_relay(
        "ws://LOCALHOST:8735",
        ("ws://localhost:8735",),
    ) == "ws://localhost:8735"


def test_ws_relay_allowlist_requires_explicit_port() -> None:
    with pytest.raises(ValueError, match="explicit port"):
        normalize_bootstrap_relay(
            "ws://localhost",
            ("ws://localhost",),
        )


def test_login_accepts_exactly_allowlisted_ws_relay() -> None:
    settings = replace(
        TEST_SETTINGS,
        allowed_ws_relays=("ws://localhost:8735",),
    )
    client = TestClient(
        create_app(settings),
        base_url="https://safebox.example",
    )

    response = client.post(
        "/login",
        data={
            "csrf_token": CsrfProtector(settings).issue(),
            "secret_type": "nsec",
            "secret": TEST_NSEC,
            "bootstrap_relay": "ws://LOCALHOST:8735",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    token = client.cookies.get(SECURE_COOKIE_NAME)
    assert token is not None
    credentials = SessionCipher(settings).decode(token)
    assert credentials.bootstrap_relay == "ws://localhost:8735"


def test_legacy_session_lifetime_seconds_remains_supported(
    tmp_path, monkeypatch
) -> None:
    env_key = Fernet.generate_key().decode("ascii")
    (tmp_path / ".env").write_text(
        f"SAFEBOX_COOKIE_KEY={env_key}\nSAFEBOX_SESSION_TTL_SECONDS=7200\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SAFEBOX_COOKIE_KEY", raising=False)
    monkeypatch.delenv("SAFEBOX_SESSION_TTL_HOURS", raising=False)
    monkeypatch.delenv("SAFEBOX_SESSION_TTL_SECONDS", raising=False)

    settings = Settings.from_env()

    assert settings.session_ttl_seconds == 7200


def test_non_loopback_http_is_rejected() -> None:
    client = TestClient(create_app(TEST_SETTINGS), base_url="http://safebox.example")

    response = client.get("/health")

    assert response.status_code == 400
    assert "HTTPS is required" in response.json()["detail"]


def test_direct_127001_http_is_allowed() -> None:
    client = TestClient(
        create_app(TEST_SETTINGS),
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50123),
    )

    response = client.get("/health")

    assert response.status_code == 200


def test_page_displays_acorn_safebox_relationship_visual() -> None:
    response = make_https_client().get("/")

    assert response.status_code == 200
    assert 'aria-label="Acorn connected with Safebox"' in response.text
    assert 'role="img" aria-label="Acorn"' in response.text
    assert 'role="img" aria-label="Safebox"' in response.text
    assert "User-controlled component" not in response.text
    assert "User-controlled session" not in response.text
    assert "Web service surface" not in response.text


def test_root_redirects_valid_existing_session_to_wallet() -> None:
    client = make_https_client()
    client.cookies.set(
        SECURE_COOKIE_NAME,
        SessionCipher(TEST_SETTINGS).encode(
            SessionCredentials(
                nsec=TEST_NSEC,
                bootstrap_relay="wss://relay.example.com",
            )
        ),
        domain="safebox.example",
        path="/",
    )

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/wallet"


def test_root_keeps_landing_page_for_invalid_session() -> None:
    client = make_https_client()
    client.cookies.set(
        SECURE_COOKIE_NAME,
        "invalid-session",
        domain="safebox.example",
        path="/",
    )

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 200
    assert "Connect an Acorn" in response.text


def test_progress_script_is_served_from_same_origin() -> None:
    response = make_https_client().get("/static/forms.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert 'form.setAttribute("aria-busy", "true")' in response.text
    assert "button.disabled = true" in response.text
    assert "navigator.clipboard.writeText(target.value)" in response.text
    assert 'document.execCommand("copy")' in response.text


def test_theme_defaults_to_dark_and_script_is_served() -> None:
    page = make_https_client().get("/")
    script = make_https_client().get("/static/theme.js")

    assert page.status_code == 200
    assert '<html lang="en" data-theme="dark">' in page.text
    assert 'data-theme-toggle aria-label="Switch colour theme"' in page.text
    assert 'src="/static/theme.js"' in page.text
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert 'theme === "dark" ? "light" : "dark"' in script.text
    assert "safebox_theme=" in script.text


def test_pages_include_mobile_layout_safeguards() -> None:
    client = make_https_client()
    response = client.get("/")
    stylesheet = client.get("/static/styles.css")

    assert response.status_code == 200
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in response.text
    assert 'href="/static/styles.css"' in response.text
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert "-webkit-text-size-adjust: 100%" in stylesheet.text
    assert "min-height: 2.75rem" in stylesheet.text
    assert "@media (max-width: 36rem)" in stylesheet.text
    assert "@media (max-width: 24rem)" in stylesheet.text
    assert ".transaction-details { grid-template-columns: 1fr; }" in stylesheet.text
    assert ".safekeeping-message" in stylesheet.text


def test_wallet_navigation_links_are_presented_as_action_buttons(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    app.dependency_overrides[get_loaded_acorn] = lambda: FakeLoadedAcorn()
    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/wallet")

    assert response.status_code == 200
    assert '<nav class="wallet-actions" aria-label="Wallet actions">' in response.text
    assert '<h1 class="wallet-headline">Acorn is Connected</h1>' in response.text
    assert '<a href="/deposit">Deposit funds</a>' in response.text
    assert '<a href="/records">Manage private records</a>' in response.text
    assert '<section class="wallet-balance"' in response.text
    assert "321 <span>sats</span>" in response.text
    assert response.text.index("wallet-balance") < response.text.index("wallet-actions")
    assert response.text.index("wallet-actions") < response.text.index("Component public key")


def test_wallet_shows_collapsible_silent_payment_address_and_qr(tmp_path) -> None:
    client = TestClient(
        create_app(database_settings(tmp_path)),
        base_url="https://safebox.example",
    )
    fake = FakeLoadedAcorn()
    client.app.dependency_overrides[get_loaded_acorn] = lambda: fake

    with client:
        response = client.get("/wallet")

    assert response.status_code == 200
    assert (
        '<details class="receive-payment-card silent-payment-card">'
        in response.text
    )
    assert "Receive Silent Payment" in response.text
    assert "sp1qqt0uh8dlt9yp" in response.text
    assert 'aria-label="Silent Payment address QR code"' in response.text
    assert '<div class="silent-payment-qr"' in response.text
    assert "<svg" in response.text
    assert 'id="qr-background"' in response.text
    assert 'fill="#ffffff"' in response.text
    assert 'id="qr-path" fill="#000000"' in response.text
    assert 'action="/bitcoin/silent-payment/detect"' in response.text
    assert "Check Payment" in response.text


def test_silent_payment_detection_shows_available_sweep_form(
    tmp_path, monkeypatch
) -> None:
    txid = "ab" * 32
    settings = database_settings(tmp_path)
    app = create_app(settings)
    app.dependency_overrides[get_session_credentials] = lambda: SessionCredentials(
        nsec=TEST_NSEC,
        bootstrap_relay="wss://relay.example.com",
    )

    def fake_detect(**kwargs):
        assert kwargs["nsec"] == TEST_NSEC
        assert kwargs["txid"] == txid
        return {
            "txid": txid,
            "silent_payment_address": "sp1example",
            "matches": [
                {
                    "txid": txid,
                    "vout": 1,
                    "value": 21_000,
                    "source_address": "bc1psource",
                    "confirmed": True,
                    "block_height": 900_000,
                    "availability": "available",
                }
            ],
        }

    monkeypatch.setattr(main_module, "detect_silent_payment_receipts", fake_detect)
    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.post(
            "/bitcoin/silent-payment/detect",
            data={"csrf_token": valid_csrf_token(), "txid": txid},
        )

    assert response.status_code == 200
    assert "21,000 sats" in response.text
    assert "Confirmed and currently reported as unspent" in response.text
    assert 'action="/bitcoin/silent-payment/sweep/preview"' in response.text
    assert 'name="destination_address"' in response.text
    assert TEST_NSEC not in response.text


def test_silent_payment_sweep_requires_review_then_confirmation(
    tmp_path, monkeypatch
) -> None:
    txid = "ab" * 32
    signed_txid = "cd" * 32
    settings = database_settings(tmp_path)
    app = create_app(settings)
    app.dependency_overrides[get_session_credentials] = lambda: SessionCredentials(
        nsec=TEST_NSEC,
        bootstrap_relay="wss://relay.example.com",
    )
    preview = {
        "txid": signed_txid,
        "receipt_txid": txid,
        "vout": 1,
        "source_address": "bc1psource",
        "destination_address": "bc1pdestination",
        "matched_value": 21_000,
        "amount_sats": 20_800,
        "fee_sats": 200,
        "fee_rate": 2.0,
        "vsize": 100,
    }
    calls = []

    def fake_preview(**kwargs):
        calls.append(("preview", kwargs))
        return preview

    def fake_broadcast(**kwargs):
        calls.append(("broadcast", kwargs))
        return {**preview, "broadcast_txid": signed_txid}

    monkeypatch.setattr(
        main_module,
        "create_silent_payment_sweep_preview",
        fake_preview,
    )
    monkeypatch.setattr(
        main_module,
        "broadcast_silent_payment_sweep",
        fake_broadcast,
    )

    with TestClient(app, base_url="https://safebox.example") as client:
        review = client.post(
            "/bitcoin/silent-payment/sweep/preview",
            data={
                "csrf_token": valid_csrf_token(),
                "txid": txid,
                "vout": "1",
                "destination_address": "bc1pdestination",
            },
        )
        unconfirmed = client.post(
            "/bitcoin/silent-payment/sweep",
            data={
                "csrf_token": valid_csrf_token(),
                "txid": txid,
                "vout": "1",
                "destination_address": "bc1pdestination",
            },
        )
        broadcast = client.post(
            "/bitcoin/silent-payment/sweep",
            data={
                "csrf_token": valid_csrf_token(),
                "txid": txid,
                "vout": "1",
                "destination_address": "bc1pdestination",
                "confirmed": "yes",
            },
        )

    assert review.status_code == 200
    assert "20,800 sats" in review.text
    assert "200 sats at 2.0 sat/vB" in review.text
    assert "Receive Funds" in review.text
    assert "signed-transaction" not in review.text
    assert unconfirmed.status_code == 400
    assert "Confirm that the transaction is irreversible" in unconfirmed.text
    assert broadcast.status_code == 200
    assert "received successfully" in broadcast.text
    assert signed_txid in broadcast.text
    assert [name for name, _ in calls] == ["preview", "broadcast"]


def test_pages_use_external_jinja_layout_assets() -> None:
    response = make_https_client().get("/")

    assert response.status_code == 200
    assert response.text.count("<!doctype html>") == 1
    assert "<style>" not in response.text
    assert "style-src 'self'" in response.headers["content-security-policy"]


def test_login_page_links_to_new_acorn_creation() -> None:
    response = make_https_client().get("/login")

    assert response.status_code == 200
    assert response.text.index('value="mnemonic"') < response.text.index('value="nsec"')
    assert 'href="/create"' in response.text
    assert "Create a new Acorn" in response.text
    assert "Restore protected record access" in response.text
    assert 'name="record_protection_recovery"' in response.text
    assert 'name="record_protection_entropy"' in response.text


def test_create_form_displays_default_relay_and_mint() -> None:
    response = make_https_client().get("/create")

    assert response.status_code == 200
    assert 'name="home_relay"' in response.text
    assert TEST_SETTINGS.default_bootstrap_relay in response.text
    assert 'name="home_mint"' in response.text
    assert TEST_SETTINGS.default_home_mint in response.text
    assert 'name="mnemonic_words"' in response.text
    assert '<option value="12" selected>' in response.text
    assert '<option value="24">' in response.text
    assert "Bring your own entropy" in response.text
    assert 'name="entropy_hex" type="password"' in response.text
    assert 'name="entropy_confirmation" type="password"' in response.text
    assert 'pattern="[0-9A-Fa-f]{64}"' in response.text
    assert "Bring your own record-protection entropy" in response.text
    assert 'name="record_protection_entropy_hex" type="password"' in response.text
    assert 'name="record_protection_entropy_confirmation" type="password"' in response.text
    assert 'name="confirmed"' in response.text


def test_create_acorn_initializes_relay_state_and_starts_session(monkeypatch) -> None:
    FakeCreatedAcorn.instances.clear()
    generated_strengths = []
    monkeypatch.setattr(main_module, "Acorn", FakeCreatedAcorn)
    monkeypatch.setattr(
        main_module,
        "generate_seed_phrase_and_nsec",
        lambda strength=128: (
            generated_strengths.append(strength) or TEST_MNEMONIC,
            TEST_NSEC,
        ),
    )
    monkeypatch.setattr(
        main_module, "generate_record_protection_key", lambda: TEST_RPK
    )
    client = make_https_client()

    response = client.post(
        "/create",
        data={
            "csrf_token": valid_csrf_token(),
            "home_relay": "relay.example.com",
            "home_mint": "mint.example.com/",
            "mnemonic_words": "24",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 201
    assert "New Acorn created" in response.text
    assert TEST_MNEMONIC in response.text
    assert TEST_NSEC in response.text
    assert TEST_RPK_PHRASE in response.text
    assert "Safebox Acorn safekeeping message" in response.text
    assert "Safebox Acorn mnemonic:" in response.text
    assert "Protected record mnemonic:" in response.text
    assert "Bootstrap relay: wss://relay.example.com" in response.text
    assert 'data-copy-target="safekeeping-message"' in response.text
    assert "wss://relay.example.com" in response.text
    assert "https://mint.example.com" in response.text
    created = FakeCreatedAcorn.instances[0]
    assert created.kwargs == {
        "nsec": TEST_NSEC,
        "home_relay": "wss://relay.example.com",
        "relays": ["wss://relay.example.com"],
        "mints": ["https://mint.example.com"],
    }
    assert created.created_seed_phrase == TEST_MNEMONIC
    assert created.loaded is True
    assert generated_strengths == [256]

    token = client.cookies.get(SECURE_COOKIE_NAME)
    assert token is not None
    assert TEST_NSEC not in token
    assert TEST_MNEMONIC not in token
    assert TEST_RPK not in token
    credentials = SessionCipher(TEST_SETTINGS).decode(token)
    assert credentials.nsec == TEST_NSEC
    assert credentials.bootstrap_relay == "wss://relay.example.com"
    assert credentials.record_protection_key == TEST_RPK
    assert credentials.record_protection_backup_confirmed is False


def test_record_protection_recovery_display_requires_confirmation_and_marks_backup() -> None:
    client = make_https_client()
    credentials = SessionCredentials(
        nsec=TEST_NSEC,
        bootstrap_relay="wss://relay.example.com",
        record_protection_key=TEST_RPK,
    )
    client.cookies.set(
        SECURE_COOKIE_NAME,
        SessionCipher(TEST_SETTINGS).encode(credentials),
        domain="safebox.example",
        path="/",
    )

    warning = client.get("/record-protection/recovery")
    assert warning.status_code == 200
    assert "Sensitive recovery material is about to be displayed" in warning.text
    assert TEST_RPK_PHRASE not in warning.text
    assert warning.headers["cache-control"] == "no-store"

    rejected = client.post(
        "/record-protection/recovery",
        data={"csrf_token": valid_csrf_token()},
    )
    assert rejected.status_code == 403
    assert TEST_RPK_PHRASE not in rejected.text

    displayed = client.post(
        "/record-protection/recovery",
        data={
            "csrf_token": valid_csrf_token(),
            "confirmed": "yes",
        },
    )
    assert displayed.status_code == 200
    assert TEST_RPK_PHRASE in displayed.text
    assert TEST_RPK not in displayed.text
    assert displayed.headers["cache-control"] == "no-store"

    confirmed = client.post(
        "/record-protection/confirm",
        data={
            "csrf_token": valid_csrf_token(),
            "confirmed": "yes",
        },
        follow_redirects=False,
    )
    assert confirmed.status_code == 303
    updated_token = client.cookies.get(SECURE_COOKIE_NAME)
    updated = SessionCipher(TEST_SETTINGS).decode(updated_token)
    assert updated.record_protection_key == TEST_RPK
    assert updated.record_protection_backup_confirmed is True


def test_login_restores_record_protection_key_from_recovery_phrase() -> None:
    client = make_https_client()

    response = client.post(
        "/login",
        data={
            "csrf_token": valid_csrf_token(),
            "secret_type": "nsec",
            "secret": TEST_NSEC,
            "bootstrap_relay": "relay.example.com",
            "record_protection_recovery": TEST_RPK_PHRASE,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    token = client.cookies.get(SECURE_COOKIE_NAME)
    assert token is not None
    assert TEST_RPK_PHRASE not in token
    assert TEST_RPK not in token
    credentials = SessionCipher(TEST_SETTINGS).decode(token)
    assert credentials.record_protection_key == TEST_RPK
    assert credentials.record_protection_backup_confirmed is True


def test_login_restores_record_protection_key_from_external_entropy() -> None:
    entropy = "17" * 32
    expected_rpk = record_protection_key_from_entropy(entropy)
    client = make_https_client()

    response = client.post(
        "/login",
        data={
            "csrf_token": valid_csrf_token(),
            "secret_type": "nsec",
            "secret": TEST_NSEC,
            "bootstrap_relay": "relay.example.com",
            "record_protection_entropy": entropy,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    token = client.cookies.get(SECURE_COOKIE_NAME)
    assert token is not None
    assert entropy not in token
    assert expected_rpk not in token
    credentials = SessionCipher(TEST_SETTINGS).decode(token)
    assert credentials.record_protection_key == expected_rpk
    assert credentials.record_protection_backup_confirmed is True


def test_login_rejects_invalid_record_protection_phrase_without_echoing_it() -> None:
    invalid_phrase = "abandon " * 23 + "abandon"
    client = make_https_client()

    response = client.post(
        "/login",
        data={
            "csrf_token": valid_csrf_token(),
            "secret_type": "nsec",
            "secret": TEST_NSEC,
            "bootstrap_relay": "relay.example.com",
            "record_protection_recovery": invalid_phrase,
        },
    )

    assert response.status_code == 400
    assert "Protected record mnemonic is not valid" in response.text
    assert invalid_phrase not in response.text
    assert SECURE_COOKIE_NAME not in response.headers.get("set-cookie", "")


def test_create_acorn_uses_external_record_protection_entropy(monkeypatch) -> None:
    FakeCreatedAcorn.instances.clear()
    rpk_entropy = "03" * 32
    calls = []
    monkeypatch.setattr(main_module, "Acorn", FakeCreatedAcorn)
    monkeypatch.setattr(
        main_module,
        "generate_seed_phrase_and_nsec",
        lambda strength=128: (TEST_MNEMONIC, TEST_NSEC),
    )
    monkeypatch.setattr(
        main_module,
        "record_protection_key_from_entropy",
        lambda supplied: calls.append(supplied) or TEST_RPK,
    )
    monkeypatch.setattr(
        main_module,
        "generate_record_protection_key",
        lambda: (_ for _ in ()).throw(
            AssertionError("random RPK generation must not be used")
        ),
    )
    client = make_https_client()

    response = client.post(
        "/create",
        data={
            "csrf_token": valid_csrf_token(),
            "home_relay": "relay.example.com",
            "home_mint": "mint.example.com",
            "record_protection_entropy_hex": rpk_entropy,
            "record_protection_entropy_confirmation": rpk_entropy,
            "confirmed": "yes",
        },
    )

    assert response.status_code == 201
    assert calls == [rpk_entropy]
    assert rpk_entropy not in response.text
    token = client.cookies.get(SECURE_COOKIE_NAME)
    assert token is not None
    credentials = SessionCipher(TEST_SETTINGS).decode(token)
    assert credentials.record_protection_key == TEST_RPK


def test_create_acorn_rejects_mismatched_record_protection_entropy() -> None:
    FakeCreatedAcorn.instances.clear()
    entropy = "04" * 32
    client = make_https_client()

    response = client.post(
        "/create",
        data={
            "csrf_token": valid_csrf_token(),
            "home_relay": "relay.example.com",
            "home_mint": "mint.example.com",
            "record_protection_entropy_hex": entropy,
            "record_protection_entropy_confirmation": "05" * 32,
            "confirmed": "yes",
        },
    )

    assert response.status_code == 400
    assert "record-protection entropy values do not match" in response.text
    assert entropy not in response.text
    assert FakeCreatedAcorn.instances == []


def test_create_acorn_rejects_reused_wallet_and_record_protection_entropy() -> None:
    entropy = "06" * 32
    client = make_https_client()

    response = client.post(
        "/create",
        data={
            "csrf_token": valid_csrf_token(),
            "home_relay": "relay.example.com",
            "home_mint": "mint.example.com",
            "entropy_hex": entropy,
            "entropy_confirmation": entropy,
            "record_protection_entropy_hex": entropy.upper(),
            "record_protection_entropy_confirmation": entropy.upper(),
            "confirmed": "yes",
        },
    )

    assert response.status_code == 400
    assert "must be independent" in response.text
    assert entropy not in response.text


def test_create_acorn_requires_confirmation_before_generating(monkeypatch) -> None:
    generated = False

    def must_not_generate():
        nonlocal generated
        generated = True
        return TEST_MNEMONIC, TEST_NSEC

    monkeypatch.setattr(main_module, "generate_seed_phrase_and_nsec", must_not_generate)
    client = make_https_client()

    response = client.post(
        "/create",
        data={
            "csrf_token": valid_csrf_token(),
            "home_relay": "relay.example.com",
            "home_mint": "mint.example.com",
        },
    )

    assert response.status_code == 400
    assert "Explicit confirmation is required" in response.text
    assert generated is False
    assert SECURE_COOKIE_NAME not in response.headers.get("set-cookie", "")


def test_create_acorn_uses_12_words_by_default(monkeypatch) -> None:
    FakeCreatedAcorn.instances.clear()
    generated_strengths = []
    monkeypatch.setattr(main_module, "Acorn", FakeCreatedAcorn)

    def generate(strength=128):
        generated_strengths.append(strength)
        return TEST_MNEMONIC, TEST_NSEC

    monkeypatch.setattr(main_module, "generate_seed_phrase_and_nsec", generate)
    client = make_https_client()

    response = client.post(
        "/create",
        data={
            "csrf_token": valid_csrf_token(),
            "home_relay": "relay.example.com",
            "home_mint": "mint.example.com",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 201
    assert generated_strengths == [128]


def test_create_acorn_uses_confirmed_external_entropy(monkeypatch) -> None:
    FakeCreatedAcorn.instances.clear()
    entropy_hex = "02" * 32
    entropy_calls = []
    monkeypatch.setattr(main_module, "Acorn", FakeCreatedAcorn)
    monkeypatch.setattr(
        main_module,
        "seed_phrase_and_nsec_from_entropy",
        lambda supplied: (
            entropy_calls.append(supplied) or TEST_MNEMONIC,
            TEST_NSEC,
        ),
    )
    monkeypatch.setattr(
        main_module,
        "generate_seed_phrase_and_nsec",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("random generation must not be used")
        ),
    )
    client = make_https_client()

    response = client.post(
        "/create",
        data={
            "csrf_token": valid_csrf_token(),
            "home_relay": "relay.example.com",
            "home_mint": "mint.example.com",
            "mnemonic_words": "12",
            "entropy_hex": entropy_hex,
            "entropy_confirmation": entropy_hex,
            "confirmed": "yes",
        },
    )

    assert response.status_code == 201
    assert entropy_calls == [entropy_hex]
    assert TEST_MNEMONIC in response.text
    assert entropy_hex not in response.text
    assert FakeCreatedAcorn.instances[0].created_seed_phrase == TEST_MNEMONIC


def test_create_acorn_rejects_mismatched_external_entropy(monkeypatch) -> None:
    entropy_hex = "02" * 32
    monkeypatch.setattr(
        main_module,
        "seed_phrase_and_nsec_from_entropy",
        lambda supplied: (_ for _ in ()).throw(
            AssertionError("mismatched entropy must not be derived")
        ),
    )
    client = make_https_client()

    response = client.post(
        "/create",
        data={
            "csrf_token": valid_csrf_token(),
            "home_relay": "relay.example.com",
            "home_mint": "mint.example.com",
            "entropy_hex": entropy_hex,
            "entropy_confirmation": "03" * 32,
            "confirmed": "yes",
        },
    )

    assert response.status_code == 400
    assert "external entropy values do not match" in response.text
    assert entropy_hex not in response.text


def test_create_acorn_rejects_invalid_external_entropy() -> None:
    invalid_entropy = "not-hex"
    client = make_https_client()

    response = client.post(
        "/create",
        data={
            "csrf_token": valid_csrf_token(),
            "home_relay": "relay.example.com",
            "home_mint": "mint.example.com",
            "entropy_hex": invalid_entropy,
            "entropy_confirmation": invalid_entropy,
            "confirmed": "yes",
        },
    )

    assert response.status_code == 400
    assert "exactly 64 hexadecimal characters" in response.text
    assert invalid_entropy not in response.text


def test_create_acorn_rejects_invalid_mnemonic_length(monkeypatch) -> None:
    generated = False

    def must_not_generate(strength=128):
        nonlocal generated
        generated = True
        return TEST_MNEMONIC, TEST_NSEC

    monkeypatch.setattr(main_module, "generate_seed_phrase_and_nsec", must_not_generate)
    client = make_https_client()

    response = client.post(
        "/create",
        data={
            "csrf_token": valid_csrf_token(),
            "home_relay": "relay.example.com",
            "home_mint": "mint.example.com",
            "mnemonic_words": "18",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 400
    assert "Choose a 12- or 24-word Safebox Acorn mnemonic" in response.text
    assert generated is False


def test_create_acorn_rejects_insecure_remote_mint() -> None:
    client = make_https_client()

    response = client.post(
        "/create",
        data={
            "csrf_token": valid_csrf_token(),
            "home_relay": "relay.example.com",
            "home_mint": "http://mint.example.com",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 400
    assert "allowed only on loopback" in response.text
    assert SECURE_COOKIE_NAME not in response.headers.get("set-cookie", "")


def test_loaded_acorn_dependency_loads_request_scoped_state() -> None:
    acorn = FakeLoadedAcorn()

    result = asyncio.run(get_loaded_acorn(acorn, TEST_SETTINGS))

    assert result is acorn
    assert acorn.loaded is True


def test_wallet_page_displays_loaded_balance(tmp_path) -> None:
    settings = database_settings(tmp_path)
    app = create_app(settings)
    acorn = FakeLoadedAcorn(balance=12_345)
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/wallet")

    assert response.status_code == 200
    assert "12,345 sats" in response.text
    assert "Relay-visible proof total" in response.text
    assert "Mint-confirmed spendable balance" in response.text
    assert "wss://relay.example.com" in response.text
    assert "not stored" in response.text
    assert "NIP-05 address" not in response.text


def test_wallet_warns_when_relay_total_exceeds_mint_confirmed_balance(tmp_path) -> None:
    settings = database_settings(tmp_path)
    app = create_app(settings)
    acorn = FakeLoadedAcorn(
        balance=33_926,
        verified_balance=52,
        verification_status="repair-recommended",
    )
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/wallet")

    assert response.status_code == 200
    assert "Relay-visible proof total: <strong>33,926 sats" in response.text
    assert "Mint-confirmed spendable balance: <strong>52 sats" in response.text
    assert "33,874 sats not confirmed as spendable" in response.text
    assert "Do not make a payment" in response.text


def test_startup_migrates_a_new_sqlite_database(tmp_path) -> None:
    settings = database_settings(tmp_path)
    database_path = tmp_path / "database.db"

    with TestClient(
        create_app(settings), base_url="https://safebox.example"
    ) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/handle").status_code == 401

    assert database_path.is_file()
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        handle_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(claimed_handle)")
        }
        payment_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(provider_payment)")
        }
        provider_identity_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(provider_identity)")
        }
        provider_zap_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(provider_zap)")
        }
    assert {
        "alembic_version",
        "claimed_handle",
        "provider_identity",
        "provider_payment",
        "provider_zap",
    }.issubset(tables)
    assert revision == ("20260808_0003",)
    assert handle_columns == {"id", "claimed_handle", "npub", "home_relay"}
    assert {
        "id",
        "payment_id",
        "claimed_handle",
        "recipient_npub",
        "recipient_relay",
        "amount_msat",
        "amount_sat",
        "comment",
        "lnurl_metadata",
        "status",
        "mint",
        "mint_quote",
        "invoice",
        "delivery_event_id",
        "error",
        "attempts",
        "created_at",
        "updated_at",
        "next_check_at",
    } == payment_columns
    assert {"name", "nostr_pubkey", "updated_at"} == provider_identity_columns
    assert {
        "id",
        "payment_id",
        "request_event_id",
        "request_json",
        "receipt_relays_json",
        "receipt_event_id",
        "receipt_json",
        "receipt_error",
    } == provider_zap_columns


def test_web_lifespan_does_not_own_the_service_acorn(tmp_path) -> None:
    settings = replace(
        database_settings(tmp_path),
        service_acorn_enabled=True,
        service_acorn_state_file=str(tmp_path / "service-acorn.json"),
    )
    app = create_app(settings)

    with TestClient(app, base_url="https://safebox.example") as client:
        assert client.get("/health").status_code == 200
        assert not hasattr(app.state, "service_acorn_runtime")
        assert not hasattr(app.state, "service_acorn")

    assert not (tmp_path / "service-acorn.json").exists()


def test_connected_acorn_can_claim_and_resolve_a_nip05_handle(tmp_path) -> None:
    settings = database_settings(tmp_path)
    app = create_app(settings)
    acorn = main_module.Acorn(
        nsec=TEST_NSEC,
        home_relay="wss://relay.one.example",
        relays=["wss://relay.one.example"],
    )
    app.dependency_overrides[get_acorn] = lambda: acorn
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        form = client.get("/handle")
        assert form.status_code == 200
        assert "Claim a NIP-05 handle" in form.text
        assert "alice@safebox.example" not in form.text

        claim = client.post(
            "/handle",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "claimed_handle": "Alice",
            },
            follow_redirects=False,
        )
        assert claim.status_code == 303
        assert claim.headers["location"] == "/handle"

        claimed = client.get("/handle")
        assert claimed.status_code == 200
        assert "alice@safebox.example" in claimed.text
        assert acorn.pubkey_bech32 in claimed.text

        wallet_page = client.get("/wallet")
        assert wallet_page.status_code == 200
        assert "NIP-05 address" in wallet_page.text
        assert "alice@safebox.example" in wallet_page.text
        assert '<a href="/handle">alice@safebox.example</a>' in wallet_page.text

        resolution = client.get(
            "/.well-known/nostr.json", params={"name": "alice"}
        )
        assert resolution.status_code == 200
        assert resolution.headers["access-control-allow-origin"] == "*"
        assert resolution.json() == {
            "names": {"alice": acorn.pubkey_hex},
            "relays": {acorn.pubkey_hex: ["wss://relay.one.example"]},
        }

        # The same component can idempotently refresh its current relay.
        acorn.home_relay = "wss://relay.two.example"
        refreshed = client.post(
            "/handle",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "claimed_handle": "alice",
            },
            follow_redirects=False,
        )
        assert refreshed.status_code == 303
        assert client.get(
            "/.well-known/nostr.json", params={"name": "alice"}
        ).json()["relays"] == {
            acorn.pubkey_hex: ["wss://relay.two.example"]
        }

        unconfirmed_remove = client.post(
            "/handle/remove",
            data={"csrf_token": CsrfProtector(settings).issue()},
        )
        assert unconfirmed_remove.status_code == 400
        assert "Explicit removal confirmation is required" in unconfirmed_remove.text

        removed = client.post(
            "/handle/remove",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "confirmed": "yes",
            },
            follow_redirects=False,
        )
        assert removed.status_code == 303
        assert removed.headers["location"] == "/handle"
        assert client.get(
            "/.well-known/nostr.json", params={"name": "alice"}
        ).status_code == 404
        assert "Claim a NIP-05 handle" in client.get("/handle").text
        assert "NIP-05 address" not in client.get("/wallet").text


def test_wallet_shows_lnurl_qr_for_enabled_claimed_lightning_address(
    tmp_path,
) -> None:
    settings = replace(database_settings(tmp_path), service_acorn_enabled=True)
    app = create_app(settings)
    acorn = main_module.Acorn(
        nsec=TEST_NSEC,
        home_relay="wss://relay.one.example",
        relays=["wss://relay.one.example"],
    )
    app.dependency_overrides[get_acorn] = lambda: acorn
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        claim = client.post(
            "/handle",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "claimed_handle": "alice",
            },
            follow_redirects=False,
        )
        assert claim.status_code == 303

        wallet_page = client.get("/wallet")

    expected_endpoint = "https://safebox.example/.well-known/lnurlp/alice"
    expected_lnurl = main_module.encode_lnurl(expected_endpoint)
    assert wallet_page.status_code == 200
    assert "Receive Lightning" in wallet_page.text
    assert "alice@safebox.example" in wallet_page.text
    assert '<details class="receive-payment-card lightning-address-card">' in wallet_page.text
    assert 'class="lightning-address-qr"' in wallet_page.text
    assert "Lightning address QR code" in wallet_page.text
    assert expected_lnurl in wallet_page.text
    assert "<svg" in wallet_page.text
    assert 'id="qr-background"' in wallet_page.text
    assert 'fill="#ffffff"' in wallet_page.text
    assert 'id="qr-path" fill="#000000"' in wallet_page.text
    assert 'id="acorn-qr-mark"' in wallet_page.text
    assert "General advisory" in wallet_page.text
    assert "Ecash message retention" in wallet_page.text
    assert "for 1 week after publication" in wallet_page.text
    assert "Relay enforcement and physical deletion can vary" in wallet_page.text
    assert wallet_page.text.index("Receive Lightning") < wallet_page.text.index(
        "Receive Silent Payment"
    )
    assert wallet_page.text.index("Disconnect") < wallet_page.text.index("General advisory")


def test_invoice_qr_is_black_and_white_without_centre_mark() -> None:
    svg = main_module._invoice_svg("lnbc1pytest")

    assert 'id="qr-background"' in svg
    assert 'fill="#ffffff"' in svg
    assert 'id="qr-path" fill="#000000"' in svg
    assert 'id="acorn-qr-mark"' not in svg


def test_wallet_hides_lightning_qr_when_provider_is_disabled(tmp_path) -> None:
    settings = database_settings(tmp_path)
    app = create_app(settings)
    acorn = main_module.Acorn(
        nsec=TEST_NSEC,
        home_relay="wss://relay.one.example",
        relays=["wss://relay.one.example"],
    )
    app.dependency_overrides[get_acorn] = lambda: acorn
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        client.post(
            "/handle",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "claimed_handle": "alice",
            },
        )
        wallet_page = client.get("/wallet")

    assert "NIP-05 address" in wallet_page.text
    assert "Receive Lightning" not in wallet_page.text
    assert '<div class="lightning-address-qr"' not in wallet_page.text


def test_handle_and_component_uniqueness_are_enforced(tmp_path) -> None:
    settings = database_settings(tmp_path)
    app = create_app(settings)
    first = main_module.Acorn(
        nsec=TEST_NSEC,
        home_relay="wss://relay.one.example",
        relays=["wss://relay.one.example"],
    )
    _second_mnemonic, second_nsec = main_module.generate_seed_phrase_and_nsec()
    second = main_module.Acorn(
        nsec=second_nsec,
        home_relay="wss://relay.two.example",
        relays=["wss://relay.two.example"],
    )
    active = {"acorn": first}
    app.dependency_overrides[get_acorn] = lambda: active["acorn"]

    with TestClient(app, base_url="https://safebox.example") as client:
        first_claim = client.post(
            "/handle",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "claimed_handle": "taken",
            },
            follow_redirects=False,
        )
        assert first_claim.status_code == 303

        active["acorn"] = second
        competing_claim = client.post(
            "/handle",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "claimed_handle": "taken",
            },
        )
        assert competing_claim.status_code == 409
        assert "already been claimed" in competing_claim.text

        active["acorn"] = first
        changed_handle = client.post(
            "/handle",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "claimed_handle": "another",
            },
            follow_redirects=False,
        )
        assert changed_handle.status_code == 303
        assert client.get(
            "/.well-known/nostr.json", params={"name": "taken"}
        ).status_code == 404
        changed_resolution = client.get(
            "/.well-known/nostr.json", params={"name": "another"}
        )
        assert changed_resolution.status_code == 200
        assert changed_resolution.json()["names"] == {
            "another": first.pubkey_hex
        }

        # Renaming releases the old handle for another authenticated Acorn.
        active["acorn"] = second
        released_claim = client.post(
            "/handle",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "claimed_handle": "taken",
            },
            follow_redirects=False,
        )
        assert released_claim.status_code == 303

        assert client.get(
            "/.well-known/nostr.json", params={"name": "missing"}
        ).status_code == 404


def test_transaction_history_renders_mobile_friendly_journal_cards() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(
        transaction_history=[
            {
                "create_time": "2026-08-03 18:42:10",
                "tx_type": "C",
                "amount": 21,
                "comment": "safebox web deposit <confirmed>",
                "tendered_amount": 21.0,
                "tendered_currency": "SAT",
                "fees": 0,
                "current_balance": 52,
                "invoice": "invoice-is-not-rendered",
                "preimage": "preimage-is-not-rendered",
            },
            {
                "create_time": "2026-08-03 18:45:00",
                "tx_type": "D",
                "amount": 5,
                "comment": "coffee",
                "tendered_amount": 5.0,
                "tendered_currency": "SAT",
                "fees": 1,
                "current_balance": 46,
            },
        ]
    )
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/transactions")

    assert response.status_code == 200
    assert response.text.index("← Back to wallet") < response.text.index(
        'aria-label="Transaction history"'
    )
    assert 'aria-label="Transaction history"' in response.text
    assert 'class="transaction-card credit"' in response.text
    assert 'class="transaction-card debit"' in response.text
    assert "+21 sats" in response.text
    assert "−5 sats" in response.text
    assert response.text.index("−5 sats") < response.text.index("+21 sats")
    assert "52 sats" in response.text
    assert "safebox web deposit &lt;confirmed&gt;" in response.text
    assert "invoice-is-not-rendered" not in response.text
    assert "preimage-is-not-rendered" not in response.text
    assert 'href="/static/styles.css"' in response.text
    assert 'action="/transactions/receive"' in response.text
    assert 'name="csrf_token"' in response.text
    assert "Check and receive ecash" in response.text
    assert "Receiving ecash…" in response.text


def test_transaction_history_has_an_empty_state() -> None:
    app = create_app(TEST_SETTINGS)
    app.dependency_overrides[get_loaded_acorn] = lambda: FakeLoadedAcorn()
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/transactions")

    assert response.status_code == 200
    assert "No transaction history was found" in response.text


def test_transaction_history_can_receive_incoming_ecash() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(
        receive_result={
            "queried": 1,
            "accepted_count": 1,
            "accepted_amount": 3,
        },
        transaction_history=[
            {
                "create_time": "2026-08-04 12:00:00",
                "tx_type": "C",
                "amount": 3,
                "comment": "ecash transfer received",
                "current_balance": 324,
            }
        ],
    )
    app.dependency_overrides[get_receive_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/transactions/receive",
        data={"csrf_token": valid_csrf_token()},
    )

    assert response.status_code == 200
    assert acorn.receive_calls == 1
    assert "Received 3 sats from 1 incoming ecash transfer(s)." in response.text
    assert "+3 sats" in response.text
    assert "ecash transfer received" in response.text


def test_receive_incoming_ecash_rejects_invalid_csrf() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn()
    app.dependency_overrides[get_receive_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/transactions/receive",
        data={"csrf_token": "invalid"},
    )

    assert response.status_code == 403
    assert acorn.receive_calls == 0


def test_payment_form_displays_balance_and_confirmation() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/pay")

    assert response.status_code == 200
    assert "500 sats" in response.text
    assert 'name="csrf_token"' in response.text
    assert 'name="confirmed"' in response.text
    assert "Payment in progress. Please wait" in response.text
    assert "Sending payment…" in response.text


def test_lightning_address_scanner_is_authenticated_and_self_contained() -> None:
    app = create_app(TEST_SETTINGS)
    app.dependency_overrides[get_session_credentials] = lambda: SessionCredentials(
        nsec=TEST_NSEC,
        bootstrap_relay="wss://relay.example.com",
    )
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/scan/lightning")

    assert response.status_code == 200
    assert 'data-lightning-scanner' in response.text
    assert 'src="/static/lightning-scan.js"' in response.text
    assert 'method="post" action="/scan/lightning"' in response.text
    assert "Camera scanning requires JavaScript" in response.text


def test_scanned_lightning_address_prefills_payment_review() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/scan/lightning",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_payment": "LIGHTNING:alice@example.com",
        },
    )

    assert response.status_code == 200
    assert "Pay a Lightning address" in response.text
    assert 'value="alice@example.com"' in response.text
    assert acorn.payments == []


def test_scanned_non_lightning_qr_is_rejected_without_payment() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/scan/lightning",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_payment": "https://example.com/not-a-lightning-address",
        },
    )

    assert response.status_code == 400
    assert "does not contain a supported Lightning address or fixed-amount" in response.text
    assert acorn.payments == []


@pytest.mark.parametrize(
    "payload",
    (
        "https://alice@example.com",
        "alice@example",
        "alice@@example.com",
        "alice example@example.com",
    ),
)
def test_lightning_address_normalizer_rejects_non_address_payloads(payload: str) -> None:
    assert main_module._normalize_lightning_address(payload) is None


def test_real_bolt11_invoice_is_decoded_for_review() -> None:
    bolt11_module = main_module.bolt11
    invoice = bolt11_module.Bolt11(
        currency="bc",
        date=int(time.time()),
        amount_msat=bolt11_module.MilliSatoshi(21_000),
        tags=bolt11_module.Tags(
            [
                bolt11_module.Tag(bolt11_module.TagChar.payment_hash, "00" * 32),
                bolt11_module.Tag(bolt11_module.TagChar.payment_secret, "11" * 32),
                bolt11_module.Tag(bolt11_module.TagChar.description, "Test invoice"),
            ]
        ),
    )
    encoded = bolt11_module.encode(invoice, private_key="12" * 32)

    decoded = main_module._decode_lightning_invoice(f"lightning:{encoded}")

    assert decoded is not None
    assert decoded["amount"] == 21
    assert decoded["description"] == "Test invoice"


def test_invoice_review_state_rejects_tampering() -> None:
    cipher = InvoicePaymentCipher(TEST_SETTINGS)
    token = cipher.encode(
        InvoicePaymentState(invoice="lnbc210n1pytestinvoice", amount=21)
    )

    with pytest.raises(ValueError, match="invalid or expired"):
        cipher.decode(token[:-2] + "xx")


def test_scanned_invoice_renders_review_without_exposing_raw_invoice(monkeypatch) -> None:
    invoice = "lnbc210n1pytestinvoice"
    monkeypatch.setattr(
        main_module,
        "_decode_lightning_invoice",
        lambda value: {
            "invoice": invoice,
            "amount": 21,
            "description": "Coffee",
            "expiry": "2026-08-06 23:59:00",
            "payment_hash": "ab" * 32,
        }
        if str(value).removeprefix("lightning:") == invoice
        else None,
    )
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/scan/lightning",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_payment": f"lightning:{invoice}",
        },
    )

    assert response.status_code == 200
    assert "Review Lightning invoice" in response.text
    assert "21 sats" in response.text
    assert "Coffee" in response.text
    assert 'name="invoice_state"' in response.text
    assert invoice not in response.text
    assert acorn.invoice_payments == []


def test_confirmed_scanned_invoice_delegates_to_acorn(monkeypatch) -> None:
    invoice = "lnbc210n1pytestinvoice"
    decoded = {
        "invoice": invoice,
        "amount": 21,
        "description": "Coffee",
        "expiry": "2026-08-06 23:59:00",
        "payment_hash": "ab" * 32,
    }
    monkeypatch.setattr(main_module, "_decode_lightning_invoice", lambda value: decoded)
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")
    state_token = InvoicePaymentCipher(TEST_SETTINGS).encode(
        InvoicePaymentState(invoice=invoice, amount=21)
    )

    response = client.post(
        "/pay/invoice",
        data={
            "csrf_token": valid_csrf_token(),
            "invoice_state": state_token,
            "comment": "pytest invoice",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 200
    assert "Invoice payment successful" in response.text
    assert acorn.invoice_payments == [
        {"invoice": invoice, "comment": "pytest invoice"}
    ]


def test_deposit_form_displays_home_mint_and_amount_field() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_deposit_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/deposit")

    assert response.status_code == 200
    assert "500 sats" in response.text
    assert "https://mint.example.com" in response.text
    assert 'name="amount"' in response.text
    assert "Creating a deposit invoice. Please wait." in response.text
    assert "Creating invoice…" in response.text


def test_deposit_creates_invoice_qr_without_polling() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_deposit_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/deposit",
        data={"csrf_token": valid_csrf_token(), "amount": "21"},
    )

    assert response.status_code == 200
    assert "Pay deposit invoice" in response.text
    assert "21 sats" in response.text
    assert "lnbc21n1pytestinvoice" in response.text
    assert '<div class="invoice-qr"><svg' in response.text
    assert 'action="/deposit/check"' in response.text
    assert "Checking and finalizing the deposit. Please wait" in response.text
    assert "Checking deposit…" in response.text
    assert acorn.deposit_calls == [21]
    assert acorn.quote_checks == []

    token_match = re.search(
        r'name="deposit_token" value="([^"]+)"', response.text
    )
    assert token_match is not None
    deposit_token = unescape(token_match.group(1))
    assert "pytest-deposit-quote" not in deposit_token
    state = DepositQuoteCipher(TEST_SETTINGS).decode(deposit_token)
    assert state == DepositQuoteState(
        quote="pytest-deposit-quote",
        amount=21,
        mint="https://mint.example.com",
        invoice="lnbc21n1pytestinvoice",
    )


def test_paid_deposit_is_finalized_and_redirects_to_updated_wallet() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500, deposit_paid=True)
    app.dependency_overrides[get_deposit_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")
    state = DepositQuoteState(
        quote="pytest-deposit-quote",
        amount=21,
        mint=acorn.home_mint,
        invoice="lnbc21n1pytestinvoice",
    )

    response = client.post(
        "/deposit/check",
        data={
            "csrf_token": valid_csrf_token(),
            "deposit_token": DepositQuoteCipher(TEST_SETTINGS).encode(state),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/wallet"
    assert acorn.quote_checks == [("pytest-deposit-quote", 21)]
    assert acorn.balance == 521
    assert acorn.history_entries == [
        {"tx_type": "C", "amount": 21, "comment": "safebox web deposit"}
    ]


def test_unpaid_deposit_keeps_same_invoice_available_for_recheck() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500, deposit_paid=False)
    app.dependency_overrides[get_deposit_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")
    state = DepositQuoteState(
        quote="pytest-deposit-quote",
        amount=21,
        mint=acorn.home_mint,
        invoice="lnbc21n1pytestinvoice",
    )

    response = client.post(
        "/deposit/check",
        data={
            "csrf_token": valid_csrf_token(),
            "deposit_token": DepositQuoteCipher(TEST_SETTINGS).encode(state),
        },
    )

    assert response.status_code == 409
    assert "has not confirmed payment yet" in response.text
    assert "lnbc21n1pytestinvoice" in response.text
    assert 'action="/deposit/check"' in response.text
    assert acorn.history_entries == []


def test_confirmed_lightning_payment_delegates_to_acorn() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/pay",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_address": "alice@example.com",
            "amount": "21",
            "comment": "pytest web payment",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 200
    assert "Payment successful" in response.text
    assert "21 sats" in response.text
    assert "Fee: <strong>1 sat" in response.text
    assert acorn.payments == [
        {
            "amount": 21,
            "lnaddress": "alice@example.com",
            "comment": "pytest web payment",
        }
    ]


def test_payment_requires_explicit_confirmation_before_calling_acorn() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/pay",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_address": "alice@example.com",
            "amount": "21",
            "comment": "must not run",
        },
    )

    assert response.status_code == 400
    assert "Explicit payment confirmation is required" in response.text
    assert acorn.payments == []


def test_payment_is_blocked_when_mint_verification_is_not_clean() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(
        balance=33_926,
        verified_balance=52,
        verification_status="repair-recommended",
    )
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/pay",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_address": "alice@example.com",
            "amount": "21",
            "comment": "must not run",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 409
    assert "Payment is blocked" in response.text
    assert acorn.payments == []


def test_record_index_links_encoded_labels() -> None:
    app = create_app(TEST_SETTINGS)
    app.dependency_overrides[get_loaded_acorn] = lambda: FakeLoadedAcorn()
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/records")

    assert response.status_code == 200
    assert "Field Notes" in response.text
    assert "/record?label=Field+Notes" in response.text
    assert "/record?label=Travel%2F2026" in response.text
    assert "/record?label=A+%26+B" in response.text
    assert 'href="/record/edit"' in response.text
    assert 'href="/blob/upload"' not in response.text
    assert '<table class="record-table">' in response.text
    assert '<th scope="col">Private Record</th>' in response.text
    assert response.text.count('class="record-open"') == 3


def test_legacy_blob_upload_page_redirects_to_unified_record_form() -> None:
    app = create_app(TEST_SETTINGS)
    app.dependency_overrides[get_loaded_acorn] = lambda: FakeBlobAcorn()
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/blob/upload", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/record/edit"


def test_blob_upload_passes_plaintext_to_acorn_encryption_boundary() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn()
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/blob/upload",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Private Notes",
            "description": "Encrypted attachment",
            "confirmed": "yes",
        },
        files={"blob": ("notes.txt", b"private blob contents", "text/plain")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/record?label=Private+Notes&saved=1"
    assert acorn.record_put_calls == [
        {
            "record_name": "Private Notes",
            "record_value": {
                "description": "Encrypted attachment",
                "filename": "notes.txt",
                "size": 21,
            },
            "record_type": "blob",
            "record_kind": 37375,
            "blob_data": b"private blob contents",
            "return_result": True,
        }
    ]


def test_blob_upload_rejects_existing_label_without_orphaning_blob() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(existing_labels={"Private Notes"})
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/blob/upload",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Private Notes",
            "confirmed": "yes",
        },
        files={"blob": ("notes.txt", b"do not upload", "text/plain")},
    )

    assert response.status_code == 409
    assert "already exists" in response.text
    assert acorn.record_put_calls == []


def test_blob_upload_fails_closed_when_existing_record_cannot_be_read() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(
        record_lookup_error=ValueError("Could not decrypt private record")
    )
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/blob/upload",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Uncertain Label",
            "confirmed": "yes",
        },
        files={"blob": ("notes.txt", b"do not upload", "text/plain")},
    )

    assert response.status_code == 502
    assert "could not confirm" in response.text
    assert acorn.record_put_calls == []


def test_blob_upload_enforces_configured_size_limit() -> None:
    settings = replace(TEST_SETTINGS, max_blob_bytes=4)
    app = create_app(settings)
    acorn = FakeBlobAcorn()
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/blob/upload",
        data={
            "csrf_token": CsrfProtector(settings).issue(),
            "label": "Too Large",
            "confirmed": "yes",
        },
        files={"blob": ("large.bin", b"12345", "application/octet-stream")},
    )

    assert response.status_code == 413
    assert "exceeds the 4-byte upload limit" in response.text
    assert acorn.record_put_calls == []


def test_blob_record_download_returns_decrypted_attachment() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(existing_labels={"Private Notes"})
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    detail = client.get("/record", params={"label": "Private Notes"})
    response = client.get("/record/blob", params={"label": "Private Notes"})

    assert detail.status_code == 200
    assert "Original Record fingerprint: <code>1EA23F2B</code>" in detail.text
    assert "Download Original Record" in detail.text
    assert "/record/blob?label=Private+Notes" in detail.text
    assert response.status_code == 200
    assert response.content == b"private blob contents"
    assert response.headers["content-type"].startswith("text/plain")
    assert "attachment" in response.headers["content-disposition"]
    assert acorn.blob_reads == ["Private Notes"]


def test_image_blob_uses_native_authenticated_inline_preview() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(
        existing_labels={"Photo"},
        downloaded_type="image/png",
        downloaded_data=b"png bytes",
        blob_type="image/png",
    )
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    detail = client.get("/record", params={"label": "Photo"})
    preview = client.get(
        "/record/blob", params={"label": "Photo", "inline": "1"}
    )

    assert detail.status_code == 200
    assert '<img src="/record/blob?label=Photo&amp;inline=1"' in detail.text
    assert "fetch(" not in detail.text
    assert "URL.createObjectURL" not in detail.text
    assert preview.status_code == 200
    assert preview.headers["content-disposition"].startswith("inline;")
    assert preview.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in preview.headers["content-security-policy"]


def test_pdf_blob_uses_pdfjs_progressive_viewer_with_download_fallback() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(
        existing_labels={"Report"},
        downloaded_type="application/pdf",
        downloaded_data=b"%PDF test",
        blob_type="application/pdf",
    )
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/record", params={"label": "Report"})

    assert response.status_code == 200
    assert 'class="blob-preview blob-preview-pdf" data-pdf-viewer' in response.text
    assert 'src="/static/pdf-viewer.js"' in response.text
    assert "data-pdf-previous" in response.text
    assert "data-pdf-next" in response.text
    assert 'href="/record/blob?label=Report&amp;inline=1"' in response.text
    assert "Open PDF full screen" in response.text
    assert "JavaScript is required for the inline PDF preview" in response.text
    assert "Download Original Record" in response.text


def test_blob_fingerprint_is_hidden_when_plaintext_digest_is_invalid() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(
        existing_labels={"Legacy blob"},
        orig_sha256="not-a-sha256",
    )
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/record", params={"label": "Legacy blob"})

    assert response.status_code == 200
    assert "Blob fingerprint:" not in response.text


def test_blob_record_delete_form_requires_explicit_confirmation() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(existing_labels={"Report"})
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    detail = client.get("/record", params={"label": "Report"})
    response = client.post(
        "/record/delete",
        data={"csrf_token": valid_csrf_token(), "label": "Report"},
    )

    assert 'action="/record/delete"' in detail.text
    assert "Delete this record and Original Record" in detail.text
    assert response.status_code == 400
    assert "Explicit deletion confirmation is required" in response.text
    assert acorn.record_delete_calls == []


def test_blob_record_delete_removes_record_and_requests_blob_cleanup() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(existing_labels={"Report"})
    acorn.record_delete_result = {
        "status": "DELETE_REQUESTED",
        "hidden_on": [acorn.home_relay],
        "blob_cleanup": {
            "requested": True,
            "deleted": True,
            "sha256": "abc123",
        },
        "index_error": None,
    }
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/record/delete",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Report",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 200
    assert "Deletion was requested for Report" in response.text
    assert "accepted the Original Record deletion request" in response.text
    assert acorn.record_delete_calls == [
        {
            "label": "Report",
            "record_kind": 37375,
            "delete_blob": True,
        }
    ]


def test_blob_record_delete_reports_partial_blob_cleanup() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(existing_labels={"Report"})
    acorn.record_delete_result = {
        "status": "DELETE_REQUESTED",
        "hidden_on": [],
        "blob_cleanup": {"requested": True, "deleted": False},
        "index_error": None,
    }
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/record/delete",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Report",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 200
    assert "could not confirm deletion of the Original Record" in response.text
    assert "could not confirm that the original record is already hidden" in response.text


def test_unsafe_blob_type_cannot_be_forced_inline() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(
        existing_labels={"Markup"},
        downloaded_type="text/html",
        downloaded_data=b"<script>alert(1)</script>",
        blob_type="text/html",
    )
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    detail = client.get("/record", params={"label": "Markup"})
    attempted_preview = client.get(
        "/record/blob", params={"label": "Markup", "inline": "1"}
    )

    assert "No inline preview is available" in detail.text
    assert "<object" not in detail.text
    assert attempted_preview.headers["content-disposition"].startswith("attachment;")
    assert attempted_preview.headers["x-frame-options"] == "DENY"


def test_record_detail_renders_escaped_payload() -> None:
    app = create_app(TEST_SETTINGS)
    app.dependency_overrides[get_loaded_acorn] = lambda: FakeLoadedAcorn()
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/record", params={"label": "Field Notes"})

    assert response.status_code == 200
    assert "Field Notes" in response.text
    assert "&lt;script&gt;" in response.text
    assert "<script>" not in response.text
    assert "/record/edit?label=Field+Notes" in response.text


def test_record_edit_form_loads_and_escapes_existing_payload() -> None:
    app = create_app(TEST_SETTINGS)
    app.dependency_overrides[get_loaded_acorn] = lambda: FakeLoadedAcorn()
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/record/edit", params={"label": "Field Notes"})

    assert response.status_code == 200
    assert "Update private record" in response.text
    assert 'value="Field Notes"' in response.text
    assert 'value="Field Notes" readonly' in response.text
    assert '<option value="json" selected>JSON</option>' in response.text
    assert "&lt;script&gt;alert" in response.text
    assert "<script>alert" not in response.text
    assert 'enctype="multipart/form-data"' in response.text
    assert 'name="attachment" type="file"' in response.text
    assert "Original Record (optional)" in response.text
    assert "Select a file to attach it to this record" in response.text


def test_record_save_encrypts_publishes_and_verifies() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn()
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/record/save",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Field Notes",
            "payload": "A private update",
            "confirmed": "yes",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/record?label=Field+Notes&saved=1"
    assert acorn.record_put_calls == [
        {
            "record_name": "Field Notes",
            "record_value": "A private update",
            "record_type": "generic",
            "record_kind": 37375,
            "blob_data": None,
            "preserve_existing_blob": True,
            "return_result": True,
        }
    ]


def test_record_save_can_include_an_encrypted_file_attachment() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn()
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/record/save",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Travel Documents",
            "payload": "Boarding pass",
            "confirmed": "yes",
        },
        files={"attachment": ("pass.pdf", b"private pdf", "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert acorn.record_put_calls == [
        {
            "record_name": "Travel Documents",
            "record_value": "Boarding pass",
            "record_type": "generic",
            "record_kind": 37375,
            "blob_data": b"private pdf",
            "preserve_existing_blob": True,
            "return_result": True,
        }
    ]


def test_record_save_allows_a_file_only_private_record() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn()
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/record/save",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Passport",
            "payload": "",
            "confirmed": "yes",
        },
        files={"attachment": ("passport.pdf", b"private pdf", "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert acorn.record_put_calls[0]["record_value"] == ""
    assert acorn.record_put_calls[0]["blob_data"] == b"private pdf"


def test_record_save_requires_confirmation_without_mutating() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn()
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/record/save",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Field Notes",
            "payload": "Do not store",
        },
    )

    assert response.status_code == 400
    assert "Explicit confirmation is required" in response.text
    assert acorn.record_put_calls == []


def test_record_save_preserves_structured_json_payload() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn()
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/record/save",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Preferences",
            "payload": '{"theme":"green","alerts":true}',
            "payload_format": "json",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 200
    assert acorn.record_put_calls[0]["record_value"] == {
        "theme": "green",
        "alerts": True,
    }


def test_loopback_login_accepts_matching_browser_origin() -> None:
    client = TestClient(
        create_app(TEST_SETTINGS),
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50123),
    )

    response = client.post(
        "/login",
        headers={"Origin": "http://127.0.0.1:8000/"},
        data={
            "csrf_token": valid_csrf_token(),
            "secret_type": "nsec",
            "secret": TEST_NSEC,
            "bootstrap_relay": "relay.example.com",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_loopback_login_accepts_null_origin_with_valid_form_token() -> None:
    client = TestClient(
        create_app(TEST_SETTINGS),
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50123),
    )

    response = client.post(
        "/login",
        headers={"Origin": "null"},
        data={
            "csrf_token": valid_csrf_token(),
            "secret_type": "nsec",
            "secret": TEST_NSEC,
            "bootstrap_relay": "relay.example.com",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_null_origin_without_valid_form_token_is_rejected() -> None:
    client = TestClient(
        create_app(TEST_SETTINGS),
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50123),
    )

    response = client.post(
        "/login",
        headers={"Origin": "null"},
        data={
            "csrf_token": "invalid",
            "secret_type": "nsec",
            "secret": TEST_NSEC,
            "bootstrap_relay": "relay.example.com",
        },
    )

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")
    assert "form token is invalid" in response.text.lower()


def test_login_rejects_cross_origin_submission() -> None:
    client = make_https_client()

    response = client.post(
        "/login",
        headers={"Origin": "https://attacker.example"},
        data={
            "csrf_token": valid_csrf_token(),
            "secret_type": "nsec",
            "secret": TEST_NSEC,
            "bootstrap_relay": "relay.example.com",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Origin rejected"


def test_nsec_login_uses_encrypted_secure_cookie_and_dependency() -> None:
    client = make_https_client()

    response = client.post(
        "/login",
        data={
            "csrf_token": valid_csrf_token(),
            "secret_type": "nsec",
            "secret": TEST_NSEC,
            "bootstrap_relay": "relay.example.com",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    set_cookie = response.headers["set-cookie"]
    assert f"{SECURE_COOKIE_NAME}=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert f"Max-Age={TEST_SETTINGS.session_ttl_seconds}" in set_cookie
    assert "expires=" in set_cookie.lower()

    token = client.cookies.get(SECURE_COOKIE_NAME)
    assert token is not None
    assert token.startswith("v2.")
    assert TEST_NSEC not in token
    assert "relay.example.com" not in token

    credentials = SessionCipher(TEST_SETTINGS).decode(token)
    assert credentials.nsec == TEST_NSEC
    assert credentials.bootstrap_relay == "wss://relay.example.com"

    session_response = client.get("/api/session")
    assert session_response.status_code == 200
    payload = session_response.json()
    assert payload["authenticated"] is True
    assert payload["bootstrap_relay"] == "wss://relay.example.com"
    assert payload["npub"].startswith(TEST_NPUB)
    assert "nsec" not in payload
    assert "record_protection_key" not in payload


def test_session_cipher_uses_randomized_aes_256_gcm_tokens() -> None:
    cipher = SessionCipher(TEST_SETTINGS)
    credentials = SessionCredentials(
        nsec=TEST_NSEC,
        bootstrap_relay="wss://relay.example.com",
        record_protection_key=TEST_RPK,
    )

    first = cipher.encode(credentials)
    second = cipher.encode(credentials)

    assert first.startswith("v2.")
    assert second.startswith("v2.")
    assert first != second
    assert cipher.decode(first) == credentials
    assert cipher.decode(second) == credentials


def test_aes_256_gcm_session_rejects_tampering() -> None:
    cipher = SessionCipher(TEST_SETTINGS)
    token = cipher.encode(
        SessionCredentials(
            nsec=TEST_NSEC,
            bootstrap_relay="wss://relay.example.com",
        )
    )
    replacement = "A" if token[-2] != "A" else "B"
    tampered = token[:-2] + replacement + token[-1]

    with pytest.raises(ValueError, match="invalid or expired"):
        cipher.decode(tampered)


def test_aes_256_gcm_session_rejects_invalid_record_protection_key() -> None:
    cipher = SessionCipher(TEST_SETTINGS)
    with pytest.raises(ValueError, match="record protection key is invalid"):
        cipher.encode(
            SessionCredentials(
                nsec=TEST_NSEC,
                bootstrap_relay="wss://relay.example.com",
                record_protection_key="not-a-key",
            )
        )


def test_session_rejects_confirmed_backup_without_record_protection_key() -> None:
    cipher = SessionCipher(TEST_SETTINGS)

    with pytest.raises(ValueError, match="cannot confirm a missing"):
        cipher.encode(
            SessionCredentials(
                nsec=TEST_NSEC,
                bootstrap_relay="wss://relay.example.com",
                record_protection_backup_confirmed=True,
            )
        )


def test_aes_256_gcm_session_enforces_absolute_expiry(monkeypatch) -> None:
    cipher = SessionCipher(TEST_SETTINGS)
    monkeypatch.setattr(security_module, "time", lambda: 1_000_000)
    token = cipher.encode(
        SessionCredentials(
            nsec=TEST_NSEC,
            bootstrap_relay="wss://relay.example.com",
        )
    )

    monkeypatch.setattr(
        security_module,
        "time",
        lambda: 1_000_000 + TEST_SETTINGS.session_ttl_seconds + 1,
    )

    with pytest.raises(ValueError, match="invalid or expired"):
        cipher.decode(token)


def test_session_cipher_accepts_unexpired_legacy_fernet_cookie() -> None:
    credentials = SessionCredentials(
        nsec=TEST_NSEC,
        bootstrap_relay="wss://relay.example.com",
    )
    legacy_payload = json.dumps(
        {
            "bootstrap_relay": credentials.bootstrap_relay,
            "nsec": credentials.nsec,
            "version": credentials.version,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    legacy_token = Fernet(TEST_KEY.encode("ascii")).encrypt(legacy_payload).decode("ascii")

    assert SessionCipher(TEST_SETTINGS).decode(legacy_token) == credentials


def test_offline_mnemonic_login_derives_nsec_but_does_not_store_phrase() -> None:
    client = make_https_client()

    response = client.post(
        "/login",
        data={
            "csrf_token": valid_csrf_token(),
            "secret_type": "mnemonic",
            "secret": TEST_MNEMONIC,
            "bootstrap_relay": "wss://relay.example.com",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    token = client.cookies.get(SECURE_COOKIE_NAME)
    assert token is not None
    assert "abandon" not in token
    credentials = SessionCipher(TEST_SETTINGS).decode(token)
    assert credentials.nsec == TEST_NSEC
    assert not hasattr(credentials, "mnemonic")


def test_invalid_secret_is_rejected_without_echoing_it() -> None:
    client = make_https_client()
    bad_secret = "not-a-real-private-key"

    response = client.post(
        "/login",
        data={
            "csrf_token": valid_csrf_token(),
            "secret_type": "nsec",
            "secret": bad_secret,
            "bootstrap_relay": "relay.example.com",
        },
    )

    assert response.status_code == 400
    assert bad_secret not in response.text
    assert SECURE_COOKIE_NAME not in response.headers.get("set-cookie", "")


def test_tampered_cookie_is_rejected() -> None:
    client = make_https_client()
    client.cookies.set(
        SECURE_COOKIE_NAME,
        "not-a-valid-session",
        domain="safebox.example",
        path="/",
    )

    response = client.get("/api/session")

    assert response.status_code == 401
    assert response.json()["detail"] == "Acorn session is invalid or expired"


def test_unlisted_ws_relay_is_rejected() -> None:
    client = make_https_client()

    response = client.post(
        "/login",
        data={
            "csrf_token": valid_csrf_token(),
            "secret_type": "nsec",
            "secret": TEST_NSEC,
            "bootstrap_relay": "ws://relay.example.com:8735",
        },
    )

    assert response.status_code == 400
    assert "SAFEBOX_ALLOWED_WS_RELAYS" in response.text
