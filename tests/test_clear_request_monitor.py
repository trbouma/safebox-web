import asyncio
from dataclasses import replace
from time import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cryptography.fernet import Fernet
import pytest

from app import clear_request_monitor as monitor


def state():
    return monitor.ClearRequestState(
        npub="owner", request_id="request-1", mint="https://mint.example",
        unit="cmu-one", amount=25, display_name="Credits", payment_request="creqA-test",
        description="Test", started_at=time(),
    )


def receipt(**kwargs):
    return {"event_id": "a" * 64, "payment_request_id": "request-1",
            "mint": "https://mint.example", "unit": "cmu-one", "amount": 25,
            "status": "pending", **kwargs}


def test_ticket_is_authenticated_and_owner_bound():
    cipher = monitor.ClearRequestCipher(SimpleNamespace(
        cookie_key=Fernet.generate_key().decode(), session_ttl_seconds=3600))
    original = state()
    ticket = cipher.encode(original)
    assert cipher.decode(ticket, "owner") == original
    for token, owner in [(ticket, "other"), (ticket[:-4] + "xxxx", "owner")]:
        with pytest.raises(ValueError):
            cipher.decode(token, owner)


@pytest.mark.parametrize("change", [
    {"payment_request_id": "unrelated"}, {"mint": "https://other.example"},
    {"unit": "cmu-two"}, {"status": "deleted"},
])
def test_matching_rejects_unrelated_receipts(change):
    assert not monitor.matches(receipt(**change), state())


def test_preview_matching_supports_single_mint():
    row = receipt()
    row.pop("mint")
    row["mints"] = ["https://mint.example/"]
    assert monitor.matches(row, state())
    row["mints"].append("https://other.example")
    assert not monitor.matches(row, state())


@pytest.mark.parametrize("accepted,amount,source,expected", [
    (False, 25, "a" * 64, "RUNNING"),
    (True, 25, "a" * 64, "COMPLETE"),
    (True, 24, "a" * 64, "REVIEW"),
    (True, 25, "b" * 64, "REVIEW"),
])
def test_confirmation_requires_exact_accepted_receipt_and_history(accepted, amount, source, expected):
    acorn = SimpleNamespace(
        get_clear_receipts=AsyncMock(return_value=[receipt(status="accepted" if accepted else "pending")]),
        get_clear_transaction_history=AsyncMock(return_value=[{
            "source_event": source, "direction": "in", "operation": "accept",
            "mint": "https://mint.example", "unit": "cmu-one", "amount": amount,
        }]),
    )
    assert asyncio.run(monitor.request_status(acorn, state()))["status"] == expected


def test_expired_monitor_remains_pending():
    acorn = SimpleNamespace(get_clear_receipts=AsyncMock(return_value=[]))
    assert asyncio.run(monitor.request_status(acorn, replace(state(), started_at=time() - 121)))["status"] == "PENDING"


def test_monitor_accepts_only_matching_transfer_through_existing_worker(monkeypatch):
    acorn = SimpleNamespace(
        load_data=AsyncMock(), get_clear_receipts=AsyncMock(return_value=[]),
        sweep_clear_transfers=AsyncMock(return_value={"previewed": [
            receipt(event_id="b" * 64, payment_request_id="other"), receipt(),
        ]}),
    )
    claims = []
    def claim(*args, **kwargs):
        claims.append(args)
        return True, "lease", {}
    monkeypatch.setattr(monitor, "get_outgoing_payment_job", lambda *args: None)
    monkeypatch.setattr(monitor, "claim_clear_acceptance_job", claim)
    accept = AsyncMock()
    monkeypatch.setattr(monitor, "run_clear_acceptance_job", accept)
    asyncio.run(monitor.monitor_request(engine=None, acorn=acorn, state=state(), worker_id="worker"))
    assert claims[0][2] == "a" * 64
    assert accept.await_args.kwargs["event_id"] == "a" * 64
    assert accept.await_args.kwargs["owner_token"] == "lease"
    acorn.sweep_clear_transfers.assert_awaited_once_with(preview_only=True, advance_cursor=False)


@pytest.mark.parametrize("row", [receipt(amount=24), receipt(payment_request_id="unrelated")])
def test_monitor_never_accepts_underpayments_or_unrelated_transfers(monkeypatch, row):
    monkeypatch.setattr(monitor, "MONITOR_SECONDS", 0.01)
    monkeypatch.setattr(monitor, "get_outgoing_payment_job", lambda *args: None)
    claim = AsyncMock()
    monkeypatch.setattr(monitor, "claim_clear_acceptance_job", claim)
    acorn = SimpleNamespace(load_data=AsyncMock(), get_clear_receipts=AsyncMock(return_value=[]),
                            sweep_clear_transfers=AsyncMock(return_value={"previewed": [row]}))
    asyncio.run(monitor.monitor_request(engine=None, acorn=acorn, state=state(), worker_id="worker"))
    claim.assert_not_called()


def test_already_accepted_request_does_not_accept_another_receipt(monkeypatch):
    claim = AsyncMock()
    monkeypatch.setattr(monitor, "claim_clear_acceptance_job", claim)
    acorn = SimpleNamespace(load_data=AsyncMock(), get_clear_receipts=AsyncMock(return_value=[
        receipt(status="accepted"), receipt(event_id="b" * 64),
    ]))
    asyncio.run(monitor.monitor_request(engine=None, acorn=acorn, state=state(), worker_id="worker"))
    claim.assert_not_called()
