import asyncio
import json
import socket
from unittest.mock import AsyncMock

import pytest

from app import private_messages as dm


@pytest.mark.parametrize("content", ["hello", "From: old@example.com\n\nHello", "{bad", '[1,2]', '{"message":"unrelated"}', '{"type":"safebox.dm","version":2,"message":"future"}'])
def test_plain_and_unknown_json_preserved(content):
    assert dm.parse_message(content)["content"] == content
    assert dm.parse_message(content)["claimed_sender"] is None


@pytest.mark.parametrize("unit", ["sat", "cmu-test"])
def test_nut18_is_not_chat_even_if_malformed(unit):
    assert dm.parse_message(json.dumps({"mint": "https://mint", "unit": unit,
        "proofs": None, "type": "safebox.dm", "version": 1, "message": "hidden"})) is None


def test_verification_matches_authenticated_author_and_preserves_text(monkeypatch):
    lookup = AsyncMock(side_effect=["a" * 64, "b" * 64, None])
    monkeypatch.setattr(dm, "_fetch_identity", lookup)
    dm._cache.clear()
    messages = [{"sender": "a" * 64, "content": dm.encode_message("<hello>", address)}
                for address in ["alice@example.com", "bob@example.com", "offline@example.com"]]
    result = asyncio.run(dm.present_messages(messages))
    assert [m["verification"] for m in result] == ["verified", "mismatch", "unverified"]
    assert all(m["content"] == "<hello>" and m["sender"] == "a" * 64 for m in result)
    asyncio.run(dm.present_messages(messages))
    assert lookup.await_count == 3
    dm._cache.clear()


def test_lookup_failure_does_not_hide_message(monkeypatch):
    dm._cache.clear()
    monkeypatch.setattr(dm, "_fetch_identity", AsyncMock(side_effect=TimeoutError()))
    result = asyncio.run(dm.present_messages([{"sender": "a" * 64,
        "content": dm.encode_message("hello", "fail@example.com")}]))
    assert result[0]["verification"] == "unverified"
    assert result[0]["content"] == "hello"
    dm._cache.clear()


@pytest.mark.parametrize("address", ["a@localhost", "a@host:443", "a@host/path", "a@user@example.com", "a@127.0.0.1\n"])
def test_invalid_addresses_rejected(address):
    with pytest.raises(ValueError):
        dm._address_parts(address)


def test_private_dns_destination_never_fetched(monkeypatch):
    async def run():
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "getaddrinfo", AsyncMock(return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]))
        def forbidden(**kwargs):
            raise AssertionError("No private-network request")
        monkeypatch.setattr(dm.httpx, "AsyncClient", forbidden)
        assert await dm._fetch_identity("alice@example.com") is None
    asyncio.run(run())


def test_public_lookup_pins_ip_and_tls_hostname(monkeypatch):
    import httpx
    original = httpx.AsyncClient
    def handler(request):
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] == "example.com"
        assert request.extensions["sni_hostname"] == "example.com"
        assert request.url.params["name"] == "alice"
        return httpx.Response(200, json={"names": {"alice": "a" * 64}})
    def client(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return original(**kwargs, transport=httpx.MockTransport(handler))
    async def run():
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]))
        monkeypatch.setattr(dm.httpx, "AsyncClient", client)
        assert await dm._fetch_identity("alice@example.com") == "a" * 64
    asyncio.run(run())
