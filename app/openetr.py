"""Small, read-only OpenETR projection for Safebox record pages.

This adapter deliberately implements only the wire-level query needed by the
initial UI.  It does not import OpenETR, publish events, or make recognition or
legal-validity claims.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any, Iterable
from urllib.parse import urlsplit

from monstr.client.client import ClientPool
from monstr.encrypt import Keys
from monstr.event.event import Event


ORIGIN_KIND = 1415
CONTROL_KIND = 1416
PROFILE_KIND = 0
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
HEX_PUBKEY_PATTERN = re.compile(r"[0-9a-f]{64}")

ACTION_LABELS = {
    "issue": "Origin issued",
    "initiate": "Transfer initiated",
    "accept": "Transfer accepted",
    "terminate": "Control terminated",
    "attest": "Attestation recorded",
    "encumber": "Encumbrance recorded",
    "discharge": "Encumbrance discharged",
    "redeem": "Presented for redemption",
}


def _tag_value(event: Event, name: str) -> str | None:
    for tag in event.tags or []:
        if len(tag) >= 2 and tag[0] == name:
            return str(tag[1])
    return None


def _npub(pubkey_hex: str) -> str:
    try:
        return Keys.hex_to_bech32(pubkey_hex, prefix="npub")
    except Exception:
        return pubkey_hex


def _timestamp(value: Any) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp())
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _display_time(value: Any) -> str:
    seconds = _timestamp(value)
    if not seconds:
        return "Unknown"
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _event_is_valid(event: Event) -> bool:
    try:
        return bool(event.is_valid())
    except Exception:
        return False


def _event_view(event: Event) -> dict[str, Any]:
    action = (_tag_value(event, "action") or "").strip().lower()
    return {
        "id": event.id,
        "author": _npub(event.pub_key),
        "author_hex": event.pub_key,
        "created_at": _display_time(event.created_at),
        "kind": event.kind,
        "action": action or ("issue" if event.kind == ORIGIN_KIND else "unknown"),
        "action_label": ACTION_LABELS.get(
            action,
            "Origin issued" if event.kind == ORIGIN_KIND else "Control event",
        ),
        "content": event.content or "",
        "prior_event_id": _tag_value(event, "e"),
        "origin_event_id": _tag_value(event, "origin"),
        "participant": (
            _npub(_tag_value(event, "p")) if _tag_value(event, "p") else None
        ),
    }


def _profile_text(value: Any, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.strip().split())
    if not cleaned:
        return None
    return cleaned[:limit]


def _profile_url(value: Any) -> str | None:
    candidate = _profile_text(value, limit=2048)
    if candidate is None:
        return None
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    return candidate


def build_issuer_profile(
    pubkey_hex: str,
    events: Iterable[Event],
) -> dict[str, Any] | None:
    """Return the latest valid, well-formed kind-0 profile for one signer."""

    normalized_pubkey = str(pubkey_hex or "").strip().lower()
    if not HEX_PUBKEY_PATTERN.fullmatch(normalized_pubkey):
        return None
    candidates = [
        event
        for event in events
        if event.kind == PROFILE_KIND
        and str(event.pub_key or "").lower() == normalized_pubkey
        and _event_is_valid(event)
    ]
    if not candidates:
        return None
    latest = max(candidates, key=lambda item: (_timestamp(item.created_at), item.id))
    try:
        payload = json.loads(latest.content or "{}")
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None

    name = _profile_text(payload.get("name"), limit=100)
    display_name = _profile_text(
        payload.get("display_name") or payload.get("displayName"),
        limit=100,
    )
    return {
        "event_id": latest.id,
        "author": _npub(normalized_pubkey),
        "created_at": _display_time(latest.created_at),
        "display_name": display_name,
        "name": name if name != display_name else None,
        "about": _profile_text(payload.get("about"), limit=500),
        "nip05": _profile_text(payload.get("nip05"), limit=254),
        "lightning_address": _profile_text(payload.get("lud16"), limit=254),
        "website": _profile_url(payload.get("website")),
        "picture": _profile_url(payload.get("picture")),
    }


def build_openetr_history(
    digest: str,
    events: Iterable[Event],
    relays: Iterable[str],
) -> dict[str, Any]:
    """Build a conservative view of signed events linked to one origin."""

    normalized_digest = str(digest or "").strip().lower()
    if not SHA256_PATTERN.fullmatch(normalized_digest):
        raise ValueError("OpenETR object digest must be a 64-character SHA-256 value")

    matching: list[Event] = []
    invalid_count = 0
    seen_ids: set[str] = set()
    for event in events:
        if event.id in seen_ids or event.kind not in {ORIGIN_KIND, CONTROL_KIND}:
            continue
        if (_tag_value(event, "o") or "").lower() != normalized_digest:
            continue
        seen_ids.add(event.id)
        if not _event_is_valid(event):
            invalid_count += 1
            continue
        matching.append(event)

    matching.sort(key=lambda item: (_timestamp(item.created_at), item.id))
    origins = [item for item in matching if item.kind == ORIGIN_KIND]
    controls = [item for item in matching if item.kind == CONTROL_KIND]
    selected_origin = origins[0] if origins else None

    related_controls: list[Event] = []
    orphan_controls: list[Event] = []
    if selected_origin is not None:
        chain_ids = {selected_origin.id}
        remaining = list(controls)
        while remaining:
            progressed = False
            for event in list(remaining):
                prior_id = _tag_value(event, "e")
                origin_id = _tag_value(event, "origin")
                if prior_id in chain_ids and origin_id in {None, selected_origin.id}:
                    related_controls.append(event)
                    chain_ids.add(event.id)
                    remaining.remove(event)
                    progressed = True
            if not progressed:
                orphan_controls.extend(remaining)
                break
    else:
        orphan_controls = controls

    warnings: list[str] = []
    if len(origins) > 1:
        warnings.append(
            f"{len(origins)} origin events were found; the earliest signed origin is shown."
        )
    if orphan_controls:
        warnings.append(
            f"{len(orphan_controls)} control event(s) did not form a complete chain from the shown origin."
        )
    if invalid_count:
        warnings.append(
            f"{invalid_count} event(s) failed cryptographic validation and were excluded."
        )

    return {
        "digest": normalized_digest,
        "relays": tuple(relays),
        "origin": _event_view(selected_origin) if selected_origin else None,
        "controls": [_event_view(item) for item in related_controls],
        "origin_count": len(origins),
        "warnings": warnings,
        "error": None,
        "issuer_profile": None,
        "issuer_profile_error": None,
    }


async def query_openetr_history(
    digest: str,
    relays: Iterable[str],
    *,
    timeout: float = 5.0,
    limit: int = 100,
) -> dict[str, Any]:
    """Query current OpenETR origin and control kinds from configured relays."""

    relay_list = [str(relay).strip() for relay in relays if str(relay).strip()]
    if not relay_list:
        raise ValueError("At least one OpenETR relay is required")
    normalized_digest = str(digest or "").strip().lower()
    if not SHA256_PATTERN.fullmatch(normalized_digest):
        raise ValueError("OpenETR object digest must be a 64-character SHA-256 value")

    event_filter = {
        "#o": [normalized_digest],
        "limit": limit,
    }
    async with ClientPool(
        relay_list,
        query_timeout=timeout,
        timeout=timeout,
    ) as client:
        origin_events = await client.query(
            {**event_filter, "kinds": [ORIGIN_KIND]},
            emulate_single=True,
            wait_connect=True,
            timeout=timeout,
        )
        control_events = await client.query(
            {**event_filter, "kinds": [CONTROL_KIND]},
            emulate_single=True,
            wait_connect=True,
            timeout=timeout,
        )
        history = build_openetr_history(
            normalized_digest,
            [*origin_events, *control_events],
            relay_list,
        )
        if history["origin"] is not None:
            try:
                profile_events = await client.query(
                    {
                        "kinds": [PROFILE_KIND],
                        "authors": [history["origin"]["author_hex"]],
                        "limit": 10,
                    },
                    emulate_single=True,
                    wait_connect=True,
                    timeout=timeout,
                )
            except Exception:
                history["issuer_profile_error"] = (
                    "Issuer profile metadata is temporarily unavailable."
                )
            else:
                history["issuer_profile"] = build_issuer_profile(
                    history["origin"]["author_hex"],
                    profile_events,
                )

    return history
