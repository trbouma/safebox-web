"""Wallet discovery never resolves social NIP-05 identities."""
from stroma import Keys
from acorn.safebox_discovery import (
    CASH, CLEAR, WALLET_MESSAGE, SafeboxNotFound,
    normalize_safebox_address, resolve_safebox_address,
)


async def resolve_payment_recipient(address, *, capability=CASH, unit='sat', timeout=5,
                                    legacy_domains=()):
    # legacy_domains is retained as an ignored argument for older callers.
    address = normalize_safebox_address(address)
    source = 'safebox-v1'
    try:
        destination = await resolve_safebox_address(address, timeout=min(timeout, 5))
    except SafeboxNotFound:
        return None  # Never reinterpret a social key as a wallet, even on old configurations.
    if destination is None or not destination.supports(capability, unit):
        return None
    return {'pubkey':destination.pubkey, 'npub':Keys.hex_to_bech32(destination.pubkey, prefix='npub'),
            'relay_hints':list(destination.relays), 'route_scope':'external',
            'discovery_source':source}


async def resolve_wallet_message_recipient(address, *, timeout=5, legacy_domains=()):
    """Resolve only wallet messaging; an absent capability never becomes social DM."""
    return await resolve_payment_recipient(address, capability=WALLET_MESSAGE, unit=None,
                                           timeout=timeout, legacy_domains=legacy_domains)
