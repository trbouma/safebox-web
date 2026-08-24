from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.openetr import (
    CONTROL_KIND,
    ORIGIN_KIND,
    PROFILE_KIND,
    build_issuer_profile,
    build_openetr_history,
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
    origin = event(
        "01" * 32,
        kind=ORIGIN_KIND,
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
            ["e", origin.id],
            ["origin", origin.id],
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
            ["origin", origin.id],
            ["action", "accept"],
        ],
        pubkey="22" * 32,
    )

    history = build_openetr_history(
        digest,
        [accepted, origin, initiated],
        ["wss://relay.openetr.org"],
    )

    assert history["origin"]["id"] == origin.id
    assert [item["id"] for item in history["controls"]] == [
        initiated.id,
        accepted.id,
    ]
    assert history["controls"][0]["action_label"] == "Transfer initiated"
    assert history["controls"][0]["participant"].startswith("npub1")
    assert history["warnings"] == []


def test_openetr_history_excludes_invalid_and_unlinked_events() -> None:
    digest = "cd" * 32
    origin = event(
        "04" * 32,
        kind=ORIGIN_KIND,
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
        tags=[["o", digest], ["e", origin.id], ["action", "accept"]],
        valid=False,
    )

    history = build_openetr_history(digest, [origin, orphan, invalid], ["wss://relay.example"])

    assert history["controls"] == []
    assert any("complete chain" in warning for warning in history["warnings"])
    assert any("cryptographic validation" in warning for warning in history["warnings"])


def test_openetr_history_rejects_non_sha256_object_identifier() -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        build_openetr_history("not-a-digest", [], ["wss://relay.example"])


def test_issuer_profile_uses_latest_valid_kind_zero_from_same_signer() -> None:
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

    profile = build_issuer_profile(pubkey, [older, latest, wrong_signer])

    assert profile is not None
    assert profile["event_id"] == latest.id
    assert profile["display_name"] == "Warehouse Authority"
    assert profile["name"] == "issuer"
    assert profile["nip05"] == "issuer@example.com"
    assert profile["lightning_address"] == "pay@example.com"
    assert profile["website"] == "https://example.com"
    assert profile["picture"] == "https://example.com/profile.png"


def test_issuer_profile_rejects_invalid_or_unsafe_metadata() -> None:
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

    profile = build_issuer_profile(pubkey, [profile_event])

    assert profile is not None
    assert profile["name"] == "Issuer"
    assert profile["website"] is None
    assert profile["picture"] is None
    assert build_issuer_profile("not-a-key", [profile_event]) is None
