from types import SimpleNamespace
from unittest.mock import AsyncMock

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import get_acorn
from app.messages import router
from app.security import CsrfProtector


def setup():
    app = FastAPI()
    app.state.settings = Settings(cookie_key=Fernet.generate_key().decode())
    app.include_router(router)
    acorn = SimpleNamespace(get_private_messages=AsyncMock(return_value=[{
        "sender": "a" * 64, "content": "<script>alert(1)</script>", "created_at": 1700000000,
    }]), secure_dm=AsyncMock(return_value="message sent"))
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
    acorn.secure_dm.assert_awaited_once_with("alice@example.com", "hello")


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
