"""Session-bound background Clear acceptance without persistent secrets."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import secrets
from typing import Any

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.models import ClearAcceptanceJob, WebWorkerHeartbeat, utc_now
from app.worker_liveness import WORKER_STALE_SECONDS, worker_is_live


logger = logging.getLogger("safebox_web.clear_acceptance")
JOB_LEASE_SECONDS = 15 * 60
JOB_HEARTBEAT_SECONDS = 30


def _job_values(job: ClearAcceptanceJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {
        "npub": job.npub,
        "event_id": job.event_id,
        "status": job.status,
        "phase": job.phase,
        "amount": int(job.amount),
        "mint": job.mint,
        "unit": job.unit,
        "error": job.error,
        "started_at": job.started_at,
        "updated_at": job.updated_at,
        "lease_expires_at": job.lease_expires_at,
        "owner_worker_id": job.owner_worker_id,
    }


def get_clear_acceptance_job(engine: Engine, npub: str) -> dict[str, Any] | None:
    with Session(engine) as session:
        values = _job_values(session.get(ClearAcceptanceJob, npub))
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
            "acceptance again to resume from relay-backed Clear state."
        )
    return values


def claim_clear_acceptance_job(
    engine: Engine,
    npub: str,
    event_id: str,
    *,
    worker_id: str | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """Claim the one Clear acceptance lease for an Acorn without storing keys."""

    now = utc_now()
    worker_cutoff = now - timedelta(seconds=WORKER_STALE_SECONDS)
    lease_expires_at = now + timedelta(seconds=JOB_LEASE_SECONDS)
    owner_token = secrets.token_urlsafe(24)
    with Session(engine) as session:
        existing = session.get(ClearAcceptanceJob, npub)
        if existing is None:
            job = ClearAcceptanceJob(
                npub=npub,
                event_id=event_id,
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
                == ClearAcceptanceJob.owner_worker_id
            )
            .where(WebWorkerHeartbeat.heartbeat_at > worker_cutoff)
        )
        statement = (
            update(ClearAcceptanceJob)
            .where(ClearAcceptanceJob.npub == npub)
            .where(
                or_(
                    ClearAcceptanceJob.status != "RUNNING",
                    and_(
                        ClearAcceptanceJob.owner_worker_id.is_(None),
                        ClearAcceptanceJob.lease_expires_at <= now,
                    ),
                    and_(
                        ClearAcceptanceJob.owner_worker_id.is_not(None),
                        ~live_owner,
                    ),
                )
            )
            .values(
                event_id=event_id,
                owner_token=owner_token,
                owner_worker_id=worker_id,
                status="RUNNING",
                phase="STARTING",
                amount=0,
                mint=None,
                unit=None,
                error=None,
                started_at=now,
                updated_at=now,
                lease_expires_at=lease_expires_at,
            )
        )
        result = session.exec(statement)
        session.commit()
        job = session.get(ClearAcceptanceJob, npub)
        claimed = bool(result.rowcount)
        return claimed, owner_token if claimed else "", _job_values(job) or {}


def update_clear_acceptance_job(
    engine: Engine,
    npub: str,
    owner_token: str,
    **changes: Any,
) -> bool:
    changes = dict(changes)
    changes["updated_at"] = utc_now()
    with Session(engine) as session:
        statement = (
            update(ClearAcceptanceJob)
            .where(ClearAcceptanceJob.npub == npub)
            .where(ClearAcceptanceJob.owner_token == owner_token)
            .values(**changes)
        )
        result = session.exec(statement)
        session.commit()
        return bool(result.rowcount)


async def run_clear_acceptance_job(
    *,
    engine: Engine,
    acorn,
    npub: str,
    event_id: str,
    owner_token: str,
    load_timeout_seconds: float = 20.0,
) -> None:
    """Load and accept one Clear receipt after the HTTP response returns."""

    async def maintain_lease() -> None:
        while True:
            await asyncio.sleep(JOB_HEARTBEAT_SECONDS)
            renewed = update_clear_acceptance_job(
                engine,
                npub,
                owner_token,
                lease_expires_at=utc_now() + timedelta(seconds=JOB_LEASE_SECONDS),
            )
            if not renewed:
                raise RuntimeError("Background Clear acceptance lease was lost")

    heartbeat = asyncio.create_task(
        maintain_lease(),
        name=f"clear-acceptance-heartbeat:{npub}",
    )
    job_task = asyncio.current_task()

    def stop_job_if_lease_fails(completed: asyncio.Task) -> None:
        if not completed.cancelled() and completed.exception() is not None:
            if job_task is not None:
                job_task.cancel()

    heartbeat.add_done_callback(stop_job_if_lease_fails)
    try:
        update_clear_acceptance_job(
            engine,
            npub,
            owner_token,
            phase="LOADING",
        )
        await asyncio.wait_for(
            acorn.load_data(),
            timeout=load_timeout_seconds,
        )
        update_clear_acceptance_job(
            engine,
            npub,
            owner_token,
            phase="ACCEPTING",
        )
        try:
            result = await acorn.accept_pending_clear_receipt(event_id)
        except ValueError as exc:
            if "not found" not in str(exc).lower():
                raise
            update_clear_acceptance_job(
                engine,
                npub,
                owner_token,
                phase="DISCOVERING",
            )
            await acorn.sweep_clear_transfers(
                event_id=event_id,
                advance_cursor=False,
            )
            update_clear_acceptance_job(
                engine,
                npub,
                owner_token,
                phase="ACCEPTING",
            )
            result = await acorn.accept_pending_clear_receipt(event_id)

        update_clear_acceptance_job(
            engine,
            npub,
            owner_token,
            status="COMPLETE",
            phase="COMPLETE",
            amount=int((result or {}).get("amount") or 0),
            mint=str((result or {}).get("mint") or "") or None,
            unit=str((result or {}).get("unit") or "") or None,
            error=None,
        )
        logger.info(
            "background Clear acceptance finished npub=%s event_id=%s amount=%s",
            npub,
            event_id,
            int((result or {}).get("amount") or 0),
        )
    except asyncio.CancelledError:
        update_clear_acceptance_job(
            engine,
            npub,
            owner_token,
            status="INTERRUPTED",
            phase="INTERRUPTED",
            error="Web process stopped before acceptance completed; retry to resume.",
        )
        raise
    except Exception as exc:
        logger.exception(
            "background Clear acceptance failed npub=%s event_id=%s",
            npub,
            event_id,
        )
        update_clear_acceptance_job(
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
