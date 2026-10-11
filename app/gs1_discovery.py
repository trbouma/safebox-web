"""Bounded barcode acquisition and independent, per-relay GTIN discovery."""
from __future__ import annotations

import asyncio
import re

from stroma import ClientPool
from app.gs1 import ProductLink, parse_product_link
from app.openetr_rules import validate

GS = "\x1d"
QUALIFIERS = {"10": "lot", "17": "expiry", "21": "serial"}


def normalize_gtin(value: str) -> str:
    if not re.fullmatch(r"[0-9]{8}|[0-9]{12,14}", value):
        raise ValueError("GTIN must contain 8, 12, 13 or 14 ASCII digits.")
    value = value.zfill(14)
    total = sum(int(n) * (3 if i % 2 == 0 else 1) for i, n in enumerate(reversed(value[:-1])))
    if (10 - total % 10) % 10 != int(value[-1]):
        raise ValueError("GTIN check digit is invalid.")
    return value


def expand_upce(value: str) -> str:
    if not re.fullmatch(r"[01][0-9]{7}", value):
        raise ValueError("UPC-E requires an eight-digit decoder result including number system and check digit.")
    system, digits, check = value[0], value[1:7], value[7]
    last = digits[-1]
    if last in "012":
        body = digits[:2] + last + "0000" + digits[2:5]
    elif last == "3":
        body = digits[:3] + "00000" + digits[3:5]
    elif last == "4":
        body = digits[:4] + "00000" + digits[4]
    else:
        body = digits[:5] + "0000" + last
    return normalize_gtin(system + body + check)


def validate_expiry(value: str) -> None:
    if not re.fullmatch(r"[0-9]{6}", value):
        raise ValueError("Expiry must contain six digits in YYMMDD format.")
    year, month, day = int(value[:2]), int(value[2:4]), int(value[4:])
    days = [31, 29 if year % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if not 1 <= month <= 12 or day > days[month - 1]:
        raise ValueError("Invalid GS1 expiry month or day.")


def validate_qualifier(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9!\"%&'()*+,\-./:;<=>?_]{1,20}", value):
        raise ValueError("Lot and serial require 1–20 supported GS1 characters; spaces are not trimmed.")


def acquire_product(raw: str, mode: str = "auto", symbology: str = "") -> ProductLink | None:
    """Preserve raw input; GS1 element interpretation requires explicit metadata/mode."""
    if len(raw) > 2048:
        raise ValueError("Barcode payload is too long.")
    if mode not in {"auto", "gs1", "gtin"}:
        raise ValueError("Unsupported acquisition mode.")
    if symbology == "upc_e":
        return ProductLink(expand_upce(raw))
    if symbology in {"ean_8", "ean_13", "upc_a"}:
        required = {"ean_8": 8, "ean_13": 13, "upc_a": 12}[symbology]
        if len(raw) != required:
            raise ValueError("Barcode length does not match the decoded symbology.")
        return ProductLink(normalize_gtin(raw))
    link = parse_product_link(raw)
    if link is not None:
        return link
    if mode == "gtin":
        return ProductLink(normalize_gtin(raw))
    aim = raw[:3]
    if aim in {"]d2", "]C1", "]Q3"}:
        raw = raw[3:]
        mode = "gs1"
    if mode != "gs1":
        if re.fullmatch(r"[0-9]{12,14}", raw):
            return ProductLink(normalize_gtin(raw))
        if re.fullmatch(r"[0-9]{8}", raw):
            raise ValueError("Eight digits are ambiguous without symbology. Select GTIN mode for GTIN-8; UPC-E requires decoder metadata.")
        if symbology in {"data_matrix", "code_128"} or GS in raw or raw.startswith("(01)") or (raw.isascii() and raw.isdigit() and raw.startswith("01")):
            raise ValueError("Select GS1 element-string mode; GS1 metadata is unavailable. Preserve ASCII group separators between variable fields.")
        return None
    fields = {}
    # Parenthesized notation is accepted only in the explicitly selected mode.
    if raw.startswith("("):
        tokens = list(re.finditer(r"\(([0-9]{2,4})\)", raw))
        if not tokens or tokens[0].start() != 0:
            raise ValueError("Malformed parenthesized GS1 notation.")
        pairs = [(m.group(1), raw[m.end():tokens[i + 1].start() if i + 1 < len(tokens) else len(raw)]) for i, m in enumerate(tokens)]
    else:
        pairs = []
        # Some acquisition SDKs represent the initial FNC1 as ASCII GS.
        # Interpret this representation only after explicit GS1 classification.
        cursor = 1 if raw.startswith(GS) else 0
        while cursor < len(raw):
            ai = raw[cursor:cursor + 2]
            cursor += 2
            if ai in {"01", "17"}:
                size = 14 if ai == "01" else 6
                value = raw[cursor:cursor + size]
                if len(value) != size:
                    raise ValueError("Truncated fixed-length GS1 field.")
                cursor += size
            elif ai in {"10", "21"}:
                end = raw.find(GS, cursor)
                end = len(raw) if end < 0 else end
                value, cursor = raw[cursor:end], end
            else:
                raise ValueError("Unsupported GS1 AI or missing variable-field separator.")
            pairs.append((ai, value))
            if cursor < len(raw) and raw[cursor] == GS:
                cursor += 1
                if cursor == len(raw):
                    raise ValueError("Trailing GS1 separator has no following field.")
    for ai, value in pairs:
        if ai not in {"01", "10", "17", "21"}:
            raise ValueError("Only GS1 AIs 01, 10, 17 and 21 are supported.")
        if ai in fields:
            raise ValueError("Duplicate GS1 AI is ambiguous.")
        if ai == "01":
            if len(value) != 14:
                raise ValueError("AI 01 requires 14 digits.")
            normalize_gtin(value)
        elif ai == "17":
            validate_expiry(value)
        else:
            validate_qualifier(value)
        fields[ai] = value
    if "01" not in fields:
        raise ValueError("A GS1 product scan requires AI 01 (GTIN).")
    return ProductLink(fields["01"], fields.get("10"), fields.get("21"), expiry=fields.get("17"))


def association_fields(tags: list, *, indexed: bool = False) -> dict:
    result = {}
    for tag_name, field in (("g", "gtin"), ("gs1_gtin", "legacy_gtin"), ("gs1_lot", "lot"), ("gs1_expiry", "expiry"), ("gs1_serial", "serial")):
        entries = [tag for tag in tags if tag and tag[0] == tag_name]
        if not entries:
            continue
        if len(entries) != 1 or len(entries[0]) != 2 or not isinstance(entries[0][1], str) or not entries[0][1]:
            raise ValueError(f"Malformed or duplicate {tag_name} tag.")
        value = entries[0][1]
        if field in {"gtin", "legacy_gtin"}:
            if len(value) != 14 or normalize_gtin(value) != value:
                raise ValueError("Anchor GTIN is not canonical.")
        elif field == "expiry":
            validate_expiry(value)
        else:
            validate_qualifier(value)
        result[field] = value
    if indexed and "gtin" not in result:
        raise ValueError("Indexed discovery requires a canonical g tag.")
    if "gtin" in result and "legacy_gtin" in result and result["gtin"] != result["legacy_gtin"]:
        raise ValueError("Conflicting g and gs1_gtin aliases.")
    result["gtin"] = result.get("gtin", result.get("legacy_gtin"))
    if not result["gtin"]:
        raise ValueError("No product GTIN association.")
    return result


def match_fields(product: ProductLink, fields: dict, digest: str) -> tuple[str, list[str]]:
    missing, different, extra = [], [], []
    for name in ("gtin", "lot", "expiry", "serial"):
        expected, actual = getattr(product, name), fields.get(name)
        if expected is not None and actual is None:
            missing.append(name)
        elif expected is not None and actual != expected:
            different.append(name)
        elif expected is None and actual is not None:
            extra.append(name)
    if product.digest and product.digest != digest:
        different.append("digest")
    if different:
        return "Mismatch", ["Different: " + ", ".join(different)]
    if missing:
        general = not any(fields.get(n) for n in ("lot", "expiry", "serial"))
        return ("General product record — incomplete" if general else "Incomplete"), ["Missing: " + ", ".join(missing)]
    if extra:
        return "More-specific candidate", ["Not established by scan: " + ", ".join(extra)]
    return "Exact field match", []


def evaluate_candidate(product: ProductLink, event) -> dict:
    digest_tags = [tag for tag in event.tags if tag and tag[0] == "o"]
    digest = digest_tags[0][1] if len(digest_tags) == 1 and len(digest_tags[0]) >= 2 else ""
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Invalid artifact digest tag.")
    errors = validate(event, digest)
    if errors or event.kind != 1415:
        raise ValueError(", ".join(e["code"] for e in errors) or "Not an anchor.")
    fields = association_fields(event.tags, indexed=True)
    if fields["gtin"] != product.gtin:
        raise ValueError("Relay returned an unrelated GTIN.")
    status, reasons = match_fields(product, fields, digest)
    return {"id": event.id, "publisher": event.pub_key, "digest": digest,
            "fields": fields, "status": status, "reasons": reasons, "event": event.data()}


async def discover_product(product: ProductLink, relays: tuple[str, ...], *, timeout: float, limit: int, pages: int = 4) -> dict:
    async def query_relay(relay):
        found, rejected, seen = {}, [], set()
        coverage = {"relay": relay, "pages": 0, "limit": limit, "status": "page limit reached", "may_be_incomplete": True}
        until = None
        try:
            async with asyncio.timeout(timeout * pages + 1):
                async with ClientPool([relay], query_timeout=timeout, timeout=timeout) as client:
                    for _ in range(pages):
                        query = {"kinds": [1415], "#g": [product.gtin], "limit": limit}
                        if until is not None:
                            query["until"] = until
                        batch = await client.query(query, emulate_single=True, wait_connect=True, timeout=timeout)
                        coverage["pages"] += 1
                        new = False
                        times = []
                        for event in batch:
                            try:
                                candidate = evaluate_candidate(product, event)
                                found[candidate["id"]] = candidate
                            except Exception as exc:
                                rejected.append({"id": getattr(event, "id", "unknown"), "reason": str(exc)})
                            event_id = event.id
                            new |= event_id not in seen
                            seen.add(event_id)
                            timestamp = event.data().get("created_at")
                            if isinstance(timestamp, int) and timestamp >= 0:
                                times.append(timestamp)
                        if len(batch) < limit:
                            coverage["status"] = "Stored response exhausted; retention and global completeness unknown"
                            break
                        oldest = min(times) if times else None
                        if oldest is None or (until is not None and oldest >= until) or not new:
                            coverage["status"] = "Timestamp bucket saturated; pagination cannot safely advance"
                            break
                        until = oldest  # Inclusive overlap: never skip a shared-timestamp bucket.
        except Exception:
            coverage["status"] = "Relay query unavailable or timed out"
        return found, rejected, coverage

    results = await asyncio.gather(*(query_relay(r) for r in dict.fromkeys(relays)))
    candidates, rejected, coverage = {}, [], []
    for found, invalid, source in results:
        candidates.update(found)
        rejected.extend(invalid)
        coverage.append(source)
    return {"candidates": sorted(candidates.values(), key=lambda c: c["id"]),
            "rejected": rejected, "coverage": coverage, "may_be_incomplete": True}
