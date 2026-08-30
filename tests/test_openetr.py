from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.openetr import (
    ANCHOR_KIND,
    CONTROL_KIND,
    PROFILE_KIND,
    build_openetr_history,
    build_signer_profile,
)


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


def test_openetr_history_follows_exact_prior_event_chain() -> None:
    digest = "ab" * 32
    anchor = event(
        "01" * 32,
        kind=ANCHOR_KIND,
        created_at=100,
        tags=[["o", digest], ["action", "issue"]],
        content="Issued record",
    )
    initiated = event(
        "02" * 32,
        kind=CONTROL_KIND,
        created_at=200,
        tags=[
            ["o", digest],
            ["e", anchor.id],
            ["origin", anchor.id],
            ["action", "initiate"],
            ["p", "22" * 32],
        ],
    )
    accepted = event(
        "03" * 32,
        kind=CONTROL_KIND,
        created_at=300,
        tags=[
            ["o", digest],
            ["e", initiated.id],
            ["origin", anchor.id],
            ["action", "accept"],
        ],
        pubkey="22" * 32,
    )

    history = build_openetr_history(
        digest,
        [accepted, anchor, initiated],
        ["wss://relay.openetr.org"],
    )

    assert len(history["candidate_graphs"]) == 1
    graph = history["candidate_graphs"][0]
    assert graph["anchor"]["id"] == anchor.id
    assert graph["anchor"]["action_label"] == "Anchor recorded"
    assert [item["id"] for item in graph["controls"]] == [
        initiated.id,
        accepted.id,
    ]
    assert graph["controls"][0]["action_label"] == "Transfer initiated"
    assert graph["controls"][0]["participant"].startswith("npub1")
    assert graph["consequential_state"] == {
        "status": "not_derived",
        "protocol_version": None,
        "controller": None,
        "lifecycle": None,
        "standing": None,
        "active_guards": [],
        "basis_event_ids": [],
    }
    assert graph["recognition"] == {"status": "not_evaluated", "basis": None}
    assert graph["effect"] == {
        "status": "not_evaluated",
        "value": None,
        "purpose": None,
    }
    assert history["warnings"] == []


def test_openetr_history_excludes_invalid_and_unlinked_events() -> None:
    digest = "cd" * 32
    anchor = event(
        "04" * 32,
        kind=ANCHOR_KIND,
        created_at=100,
        tags=[["o", digest]],
    )
    orphan = event(
        "05" * 32,
        kind=CONTROL_KIND,
        created_at=200,
        tags=[["o", digest], ["e", "ff" * 32], ["action", "initiate"]],
    )
    invalid = event(
        "06" * 32,
        kind=CONTROL_KIND,
        created_at=300,
        tags=[["o", digest], ["e", anchor.id], ["action", "accept"]],
        valid=False,
    )

    history = build_openetr_history(digest, [anchor, orphan, invalid], ["wss://relay.example"])

    assert history["candidate_graphs"][0]["controls"] == []
    assert [item["id"] for item in history["unlinked_events"]] == [orphan.id]
    assert any("linked unambiguously" in warning for warning in history["warnings"])
    assert any("cryptographic validation" in warning for warning in history["warnings"])
    assert history["invalid_event_count"] == 1


def test_openetr_history_retains_independent_candidate_anchor_graphs() -> None:
    digest = "ef" * 32
    first_anchor = event(
        "11" * 32,
        kind=ANCHOR_KIND,
        created_at=100,
        tags=[["o", digest], ["action", "issue"]],
        pubkey="11" * 32,
        content="First candidate",
    )
    second_anchor = event(
        "22" * 32,
        kind=ANCHOR_KIND,
        created_at=200,
        tags=[["o", digest], ["action", "issue"]],
        pubkey="22" * 32,
        content="Second candidate",
    )
    first_control = event(
        "33" * 32,
        kind=CONTROL_KIND,
        created_at=300,
        tags=[
            ["o", digest],
            ["e", first_anchor.id],
            ["origin", first_anchor.id],
            ["action", "attest"],
        ],
    )
    second_control = event(
        "44" * 32,
        kind=CONTROL_KIND,
        created_at=400,
        tags=[
            ["o", digest],
            ["e", second_anchor.id],
            ["origin", second_anchor.id],
            ["action", "attest"],
        ],
    )

    history = build_openetr_history(
        digest,
        [second_control, second_anchor, first_control, first_anchor],
        ["wss://relay.example"],
    )

    assert [graph["anchor"]["id"] for graph in history["candidate_graphs"]] == [
        first_anchor.id,
        second_anchor.id,
    ]
    assert [item["id"] for item in history["candidate_graphs"][0]["controls"]] == [
        first_control.id
    ]
    assert [item["id"] for item in history["candidate_graphs"][1]["controls"]] == [
        second_control.id
    ]
    assert "earliest" not in " ".join(history["warnings"]).lower()
    assert "No candidate was selected as authoritative" in history["warnings"][0]


def test_openetr_history_rejects_non_sha256_object_identifier() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        build_openetr_history("not-a-digest", [], ["wss://relay.example"])


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
