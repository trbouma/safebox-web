"""Cross-process liveness for session-bound background jobs.

Only an opaque process identifier and timestamps are persisted. Wallet keys and
other session material remain in the request worker's memory.
"""

from __future__ import annotations

from datetime import timedelta
import logging
import os
import secrets
import threading

from sqlalchemy import delete
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.models import WebWorkerHeartbeat, utc_now


logger = logging.getLogger("safebox_web.worker_liveness")
WORKER_HEARTBEAT_SECONDS = 15
WORKER_STALE_SECONDS = 60


def new_worker_id() -> str:
    """Return an opaque identifier unique to this process lifetime."""

    return secrets.token_urlsafe(24)


def heartbeat_worker(engine: Engine, worker_id: str) -> None:
    now = utc_now()
    with Session(engine) as session:
        worker = session.get(WebWorkerHeartbeat, worker_id)
        if worker is None:
            session.add(
                WebWorkerHeartbeat(
                    worker_id=worker_id,
                    started_at=now,
                    heartbeat_at=now,
                )
            )
        else:
            worker.heartbeat_at = now
            session.add(worker)
        session.commit()


def remove_worker(engine: Engine, worker_id: str) -> None:
    with Session(engine) as session:
        session.exec(
            delete(WebWorkerHeartbeat).where(
                WebWorkerHeartbeat.worker_id == worker_id
            )
        )
        session.commit()


def worker_is_live(engine: Engine, worker_id: str | None) -> bool:
    if not worker_id:
        return False
    cutoff = utc_now() - timedelta(seconds=WORKER_STALE_SECONDS)
    with Session(engine) as session:
        worker = session.get(WebWorkerHeartbeat, worker_id)
        return worker is not None and worker.heartbeat_at > cutoff


def start_worker_heartbeat(
    engine: Engine,
    worker_id: str,
) -> tuple[threading.Event, threading.Thread]:
    """Keep process liveness current even if its asyncio loop is busy."""

    stop_event = threading.Event()
    heartbeat_worker(engine, worker_id)

    def maintain() -> None:
        while not stop_event.wait(WORKER_HEARTBEAT_SECONDS):
            try:
                heartbeat_worker(engine, worker_id)
            except Exception:
                logger.exception("web worker heartbeat update failed")

    thread = threading.Thread(
        target=maintain,
        name=f"safebox-web-heartbeat-{os.getpid()}",
        daemon=True,
    )
    thread.start()
    return stop_event, thread


def stop_worker_heartbeat(
    engine: Engine,
    worker_id: str,
    stop_event: threading.Event,
    thread: threading.Thread,
) -> None:
    stop_event.set()
    thread.join(timeout=WORKER_HEARTBEAT_SECONDS + 2)
    try:
        remove_worker(engine, worker_id)
    except Exception:
        logger.exception("web worker heartbeat removal failed")
