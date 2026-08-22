"""Session-bound background finalization without persistent recipient secrets."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import secrets
from typing import Any, Callable

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.models import FundsFinalizationJob, WebWorkerHeartbeat, utc_now
from app.worker_liveness import WORKER_STALE_SECONDS, worker_is_live


logger = logging.getLogger("safebox_web.funds_finalization")
JOB_LEASE_SECONDS = 15 * 60
JOB_HEARTBEAT_SECONDS = 30


def _job_values(job: FundsFinalizationJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {
        "npub": job.npub,
        "status": job.status,
        "phase": job.phase,
        "discovered_count": int(job.discovered_count),
        "discovered_amount": int(job.discovered_amount),
        "confirmed_count": int(job.confirmed_count),
        "confirmed_amount": int(job.confirmed_amount),
        "pending_count": int(job.pending_count),
        "pending_amount": int(job.pending_amount),
        "error": job.error,
        "started_at": job.started_at,
        "updated_at": job.updated_at,
        "lease_expires_at": job.lease_expires_at,
        "owner_worker_id": job.owner_worker_id,
    }


def get_finalization_job(engine: Engine, npub: str) -> dict[str, Any] | None:
    with Session(engine) as session:
        values = _job_values(session.get(FundsFinalizationJob, npub))
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
            "The previous web process stopped reporting progress; reconnect "
            "to resume relay-backed finalization."
        )
    return values


def claim_finalization_job(
    engine: Engine,
    npub: str,
    *,
    worker_id: str | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Claim a cross-worker lease without storing any Acorn credential."""

    now = utc_now()
    worker_cutoff = now - timedelta(seconds=WORKER_STALE_SECONDS)
    lease_expires_at = now + timedelta(seconds=JOB_LEASE_SECONDS)
    owner_token = secrets.token_urlsafe(24)
    with Session(engine) as session:
        existing = session.get(FundsFinalizationJob, npub)
        if existing is None:
            job = FundsFinalizationJob(
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
                == FundsFinalizationJob.owner_worker_id
            )
            .where(WebWorkerHeartbeat.heartbeat_at > worker_cutoff)
        )
        statement = (
            update(FundsFinalizationJob)
            .where(FundsFinalizationJob.npub == npub)
            .where(
                or_(
                    FundsFinalizationJob.status != "RUNNING",
                    and_(
                        FundsFinalizationJob.owner_worker_id.is_(None),
                        FundsFinalizationJob.lease_expires_at <= now,
                    ),
                    and_(
                        FundsFinalizationJob.owner_worker_id.is_not(None),
                        ~live_owner,
                    ),
                )
            )
            .values(
                owner_token=owner_token,
                owner_worker_id=worker_id,
                status="RUNNING",
                phase="STARTING",
                discovered_count=0,
                discovered_amount=0,
                confirmed_count=0,
                confirmed_amount=0,
                pending_count=0,
                pending_amount=0,
                error=None,
                started_at=now,
                updated_at=now,
                lease_expires_at=lease_expires_at,
            )
        )
        result = session.exec(statement)
        session.commit()
        job = session.get(FundsFinalizationJob, npub)
        claimed = bool(result.rowcount)
        return claimed, owner_token if claimed else "", _job_values(job) or {}


def update_finalization_job(
    engine: Engine,
    npub: str,
    owner_token: str,
    **changes: Any,
) -> bool:
    changes = dict(changes)
    changes["updated_at"] = utc_now()
    with Session(engine) as session:
        statement = (
            update(FundsFinalizationJob)
            .where(FundsFinalizationJob.npub == npub)
            .where(FundsFinalizationJob.owner_token == owner_token)
            .values(**changes)
        )
        result = session.exec(statement)
        session.commit()
        return bool(result.rowcount)


async def run_finalization_job(
    *,
    engine: Engine,
    acorn,
    npub: str,
    owner_token: str,
) -> None:
    """Stage and finalize every visible transfer sequentially in the background."""

    async def maintain_lease() -> None:
        while True:
            await asyncio.sleep(JOB_HEARTBEAT_SECONDS)
            renewed = update_finalization_job(
                engine,
                npub,
                owner_token,
                lease_expires_at=utc_now() + timedelta(seconds=JOB_LEASE_SECONDS),
            )
            if not renewed:
                raise RuntimeError("Background finalization lease ownership was lost")

    heartbeat = asyncio.create_task(
        maintain_lease(),
        name=f"funds-finalization-heartbeat:{npub}",
    )
    job_task = asyncio.current_task()

    def stop_job_if_lease_fails(completed: asyncio.Task) -> None:
        if completed.cancelled():
            return
        if completed.exception() is not None and job_task is not None:
            job_task.cancel()

    heartbeat.add_done_callback(stop_job_if_lease_fails)
    try:
        update_finalization_job(
            engine,
            npub,
            owner_token,
            phase="DISCOVERING",
        )
        discovered = await acorn.sweep_ecash_transfers(finalize=False)
        discovered_count = int(discovered.get("provisional_count", 0))
        discovered_amount = int(discovered.get("provisional_amount", 0))
        update_finalization_job(
            engine,
            npub,
            owner_token,
            phase="FINALIZING",
            discovered_count=discovered_count,
            discovered_amount=discovered_amount,
            pending_count=discovered_count,
            pending_amount=discovered_amount,
        )

        reconciled = await acorn.reconcile_continuity_receipts()
        confirmed_count = int(reconciled.get("confirmed_count", 0))
        confirmed_amount = int(reconciled.get("confirmed_amount", 0))
        pending_count = int(reconciled.get("pending_count", 0))
        pending_amount = int(reconciled.get("pending_amount", 0))
        terminal_errors = int(reconciled.get("terminal_error_count", 0))
        status = "COMPLETE" if pending_count == 0 else "PARTIAL"
        error = None
        if terminal_errors:
            error = f"{terminal_errors} incoming transfer(s) could not be credited"
        update_finalization_job(
            engine,
            npub,
            owner_token,
            status=status,
            phase="COMPLETE" if status == "COMPLETE" else "REVIEW",
            confirmed_count=confirmed_count,
            confirmed_amount=confirmed_amount,
            pending_count=pending_count,
            pending_amount=pending_amount,
            error=error,
        )
        logger.info(
            "background funds finalization finished npub=%s status=%s confirmed_count=%s pending_count=%s",
            npub,
            status,
            confirmed_count,
            pending_count,
        )
    except asyncio.CancelledError:
        update_finalization_job(
            engine,
            npub,
            owner_token,
            status="INTERRUPTED",
            phase="INTERRUPTED",
            error="Web process stopped before finalization completed; reconnect to resume.",
        )
        raise
    except Exception as exc:
        logger.exception("background funds finalization failed npub=%s", npub)
        update_finalization_job(
            engine,
            npub,
            owner_token,
            status="FAILED",
            phase="REVIEW",
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)


def run_finalization_job_in_thread(
    *,
    engine: Engine,
    acorn_factory: Callable[[], Any],
    npub: str,
    owner_token: str,
    load_timeout_seconds: float,
) -> None:
    """Create and finalize an Acorn wholly inside an executor thread."""

    async def execute() -> None:
        acorn = acorn_factory()
        await asyncio.wait_for(
            acorn.load_data(),
            timeout=load_timeout_seconds,
        )
        await run_finalization_job(
            engine=engine,
            acorn=acorn,
            npub=npub,
            owner_token=owner_token,
        )

    try:
        asyncio.run(execute())
    except Exception as exc:
        logger.exception("background funds finalization thread failed npub=%s", npub)
        update_finalization_job(
            engine,
            npub,
            owner_token,
            status="FAILED",
            phase="REVIEW",
            error=f"{type(exc).__name__}: {exc}",
        )
