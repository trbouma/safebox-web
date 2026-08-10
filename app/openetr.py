"""Small, read-only OpenETR projection for Safebox record pages.

This adapter deliberately implements only the wire-level query needed by the
initial UI.  It does not import OpenETR, publish events, or make recognition or
legal-validity claims.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Iterable

from monstr.client.client import ClientPool
from monstr.encrypt import Keys
from monstr.event.event import Event


ORIGIN_KIND = 1415
CONTROL_KIND = 1416
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

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

    return build_openetr_history(
        normalized_digest,
        [*origin_events, *control_events],
        relay_list,
    )
