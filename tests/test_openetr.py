from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from stroma import Event
from app import openetr as openetr_module
from app.templating import render_template
from app.openetr import build_openetr_history, build_signer_profile, ANCHOR_KIND, CONTROL_KIND, PROFILE_KIND
from app.openetr_rules import evaluate_core_record

DIGEST = "ab" * 32
KEY = "01" * 32


def signed(kind=1415, *, tags=None, time=100, key=KEY, content="Record"):
    evt = Event(kind=kind, created_at=time, content=content,
                tags=tags if tags is not None else [["o", DIGEST], ["action", "issue"]])
    evt.sign(key)
    return evt


def notice(parent, notice_type="caution", *, time=200, key=KEY):
    return signed(1416, tags=[["o", DIGEST], ["action", "notice"],
                              ["e", parent.id], ["notice_type", notice_type]],
                  time=time, key=key, content="Publisher statement")


def evaluate(*events):
    return evaluate_core_record(DIGEST, events)


def codes(result):
    return {f["code"] for f in result["findings"]}


def test_anchor_and_no_anchor():
    anchor = signed()
    result = evaluate(anchor)
    assert result["ruleset"]["id"] == "openetr:core-record:1.0"
    assert result["aggregate_state"] == "anchored"
    assert result["candidates"][0]["publisher_position"] == "no_notice_found"
    assert result["artifact"]["digest_source"] == "supplied"
    assert result["evidence"] == [anchor.data()]
    assert result["retrieval"]["complete"] is None
    assert evaluate()["aggregate_state"] == "not_anchored"
    assert evaluate_core_record(DIGEST, [anchor], digest_source="computed")["artifact"]["digest_source"] == "computed"


@pytest.mark.parametrize("mutation,code", [
    ("signature", "invalid_signature"), ("id", "invalid_event_id"),
    ("digest", "artifact_mismatch"), ("action", "invalid_anchor_action"),
    ("missing_action", "invalid_anchor_action"), ("duplicate_action", "invalid_anchor_action"),
    ("duplicate_digest", "artifact_mismatch"),
])
def test_anchor_invalidity(mutation, code):
    tags = [["o", DIGEST], ["action", "issue"]]
    if mutation == "digest": tags[0][1] = "cd" * 32
    if mutation == "action": tags[1][1] = "notice"
    if mutation == "missing_action": tags.pop()
    if mutation == "duplicate_action": tags.append(["action", "issue"])
    if mutation == "duplicate_digest": tags.append(["o", DIGEST])
    anchor = signed(tags=tags)
    if mutation == "signature": anchor._sig = "00" * 64
    if mutation == "id": anchor._id = "00" * 32
    result = evaluate(anchor)
    assert result["aggregate_state"] == "invalid_anchor_evidence"
    assert not result["candidates"]
    assert code in codes(result)
    assert result["non_qualifying_evidence"]
    assert result["evidence"]


def test_notice_chain_ignores_backdated_timestamps_and_withdrawal_preserves_anchor():
    anchor = signed()
    first = notice(anchor, "caution", time=500)
    last = notice(first, "withdrawn", time=1)
    result = evaluate(last, anchor, first)
    candidate = result["candidates"][0]
    assert candidate["anchor_state"] == "anchored"
    assert candidate["publisher_position"] == "withdrawn"
    assert candidate["notice_chains"] == [[anchor.id, first.id, last.id]]
    assert candidate["terminal_notice_event_ids"] == [last.id]
    assert result == evaluate(last, anchor, first)
    assert result == evaluate(first, last, anchor)


def test_conflict_preserves_every_branch_without_timestamp_winner():
    anchor = signed()
    first, second = notice(anchor, "caution"), notice(anchor, "do_not_use", time=9999)
    candidate = evaluate(anchor, second, first)["candidates"][0]
    assert candidate["publisher_position"] == "conflicting_notices"
    assert set(candidate["terminal_notice_event_ids"]) == {first.id, second.id}
    assert len(candidate["notice_chains"]) == 2


def test_multiple_anchors_are_separate_even_with_same_publisher():
    first, second = signed(time=1), signed(time=2)
    update = notice(first)
    result = evaluate(first, second, update)
    assert result["aggregate_state"] == "multiple_candidate_anchors"
    assert len(result["candidates"]) == 2
    positions = {c["anchor_event_id"]: c["publisher_position"] for c in result["candidates"]}
    assert positions == {first.id: "caution", second.id: "no_notice_found"}


def test_wrong_signer_and_legacy_control_cannot_set_position():
    anchor = signed()
    wrong = notice(anchor, key="02" * 32)
    legacy = signed(1416, tags=[["o", DIGEST], ["e", anchor.id], ["action", "accept"]])
    result = evaluate(anchor, wrong, legacy)
    assert result["candidates"][0]["publisher_position"] == "no_notice_found"
    assert "notice_signer_mismatch" in codes(result)
    assert "invalid_notice_action" in codes(result)
    assert len(result["non_qualifying_evidence"]) == 2


def test_missing_and_invalid_predecessor_are_distinguished():
    anchor = signed()
    absent = signed(time=22)
    orphan = notice(absent)
    result = evaluate(anchor, orphan)
    assert "notice_parent_missing" in codes(result)
    assert result["candidates"][0]["publisher_position"] == "incomplete_notice_chain"
    absent._sig = "00" * 64
    result = evaluate(anchor, absent, orphan)
    assert "notice_parent_invalid" in codes(result)
    assert not result["candidates"][0]["notice_event_ids"]


def test_unsupported_notice_preserved_and_reported():
    anchor = signed()
    unknown = notice(anchor, "extension:unknown")
    result = evaluate(anchor, unknown)
    assert result["candidates"][0]["publisher_position"] == "unsupported_notice_type"
    assert "unsupported_notice_type" in codes(result)


def test_duplicate_observations_and_corrupt_copy_do_not_hide_valid_anchor():
    anchor = signed()
    corrupt = Event(**{k: v for k, v in anchor.data().items() if k != "pubkey"},
                    pub_key=anchor.pub_key)
    corrupt._sig = "00" * 64
    result = evaluate(corrupt, anchor, anchor)
    assert len(result["candidates"]) == 1
    assert len(result["evidence"]) == 3


def test_cycle_is_rejected_even_with_mocked_crypto():
    # Actual event-hash cycles are infeasible; exercise the defensive graph guard.
    a = SimpleNamespace(id="11"*32, pub_key="33"*32, kind=1416,
        tags=[["o", DIGEST], ["action", "notice"], ["notice_type", "caution"], ["e", "22"*32]],
        calculate_id=lambda: "11"*32, is_valid=lambda: True, data=lambda: {})
    b = SimpleNamespace(**{**vars(a), "id": "22"*32,
        "tags": [["o", DIGEST], ["action", "notice"], ["notice_type", "caution"], ["e", "11"*32]],
        "calculate_id": lambda: "22"*32})
    assert "notice_cycle" in codes(evaluate(a, b))


@pytest.mark.parametrize("outcome", ["found", "empty", "unavailable"])
def test_anchor_check_render(outcome):
    history = build_openetr_history(DIGEST, [signed()] if outcome == "found" else [], ["wss://relay.example"])
    if outcome == "unavailable": history["error"] = "timeout"
    html = render_template("_control_history_content.html", has_blob=True,
                           blob_fingerprint=DIGEST, openetr_history=history)
    expected = {"found": "Anchor found", "empty": "No anchor found", "unavailable": "Check unavailable"}[outcome]
    assert f"<strong>{expected}</strong>" in html
    assert "openetr:core-record:1.0" in html
    if outcome == "found":
        assert "Publisher position:" in html
        assert "Signer-declared anchor time" in html
        assert "No qualifying publisher notice" in html


def test_withdrawal_and_invalid_evidence_are_inspectable_in_ui():
    anchor = signed()
    update = notice(anchor, "withdrawn")
    invalid = signed(time=44)
    invalid._sig = "00"*64
    history = build_openetr_history(DIGEST, [anchor, update, invalid], [])
    html = render_template("_control_history_content.html", has_blob=True,
                           blob_fingerprint=DIGEST, openetr_history=history)
    assert "publisher has withdrawn" in html
    assert "invalid_signature" in html
    assert "Anchor state</dt><dd>anchored" in html


def test_related_query_failure_does_not_mean_no_notice(monkeypatch):
    class Pool:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def query(self, filters, **kwargs):
            if filters["kinds"] == [1415]: return [signed()]
            raise TimeoutError()
    monkeypatch.setattr(openetr_module, "ClientPool", Pool)
    history = asyncio.run(openetr_module.query_openetr_history(DIGEST, ["wss://relay.example"]))
    result = history["verification"]
    assert result["candidates"][0]["anchor_state"] == "anchored"
    assert result["candidates"][0]["publisher_position"] == "unverifiable"
    assert result["retrieval"]["notice_query_failed"]
    assert "retrieval_incomplete" in codes(result)


@pytest.mark.parametrize("extra", [
    ["e", "ff" * 32], ["action", "notice"], ["notice_type", "withdrawn"], ["o", DIGEST],
])
def test_duplicate_required_notice_tags_are_rejected(extra):
    anchor = signed()
    update = notice(anchor)
    malformed = signed(1416, tags=[*update.tags, extra])
    result = evaluate(anchor, malformed)
    assert result["candidates"][0]["publisher_position"] == "no_notice_found"
    assert result["non_qualifying_evidence"]


def test_query_records_bounded_retrieval_even_when_successful(monkeypatch):
    class Pool:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def query(self, filters, **kwargs):
            return [signed()] if filters["kinds"] == [1415] else []
    monkeypatch.setattr(openetr_module, "ClientPool", Pool)
    history = asyncio.run(openetr_module.query_openetr_history(DIGEST, ["wss://relay.example"], limit=1))
    result = history["verification"]
    assert result["retrieval"]["complete"] is None
    assert result["retrieval"]["filters"][0]["limit"] == 1
    assert result["retrieval"]["started_at"] <= result["retrieval"]["ended_at"]
    assert result["candidates"][0]["publisher_position"] == "no_notice_found"


def test_unavailable_result_is_not_an_absence_finding():
    history = openetr_module.unavailable_openetr_history(DIGEST, ["wss://relay.example"])
    result = history["verification"]
    assert result["aggregate_state"] == "unverifiable"
    assert "anchor_not_found" not in codes(result)
    assert history["error"]


def test_unavailable_notice_crypto_does_not_assert_no_notice():
    anchor = signed()
    update = notice(anchor)
    def unavailable():
        raise RuntimeError("Verifier unavailable")
    update.is_valid = unavailable
    result = evaluate(anchor, update)
    assert result["candidates"][0]["publisher_position"] == "unverifiable"
    assert "verification_unavailable" in codes(result)


def test_legacy_origin_does_not_override_exact_notice_links():
    first, second = signed(time=1), signed(time=2)
    update = notice(first)
    update = signed(1416, tags=[*update.tags, ["origin", second.id]])
    result = evaluate(first, second, update)
    positions = {c["anchor_event_id"]: c["publisher_position"] for c in result["candidates"]}
    assert positions == {first.id: "caution", second.id: "no_notice_found"}


def test_extreme_signer_time_does_not_hide_valid_evidence():
    history = build_openetr_history(DIGEST, [signed(time=10**30)], [])
    assert history["verification"]["aggregate_state"] == "anchored"
    assert history["candidate_graphs"][0]["anchor"]["created_at"] == "Outside displayable range"


def test_rejects_non_digest():
    with pytest.raises(ValueError, match="SHA-256"):
        evaluate_core_record("not-a-digest", [])

def event(
    event_id: str,
    *,
    kind: int,
    created_at: int,
    tags: list[list[str]],
    pubkey: str = "11" * 32,
    content: str = "",
    valid: bool = True,
):
    return SimpleNamespace(
        id=event_id,
        kind=kind,
        created_at=created_at,
        tags=tags,
        pub_key=pubkey,
        content=content,
        is_valid=lambda: valid,
    )


def test_signer_profile_uses_latest_valid_kind_zero_from_same_signer() -> None:
    pubkey = "33" * 32
    older = event(
        "07" * 32,
        kind=PROFILE_KIND,
        created_at=100,
        tags=[],
        pubkey=pubkey,
        content='{"name":"old-name"}',
    )
    latest = event(
        "08" * 32,
        kind=PROFILE_KIND,
        created_at=200,
        tags=[],
        pubkey=pubkey,
        content=(
            '{"name":"issuer","display_name":"Warehouse Authority",'
            '"nip05":"issuer@example.com","lud16":"pay@example.com",'
            '"website":"https://example.com","about":"Issues records",'
            '"picture":"https://example.com/profile.png"}'
        ),
    )
    wrong_signer = event(
        "09" * 32,
        kind=PROFILE_KIND,
        created_at=300,
        tags=[],
        pubkey="44" * 32,
        content='{"name":"wrong"}',
    )

    profile = build_signer_profile(pubkey, [older, latest, wrong_signer])

    assert profile is not None
    assert profile["event_id"] == latest.id
    assert profile["display_name"] == "Warehouse Authority"
    assert profile["name"] == "issuer"
    assert profile["nip05"] == "issuer@example.com"
    assert profile["lightning_address"] == "pay@example.com"
    assert profile["website"] == "https://example.com"
    assert profile["picture"] == "https://example.com/profile.png"


def test_signer_profile_rejects_invalid_or_unsafe_metadata() -> None:
    pubkey = "55" * 32
    profile_event = event(
        "10" * 32,
        kind=PROFILE_KIND,
        created_at=100,
        tags=[],
        pubkey=pubkey,
        content=(
            '{"name":"Issuer","website":"javascript:alert(1)",'
            '"picture":"data:image/png;base64,abc"}'
        ),
    )

    profile = build_signer_profile(pubkey, [profile_event])

    assert profile is not None
    assert profile["name"] == "Issuer"
    assert profile["website"] is None
    assert profile["picture"] is None
    assert build_signer_profile("not-a-key", [profile_event]) is None
