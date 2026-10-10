"""Bounded GS1/OpenETR Digital Links and configured-source artifact retrieval.

This is the OpenQR-style /01, /10, /21 subset, not a general GS1 resolver.
Nothing in a scanned URL or an event can select a network destination here.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import hashlib
import json
import re
from urllib.parse import parse_qsl, unquote, urlsplit

import httpx


@dataclass(frozen=True)
class ProductLink:
    gtin: str
    lot: str | None = None
    serial: str | None = None
    digest: str | None = None


def parse_product_link(value: str) -> ProductLink | None:
    """Return None for non-product codes; reject malformed product candidates."""
    value = value.strip()
    try:
        url = urlsplit(value)
    except ValueError:
        return None
    if url.scheme not in {"http", "https"} or not (
        url.path == "/01" or url.path.startswith("/01/")
    ):
        return None
    try:
        port = url.port
    except ValueError as exc:
        raise ValueError("Invalid GS1 Digital Link port.") from exc
    if (len(value) > 2048 or not url.hostname or url.username is not None
            or url.password is not None or url.fragment or "\\" in value
            or (port is not None and not 1 <= port <= 65535)
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
            or re.search(r"%(?![0-9a-fA-F]{2})", value)):
        raise ValueError("Invalid GS1 Digital Link URL.")
    try:
        parts = [unquote(part, errors="strict") for part in url.path.split("/")[1:]]
        params = parse_qsl(url.query, keep_blank_values=True, errors="strict", max_num_fields=10)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid GS1 Digital Link encoding.") from exc
    if len(parts) not in {2, 4, 6} or parts[0] != "01":
        raise ValueError("Use /01/{GTIN}, optionally followed by /10/{lot} and /21/{serial}.")
    gtin = parts[1]
    if not re.fullmatch(r"[0-9]{14}", gtin):
        raise ValueError("Digital Link GTIN must contain 14 digits.")
    total = sum(int(n) * (3 if i % 2 == 0 else 1) for i, n in enumerate(reversed(gtin[:-1])))
    if (10 - total % 10) % 10 != int(gtin[-1]):
        raise ValueError("GTIN check digit is invalid.")
    if parts[2::2] not in ([], ["10"], ["21"], ["10", "21"]):
        raise ValueError("Unsupported or out-of-order GS1 qualifiers; only lot (10) and serial (21) are supported.")
    fields = dict(zip(parts[2::2], parts[3::2]))
    for field in fields.values():
        if (not re.fullmatch(r"[A-Za-z0-9!\"%&'()*+,\-.:;<=>?_]{1,20}", field)
                or field in {".", ".."}):
            raise ValueError("Lot and serial must contain 1–20 supported GS1 characters, without spaces or slashes.")
    digest = None
    if params:
        if len(params) != 1 or params[0][0] not in {"d", "digest"}:
            raise ValueError("Use at most one d or digest parameter, with no other query parameters.")
        reference = params[0][1]
        if re.fullmatch(r"[0-9a-f]{64}", reference):
            digest = reference
        elif re.fullmatch(r"[A-Za-z0-9_-]{43}", reference):
            raw = base64.urlsafe_b64decode(reference + "=")
            if base64.urlsafe_b64encode(raw).decode().rstrip("=") != reference:
                raise ValueError("The digest must use canonical unpadded Base64URL encoding.")
            digest = raw.hex()
        else:
            raise ValueError("The digest must be 64 lowercase hex or 43 unpadded Base64URL characters.")
    return ProductLink(gtin, fields.get("10"), fields.get("21"), digest)


def product_associations(product: ProductLink, history: dict) -> list[dict]:
    """Compare identifiers only on anchors already accepted by the core ruleset."""
    evidence = {}
    for event in history["verification"]["evidence"]:
        # Rejected evidence can reuse a qualifying event's claimed ID. Bind
        # these fields to the accepted ID, rather than allowing last-write wins.
        serialized = json.dumps([0, event["pubkey"], event["created_at"],
                                 event["kind"], event["tags"], event["content"]],
                                ensure_ascii=False, separators=(",", ":"))
        if hashlib.sha256(serialized.encode()).hexdigest() == event["id"]:
            evidence[event["id"]] = event
    results = []
    for graph in history["candidate_graphs"]:
        event = evidence[graph["anchor"]["id"]]
        comparisons = []
        for name, expected in (("gs1_gtin", product.gtin), ("gs1_lot", product.lot), ("gs1_serial", product.serial)):
            tags = [tag for tag in event.get("tags", []) if tag and tag[0] == name]
            values = [tag[1] for tag in tags if len(tag) >= 2]
            matches = (len(tags) == 1 and values == [expected]) if expected is not None else not tags
            comparisons.append({"name": name, "expected": expected, "values": values, "matches": matches})
        results.append({"event_id": event["id"], "matches": all(c["matches"] for c in comparisons), "fields": comparisons})
    return results


async def retrieve_product_artifact(digest: str, servers: tuple[str, ...], *,
                                    timeout: float, max_bytes: int) -> dict:
    """Bounded exact-byte downloads, no redirects, credentials, cookies or hints."""
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid artifact digest")
    attempts = []
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
        for server in dict.fromkeys(servers):
            client.cookies.clear()
            status = "unavailable"
            try:
                base = urlsplit(server)
                if (base.scheme not in {"http", "https"} or not base.hostname
                        or base.username is not None or base.password is not None
                        or base.query or base.fragment or "\\" in server):
                    raise ValueError("Invalid configured Blossom server")
                # Timeout covers the whole response, not only idle read intervals.
                async with asyncio.timeout(timeout):
                    async with client.stream("GET", server.rstrip("/") + "/" + digest,
                                             headers={"Accept-Encoding": "identity"}) as response:
                        if response.status_code == 404:
                            status = "not_found"
                        elif response.status_code == 200:
                            if response.headers.get("content-encoding", "identity").lower() != "identity":
                                status = "unsupported_encoding"
                            elif int(response.headers.get("content-length", "0")) > max_bytes:
                                status = "too_large"
                            else:
                                content = bytearray()
                                async for chunk in response.aiter_raw():
                                    if len(content) + len(chunk) > max_bytes:
                                        status = "too_large"
                                        break
                                    content.extend(chunk)
                                else:
                                    if hashlib.sha256(content).hexdigest() == digest:
                                        return {"status": "verified", "data": bytes(content), "server": server,
                                                "size": len(content), "attempts": attempts + [{"server": server, "status": "verified"}]}
                                    status = "digest_mismatch"
            except (httpx.HTTPError, TimeoutError, ValueError):
                status = "unavailable"
            attempts.append({"server": server, "status": status})
    return {"status": "not_found" if attempts and all(a["status"] == "not_found" for a in attempts) else "unavailable",
            "data": None, "attempts": attempts}
