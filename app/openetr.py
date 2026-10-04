"""Small, read-only OpenETR projection for Safebox record pages.

This adapter retrieves evidence and applies Core Record Ruleset 1.0. It does
not publish events or make recognition or legal-validity claims.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any, Iterable
from urllib.parse import urlsplit

from stroma import ClientPool, Event, Keys
from app.openetr_rules import evaluate_core_record, POSITION_LABELS


ANCHOR_KIND = 1415
CONTROL_KIND = 1416
PROFILE_KIND = 0
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
HEX_PUBKEY_PATTERN = re.compile(r"[0-9a-f]{64}")

ACTION_LABELS = {
    "notice": "Publisher notice",
    "issue": "Anchor recorded",
    "initiate": "Transfer initiated",
    "accept": "Transfer accepted",
    "terminate": "Control terminated",
    "attest": "Attestation recorded",
    "encumber": "Encumbrance recorded",
    "discharge": "Encumbrance discharged",
    "redeem": "Presented for redemption",
}


def derive_consequential_state(
    artifact_id: str,
    dcr_records: Iterable[Event],
) -> dict[str, Any]:
    """Evaluate supplied evidence under Core Record Ruleset 1.0."""
    return evaluate_core_record(artifact_id, dcr_records)


def build_digital_controllable_record(
    artifact_id: str,
    records: Iterable[Event],
) -> dict[str, Any]:
    """Build the inspectable DCR layer without asserting derived state.

    The Digital Artifact is the digest-identified content. This returned
    structure is the distinct signed evidence layer concerning that artifact.
    Ruleset evaluation is returned separately; no authority is inferred here.
    """

    normalized_artifact_id = str(artifact_id or "").strip().lower()
    if not SHA256_PATTERN.fullmatch(normalized_artifact_id):
        raise ValueError(
            "OpenETR artifact digest must be a 64-character SHA-256 value"
        )
    record_list = list(records)
    return {
        "status": "candidate",
        "artifact_id": normalized_artifact_id,
        "record_count": len(record_list),
        "record_event_ids": [record.id for record in record_list],
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
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, OverflowError, OSError):
        return "Outside displayable range"


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
        "action": action or "unknown",
        "action_label": ACTION_LABELS.get(
            action,
            "Unrecognized evidence event",
        ),
        "content": event.content or "",
        "notice_type": _tag_value(event, "notice_type"),
        "prior_event_id": _tag_value(event, "e"),
        # The legacy wire tag remains ``origin``; the application model uses
        # current Anchor Event terminology.
        "anchor_event_id": _tag_value(event, "origin"),
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


def build_signer_profile(
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
    """Adapt ruleset results to the existing record-page presentation."""
    event_list = list(events)
    result = evaluate_core_record(digest, event_list)
    normalized_digest = result["artifact"]["digest"]
    by_id = {}
    for event in event_list:
        # Prefer authenticated copies when duplicate claimed IDs occur.
        if event.id not in by_id or _event_is_valid(event):
            by_id[event.id] = event
    graphs = []
    for candidate in result["candidates"]:
        anchor = by_id[candidate["anchor_event_id"]]
        notices = [by_id[key] for key in candidate["notice_event_ids"]]
        graphs.append({
            "artifact": {"id": normalized_digest, "identity_method": "sha256"},
            "digital_controllable_record": build_digital_controllable_record(
                normalized_digest, [anchor, *notices]),
            "anchor": _event_view(anchor), "signer_profile": None,
            "signer_profile_error": None,
            "controls": [_event_view(event) for event in notices],
            "warnings": [],
            "consequential_state": candidate,
            "publisher_position_label": POSITION_LABELS[candidate["publisher_position"]],
            "recognition": {"status": "not_evaluated", "basis": None},
            "effect": {"status": "not_evaluated", "value": None, "purpose": None},
        })
    warnings = []
    if len(graphs) > 1:
        warnings.append(f"{len(graphs)} candidate Anchor Events were found. No candidate was selected as authoritative.")
    if result["non_qualifying_evidence"]:
        warnings.append("Some supplied evidence did not qualify under this ruleset. Inspect the findings below.")
    result["retrieval"]["sources"] = list(relays)
    return {
        "digest": normalized_digest,
        "relays": tuple(result["retrieval"]["sources"]),
        "candidate_graphs": graphs,
        "unlinked_events": [_event_view(by_id[key]) for key in sorted({
            item["event_id"] for item in result["non_qualifying_evidence"]})],
        "invalid_event_count": sum(f["code"] in {"invalid_event_id", "invalid_signature"}
                                   for f in result["findings"]),
        "warnings": warnings, "error": None, "verification": result,
    }


def unavailable_openetr_history(digest: str, relays: Iterable[str]) -> dict[str, Any]:
    """Represent retrieval failure without asserting absence of an anchor."""
    history = build_openetr_history(digest, [], relays)
    result = history["verification"]
    result["aggregate_state"] = "unverifiable"
    result["findings"] = [{"code": "retrieval_incomplete", "event_id": None}]
    result["retrieval"].update({"mode": "relay_discovery", "failed": True,
                               "ended_at": datetime.now(timezone.utc).isoformat()})
    history["error"] = "OpenETR verification is temporarily unavailable."
    return history


async def query_openetr_history(
    digest: str,
    relays: Iterable[str],
    *,
    timeout: float = 5.0,
    limit: int = 100,
) -> dict[str, Any]:
    """Retrieve bounded anchor/notice evidence and evaluate Core Record 1.0."""

    relay_list = [str(relay).strip() for relay in relays if str(relay).strip()]
    if not relay_list:
        raise ValueError("At least one OpenETR relay is required")
    normalized_digest = str(digest or "").strip().lower()
    if not SHA256_PATTERN.fullmatch(normalized_digest):
        raise ValueError("OpenETR artifact digest must be a 64-character SHA-256 value")

    event_filter = {
        "#o": [normalized_digest],
        "limit": limit,
    }
    retrieval_started = datetime.now(timezone.utc).isoformat()
    async with ClientPool(
        relay_list,
        query_timeout=timeout,
        timeout=timeout,
    ) as client:
        anchor_events = await client.query(
            {**event_filter, "kinds": [ANCHOR_KIND]},
            emulate_single=True,
            wait_connect=True,
            timeout=timeout,
        )
        related_events_unavailable = False
        try:
            control_events = await client.query(
                {**event_filter, "kinds": [CONTROL_KIND]},
                emulate_single=True,
                wait_connect=True,
                timeout=timeout,
            )
        except Exception:
            control_events = []
            related_events_unavailable = True
        history = build_openetr_history(
            normalized_digest,
            [*anchor_events, *control_events],
            relay_list,
        )
        candidate_graphs = history["candidate_graphs"]
        result = history["verification"]
        result["retrieval"].update({
            "mode": "relay_discovery",
            "started_at": retrieval_started,
            "ended_at": datetime.now(timezone.utc).isoformat(),
            "filters": [{**event_filter, "kinds": [ANCHOR_KIND]},
                        {**event_filter, "kinds": [CONTROL_KIND]}],
            "timeout_seconds": timeout,
            "limitations": ["Bounded queries; no pagination or guarantee of global completeness.",
                            "Relay pool results do not expose per-source completion or authentication failures."],
            "notice_query_failed": related_events_unavailable,
        })
        result["findings"].append({"code": "retrieval_incomplete", "event_id": None})
        if related_events_unavailable:
            history["warnings"].append(
                "Related events are temporarily unavailable; the anchor check completed."
            )
            for graph in candidate_graphs:
                graph["consequential_state"]["publisher_position"] = "unverifiable"
                graph["publisher_position_label"] = POSITION_LABELS["unverifiable"]
        if candidate_graphs:
            signer_pubkeys = sorted(
                {graph["anchor"]["author_hex"] for graph in candidate_graphs}
            )
            try:
                profile_events = await client.query(
                    {
                        "kinds": [PROFILE_KIND],
                        "authors": signer_pubkeys,
                        "limit": max(10, len(signer_pubkeys) * 3),
                    },
                    emulate_single=True,
                    wait_connect=True,
                    timeout=timeout,
                )
            except Exception:
                for graph in candidate_graphs:
                    graph["signer_profile_error"] = (
                        "Signer profile metadata is temporarily unavailable."
                    )
            else:
                for graph in candidate_graphs:
                    graph["signer_profile"] = build_signer_profile(
                        graph["anchor"]["author_hex"], profile_events
                    )

    return history
