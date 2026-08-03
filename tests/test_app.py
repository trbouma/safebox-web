from __future__ import annotations

import asyncio
from html import unescape
import os
import re
from types import SimpleNamespace

from cryptography.fernet import Fernet


# app.main deliberately refuses to import without an explicit server-held key.
os.environ.setdefault("SAFEBOX_COOKIE_KEY", Fernet.generate_key().decode("ascii"))

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.dependencies import get_deposit_acorn, get_loaded_acorn, get_payment_acorn
from app.main import create_app
from app.security import (
    CsrfProtector,
    DepositQuoteCipher,
    DepositQuoteState,
    SECURE_COOKIE_NAME,
    SessionCipher,
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


def make_https_client() -> TestClient:
    return TestClient(create_app(TEST_SETTINGS), base_url="https://safebox.example")


def valid_csrf_token() -> str:
    return CsrfProtector(TEST_SETTINGS).issue()


class FakeLoadedAcorn:
    pubkey_bech32 = "npub1testcomponent"
    home_relay = "wss://relay.example.com"
    home_mint = "https://mint.example.com"

    def __init__(
        self,
        balance: int = 321,
        deposit_paid: bool = True,
        verified_balance: int | None = None,
        verification_status: str = "clean",
    ) -> None:
        self.balance = balance
        self.proofs = [object()] if balance else []
        self.deposit_paid = deposit_paid
        self.verified_balance = balance if verified_balance is None else verified_balance
        self.verification_status = verification_status
        self.loaded = False
        self.payments: list[dict] = []
        self.deposit_calls: list[int] = []
        self.quote_checks: list[tuple[str, int]] = []
        self.history_entries: list[dict] = []

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

    async def pay_multi(self, amount: int, lnaddress: str, comment: str):
        self.payments.append(
            {"amount": amount, "lnaddress": lnaddress, "comment": comment}
        )
        self.balance -= amount + 1
        return f"Payment of {amount} sats successful!", 1

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


def test_settings_load_cookie_key_from_working_directory_env_file(
    tmp_path, monkeypatch
) -> None:
    env_key = Fernet.generate_key().decode("ascii")
    (tmp_path / ".env").write_text(
        f"SAFEBOX_COOKIE_KEY={env_key}\nSAFEBOX_SESSION_TTL_SECONDS=7200\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SAFEBOX_COOKIE_KEY", raising=False)
    monkeypatch.delenv("SAFEBOX_SESSION_TTL_SECONDS", raising=False)

    settings = Settings.from_env()

    assert settings.cookie_key == env_key
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
    assert "User-controlled component" in response.text
    assert "User-controlled session" in response.text
    assert "Web service surface" in response.text


def test_progress_script_is_served_from_same_origin() -> None:
    response = make_https_client().get("/static/forms.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert 'form.setAttribute("aria-busy", "true")' in response.text
    assert "button.disabled = true" in response.text


def test_login_page_links_to_new_acorn_creation() -> None:
    response = make_https_client().get("/login")

    assert response.status_code == 200
    assert 'href="/create"' in response.text
    assert "Create a new Acorn" in response.text


def test_create_form_displays_default_relay_and_mint() -> None:
    response = make_https_client().get("/create")

    assert response.status_code == 200
    assert 'name="home_relay"' in response.text
    assert TEST_SETTINGS.default_bootstrap_relay in response.text
    assert 'name="home_mint"' in response.text
    assert TEST_SETTINGS.default_home_mint in response.text
    assert 'name="confirmed"' in response.text


def test_create_acorn_initializes_relay_state_and_starts_session(monkeypatch) -> None:
    FakeCreatedAcorn.instances.clear()
    monkeypatch.setattr(main_module, "Acorn", FakeCreatedAcorn)
    monkeypatch.setattr(
        main_module,
        "generate_seed_phrase_and_nsec",
        lambda: (TEST_MNEMONIC, TEST_NSEC),
    )
    client = make_https_client()

    response = client.post(
        "/create",
        data={
            "csrf_token": valid_csrf_token(),
            "home_relay": "relay.example.com",
            "home_mint": "mint.example.com/",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 201
    assert "New Acorn created" in response.text
    assert TEST_MNEMONIC in response.text
    assert TEST_NSEC in response.text
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

    token = client.cookies.get(SECURE_COOKIE_NAME)
    assert token is not None
    assert TEST_NSEC not in token
    assert TEST_MNEMONIC not in token
    credentials = SessionCipher(TEST_SETTINGS).decode(token)
    assert credentials.nsec == TEST_NSEC
    assert credentials.bootstrap_relay == "wss://relay.example.com"


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


def test_wallet_page_displays_loaded_balance() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=12_345)
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/wallet")

    assert response.status_code == 200
    assert "12,345 sats" in response.text
    assert "Relay-visible proof total" in response.text
    assert "Mint-confirmed spendable balance" in response.text
    assert "wss://relay.example.com" in response.text
    assert "not stored" in response.text


def test_wallet_warns_when_relay_total_exceeds_mint_confirmed_balance() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(
        balance=33_926,
        verified_balance=52,
        verification_status="repair-recommended",
    )
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/wallet")

    assert response.status_code == 200
    assert "Relay-visible proof total: <strong>33,926 sats" in response.text
    assert "Mint-confirmed spendable balance: <strong>52 sats" in response.text
    assert "33,874 sats not confirmed as spendable" in response.text
    assert "Do not make a payment" in response.text


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


def test_record_detail_renders_escaped_payload() -> None:
    app = create_app(TEST_SETTINGS)
    app.dependency_overrides[get_loaded_acorn] = lambda: FakeLoadedAcorn()
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/record", params={"label": "Field Notes"})

    assert response.status_code == 200
    assert "Field Notes" in response.text
    assert "&lt;script&gt;" in response.text
    assert "<script>" not in response.text


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
    assert "Form token" in response.json()["detail"]


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

    token = client.cookies.get(SECURE_COOKIE_NAME)
    assert token is not None
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


def test_insecure_remote_websocket_relay_is_rejected() -> None:
    client = make_https_client()

    response = client.post(
        "/login",
        data={
            "csrf_token": valid_csrf_token(),
            "secret_type": "nsec",
            "secret": TEST_NSEC,
            "bootstrap_relay": "ws://relay.example.com",
        },
    )

    assert response.status_code == 400
    assert "loopback" in response.text
