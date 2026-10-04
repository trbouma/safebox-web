"""Background incoming Clear discovery with persisted, non-secret status."""
from __future__ import annotations
import asyncio
from datetime import timedelta
import secrets
import logging
from typing import Any, Callable
from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session
from app.models import ClearReceiveJob, WebWorkerHeartbeat, utc_now
from app.worker_liveness import WORKER_STALE_SECONDS, worker_is_live
JOB_LEASE_SECONDS = 15 * 60
JOB_HEARTBEAT_SECONDS = 30
logger = logging.getLogger("safebox_web.clear_receive")

def _job_values(job: ClearReceiveJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {
        "npub": job.npub,
        "status": job.status,
        "phase": job.phase,
        "stored_count": int(job.stored_count),
        "error": job.error,
        "started_at": job.started_at,
        "updated_at": job.updated_at,
        "lease_expires_at": job.lease_expires_at,
        "owner_worker_id": job.owner_worker_id,
    }


def get_clear_receive_job(engine: Engine, npub: str) -> dict[str, Any] | None:
    with Session(engine) as session:
        values = _job_values(session.get(ClearReceiveJob, npub))
    owner_stopped = bool(
        values is not None
        and values["owner_worker_id"]
        and not worker_is_live(engine, values["owner_worker_id"])
    )
    legacy_lease_expired = bool(
        values is not None
        and not values["owner_worker_id"]
        and values["lease_expires_at"] <= utc_now()
    )
    if values is not None and values["status"] == "RUNNING" and (
        legacy_lease_expired or owner_stopped
    ):
        values["status"] = "INTERRUPTED"
        values["phase"] = "INTERRUPTED"
        values["error"] = (
            "The previous web process stopped reporting progress. Start "
            "incoming check again to resume from relay-backed Clear state."
        )
    return values


def claim_clear_receive_job(
    engine: Engine,
    npub: str,
    *,
    worker_id: str | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Claim the one Clear incoming check lease for an Acorn without storing keys."""

    now = utc_now()
    worker_cutoff = now - timedelta(seconds=WORKER_STALE_SECONDS)
    lease_expires_at = now + timedelta(seconds=JOB_LEASE_SECONDS)
    owner_token = secrets.token_urlsafe(24)
    with Session(engine) as session:
        existing = session.get(ClearReceiveJob, npub)
        if existing is None:
            job = ClearReceiveJob(
                npub=npub,
                owner_token=owner_token,
                owner_worker_id=worker_id,
                status="RUNNING",
                phase="STARTING",
                started_at=now,
                updated_at=now,
                lease_expires_at=lease_expires_at,
            )
            session.add(job)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
            else:
                session.refresh(job)
                return True, owner_token, _job_values(job) or {}

        live_owner = exists(
            select(WebWorkerHeartbeat.worker_id)
            .where(
                WebWorkerHeartbeat.worker_id
                == ClearReceiveJob.owner_worker_id
            )
            .where(WebWorkerHeartbeat.heartbeat_at > worker_cutoff)
        )
        statement = (
            update(ClearReceiveJob)
            .where(ClearReceiveJob.npub == npub)
            .where(
                or_(
                    ClearReceiveJob.status != "RUNNING",
                    and_(
                        ClearReceiveJob.owner_worker_id.is_(None),
                        ClearReceiveJob.lease_expires_at <= now,
                    ),
                    and_(
                        ClearReceiveJob.owner_worker_id.is_not(None),
                        ~live_owner,
                    ),
                )
            )
            .values(
                owner_token=owner_token,
                owner_worker_id=worker_id,
                status="RUNNING",
                phase="STARTING",
                stored_count=0,
                error=None,
                started_at=now,
                updated_at=now,
                lease_expires_at=lease_expires_at,
            )
        )
        result = session.exec(statement)
        session.commit()
        job = session.get(ClearReceiveJob, npub)
        claimed = bool(result.rowcount)
        return claimed, owner_token if claimed else "", _job_values(job) or {}


def update_clear_receive_job(
    engine: Engine,
    npub: str,
    owner_token: str,
    **changes: Any,
) -> bool:
    changes = dict(changes)
    changes["updated_at"] = utc_now()
    with Session(engine) as session:
        statement = (
            update(ClearReceiveJob)
            .where(ClearReceiveJob.npub == npub)
            .where(ClearReceiveJob.owner_token == owner_token)
            .values(**changes)
        )
        result = session.exec(statement)
        session.commit()
        return bool(result.rowcount)


def run_clear_receive_job_in_thread(*, engine: Engine, acorn_factory: Callable,
                                  npub: str, owner_token: str,
                                  load_timeout_seconds: float,
                                  scan_timeout_seconds: float = 300) -> None:
    async def execute():
        owner = asyncio.current_task()
        async def heartbeat():
            try:
                while True:
                    await asyncio.sleep(JOB_HEARTBEAT_SECONDS)
                    if not update_clear_receive_job(engine, npub, owner_token,
                        lease_expires_at=utc_now() + timedelta(seconds=JOB_LEASE_SECONDS)):
                        owner.cancel()
                        return
            except Exception:
                owner.cancel()
        heartbeat_task = asyncio.create_task(heartbeat())
        try:
            acorn = acorn_factory()
            await asyncio.wait_for(acorn.load_data(), timeout=load_timeout_seconds)
            if not update_clear_receive_job(engine, npub, owner_token, phase="SCANNING"):
                return
            result = await asyncio.wait_for(acorn.sweep_clear_transfers(), timeout=scan_timeout_seconds)
            if not isinstance(result, dict):
                raise ValueError("Invalid incoming scan result")
            count = max(0, int(result.get("stored_count") or 0))
            partial = bool(result.get("failed")) or result.get("status") not in (None, "OK")
            update_clear_receive_job(engine, npub, owner_token,
                status="PARTIAL" if partial else "COMPLETE",
                phase="REVIEW" if partial else "COMPLETE", stored_count=count,
                error=("Some transfers could not be checked. Stored receipts remain available; review Clear Transactions." if partial else None))
        except asyncio.CancelledError:
            update_clear_receive_job(engine, npub, owner_token, status="INTERRUPTED", phase="REVIEW",
                error="The check was interrupted. Stored receipts remain available; review Clear Transactions before checking again.")
            raise
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
    try:
        asyncio.run(execute())
    except Exception as exc:
        logger.warning("incoming Clear check failed error_type=%s", type(exc).__name__)
        update_clear_receive_job(engine, npub, owner_token, status="FAILED", phase="REVIEW",
            error="The incoming check could not finish. Transfers may already have been stored. Review Clear Transactions before checking again.")
