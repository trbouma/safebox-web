"""NIP-57 validation and receipt construction for the provider boundary."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
from urllib.parse import urlsplit

from monstr.event.event import Event


MAX_ZAP_REQUEST_BYTES = 65_536
MAX_ZAP_COMMENT_CHARS = 1_000
MAX_ZAP_RECEIPT_RELAYS = 10


@dataclass(frozen=True)
class ValidatedZapRequest:
    raw: str
    event_id: str
    sender_pubkey: str
    recipient_pubkey: str
    content: str
    relays: tuple[str, ...]


def _tag_values(tags: list[list[str]], name: str) -> list[list[str]]:
    return [tag for tag in tags if tag and tag[0] == name]


def _one_or_none(tags: list[list[str]], name: str) -> list[str] | None:
    matches = _tag_values(tags, name)
    if len(matches) > 1:
        raise ValueError(f"Zap request must contain at most one {name} tag")
    return matches[0] if matches else None


def _hex_pubkey(value: object, *, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64:
        raise ValueError(f"{label} must be a 32-byte lowercase hex public key")
    try:
        bytes.fromhex(normalized)
    except ValueError as exc:
        raise ValueError(f"{label} must be a 32-byte lowercase hex public key") from exc
    return normalized


def _receipt_relay(value: object) -> str:
    relay = str(value or "").strip()
    parsed = urlsplit(relay)
    if (
        parsed.scheme != "wss"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Zap receipt relays must be absolute public wss:// URLs")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise ValueError("Zap receipt relays must not target localhost")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if "." not in hostname:
            raise ValueError("Zap receipt relays must use a public hostname")
    else:
        if not address.is_global:
            raise ValueError("Zap receipt relays must not target private IP addresses")
    return relay


def validate_zap_request(
    raw: str,
    *,
    amount_msat: int,
    provider_pubkey: str,
    expected_lnurl: str,
) -> ValidatedZapRequest:
    """Validate the signed kind-9734 request before accepting an obligation."""

    request_raw = str(raw or "")
    if not request_raw.strip() or len(request_raw.encode("utf-8")) > MAX_ZAP_REQUEST_BYTES:
        raise ValueError("Zap request is empty or too large")
    try:
        payload = json.loads(request_raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Zap request is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Zap request must be a JSON event object")
    try:
        event = Event().load(payload)
    except Exception as exc:
        raise ValueError("Zap request is not a valid Nostr event") from exc
    if event.kind != 9734 or not event.is_valid():
        raise ValueError("Zap request must be a valid signed kind-9734 event")

    content = str(event.content or "")
    if len(content) > MAX_ZAP_COMMENT_CHARS:
        raise ValueError("Zap request comment is too long")
    tags = list(event.tags)
    if not tags:
        raise ValueError("Zap request must contain tags")

    p_tags = _tag_values(tags, "p")
    if len(p_tags) != 1 or len(p_tags[0]) < 2:
        raise ValueError("Zap request must contain exactly one p tag")
    requested_recipient = _hex_pubkey(p_tags[0][1], label="Zap recipient")

    amount_tags = _tag_values(tags, "amount")
    if len(amount_tags) > 1:
        raise ValueError("Zap request must contain at most one amount tag")
    if amount_tags:
        try:
            tagged_amount = int(amount_tags[0][1])
        except (IndexError, TypeError, ValueError) as exc:
            raise ValueError("Zap request amount tag is invalid") from exc
        if tagged_amount != amount_msat:
            raise ValueError("Zap request amount does not match the callback amount")

    lnurl_tag = _one_or_none(tags, "lnurl")
    if lnurl_tag is not None:
        if len(lnurl_tag) < 2 or str(lnurl_tag[1]).lower() != expected_lnurl.lower():
            raise ValueError("Zap request lnurl does not match this Lightning address")

    provider_tag = _one_or_none(tags, "P")
    if provider_tag is not None:
        if len(provider_tag) < 2 or _hex_pubkey(
            provider_tag[1], label="Zap provider"
        ) != _hex_pubkey(provider_pubkey, label="Service provider"):
            raise ValueError("Zap request P tag does not match this provider")

    for optional_tag in ("e", "a"):
        _one_or_none(tags, optional_tag)
    _one_or_none(tags, "k")

    relay_tags = _tag_values(tags, "relays")
    if len(relay_tags) != 1 or len(relay_tags[0]) < 2:
        raise ValueError("Zap request must contain one non-empty relays tag")
    if len(relay_tags[0][1:]) > MAX_ZAP_RECEIPT_RELAYS:
        raise ValueError("Zap request contains too many receipt relays")
    relays = tuple(dict.fromkeys(_receipt_relay(value) for value in relay_tags[0][1:]))
    if not relays:
        raise ValueError("Zap request does not contain a usable receipt relay")

    return ValidatedZapRequest(
        raw=request_raw,
        event_id=str(event.id),
        sender_pubkey=_hex_pubkey(event.pub_key, label="Zap sender"),
        recipient_pubkey=requested_recipient,
        content=content,
        relays=relays,
    )


def build_zap_receipt(*, zap_request_json: str, invoice: str, acorn) -> Event:
    """Build and sign a kind-9735 receipt for a settled provider invoice."""

    request = Event().load(json.loads(zap_request_json))
    copied_tags: list[list[str]] = []
    for name in ("p", "e", "a", "k"):
        for tag in _tag_values(list(request.tags), name):
            copied_tags.append([str(value) for value in tag])
    copied_tags.append(["P", str(request.pub_key)])
    copied_tags.extend(
        [
            ["bolt11", str(invoice)],
            ["description", zap_request_json],
        ]
    )
    receipt = Event(
        kind=9735,
        content="",
        tags=copied_tags,
        pub_key=acorn.pubkey_hex,
    )
    receipt.sign(acorn.privkey_hex)
    return receipt
