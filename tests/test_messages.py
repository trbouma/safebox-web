from types import SimpleNamespace
from unittest.mock import AsyncMock

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import get_acorn
from app.messages import router
from app.security import CsrfProtector
from app.database import get_database_session
from app.models import ClaimedHandle
from sqlmodel import Session, create_engine
from sqlalchemy.pool import StaticPool
from app.private_messages import encode_message


def setup(with_handle=True):
    app = FastAPI()
    app.state.settings = Settings(cookie_key=Fernet.generate_key().decode())
    app.include_router(router)
    acorn = SimpleNamespace(get_private_messages=AsyncMock(return_value=[{
        "sender": "a" * 64, "content": "<script>alert(1)</script>", "created_at": 1700000000,
    }]), secure_dm=AsyncMock(return_value="message sent"), pubkey_bech32="npub-sender")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    ClaimedHandle.__table__.create(engine)
    with Session(engine) as session:
        session.add(ClaimedHandle(claimed_handle="other", npub="npub-other", home_relay="wss://relay"))
        if with_handle:
            session.add(ClaimedHandle(claimed_handle="sender", npub=acorn.pubkey_bech32, home_relay="wss://relay"))
        session.commit()
    def database_session():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_database_session] = database_session
    app.dependency_overrides[get_acorn] = lambda: acorn
    return app, acorn, TestClient(app)


def test_read_only_inbox_escapes_content():
    app, acorn, client = setup()
    response = client.get("/messages")
    assert response.status_code == 200
    assert "&lt;script&gt;" in response.text
    assert "<script>alert" not in response.text
    assert response.headers["cache-control"] == "no-store"
    acorn.secure_dm.assert_not_awaited()


def test_send_requires_csrf_and_redirects():
    app, acorn, client = setup()
    data = {"recipient": "alice@example.com", "message": "hello", "csrf_token": "bad"}
    assert client.post("/messages", data=data).status_code == 403
    acorn.secure_dm.assert_not_awaited()
    data["csrf_token"] = CsrfProtector(app.state.settings).issue()
    result = client.post("/messages", data=data, follow_redirects=False)
    assert result.status_code == 303
    acorn.secure_dm.assert_awaited_once_with("alice@example.com", encode_message("hello", "sender@testserver"))


def test_sender_without_handle_uses_public_key():
    app, acorn, client = setup(with_handle=False)
    response = client.post("/messages", data={"recipient": "alice@example.com", "message": "hello",
        "csrf_token": CsrfProtector(app.state.settings).issue()}, follow_redirects=False)
    assert response.status_code == 303
    acorn.secure_dm.assert_awaited_once_with("alice@example.com", encode_message("hello", None))


def test_failure_no_retry_or_exception_leak():
    app, acorn, client = setup()
    acorn.secure_dm.side_effect = RuntimeError("secret detail")
    response = client.post("/messages", data={"recipient": "alice@example.com", "message": "hello",
        "csrf_token": CsrfProtector(app.state.settings).issue()})
    assert "may have reached" in response.text
    assert "secret detail" not in response.text
    assert acorn.secure_dm.await_count == 1


def test_inbox_failure_and_authentication():
    app, acorn, client = setup()
    acorn.get_private_messages.side_effect = TimeoutError()
    assert "could not be loaded" in client.get("/messages").text
    app.dependency_overrides.clear()
    assert client.get("/messages").status_code == 401


def test_structured_inbox_and_payments(monkeypatch):
    from app import private_messages as dm
    import json
    app, acorn, client = setup()
    monkeypatch.setattr(dm, "resolve_identity", AsyncMock(return_value="b" * 64))
    acorn.get_private_messages.return_value = [
        {"sender": "a" * 64, "created_at": 1700000000,
         "content": encode_message("<script>private text</script>", "alice@example.com")},
        {"sender": "a" * 64, "created_at": 1700000000,
         "content": json.dumps({"mint": "https://mint", "unit": "sat", "proofs": ["secret-proof"]})},
    ]
    response = client.get("/messages")
    assert "Mismatch:" in response.text
    assert "alice@example.com" in response.text
    assert "a" * 64 in response.text
    assert "&lt;script&gt;private text&lt;/script&gt;" in response.text
    assert "secret-proof" not in response.text
