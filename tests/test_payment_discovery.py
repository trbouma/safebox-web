import asyncio
from unittest.mock import AsyncMock

import pytest
from stroma import Keys
from acorn.safebox_discovery import (
    CASH, CLEAR, SafeboxDiscoveryError, SafeboxNotFound,
    build_safebox_document, parse_safebox_document,
)
from app import payment_discovery as discovery

ADDRESS = "alice@example.com"
KEY = Keys(priv_k="01" * 32).public_key_hex()
RELAY = "wss://relay.example.com"


def destination(**kwargs):
    return parse_safebox_document(build_safebox_document(ADDRESS, KEY, [RELAY], **kwargs), ADDRESS)


def test_wallet_directory_precedes_social_identity(monkeypatch):
    reader = AsyncMock(return_value=destination())
    legacy = AsyncMock(side_effect=AssertionError("Social identity must not be consulted"))
    monkeypatch.setattr(discovery, "resolve_safebox_address", reader)
    monkeypatch.setattr(discovery, "legacy_json", legacy)
    result = asyncio.run(discovery.resolve_payment_recipient(ADDRESS, legacy_domains=("example.com",)))
    assert result["npub"] == Keys.hex_to_bech32(KEY, prefix="npub")
    assert result["discovery_source"] == "safebox-v1"
    legacy.assert_not_awaited()


@pytest.mark.parametrize("capability,unit,enabled", [(CASH,"sat",True), (CLEAR,"cmu-one",True), (CLEAR,"other",False)])
def test_capabilities_are_explicit(monkeypatch, capability, unit, enabled):
    monkeypatch.setattr(discovery, "resolve_safebox_address", AsyncMock(return_value=destination(clear=True, clear_units=["cmu-one"])))
    legacy = AsyncMock()
    monkeypatch.setattr(discovery, "legacy_json", legacy)
    result = asyncio.run(discovery.resolve_payment_recipient(ADDRESS, capability=capability, unit=unit, legacy_domains=("example.com",)))
    assert bool(result) == enabled
    legacy.assert_not_awaited()


@pytest.mark.parametrize("allowed", [True, False])
def test_only_explicit_legacy_domains_after_404(monkeypatch, allowed):
    monkeypatch.setattr(discovery, "resolve_safebox_address", AsyncMock(side_effect=SafeboxNotFound("404")))
    legacy = AsyncMock(return_value={"names":{"alice":KEY},"relays":{KEY:[RELAY]}})
    monkeypatch.setattr(discovery, "legacy_json", legacy)
    result = asyncio.run(discovery.resolve_payment_recipient(ADDRESS, legacy_domains=("example.com",) if allowed else ()))
    assert bool(result) == allowed
    assert legacy.await_count == int(allowed)
    if result:
        assert result["discovery_source"] == "legacy-nip05"


@pytest.mark.parametrize("reason", ["invalid schema", "unknown version", "timeout", "HTTP 410", "HTTP 429", "HTTP 503"])
def test_errors_never_downgrade(monkeypatch, reason):
    monkeypatch.setattr(discovery, "resolve_safebox_address", AsyncMock(side_effect=SafeboxDiscoveryError(reason)))
    legacy = AsyncMock()
    monkeypatch.setattr(discovery, "legacy_json", legacy)
    with pytest.raises(SafeboxDiscoveryError):
        asyncio.run(discovery.resolve_payment_recipient(ADDRESS, legacy_domains=("example.com",)))
    legacy.assert_not_awaited()


def test_legacy_clear_requires_descriptor_and_supports_unrestricted_units():
    payload = {"names":{"alice":KEY},"relays":{KEY:[RELAY]}}
    assert discovery.legacy_destination(payload, ADDRESS, CLEAR, "cmu-one") is None
    payload["clear"] = {"alice":{"protocols":[CLEAR],"transports":["nip59"],"kinds":[7379],"units":[]}}
    assert discovery.legacy_destination(payload, ADDRESS, CLEAR, "cmu-one").pubkey == KEY


def test_legacy_fetch_rejects_private_dns_before_http(monkeypatch):
    async def run():
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=[(2,1,6,"",("127.0.0.1",443))]))
        with pytest.raises(SafeboxDiscoveryError):
            await discovery.legacy_json(ADDRESS, 1)
    asyncio.run(run())
