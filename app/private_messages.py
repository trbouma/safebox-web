"""Versioned DM presentation and bounded, public-only NIP-05 verification."""
import asyncio
from collections import OrderedDict
from ipaddress import ip_address
import json
import re
import socket
from time import monotonic
from urllib.parse import urlencode

import httpx

_cache = OrderedDict()


def encode_message(message: str, sender: str | None) -> str:
    return json.dumps({"type": "safebox.dm", "version": 1,
                       "sender": sender, "message": message}, ensure_ascii=False)


def parse_message(content: str) -> dict | None:
    """None denotes a payment; unsupported JSON remains literal text."""
    result = {"content": content, "claimed_sender": None, "verification": None}
    try:
        value = json.loads(content)
    except (ValueError, TypeError):
        return result
    if not isinstance(value, dict):
        return result
    if {"mint", "unit", "proofs"}.issubset(value):
        return None
    if (value.get("type") == "safebox.dm" and type(value.get("version")) is int
            and value["version"] == 1 and isinstance(value.get("message"), str)
            and (value.get("sender") is None or isinstance(value["sender"], str))):
        result["content"] = value["message"]
        result["claimed_sender"] = value.get("sender")
        result["verification"] = "unverified" if value.get("sender") else None
    return result


def _address_parts(address: str):
    if len(address) > 320 or address.count("@") != 1:
        raise ValueError("Invalid address")
    name, domain = address.split("@")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("Invalid name")
    domain = domain.encode("idna").decode("ascii").lower()
    if len(domain) > 253 or "." not in domain or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in domain.split(".")
    ):
        raise ValueError("Invalid domain")
    return name, domain


async def _fetch_identity(address: str) -> str | None:
    name, domain = _address_parts(address)
    resolved = await asyncio.get_running_loop().getaddrinfo(
        domain, 443, type=socket.SOCK_STREAM)
    addresses = [ip_address(row[4][0]) for row in resolved]
    if not addresses or any(not address.is_global for address in addresses):
        return None
    # Pin the checked IP while retaining TLS hostname verification. No second
    # DNS lookup, redirects, ambient proxies, or private-network requests.
    address_ip = addresses[0]
    host = f"[{address_ip}]" if address_ip.version == 6 else str(address_ip)
    url = f"https://{host}/.well-known/nostr.json?{urlencode({'name': name})}"
    async with httpx.AsyncClient(timeout=2, follow_redirects=False, trust_env=False) as client:
        async with client.stream("GET", url, headers={"Host": domain},
                                 extensions={"sni_hostname": domain}) as response:
            if response.status_code != 200:
                return None
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 65536:
                    return None
    value = json.loads(body)
    names = value.get("names") if isinstance(value, dict) else None
    pubkey = names.get(name) if isinstance(names, dict) else None
    return pubkey.lower() if isinstance(pubkey, str) and re.fullmatch(r"[0-9a-fA-F]{64}", pubkey) else None


async def resolve_identity(address: str) -> str | None:
    cached = _cache.get(address)
    if cached and cached[0] > monotonic():
        return cached[1]
    try:
        pubkey = await asyncio.wait_for(_fetch_identity(address), timeout=3)
    except Exception:
        pubkey = None
    _cache[address] = (monotonic() + (300 if pubkey else 30), pubkey)
    _cache.move_to_end(address)
    while len(_cache) > 256:
        _cache.popitem(last=False)
    return pubkey


async def present_messages(messages: list[dict]) -> list[dict]:
    presented = []
    for message in messages:
        parsed = parse_message(message["content"])
        if parsed is not None:
            presented.append({**message, **parsed})
    # At most ten distinct external lookups per page; others remain unverified.
    claims = list(dict.fromkeys(m["claimed_sender"] for m in presented if m["claimed_sender"]))[:10]
    keys = await asyncio.gather(*(resolve_identity(claim) for claim in claims))
    resolved = dict(zip(claims, keys))
    for message in presented:
        pubkey = resolved.get(message["claimed_sender"])
        if pubkey:
            message["verification"] = "verified" if pubkey == message["sender"].lower() else "mismatch"
    return presented
