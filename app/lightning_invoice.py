"""Obtain a validated Lightning invoice without invoking legacy ecash routing."""
import asyncio
import ipaddress
import json
import socket
import time
from urllib.parse import urlsplit

import bolt11
import httpx

async def public_json(url, params=None):
    """Pin a public DNS result to the TLS connection; never follow redirects."""
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.port not in (None, 443) or parsed.fragment):
        raise ValueError('Unsupported payment endpoint')
    hostname = parsed.hostname.encode('idna').decode()
    async with asyncio.timeout(12):
        addresses = await asyncio.get_running_loop().getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        ips = list(dict.fromkeys(row[4][0] for row in addresses))
        if not ips or any(not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast for ip in ips):
            raise ValueError('Payment endpoint must be public')
        target = httpx.URL(url).copy_with(host=ips[0])
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
            async with client.stream('GET', target, params=params, headers={'Host': hostname},
                                     extensions={'sni_hostname': hostname}) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 65536:
                        raise ValueError('Payment response too large')
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise ValueError('Invalid payment response')
                return data


async def _resolve_lightning_invoice(address, amount, comment=''):
    if address.count('@') != 1 or not 0 < int(amount):
        raise ValueError('Invalid Lightning address or amount')
    name, domain = address.strip().lower().split('@')
    # Address syntax is validated by the payment route; reject URL delimiters here too.
    import re
    if not re.fullmatch(r'[a-z0-9_.+-]+', name) or not re.fullmatch(r'[a-z0-9.-]+', domain):
        raise ValueError('Invalid Lightning address')
    metadata = await public_json(f'https://{domain}/.well-known/lnurlp/{name}')
    msat = int(amount) * 1000
    if (metadata.get('status') == 'ERROR' or metadata.get('tag') != 'payRequest'
            or not int(metadata.get('minSendable', 0)) <= msat <= int(metadata.get('maxSendable', 0))):
        raise ValueError('The recipient cannot accept this amount')
    callback = metadata.get('callback')
    if not isinstance(callback, str):
        raise ValueError('Missing Lightning invoice callback')
    params = dict(httpx.URL(callback).params)
    params['amount'] = str(msat)
    params.pop('comment', None)
    params.pop('safebox', None)
    limit = int(metadata.get('commentAllowed') or 0)
    if comment and limit > 0:
        if len(comment) > limit:
            raise ValueError('The recipient does not support a comment of this length')
        params['comment'] = comment
    result = await public_json(callback, params)
    invoice = result.get('pr')
    if result.get('status') == 'ERROR' or not isinstance(invoice, str) or len(invoice) > 10000:
        raise ValueError('Invalid Lightning invoice response')
    decoded = bolt11.decode(invoice)
    if decoded.currency != 'bc' or decoded.amount_msat != msat or decoded.date + decoded.expiry <= time.time():
        raise ValueError('Invoice network, amount or expiry does not match the request')
    return invoice


async def resolve_lightning_invoice(address, amount, comment=''):
    try:
        return await _resolve_lightning_invoice(address, amount, comment)
    except Exception:
        # Provider URLs, callback tokens and decoder internals are not UI messages.
        raise ValueError('The Lightning invoice could not be obtained or validated. No payment was submitted.') from None
