from __future__ import annotations

import asyncio
import os

from cryptography.fernet import Fernet


# app.main deliberately refuses to import without an explicit server-held key.
os.environ.setdefault("SAFEBOX_COOKIE_KEY", Fernet.generate_key().decode("ascii"))

from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import get_loaded_acorn
from app.main import create_app
from app.security import CsrfProtector, SECURE_COOKIE_NAME, SessionCipher


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

    def __init__(self, balance: int = 321) -> None:
        self.balance = balance
        self.loaded = False

    async def load_data(self) -> None:
        self.loaded = True

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
    assert "wss://relay.example.com" in response.text
    assert "not stored" in response.text


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
