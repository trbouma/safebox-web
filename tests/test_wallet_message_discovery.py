import asyncio
from unittest.mock import AsyncMock

import pytest
from stroma import Keys
from acorn.safebox_discovery import (
    WALLET_MESSAGE, SafeboxDiscoveryError, SafeboxNotFound, SafeboxHTTPError,
    build_safebox_document, parse_safebox_document,
)
from app import payment_discovery as directory
from app import private_messages as dm

ADDRESS = 'alice@example.com'
KEY = Keys(priv_k='01'*32).public_key_hex()
SOCIAL_KEY = Keys(priv_k='02'*32).public_key_hex()
RELAY = 'wss://wallet.example'


def wallet_entry(messaging=True):
    payload = build_safebox_document(ADDRESS, KEY, [RELAY])
    if messaging:
        payload['capabilities'][WALLET_MESSAGE] = {'transports':['nip17'],'kinds':[14]}
    return parse_safebox_document(payload, ADDRESS)


def test_wallet_and_social_keys_remain_independent(monkeypatch):
    monkeypatch.setattr(directory, 'resolve_safebox_address', AsyncMock(return_value=wallet_entry()))
    legacy = AsyncMock(side_effect=AssertionError('Do not consult social NIP-05'))
    monkeypatch.setattr(directory, 'legacy_json', legacy, raising=False)
    monkeypatch.setattr(dm, '_fetch_identity', AsyncMock(return_value=SOCIAL_KEY))
    dm._cache.clear()
    async def run():
        assert await dm.resolve_identity(ADDRESS) == SOCIAL_KEY
        recipient = await directory.resolve_wallet_message_recipient(ADDRESS, legacy_domains=('example.com',))
        assert recipient['pubkey'] == KEY and recipient['relay_hints'] == [RELAY]
        messages = await dm.present_messages([
            {'sender':KEY,'content':dm.encode_message('wallet',ADDRESS)},
            {'sender':SOCIAL_KEY,'content':dm.encode_message('not the wallet',ADDRESS)}], legacy_domains=('example.com',))
        assert [message['verification'] for message in messages] == ['verified','mismatch']
        assert await dm.resolve_identity(ADDRESS) == SOCIAL_KEY
    asyncio.run(run())
    legacy.assert_not_awaited()
    dm._cache.clear()


def test_missing_message_capability_never_falls_back(monkeypatch):
    monkeypatch.setattr(directory, 'resolve_safebox_address', AsyncMock(return_value=wallet_entry(False)))
    legacy = AsyncMock()
    monkeypatch.setattr(directory, 'legacy_json', legacy, raising=False)
    assert asyncio.run(directory.resolve_wallet_message_recipient(ADDRESS, legacy_domains=('example.com',))) is None
    legacy.assert_not_awaited()


@pytest.mark.parametrize('allowed', [False, True])
def test_404_compatibility_requires_explicit_domain(monkeypatch, allowed):
    monkeypatch.setattr(directory, 'resolve_safebox_address', AsyncMock(side_effect=SafeboxNotFound('404')))
    legacy = AsyncMock(return_value={'names':{'alice':KEY},'relays':{KEY:[RELAY]}})
    monkeypatch.setattr(directory, 'legacy_json', legacy, raising=False)
    result = asyncio.run(directory.resolve_wallet_message_recipient(ADDRESS, legacy_domains=('example.com',) if allowed else ()))
    assert result is None
    legacy.assert_not_awaited()


@pytest.mark.parametrize('error', [SafeboxHTTPError(410), SafeboxHTTPError(429), SafeboxHTTPError(503),
    SafeboxDiscoveryError('timeout'), SafeboxDiscoveryError('malformed or unknown version')])
def test_discovery_error_never_uses_legacy_key(monkeypatch, error):
    monkeypatch.setattr(directory, 'resolve_safebox_address', AsyncMock(side_effect=error))
    legacy = AsyncMock()
    monkeypatch.setattr(directory, 'legacy_json', legacy, raising=False)
    with pytest.raises(SafeboxDiscoveryError):
        asyncio.run(directory.resolve_wallet_message_recipient(ADDRESS, legacy_domains=('example.com',)))
    message = asyncio.run(dm.present_messages([{'sender':KEY,'content':dm.encode_message('still readable',ADDRESS)}], legacy_domains=('example.com',)))[0]
    assert message['verification'] == 'unverified' and message['content'] == 'still readable'
    legacy.assert_not_awaited()


def test_local_missing_claim_never_uses_external_directory(monkeypatch):
    external = AsyncMock()
    monkeypatch.setattr(dm, 'resolve_wallet_message_recipient', external)
    result = asyncio.run(dm.resolve_wallet_identity(ADDRESS, local_resolver=lambda address:(True,None)))
    assert result is None
    external.assert_not_awaited()


def test_wallet_attribution_lookup_limit(monkeypatch):
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(dm, 'resolve_wallet_message_recipient', lookup)
    messages = [{'sender':KEY,'content':dm.encode_message('hello',f'user{i}@example.com')} for i in range(15)]
    result = asyncio.run(dm.present_messages(messages))
    assert len(result) == 15 and lookup.await_count == 10
