from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

from sqlmodel import Session

from app.clear_acceptance import (
    claim_clear_acceptance_job,
    get_clear_acceptance_job,
    run_clear_acceptance_job,
)
from app.database import create_database_engine, run_migrations
from app.models import ClearAcceptanceJob, WebWorkerHeartbeat, utc_now
from app.worker_liveness import heartbeat_worker


def job_engine(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'clear-jobs.db'}"
    run_migrations(database_url)
    return create_database_engine(database_url)


def test_clear_acceptance_lease_prevents_concurrent_wallet_jobs(tmp_path) -> None:
    engine = job_engine(tmp_path)
    try:
        claimed, owner_token, job = claim_clear_acceptance_job(
            engine,
            "npub1wallet",
            "a" * 64,
        )
        duplicate, duplicate_token, duplicate_job = claim_clear_acceptance_job(
            engine,
            "npub1wallet",
            "b" * 64,
        )
    finally:
        engine.dispose()

    assert claimed is True
    assert owner_token
    assert job["event_id"] == "a" * 64
    assert duplicate is False
    assert duplicate_token == ""
    assert duplicate_job["event_id"] == "a" * 64
    assert "nsec" not in job


def test_clear_acceptance_can_reclaim_stale_worker_without_waiting_for_lease(
    tmp_path,
) -> None:
    engine = job_engine(tmp_path)
    try:
        heartbeat_worker(engine, "worker-one")
        heartbeat_worker(engine, "worker-two")
        claimed, _owner_token, _job = claim_clear_acceptance_job(
            engine,
            "npub1wallet",
            "a" * 64,
            worker_id="worker-one",
        )
        duplicate, _duplicate_token, _duplicate_job = claim_clear_acceptance_job(
            engine,
            "npub1wallet",
            "b" * 64,
            worker_id="worker-two",
        )
        with Session(engine) as session:
            stale_worker = session.get(WebWorkerHeartbeat, "worker-one")
            assert stale_worker is not None
            stale_worker.heartbeat_at = utc_now() - timedelta(minutes=2)
            session.add(stale_worker)
            session.commit()
        reclaimed, replacement_token, replacement_job = claim_clear_acceptance_job(
            engine,
            "npub1wallet",
            "b" * 64,
            worker_id="worker-two",
        )
    finally:
        engine.dispose()

    assert claimed is True
    assert duplicate is False
    assert reclaimed is True
    assert replacement_token
    assert replacement_job["owner_worker_id"] == "worker-two"
    assert replacement_job["event_id"] == "b" * 64


def test_clear_acceptance_does_not_reclaim_live_worker_with_expired_job_lease(
    tmp_path,
) -> None:
    engine = job_engine(tmp_path)
    try:
        heartbeat_worker(engine, "worker-one")
        heartbeat_worker(engine, "worker-two")
        claimed, _owner_token, _job = claim_clear_acceptance_job(
            engine,
            "npub1wallet",
            "a" * 64,
            worker_id="worker-one",
        )
        with Session(engine) as session:
            job = session.get(ClearAcceptanceJob, "npub1wallet")
            assert job is not None
            job.lease_expires_at = utc_now() - timedelta(minutes=1)
            session.add(job)
            session.commit()
        duplicate, duplicate_token, duplicate_job = claim_clear_acceptance_job(
            engine,
            "npub1wallet",
            "b" * 64,
            worker_id="worker-two",
        )
    finally:
        engine.dispose()

    assert claimed is True
    assert duplicate is False
    assert duplicate_token == ""
    assert duplicate_job["owner_worker_id"] == "worker-one"
    assert duplicate_job["event_id"] == "a" * 64


def test_background_clear_acceptance_records_confirmed_result(tmp_path) -> None:
    engine = job_engine(tmp_path)
    event_id = "c" * 64
    acorn = type(
        "FakeAcorn",
        (),
        {
            "load_data": AsyncMock(return_value=None),
            "accept_pending_clear_receipt": AsyncMock(
                return_value={
                    "status": "OK",
                    "amount": 150,
                    "mint": "https://clear.example",
                    "unit": "cmu-example",
                }
            ),
            "sweep_clear_transfers": AsyncMock(),
        },
    )()
    try:
        claimed, owner_token, _job = claim_clear_acceptance_job(
            engine,
            "npub1wallet",
            event_id,
        )
        assert claimed is True
        asyncio.run(
            run_clear_acceptance_job(
                engine=engine,
                acorn=acorn,
                npub="npub1wallet",
                event_id=event_id,
                owner_token=owner_token,
            )
        )
        job = get_clear_acceptance_job(engine, "npub1wallet")
    finally:
        engine.dispose()

    acorn.accept_pending_clear_receipt.assert_awaited_once_with(event_id)
    acorn.load_data.assert_awaited_once_with()
    acorn.sweep_clear_transfers.assert_not_awaited()
    assert job is not None
    assert job["status"] == "COMPLETE"
    assert job["amount"] == 150
    assert job["unit"] == "cmu-example"


def test_background_clear_acceptance_discovers_previewed_receipt(tmp_path) -> None:
    engine = job_engine(tmp_path)
    event_id = "d" * 64
    acorn = type(
        "PreviewAcorn",
        (),
        {
            "load_data": AsyncMock(return_value=None),
            "accept_pending_clear_receipt": AsyncMock(
                side_effect=[
                    ValueError("Pending Clear receipt was not found"),
                    {
                        "status": "OK",
                        "amount": 12,
                        "mint": "https://clear.example",
                        "unit": "cmu-example",
                    },
                ]
            ),
            "sweep_clear_transfers": AsyncMock(return_value={"stored_count": 1}),
        },
    )()
    try:
        claimed, owner_token, _job = claim_clear_acceptance_job(
            engine,
            "npub1wallet",
            event_id,
        )
        assert claimed is True
        asyncio.run(
            run_clear_acceptance_job(
                engine=engine,
                acorn=acorn,
                npub="npub1wallet",
                event_id=event_id,
                owner_token=owner_token,
            )
        )
        job = get_clear_acceptance_job(engine, "npub1wallet")
    finally:
        engine.dispose()

    acorn.sweep_clear_transfers.assert_awaited_once_with(
        event_id=event_id,
        advance_cursor=False,
    )
    acorn.load_data.assert_awaited_once_with()
    assert acorn.accept_pending_clear_receipt.await_count == 2
    assert job is not None
    assert job["status"] == "COMPLETE"
