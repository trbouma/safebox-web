from __future__ import annotations

import asyncio
from dataclasses import replace
from html import unescape
import os
import re
import sqlite3
from types import SimpleNamespace

from cryptography.fernet import Fernet


# app.main deliberately refuses to import without an explicit server-held key.
os.environ.setdefault("SAFEBOX_COOKIE_KEY", Fernet.generate_key().decode("ascii"))

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.dependencies import (
    get_acorn,
    get_deposit_acorn,
    get_loaded_acorn,
    get_payment_acorn,
    get_receive_acorn,
)
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
    pubkey_bech32 = "npub1testcomponent"
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
        self.deposit_calls: list[int] = []
        self.quote_checks: list[tuple[str, int]] = []
        self.record_put_calls: list[dict] = []
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
    response = make_https_client().get("/")

    assert response.status_code == 200
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in response.text
    assert "-webkit-text-size-adjust: 100%" in response.text
    assert "min-height: 2.75rem" in response.text
    assert "@media (max-width: 36rem)" in response.text
    assert "@media (max-width: 24rem)" in response.text
    assert ".transaction-details { grid-template-columns: 1fr; }" in response.text


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
    assert 'name="mnemonic_words"' in response.text
    assert '<option value="12" selected>' in response.text
    assert '<option value="24">' in response.text
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
    assert "Choose a 12- or 24-word offline mnemonic" in response.text
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
    assert {"alembic_version", "claimed_handle", "provider_payment"}.issubset(tables)
    assert revision == ("20260804_0002",)
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
    assert 'class="lightning-address-qr"' in wallet_page.text
    assert "Lightning address QR code" in wallet_page.text
    assert expected_lnurl in wallet_page.text
    assert "<svg" in wallet_page.text
    assert "Ecash message retention" in wallet_page.text
    assert "for 1 week after publication" in wallet_page.text
    assert "Relay enforcement and physical deletion can vary" in wallet_page.text


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
    assert "@media (max-width: 36rem)" in response.text
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
            "return_result": True,
        }
    ]


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
