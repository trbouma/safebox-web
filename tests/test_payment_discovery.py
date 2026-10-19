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
    monkeypatch.setattr(discovery, "legacy_json", legacy, raising=False)
    result = asyncio.run(discovery.resolve_payment_recipient(ADDRESS, legacy_domains=("example.com",)))
    assert result["npub"] == Keys.hex_to_bech32(KEY, prefix="npub")
    assert result["discovery_source"] == "safebox-v1"
    legacy.assert_not_awaited()


@pytest.mark.parametrize("capability,unit,enabled", [(CASH,"sat",True), (CLEAR,"cmu-one",True), (CLEAR,"other",False)])
def test_capabilities_are_explicit(monkeypatch, capability, unit, enabled):
    monkeypatch.setattr(discovery, "resolve_safebox_address", AsyncMock(return_value=destination(clear=True, clear_units=["cmu-one"])))
    legacy = AsyncMock()
    monkeypatch.setattr(discovery, "legacy_json", legacy, raising=False)
    result = asyncio.run(discovery.resolve_payment_recipient(ADDRESS, capability=capability, unit=unit, legacy_domains=("example.com",)))
    assert bool(result) == enabled
    legacy.assert_not_awaited()


@pytest.mark.parametrize("allowed", [True, False])
def test_only_explicit_legacy_domains_after_404(monkeypatch, allowed):
    monkeypatch.setattr(discovery, "resolve_safebox_address", AsyncMock(side_effect=SafeboxNotFound("404")))
    legacy = AsyncMock(return_value={"names":{"alice":KEY},"relays":{KEY:[RELAY]}})
    monkeypatch.setattr(discovery, "legacy_json", legacy, raising=False)
    result = asyncio.run(discovery.resolve_payment_recipient(ADDRESS, legacy_domains=("example.com",) if allowed else ()))
    assert result is None
    legacy.assert_not_awaited()


@pytest.mark.parametrize("reason", ["invalid schema", "unknown version", "timeout", "HTTP 410", "HTTP 429", "HTTP 503"])
def test_errors_never_downgrade(monkeypatch, reason):
    monkeypatch.setattr(discovery, "resolve_safebox_address", AsyncMock(side_effect=SafeboxDiscoveryError(reason)))
    legacy = AsyncMock()
    monkeypatch.setattr(discovery, "legacy_json", legacy, raising=False)
    with pytest.raises(SafeboxDiscoveryError):
        asyncio.run(discovery.resolve_payment_recipient(ADDRESS, legacy_domains=("example.com",)))
    legacy.assert_not_awaited()
