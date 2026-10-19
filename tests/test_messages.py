import pytest
from contextlib import contextmanager
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
from stroma import Keys

WALLET_KEY = Keys(priv_k='01'*32)
DESTINATION = {'npub': WALLET_KEY.public_key_bech32(), 'pubkey': WALLET_KEY.public_key_hex(),
               'relay_hints': ['wss://wallet.example'], 'discovery_source': 'safebox-v1'}


def mock_wallet(monkeypatch):
    lookup = AsyncMock(return_value=DESTINATION)
    monkeypatch.setattr('app.messages.resolve_wallet_message_recipient', lookup)
    return lookup



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


def test_send_requires_csrf_and_redirects(monkeypatch):
    mock_wallet(monkeypatch)
    app, acorn, client = setup()
    data = {"recipient": "alice@example.com", "message": "hello", "csrf_token": "bad"}
    assert client.post("/messages", data=data).status_code == 403
    acorn.secure_dm.assert_not_awaited()
    data["csrf_token"] = CsrfProtector(app.state.settings).issue()
    result = client.post("/messages", data=data, follow_redirects=False)
    assert result.status_code == 303
    acorn.secure_dm.assert_awaited_once_with(DESTINATION["npub"], encode_message("hello", "sender@testserver"), dm_relays=DESTINATION["relay_hints"])


def test_sender_without_handle_uses_public_key(monkeypatch):
    mock_wallet(monkeypatch)
    app, acorn, client = setup(with_handle=False)
    response = client.post("/messages", data={"recipient": "alice@example.com", "message": "hello",
        "csrf_token": CsrfProtector(app.state.settings).issue()}, follow_redirects=False)
    assert response.status_code == 303
    acorn.secure_dm.assert_awaited_once_with(DESTINATION["npub"], encode_message("hello", None), dm_relays=DESTINATION["relay_hints"])


def test_failure_no_retry_or_exception_leak(monkeypatch):
    mock_wallet(monkeypatch)
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
    monkeypatch.setattr(dm, "resolve_wallet_identity", AsyncMock(return_value={"pubkey":"b" * 64,"discovery_source":"safebox-v1"}))
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


@pytest.mark.parametrize('failure', ['unsupported', 'error'])
def test_discovery_failure_never_publishes(monkeypatch, failure):
    from acorn.safebox_discovery import SafeboxDiscoveryError
    lookup = mock_wallet(monkeypatch)
    if failure == 'unsupported':
        lookup.return_value = None
    else:
        lookup.side_effect = SafeboxDiscoveryError('private diagnostic')
    app, acorn, client = setup()
    response = client.post('/messages', data={'recipient':'alice@example.com','message':'hello',
        'csrf_token':CsrfProtector(app.state.settings).issue()})
    assert 'No message was sent' in response.text
    assert 'private diagnostic' not in response.text
    acorn.secure_dm.assert_not_awaited()


def test_explicit_npub_never_looks_up_address(monkeypatch):
    lookup = mock_wallet(monkeypatch)
    app, acorn, client = setup()
    response = client.post('/messages', data={'recipient':DESTINATION['npub'],'message':'hello',
        'csrf_token':CsrfProtector(app.state.settings).issue()}, follow_redirects=False)
    assert response.status_code == 303
    lookup.assert_not_awaited()
    acorn.secure_dm.assert_awaited_once_with(DESTINATION['npub'], encode_message('hello','sender@testserver'))


def test_local_wallet_uses_registration_and_private_relay(monkeypatch):
    lookup = mock_wallet(monkeypatch)
    app, acorn, client = setup()
    # Replace the synthetic registration with a valid public wallet key.
    with contextmanager(app.dependency_overrides[get_database_session])() as session:
        from sqlmodel import select
        row = session.exec(select(ClaimedHandle).where(ClaimedHandle.claimed_handle == 'other')).first()
        row.npub = DESTINATION['npub']
        row.home_relay = 'ws://private-grove:8080'
        session.add(row)
        session.commit()
    response = client.post('/messages', data={'recipient':'other@testserver','message':'hello',
        'csrf_token':CsrfProtector(app.state.settings).issue()}, follow_redirects=False)
    assert response.status_code == 303
    lookup.assert_not_awaited()
    acorn.secure_dm.assert_awaited_once_with(DESTINATION['npub'], encode_message('hello','sender@testserver'), dm_relays=['ws://private-grove:8080'])
