"""Bounded monitoring of explicitly requested Clear payments.

Receipts and confirmed amounts remain authoritative on the Acorn relay.
The encrypted browser ticket authorizes only one exact request for one Acorn.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
import json
import logging
import re
from time import monotonic, time

from cryptography.fernet import Fernet, InvalidToken

from app.clear_acceptance import (
    claim_clear_acceptance_job,
    get_clear_acceptance_job,
    run_clear_acceptance_job,
    update_clear_acceptance_job,
)
from app.outgoing_payment import get_outgoing_payment_job

logger = logging.getLogger(__name__)
MONITOR_SECONDS = 300
POLL_SECONDS = 3


@dataclass(frozen=True)
class ClearRequestState:
    npub: str
    request_id: str
    mint: str
    unit: str
    amount: int
    display_name: str
    payment_request: str
    description: str
    started_at: float
    purpose: str = "clear-request-monitor-v1"
    relays: tuple[str, ...] = ()


class ClearRequestCipher:
    def __init__(self, settings):
        self.cipher = Fernet(settings.cookie_key.encode("ascii"))
        self.ttl = min(settings.session_ttl_seconds, 3600)

    def encode(self, state: ClearRequestState) -> str:
        return self.cipher.encrypt(json.dumps(asdict(state)).encode()).decode()

    def decode(self, token: str, npub: str) -> ClearRequestState:
        try:
            payload = json.loads(
                self.cipher.decrypt(token.encode("ascii"), ttl=self.ttl)
            )
            payload["relays"] = tuple(payload.get("relays") or ())
            state = ClearRequestState(**payload)
            if (state.purpose != "clear-request-monitor-v1" or state.npub != npub
                    or not state.request_id or state.amount <= 0):
                raise ValueError("Invalid request")
            return state
        except (InvalidToken, UnicodeError, ValueError, TypeError) as exc:
            raise ValueError("Clear request is invalid or expired") from exc


def matches(receipt: dict, state: ClearRequestState) -> bool:
    mint = receipt.get("mint")
    if not mint and len(receipt.get("mints") or []) == 1:
        mint = receipt["mints"][0]
    return (
        str(receipt.get("payment_request_id") or "") == state.request_id
        and str(mint or "").rstrip("/") == state.mint.rstrip("/")
        and receipt.get("unit") == state.unit
        and receipt.get("status", "pending") in {"pending", "accepted"}
    )


async def request_status(acorn, state, timeout=20, engine=None):
    receipts = await asyncio.wait_for(acorn.get_clear_receipts(), timeout)
    matched = [r for r in receipts if matches(r, state)]
    accepted = {r["event_id"] for r in matched if r.get("status") == "accepted"}
    if accepted:
        history = await asyncio.wait_for(acorn.get_clear_transaction_history(), timeout)
        for row in history:
            if (row.get("source_event") in accepted and row.get("direction") == "in"
                    and row.get("operation") == "accept"
                    and str(row.get("mint") or "").rstrip("/") == state.mint.rstrip("/")
                    and row.get("unit") == state.unit):
                amount = int(row.get("amount") or 0)
                return {"status": "COMPLETE" if amount >= state.amount else "REVIEW", "amount": amount}
        return {"status": "REVIEW", "amount": 0}
    if engine is not None and matched:
        job = get_clear_acceptance_job(engine, state.npub)
        if (job and job.get("event_id") in {r.get("event_id") for r in matched}
                and job.get("status") in {"FAILED", "INTERRUPTED"}):
            return {"status": "FAILED", "amount": 0}
    return {"status": "RUNNING" if time() - state.started_at < MONITOR_SECONDS else "PENDING", "amount": 0}


async def monitor_request(*, engine, acorn, state, worker_id, timeout=20):
    await asyncio.wait_for(acorn.load_data(), timeout)
    # Keep the request's advertised inbox routes even if discovery later changes.
    routes = list(dict.fromkeys([acorn.home_relay, *state.relays])) if state.relays else None
    deadline = monotonic() + MONITOR_SECONDS
    while monotonic() < deadline:
        try:
            receipts = await asyncio.wait_for(acorn.get_clear_receipts(), timeout)
            if any(matches(r, state) and r.get("status") == "accepted" for r in receipts):
                return
            candidates = [r for r in receipts if matches(r, state)]
            if not candidates:
                preview = await asyncio.wait_for(
                    acorn.sweep_clear_transfers(preview_only=True, advance_cursor=False,
                        **({"relays": routes} if routes else {})), timeout
                )
                candidates = [r for r in preview.get("previewed", []) if matches(r, state)]
            candidate = next((r for r in candidates
                if re.fullmatch(r"[0-9a-f]{64}", str(r.get("event_id") or ""))
                and int(r.get("amount") or 0) >= state.amount), None)
            outgoing = get_outgoing_payment_job(engine, state.npub)
            if candidate and not (outgoing and outgoing.get("status") == "RUNNING"):
                event_id = candidate["event_id"]
                claimed, owner, _ = claim_clear_acceptance_job(
                    engine, state.npub, event_id, worker_id=worker_id
                )
                if claimed:
                    # Recheck under the existing per-Acorn acceptance lease.
                    # Another monitor may have just accepted this request.
                    try:
                        fresh = await asyncio.wait_for(acorn.get_clear_receipts(), timeout)
                        already = any(matches(r, state) and r.get("status") == "accepted" for r in fresh)
                    except Exception:
                        update_clear_acceptance_job(engine, state.npub, owner,
                            status="FAILED", phase="REVIEW", error="Could not recheck Clear request receipts.")
                        raise
                    if already:
                        update_clear_acceptance_job(engine, state.npub, owner,
                            status="INTERRUPTED", phase="COMPLETE", error="Request already accepted; no further transfer was accepted.")
                        return
                    await run_clear_acceptance_job(
                        engine=engine, acorn=acorn, npub=state.npub,
                        event_id=event_id, owner_token=owner, load_timeout_seconds=timeout,
                        **({"relays": routes} if routes else {}),
                    )
                    # Failed acceptance stays recoverable; do not automatically
                    # retry a mint mutation within the same monitoring session.
                    return
        except Exception as exc:
            logger.warning("Clear request monitoring check failed error_type=%s", type(exc).__name__)
        await asyncio.sleep(min(POLL_SECONDS, max(0, deadline - monotonic())))


def run_monitor(*, acorn_factory, **kwargs):
    try:
        asyncio.run(monitor_request(acorn=acorn_factory(), **kwargs))
    except Exception as exc:
        logger.warning("Clear request monitor stopped error_type=%s", type(exc).__name__)
