"""Wallet discovery first; explicitly configured legacy domains only on 404."""
import asyncio
import ipaddress
import json
import socket

import httpx
from stroma import Keys
from acorn.safebox_discovery import (
    CASH, CLEAR, WALLET_MESSAGE, SafeboxDiscoveryError, SafeboxNotFound,
    normalize_safebox_address, parse_safebox_document, resolve_safebox_address,
)


async def legacy_json(address, timeout):
    name, domain = normalize_safebox_address(address).split('@')
    try:
        async with asyncio.timeout(timeout):
            answers = await asyncio.get_running_loop().getaddrinfo(domain, 443, type=socket.SOCK_STREAM)
            ips = list(dict.fromkeys(row[4][0] for row in answers))
            if not ips or any(not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast for ip in ips):
                raise ValueError('Public host required')
            url = httpx.URL(f'https://{domain}/.well-known/nostr.json').copy_with(host=ips[0])
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                async with client.stream('GET', url, params={'name':name}, headers={'Host':domain},
                                         extensions={'sni_hostname':domain}) as response:
                    if response.status_code == 404:
                        return None
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 65536:
                            raise ValueError('Too large')
                    def unique(pairs):
                        result = {}
                        for key, value in pairs:
                            if key in result:
                                raise ValueError('Duplicate JSON key')
                            result[key] = value
                        return result
                    return json.loads(body, object_pairs_hook=unique)
    except (ValueError, httpx.HTTPError, OSError, TimeoutError):
        raise SafeboxDiscoveryError('Legacy wallet directory unavailable or invalid') from None


def legacy_destination(payload, address, capability, unit=None):
    if payload is None:
        return None
    name = address.split('@')[0]
    if not isinstance(payload, dict) or not isinstance(payload.get('names'), dict):
        raise SafeboxDiscoveryError('Invalid legacy directory')
    pubkey = payload['names'].get(name)
    if pubkey is None:
        return None
    if not isinstance(pubkey, str):
        raise SafeboxDiscoveryError('Invalid legacy wallet key')
    relay_map = payload.get('relays', {})
    if not isinstance(relay_map, dict):
        raise SafeboxDiscoveryError('Invalid legacy relay hints')
    relays = relay_map.get(pubkey, [])
    if not relays:
        return None
    if capability == CLEAR:
        descriptors = payload.get('clear', {})
        if not isinstance(descriptors, dict):
            raise SafeboxDiscoveryError('Invalid legacy Clear descriptor')
        descriptor = descriptors.get(name)
        if not descriptor:
            return None
        if not isinstance(descriptor, dict):
            raise SafeboxDiscoveryError('Invalid legacy Clear descriptor')
        protocols = descriptor.get('protocols', [])
        if not isinstance(protocols, list):
            raise SafeboxDiscoveryError('Invalid legacy Clear protocols')
        if CLEAR not in protocols:
            return None
        descriptor = {k:v for k,v in descriptor.items() if k in ('transports','kinds','units')}
        # Older instances use [] to mean unrestricted units.
        if descriptor.get('units') == []:
            descriptor.pop('units')
    elif capability == WALLET_MESSAGE:
        descriptor = {'transports':['nip17'], 'kinds':[14]}
    else:
        descriptor = {'transports':['nip59'], 'kinds':[7378], 'units':['sat']}
    destination = parse_safebox_document({'version':1,'address':address,'pubkey':pubkey.lower(),
        'relays':relays,'capabilities':{capability:descriptor}}, address)
    return destination if destination.supports(capability, unit) else None


async def resolve_payment_recipient(address, *, capability=CASH, unit='sat', timeout=5,
                                    legacy_domains=()):
    address = normalize_safebox_address(address)
    source = 'safebox-v1'
    try:
        destination = await resolve_safebox_address(address, timeout=min(timeout, 5))
    except SafeboxNotFound:
        if address.split('@')[1] not in legacy_domains:
            return None
        destination = legacy_destination(await legacy_json(address, min(timeout, 5)), address, capability, unit)
        source = 'legacy-nip05'
    if destination is None or not destination.supports(capability, unit):
        return None
    return {'pubkey':destination.pubkey, 'npub':Keys.hex_to_bech32(destination.pubkey, prefix='npub'),
            'relay_hints':list(destination.relays), 'route_scope':'external',
            'discovery_source':source}


async def resolve_wallet_message_recipient(address, *, timeout=5, legacy_domains=()):
    """Resolve only wallet messaging; an absent capability never becomes social DM."""
    return await resolve_payment_recipient(address, capability=WALLET_MESSAGE, unit=None,
                                           timeout=timeout, legacy_domains=legacy_domains)
