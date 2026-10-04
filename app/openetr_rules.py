"""Deterministic, read-only evaluation of OpenETR Core Record Ruleset 1.0.

Retrieval and recognition are deliberately outside this evaluator. Results
describe only the supplied evidence, never global completeness or authority.
"""

from __future__ import annotations

import re
import json
from typing import Any, Iterable

RULESET = {"id": "openetr:core-record:1.0", "version": "1.0"}
HEX64 = re.compile(r"[0-9a-f]{64}")
NOTICE_TYPES = {
    "information", "caution", "do_not_use", "withdrawn", "superseded",
    "corrected", "other",
}
POSITION_LABELS = {
    "no_notice_found": "No qualifying publisher notice found in the retrieved evidence.",
    "information": "The publisher supplies additional information.",
    "caution": "The publisher advises caution when using this record.",
    "do_not_use": "The publisher states that this record should not be used.",
    "withdrawn": "The publisher has withdrawn its prior assertion or support.",
    "superseded": "The publisher states that this record has been superseded.",
    "corrected": "The publisher states that this record is subject to correction.",
    "other": "The publisher has supplied another statement; inspect the notice.",
    "unsupported_notice_type": "The publisher's notice type is not supported by this ruleset.",
    "conflicting_notices": "Conflicting publisher notice branches were found; no unique current position can be determined.",
    "incomplete_notice_chain": "A publisher notice chain is incomplete; the publisher's position cannot be fully determined.",
    "unverifiable": "Publisher notices could not be checked. This does not mean no notice exists.",
}


def tag(event: Any, name: str) -> str | None:
    values = [t for t in event.tags or [] if t and t[0] == name]
    if len(values) != 1 or len(values[0]) < 2:
        return None
    value = values[0][1]
    return value if isinstance(value, str) else None


def finding(code: str, event_id: str | None = None) -> dict[str, Any]:
    return {"code": code, "event_id": event_id}


def validate(event: Any, digest: str) -> list[dict[str, Any]]:
    codes = []
    try:
        if event.calculate_id() != event.id:
            codes.append("invalid_event_id")
        elif not event.is_valid():
            codes.append("invalid_signature")
    except Exception:
        codes.append("verification_unavailable")
    if tag(event, "o") != digest:
        codes.append("artifact_mismatch")
    if event.kind == 1415:
        if tag(event, "action") != "issue":
            codes.append("invalid_anchor_action")
    elif event.kind == 1416:
        if tag(event, "action") != "notice":
            codes.append("invalid_notice_action")
        if not HEX64.fullmatch(tag(event, "e") or ""):
            codes.append("invalid_notice_parent")
        if not tag(event, "notice_type"):
            codes.append("invalid_notice_type")
    else:
        codes.append("unsupported_event_kind")
    return [finding(code, event.id) for code in codes]


def evaluate_core_record(
    digest: str, events: Iterable[Any], *, digest_source: str = "supplied",
) -> dict[str, Any]:
    digest = str(digest).strip().lower()
    if not HEX64.fullmatch(digest):
        raise ValueError("OpenETR artifact digest must be a 64-character SHA-256 value")
    if digest_source not in {"computed", "supplied"}:
        raise ValueError("Unknown artifact digest source")
    # Validate before deduplication: a corrupt copy must not hide a valid copy
    # carrying the same claimed identifier. Preserve all supplied raw copies.
    supplied = sorted(events, key=lambda e: json.dumps(e.data(), sort_keys=True))
    valid = {}
    rejected = []
    findings = [finding("timestamp_not_independently_verified")]
    for event in supplied:
        errors = validate(event, digest)
        if errors:
            rejected.append({"event_id": event.id, "findings": errors})
            findings.extend(errors)
        else:
            valid[event.id] = event
    anchors = {key: e for key, e in valid.items() if e.kind == 1415}
    notices = {key: e for key, e in valid.items() if e.kind == 1416}
    all_ids = {e.id for e in supplied}
    roots = {}
    unresolved = []
    for key, notice in sorted(notices.items()):
        path = set()
        current = notice
        code = None
        while current.kind == 1416:
            if current.id in path:
                code = "notice_cycle"
                break
            path.add(current.id)
            parent = tag(current, "e")
            if parent not in valid:
                code = "notice_parent_invalid" if parent in all_ids else "notice_parent_missing"
                break
            current = valid[parent]
        if code is None:
            if any(notices[item].pub_key != current.pub_key for item in path):
                code = "notice_signer_mismatch"
            else:
                roots[key] = current.id
        if code:
            item = finding(code, key)
            findings.append(item)
            rejected.append({"event_id": key, "findings": [item]})
            if code == "notice_parent_missing":
                unresolved.append(notice)

    candidates = []
    for key, anchor in sorted(anchors.items()):
        ids = sorted(n for n, root in roots.items() if root == key)
        parents = {tag(notices[n], "e") for n in ids}
        terminals = sorted(set(ids) - parents)
        candidate_findings = []
        incomplete = [n for n in unresolved if n.pub_key == anchor.pub_key]
        unavailable = any(
            e.kind == 1416 and e.pub_key == anchor.pub_key
            and any(f["code"] == "verification_unavailable" and f["event_id"] == e.id
                    for f in findings)
            for e in supplied
        )
        if len(terminals) > 1:
            position = "conflicting_notices"
            candidate_findings.append(finding(position, key))
        elif incomplete:
            position = "incomplete_notice_chain"
        elif unavailable:
            position = "unverifiable"
        elif terminals:
            value = tag(notices[terminals[0]], "notice_type")
            position = value if value in NOTICE_TYPES else "unsupported_notice_type"
        else:
            position = "no_notice_found"
        candidate_findings.extend(finding("notice_parent_missing", n.id) for n in incomplete)
        for n in ids:
            if tag(notices[n], "notice_type") not in NOTICE_TYPES:
                candidate_findings.append(finding("unsupported_notice_type", n))
        chains = []
        for terminal in terminals:
            chain = [terminal]
            while (parent := tag(notices[chain[-1]], "e")) != key:
                chain.append(parent)
            chains.append([key, *reversed(chain)])
        candidates.append({
            "anchor_state": "anchored", "anchor_event_id": key,
            "anchor_publisher": anchor.pub_key, "publisher_position": position,
            "terminal_notice_event_ids": terminals, "notice_event_ids": ids,
            "notice_chains": chains, "findings": candidate_findings,
        })
    if len(anchors) > 1:
        state = "multiple_candidate_anchors"
        findings.append(finding(state))
    elif anchors:
        state = "anchored"
    elif any(f["code"] == "verification_unavailable" for f in findings):
        state = "unverifiable"
    elif any(e.kind == 1415 for e in supplied):
        state = "invalid_anchor_evidence"
    else:
        state = "not_anchored"
    if not anchors:
        findings.append(finding("anchor_not_found"))
    findings.extend(f for c in candidates for f in c["findings"])
    return {
        "ruleset": dict(RULESET),
        "artifact": {"digest_algorithm": "sha256", "digest": digest,
                     "digest_source": digest_source},
        "aggregate_state": state, "candidates": candidates,
        "non_qualifying_evidence": rejected, "findings": findings,
        "retrieval": {"sources": [], "complete": None, "mode": "supplied_evidence"},
        "evidence": [e.data() for e in supplied],
    }
