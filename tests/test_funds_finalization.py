from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

from sqlmodel import Session

from app.database import create_database_engine, run_migrations
from app.funds_finalization import (
    claim_finalization_job,
    get_finalization_job,
    run_finalization_job,
)
from app.models import FundsFinalizationJob, WebWorkerHeartbeat, utc_now
from app.worker_liveness import heartbeat_worker


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


def test_finalization_job_can_reclaim_stale_worker_without_waiting_for_lease(
    tmp_path,
) -> None:
    engine = job_engine(tmp_path)
    try:
        heartbeat_worker(engine, "worker-one")
        heartbeat_worker(engine, "worker-two")
        claimed, _owner_token, _job = claim_finalization_job(
            engine,
            "npub1wallet",
            worker_id="worker-one",
        )
        duplicate, _duplicate_token, _duplicate_job = claim_finalization_job(
            engine,
            "npub1wallet",
            worker_id="worker-two",
        )
        with Session(engine) as session:
            stale_worker = session.get(WebWorkerHeartbeat, "worker-one")
            assert stale_worker is not None
            stale_worker.heartbeat_at = utc_now() - timedelta(minutes=2)
            session.add(stale_worker)
            session.commit()
        reclaimed, replacement_token, replacement_job = claim_finalization_job(
            engine,
            "npub1wallet",
            worker_id="worker-two",
        )
    finally:
        engine.dispose()

    assert claimed is True
    assert duplicate is False
    assert reclaimed is True
    assert replacement_token
    assert replacement_job["owner_worker_id"] == "worker-two"


def test_finalization_does_not_reclaim_live_worker_with_expired_job_lease(
    tmp_path,
) -> None:
    engine = job_engine(tmp_path)
    try:
        heartbeat_worker(engine, "worker-one")
        heartbeat_worker(engine, "worker-two")
        claimed, _owner_token, _job = claim_finalization_job(
            engine,
            "npub1wallet",
            worker_id="worker-one",
        )
        with Session(engine) as session:
            job = session.get(FundsFinalizationJob, "npub1wallet")
            assert job is not None
            job.lease_expires_at = utc_now() - timedelta(minutes=1)
            session.add(job)
            session.commit()
        duplicate, duplicate_token, duplicate_job = claim_finalization_job(
            engine,
            "npub1wallet",
            worker_id="worker-two",
        )
    finally:
        engine.dispose()

    assert claimed is True
    assert duplicate is False
    assert duplicate_token == ""
    assert duplicate_job["owner_worker_id"] == "worker-one"


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
