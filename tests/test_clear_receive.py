from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlmodel import SQLModel, create_engine
from app.models import utc_now
from app.clear_receive import (
    claim_clear_receive_job, get_clear_receive_job, update_clear_receive_job,
    run_clear_receive_job_in_thread,
)


@pytest.fixture
def engine(tmp_path):
    value = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    SQLModel.metadata.create_all(value)
    yield value
    value.dispose()


@pytest.mark.parametrize("result,status,count", [
    ({"status": "OK", "stored_count": 2}, "COMPLETE", 2),
    ({"status": "OK", "stored_count": 0}, "COMPLETE", 0),
    ({"status": "PARTIAL", "stored_count": 1, "failed": [{"reason": "private"}]}, "PARTIAL", 1),
    (TimeoutError("private"), "FAILED", 0),
])
def test_scan_results_and_failures(engine, result, status, count):
    _, owner, _ = claim_clear_receive_job(engine, "alice")
    scan = AsyncMock(side_effect=result) if isinstance(result, Exception) else AsyncMock(return_value=result)
    acorn = SimpleNamespace(load_data=AsyncMock(), sweep_clear_transfers=scan)
    run_clear_receive_job_in_thread(engine=engine, acorn_factory=lambda: acorn,
        npub="alice", owner_token=owner, load_timeout_seconds=1)
    job = get_clear_receive_job(engine, "alice")
    assert job["status"] == status
    assert job["stored_count"] == count
    assert "private" not in str(job)
    scan.assert_awaited_once_with()


def test_claim_ownership_and_interruption(engine):
    claimed, owner, _ = claim_clear_receive_job(engine, "alice")
    assert claimed
    assert not claim_clear_receive_job(engine, "alice")[0]
    assert get_clear_receive_job(engine, "bob") is None
    assert not update_clear_receive_job(engine, "alice", "wrong", status="COMPLETE")
    update_clear_receive_job(engine, "alice", owner,
                             lease_expires_at=utc_now() - timedelta(seconds=1))
    assert get_clear_receive_job(engine, "alice")["status"] == "INTERRUPTED"
    claimed, replacement, _ = claim_clear_receive_job(engine, "alice")
    assert claimed and replacement != owner
    assert not update_clear_receive_job(engine, "alice", owner, status="COMPLETE")
