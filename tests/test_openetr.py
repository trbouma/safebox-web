from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.openetr import CONTROL_KIND, ORIGIN_KIND, build_openetr_history


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
