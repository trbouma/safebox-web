from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.database import create_database_engine, run_migrations
from app.funds_finalization import (
    claim_finalization_job,
    get_finalization_job,
    run_finalization_job,
)


def job_engine(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'jobs.db'}"
    run_migrations(database_url)
    return create_database_engine(database_url)


def test_finalization_job_lease_prevents_duplicate_worker(tmp_path) -> None:
    engine = job_engine(tmp_path)
    try:
        claimed, owner_token, job = claim_finalization_job(engine, "npub1wallet")
        duplicate, duplicate_token, duplicate_job = claim_finalization_job(
            engine,
            "npub1wallet",
        )
    finally:
        engine.dispose()

    assert claimed is True
    assert owner_token
    assert job["status"] == "RUNNING"
    assert duplicate is False
    assert duplicate_token == ""
    assert duplicate_job["status"] == "RUNNING"
    assert "nsec" not in job


def test_background_job_finalizes_all_visible_receipts(tmp_path) -> None:
    engine = job_engine(tmp_path)
    acorn = type(
        "FakeAcorn",
        (),
        {
            "sweep_ecash_transfers": AsyncMock(
                return_value={
                    "provisional_count": 3,
                    "provisional_amount": 50,
                }
            ),
            "reconcile_continuity_receipts": AsyncMock(
                return_value={
                    "confirmed_count": 3,
                    "confirmed_amount": 50,
                    "pending_count": 0,
                    "pending_amount": 0,
                    "terminal_error_count": 0,
                }
            ),
        },
    )()
    try:
        claimed, owner_token, _job = claim_finalization_job(engine, "npub1wallet")
        assert claimed is True
        asyncio.run(
            run_finalization_job(
                engine=engine,
                acorn=acorn,
                npub="npub1wallet",
                owner_token=owner_token,
            )
        )
        job = get_finalization_job(engine, "npub1wallet")
    finally:
        engine.dispose()

    acorn.sweep_ecash_transfers.assert_awaited_once_with(finalize=False)
    acorn.reconcile_continuity_receipts.assert_awaited_once_with()
    assert job is not None
    assert job["status"] == "COMPLETE"
    assert job["confirmed_count"] == 3
    assert job["confirmed_amount"] == 50
    assert job["pending_count"] == 0


def test_background_job_records_failure_without_persisting_key(tmp_path) -> None:
    engine = job_engine(tmp_path)
    acorn = type(
        "FailingAcorn",
        (),
        {
            "sweep_ecash_transfers": AsyncMock(
                side_effect=RuntimeError("relay unavailable")
            ),
        },
    )()
    try:
        claimed, owner_token, _job = claim_finalization_job(engine, "npub1wallet")
        assert claimed is True
        asyncio.run(
            run_finalization_job(
                engine=engine,
                acorn=acorn,
                npub="npub1wallet",
                owner_token=owner_token,
            )
        )
        job = get_finalization_job(engine, "npub1wallet")
    finally:
        engine.dispose()

    assert job is not None
    assert job["status"] == "FAILED"
    assert "relay unavailable" in str(job["error"])
    assert "nsec" not in job
