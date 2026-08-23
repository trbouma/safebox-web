from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from html import unescape
import io
import json
import os
import re
import sqlite3
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pathlib import Path
import zipfile

from cryptography.fernet import Fernet
import pytest
from sqlmodel import Session


# app.main deliberately refuses to import without an explicit server-held key.
os.environ.setdefault("SAFEBOX_COOKIE_KEY", Fernet.generate_key().decode("ascii"))

from fastapi import HTTPException
from fastapi.testclient import TestClient

from acorn import (
    Acorn,
    RECORD_PRESENTATION_PREFIX,
    RecordTransferDescriptor,
    RecordTransferEnvelope,
    encode_record_presentation_descriptor,
    encode_record_transfer_descriptor,
    encrypt_record_transfer_envelope,
    record_protection_key_from_entropy,
    record_protection_recovery_phrase,
)
import app.main as main_module
import app.security as security_module
from app.config import (
    DEFAULT_SESSION_TTL_HOURS,
    DEFAULT_SESSION_TTL_SECONDS,
    Settings,
)

PKPASS_FIXTURE = Path("/Users/trbouma/projects/safebox-acorn/tests/fixtures/Example.pkpass")
W3C_DEGREE_FIXTURE = Path("/Users/trbouma/projects/safebox-web/tests/fixtures/w3c_degree.json")
MDL_FIXTURE = Path("/Users/trbouma/projects/safebox-web/tests/fixtures/example_mdl.mdoc")
EUDI_PID_FIXTURE = Path(
    "/Users/trbouma/projects/safebox-web/tests/fixtures/eudi_pid_synthetic.mdoc"
)
PKPASS_MIME_TYPE = "application/vnd.apple.pkpass"
from app.dependencies import (
    get_background_acorn_factory,
    get_acorn,
    get_deposit_acorn,
    get_loaded_acorn,
    get_payment_acorn,
    get_record_acorn,
    get_receive_acorn,
    get_session_credentials,
)
from app.main import create_app
from app.funds_finalization import claim_finalization_job, get_finalization_job
from app.clear_acceptance import get_clear_acceptance_job
from app.security import (
    CsrfProtector,
    DepositQuoteCipher,
    DepositQuoteState,
    InvoicePaymentCipher,
    InvoicePaymentState,
    SECURE_COOKIE_NAME,
    SessionCipher,
    SessionCredentials,
    normalize_bootstrap_relay,
)


TEST_KEY = Fernet.generate_key().decode("ascii")
TEST_SETTINGS = Settings(cookie_key=TEST_KEY, session_ttl_seconds=3600)
TEST_MNEMONIC = (
    "abandon abandon abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon abandon abandon abandon abandon art"
)
TEST_NSEC = "nsec1yddnfntunakhu3v4ll56ujcuk4sxm79v526j05s2qly026ergt6q682r8v"
TEST_NPUB = "npub1"
TEST_RPK = "a5" * 32
TEST_RPK_PHRASE = record_protection_recovery_phrase(TEST_RPK)
TEST_TRANSFER_DESCRIPTOR = encode_record_transfer_descriptor(
    RecordTransferDescriptor(
        blob_url="https://blossom.getsafebox.app/" + "ab" * 32,
        ciphertext_sha256="ab" * 32,
        secret=b"s" * 32,
        expires_at=2_000_000_000,
    )
)
TEST_PRESENTATION_DESCRIPTOR = (
    RECORD_PRESENTATION_PREFIX
    + TEST_TRANSFER_DESCRIPTOR.split(":", 2)[2]
)


def make_https_client() -> TestClient:
    return TestClient(create_app(TEST_SETTINGS), base_url="https://safebox.example")


def valid_csrf_token() -> str:
    return CsrfProtector(TEST_SETTINGS).issue()


def test_ecash_retention_duration_uses_readable_units() -> None:
    assert main_module._humanize_retention(3_600) == "1 hour"
    assert main_module._humanize_retention(7_200) == "2 hours"
    assert main_module._humanize_retention(86_400) == "1 day"
    assert main_module._humanize_retention(604_800) == "1 week"
    assert main_module._humanize_retention(1_209_600) == "2 weeks"
    assert main_module._humanize_retention(2_592_000) == "1 month"


def test_ecash_retention_notice_explains_disabled_expiration() -> None:
    settings = replace(
        TEST_SETTINGS,
        service_acorn_enabled=True,
        service_acorn_gift_wrap_retention_seconds=None,
    )

    notice = main_module._ecash_retention_notice(settings)

    assert "Funds transfer message retention" in notice
    assert "does not request automatic expiration" in notice
    assert "own policy" in notice


def database_settings(tmp_path) -> Settings:
    return replace(
        TEST_SETTINGS,
        database_url=f"sqlite:///{tmp_path / 'database.db'}",
    )


class FakeLoadedAcorn:
    pubkey_bech32 = (
        "npub10xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqpkge6d"
    )
    home_relay = "wss://relay.example.com"
    home_mint = "https://mint.example.com"

    def __init__(
        self,
        balance: int = 321,
        deposit_paid: bool = True,
        verified_balance: int | None = None,
        verification_status: str = "clean",
        transaction_history: list[dict] | None = None,
        receive_result: dict | None = None,
        delayed_transaction_history: list[list[dict]] | None = None,
    ) -> None:
        self.balance = balance
        self.proofs = [object()] if balance else []
        self.deposit_paid = deposit_paid
        self.verified_balance = balance if verified_balance is None else verified_balance
        self.verification_status = verification_status
        self.proof_check_calls = 0
        self.loaded = False
        self.payments: list[dict] = []
        self.ecash_transfers: list[dict] = []
        self.invoice_payments: list[dict] = []
        self.deposit_calls: list[int] = []
        self.quote_checks: list[tuple[str, int]] = []
        self.record_put_calls: list[dict] = []
        self.record_delete_calls: list[dict] = []
        self.record_delete_result = {
            "status": "DELETE_REQUESTED",
            "hidden_on": [self.home_relay],
            "blob_cleanup": None,
            "index_error": None,
        }
        self.history_entries: list[dict] = list(transaction_history or [])
        self.receive_result = receive_result or {
            "queried": 0,
            "accepted_count": 0,
            "accepted_amount": 0,
        }
        self.receive_calls = 0
        self.receive_finalize_values: list[bool] = []
        self.preview_calls = 0
        self.incoming_preview = {"previewed_count": 0, "previewed_amount": 0}
        self.repair_calls = 0
        self.repair_force_values: list[bool] = []
        self.stale_reconciliation_calls = 0
        self.stale_reconciliation_result = {
            "status": "OK",
            "operation": "reconcile-stale-proofs",
            "removed": 0,
            "amount": 0,
            "balance": balance,
        }
        self.delayed_transaction_history = list(delayed_transaction_history or [])
        self.record_transfer_create_calls: list[dict] = []
        self.record_transfer_inspect_calls: list[dict] = []
        self.record_transfer_accept_calls: list[dict] = []
        self.record_transfer_delete_calls: list[dict] = []
        self.record_presentation_create_calls: list[dict] = []
        self.record_presentation_inspect_calls: list[dict] = []
        self.deferred_recovery_state = {"status": "ABSENT", "pending": False}
        self.deferred_recovery = {"status": "ABSENT", "pending": False}
        self.deferred_recovery_complete_calls = 0
        self.record_protection_activation_calls: list[str] = []
        self.continuity_receipts: list[dict] = []
        self.continuity_reconciliation = {
            "confirmed_count": 0,
            "confirmed_amount": 0,
            "pending_count": 0,
            "pending_amount": 0,
            "terminal_error_count": 0,
            "terminal_error_amount": 0,
        }
        self.clear_receipts: list[dict] = []
        self.clear_balances: list[dict] = []
        self.clear_transaction_history: list[dict] = []
        self.accepted_clear_receipts: list[str] = []
        self.deleted_clear_receipts: list[str] = []
        self.clear_sweep_calls = 0
        self.clear_sweep_receipts: list[dict] = []
        self.clear_preview_calls = 0
        self.clear_preview: dict = {
            "previewed_count": 0,
            "previewed_amount": 0,
            "previewed": [],
        }
        self.clear_transfers: list[dict] = []
        self.balance_snapshot_reads = 0
        self.balance_snapshot_publishes = 0

    async def load_data(self) -> None:
        self.loaded = True

    async def get_balance_snapshot(self) -> dict:
        self.balance_snapshot_reads += 1
        return {
            "type": "acorn-balance-snapshot",
            "version": 1,
            "observed_at": 1_786_430_400,
            "cash": {
                "amount": self.balance,
                "proof_count": len(self.proofs),
                "event_ids": [],
            },
            "clear": list(self.clear_balances),
        }

    async def publish_balance_snapshot(self, **kwargs) -> dict:
        self.balance_snapshot_publishes += 1
        return await self.get_balance_snapshot()

    async def get_deferred_recovery_status(self) -> dict:
        return self.deferred_recovery_state

    async def get_clear_receipts(self, status: str | None = None) -> list[dict]:
        if status is None:
            return list(self.clear_receipts)
        return [
            receipt
            for receipt in self.clear_receipts
            if str(receipt.get("status") or "pending") == status
        ]

    async def get_clear_balances(self) -> list[dict]:
        return list(self.clear_balances)

    async def get_clear_transaction_history(self) -> list[dict]:
        return list(self.clear_transaction_history)

    async def accept_pending_clear_receipt(self, event_id: str) -> dict:
        for receipt in self.clear_receipts:
            if str(receipt.get("event_id") or "") != event_id:
                continue
            if str(receipt.get("status") or "pending") != "pending":
                raise ValueError("Only pending Clear transfers can be accepted")
            receipt["status"] = "accepted"
            self.accepted_clear_receipts.append(event_id)
            amount = int(receipt.get("amount") or 0)
            mint = str(receipt.get("mint") or "")
            unit = str(receipt.get("unit") or "")
            matching = next(
                (
                    balance
                    for balance in self.clear_balances
                    if balance.get("mint") == mint and balance.get("unit") == unit
                ),
                None,
            )
            if matching is None:
                self.clear_balances.append({
                    "mint": mint,
                    "unit": unit,
                    "amount": amount,
                    "proof_count": 1,
                })
            else:
                matching["amount"] = int(matching.get("amount") or 0) + amount
            self.clear_transaction_history.append({
                "event_id": "history-" + event_id,
                "direction": "in",
                "operation": "accept",
                "amount": amount,
                "mint": mint,
                "unit": unit,
                "timestamp": int(receipt.get("timestamp") or 0),
                "memo": str(receipt.get("comment") or ""),
                "source_event": event_id,
            })
            return {
                "status": "OK",
                "event_id": event_id,
                "accepted": True,
                "amount": amount,
                "mint": mint,
                "unit": unit,
            }
        raise ValueError("Pending Clear transfer was not found")

    async def delete_pending_clear_receipt(self, event_id: str) -> dict:
        for index, receipt in enumerate(self.clear_receipts):
            if str(receipt.get("event_id") or "") != event_id:
                continue
            if str(receipt.get("status") or "pending") != "pending":
                raise ValueError("Only pending Clear transfers can be deleted")
            self.deleted_clear_receipts.append(event_id)
            self.clear_receipts.pop(index)
            return {"status": "OK", "event_id": event_id, "deleted": True}
        raise ValueError("Pending Clear transfer was not found")

    async def sweep_clear_transfers(
        self,
        *,
        preview_only: bool = False,
        advance_cursor: bool = True,
        event_id: str | None = None,
    ) -> dict:
        if preview_only:
            self.clear_preview_calls += 1
            return self.clear_preview
        self.clear_sweep_calls += 1
        source = self.clear_sweep_receipts
        if event_id:
            source = [
                receipt
                for receipt in [
                    *self.clear_sweep_receipts,
                    *(self.clear_preview.get("previewed") or []),
                ]
                if str(receipt.get("event_id") or "") == event_id
            ]
        existing_ids = {
            str(receipt.get("event_id") or "") for receipt in self.clear_receipts
        }
        stored = [
            {
                **dict(receipt),
                "mint": str(
                    receipt.get("mint")
                    or ((receipt.get("mints") or [""])[0])
                ),
                "status": "pending",
            }
            for receipt in source
            if str(receipt.get("event_id") or "") not in existing_ids
        ]
        self.clear_receipts.extend(stored)
        return {
            "status": "OK",
            "stored": stored,
            "stored_count": len(stored),
            "stored_amount": sum(int(receipt.get("amount") or 0) for receipt in stored),
        }

    async def get_deferred_recovery(self) -> dict:
        return self.deferred_recovery

    async def complete_deferred_recovery(self) -> dict:
        self.deferred_recovery_complete_calls += 1
        self.deferred_recovery_state = {"status": "COMPLETE", "pending": False}
        self.deferred_recovery = {"status": "COMPLETE", "pending": False}
        return {"status": "COMPLETE", "pending": False, "completed": True}

    async def activate_record_protection(self, *, record_protection_key: str) -> dict:
        self.record_protection_activation_calls.append(record_protection_key)
        return {"status": "ACTIVE", "active": True, "verified": True}

    async def check_proofs(self) -> dict:
        self.proof_check_calls += 1
        return {
            "status": self.verification_status,
            "recommendation": "Review the proof state.",
            "mint_confirmed_unspent": {
                "amount": self.verified_balance,
                "proof_count": len(self.proofs) if self.verified_balance else 0,
            },
        }

    async def repair_proofs(self, force_prune_stale: bool = False) -> str:
        self.repair_calls += 1
        self.repair_force_values.append(force_prune_stale)
        self.verification_status = "clean"
        self.verified_balance = self.balance
        return "repair-proofs completed"

    async def reconcile_stale_proofs(self) -> dict:
        self.stale_reconciliation_calls += 1
        return dict(self.stale_reconciliation_result)

    def get_balance(self) -> int:
        return self.balance

    async def get_user_record_labels(self) -> list[str]:
        return ["Field Notes", "Travel/2026", "A & B"]

    async def record_exists(self, label: str) -> bool:
        return label in await self.get_user_record_labels()

    async def get_record_safebox(self, record_name: str):
        class Record:
            type = "generic"
            payload = {
                "label": record_name,
                "unsafe": "<script>alert('no')</script>",
            }

        return Record()

    async def create_record_transfer(self, record_name: str, **kwargs) -> dict:
        self.record_transfer_create_calls.append({"record_name": record_name, **kwargs})
        return {
            "descriptor": TEST_TRANSFER_DESCRIPTOR,
            "expires_at": 2_000_000_000,
            "label": record_name,
            "has_original": False,
        }

    async def inspect_record_transfer(self, descriptor: str, **kwargs) -> dict:
        self.record_transfer_inspect_calls.append({"descriptor": descriptor, **kwargs})
        return {
            "label": "Shared Notes",
            "record_type": "generic",
            "has_original": True,
            "blob_type": "application/pdf",
            "expires_at": 2_000_000_000,
            "server": "https://blossom.getsafebox.app",
        }

    async def accept_record_transfer(self, descriptor: str, **kwargs) -> dict:
        self.record_transfer_accept_calls.append({"descriptor": descriptor, **kwargs})
        return {
            "status": "OK",
            "label": kwargs["record_name"],
            "has_original": True,
            "transfer_deleted": True,
            "cleanup_error": None,
        }

    async def delete_record_transfer(self, descriptor: str, **kwargs) -> dict:
        self.record_transfer_delete_calls.append({"descriptor": descriptor, **kwargs})
        return {"status": "DELETED", "transfer_deleted": True}

    async def create_record_presentation(self, record_name: str, **kwargs) -> dict:
        self.record_presentation_create_calls.append({"record_name": record_name, **kwargs})
        return {
            "descriptor": TEST_PRESENTATION_DESCRIPTOR,
            "expires_at": 2_000_000_000,
            "label": record_name,
            "has_original": True,
        }

    async def inspect_record_presentation(self, descriptor: str, **kwargs) -> dict:
        self.record_presentation_inspect_calls.append({"descriptor": descriptor, **kwargs})
        blob_data = b"presented PDF bytes"
        return {
            "label": "Presented Credential",
            "record_type": "generic",
            "payload": {"credential": "verified copy"},
            "has_original": True,
            "blob_data": blob_data,
            "blob_type": "application/pdf",
            "blob_sha256": __import__("hashlib").sha256(blob_data).hexdigest(),
            "expires_at": 2_000_000_000,
            "server": "https://blossom.getsafebox.app",
        }

    async def put_record(self, **kwargs):
        self.record_put_calls.append(kwargs)
        return {
            "status": "OK",
            "label": kwargs["record_name"],
            "event_id": "record-event-1",
            "verified": True,
        }

    async def delete_record(self, label: str, **kwargs):
        self.record_delete_calls.append({"label": label, **kwargs})
        return self.record_delete_result

    async def pay_multi(self, amount: int, lnaddress: str, comment: str):
        self.payments.append(
            {"amount": amount, "lnaddress": lnaddress, "comment": comment}
        )
        self.balance -= amount + 1
        return f"Payment of {amount} sats successful!", 1

    async def send_ecash_transfer(
        self,
        *,
        amount: int,
        recipient: str,
        relay: str,
        comment: str,
        **kwargs,
    ):
        self.ecash_transfers.append(
            {
                "amount": amount,
                "recipient": recipient,
                "relay": relay,
                "comment": comment,
                **kwargs,
            }
        )
        self.balance -= amount
        return {"status": "OK", "event_id": "ecash-event-1"}

    async def send_clear_transfer(self, **kwargs):
        self.clear_transfers.append(dict(kwargs))
        for balance in self.clear_balances:
            if (
                str(balance.get("mint") or "").rstrip("/")
                == str(kwargs.get("mint") or "").rstrip("/")
                and str(balance.get("unit") or "") == str(kwargs.get("unit") or "")
            ):
                balance["amount"] = int(balance.get("amount") or 0) - int(
                    kwargs.get("amount") or 0
                )
                break
        return {
            "status": "OK",
            "event_id": "clear-event-1",
            "fee": 0,
        }

    async def pay_multi_invoice(self, lninvoice: str, comment: str):
        self.invoice_payments.append(
            {"invoice": lninvoice, "comment": comment}
        )
        self.balance -= 22
        return "Paid 21 sats with fees 1 sats successful!", 1, "hash", "preimage", None

    def deposit(self, amount: int):
        self.deposit_calls.append(amount)
        return SimpleNamespace(
            invoice="lnbc21n1pytestinvoice",
            quote="pytest-deposit-quote",
        )

    async def check_quote(self, quote: str, amount: int):
        self.quote_checks.append((quote, amount))
        if self.deposit_paid:
            self.balance += amount
            return True, "lnbc21n1pytestinvoice"
        return False, None

    async def add_tx_history(self, **entry) -> None:
        self.history_entries.append(entry)

    async def get_tx_history(self) -> list[dict]:
        if self.delayed_transaction_history:
            self.history_entries = self.delayed_transaction_history.pop(0)
        return self.history_entries

    async def get_continuity_receipts(self) -> list[dict]:
        return self.continuity_receipts

    async def reconcile_continuity_receipts(self) -> dict:
        return self.continuity_reconciliation

    async def sweep_ecash_transfers(
        self,
        *,
        preview_only: bool = False,
        finalize: bool = True,
    ) -> dict:
        if preview_only:
            self.preview_calls += 1
            return self.incoming_preview
        self.receive_calls += 1
        self.receive_finalize_values.append(finalize)
        return self.receive_result


def stub_deferred_recovery_status(acorn) -> None:
    async def absent_status() -> dict:
        return {"status": "ABSENT", "pending": False}

    acorn.get_deferred_recovery_status = absent_status

    async def balance_snapshot() -> dict:
        return {
            "type": "acorn-balance-snapshot",
            "version": 1,
            "observed_at": 1_786_430_400,
            "cash": {"amount": 0, "proof_count": 0, "event_ids": []},
            "clear": [],
        }

    acorn.get_balance_snapshot = balance_snapshot


class FakeCreatedAcorn:
    instances: list["FakeCreatedAcorn"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.pubkey_bech32 = "npub1newcomponent"
        self.pubkey_hex = "00000000" + "11" * 28
        self.created_seed_phrase: str | None = None
        self.retain_seed_phrase: bool | None = None
        self.loaded = False
        self.deferred_recovery_store_calls: list[dict] = []
        self.__class__.instances.append(self)

    async def create_instance(
        self,
        seed_phrase: str,
        retain_seed_phrase: bool = True,
    ) -> str:
        self.created_seed_phrase = seed_phrase
        self.retain_seed_phrase = retain_seed_phrase
        return TEST_NSEC

    async def load_data(self) -> None:
        self.loaded = True

    async def store_deferred_recovery(self, **kwargs) -> dict:
        self.deferred_recovery_store_calls.append(kwargs)
        return {"status": "PENDING", "pending": True, "verified": True}


class FakeBlobAcorn(FakeLoadedAcorn):
    def __init__(
        self,
        *,
        existing_labels: set[str] | None = None,
        downloaded_type: str | None = "text/plain",
        downloaded_data: bytes | None = b"private blob contents",
        record_lookup_error: ValueError | None = None,
        blob_type: str | None = "text/plain",
        payload: dict | None = None,
        orig_sha256: str | None = "1ea23f2b" + "0" * 56,
    ) -> None:
        super().__init__()
        self.existing_labels = set(existing_labels or set())
        self.downloaded_type = downloaded_type
        self.downloaded_data = downloaded_data
        self.record_lookup_error = record_lookup_error
        self.blob_type = blob_type
        self.payload = payload or {"filename": "notes.txt", "description": "Private notes"}
        self.orig_sha256 = orig_sha256
        self.blob_reads: list[str] = []

    async def get_record_safebox(self, record_name: str):
        if self.record_lookup_error is not None:
            raise self.record_lookup_error
        if record_name not in self.existing_labels:
            raise ValueError("record not found")
        return SimpleNamespace(
            type="blob",
            payload=self.payload,
            blobref="https://blossom.example/encrypted-sha256",
            blobtype=self.blob_type,
            origsha256=self.orig_sha256,
        )

    async def get_user_record_labels(self) -> list[str]:
        return sorted(self.existing_labels)

    async def record_exists(self, label: str) -> bool:
        return label in self.existing_labels

    async def get_record_blobdata(self, record_name: str):
        self.blob_reads.append(record_name)
        return self.downloaded_type, self.downloaded_data

    async def put_record(self, **kwargs):
        result = await super().put_record(**kwargs)
        if kwargs.get("blob_data") is not None:
            self.existing_labels.add(kwargs["record_name"])
            self.payload = kwargs["record_value"]
            self.blob_type = kwargs.get("blob_type")
            self.downloaded_type = kwargs.get("blob_type")
            self.downloaded_data = kwargs.get("blob_data")
        return result


def test_settings_load_cookie_key_from_working_directory_env_file(
    tmp_path, monkeypatch
) -> None:
    env_key = Fernet.generate_key().decode("ascii")
    (tmp_path / ".env").write_text(
        f"SAFEBOX_COOKIE_KEY={env_key}\n"
        "SAFEBOX_SESSION_TTL_HOURS=2\n"
        "SAFEBOX_BACKGROUND_JOB_THREADS=3\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SAFEBOX_COOKIE_KEY", raising=False)
    monkeypatch.delenv("SAFEBOX_SESSION_TTL_HOURS", raising=False)
    monkeypatch.delenv("SAFEBOX_SESSION_TTL_SECONDS", raising=False)
    monkeypatch.delenv("SAFEBOX_BACKGROUND_JOB_THREADS", raising=False)

    settings = Settings.from_env()

    assert settings.cookie_key == env_key
    assert settings.session_ttl_seconds == 7200
    assert settings.background_job_threads == 3


def test_default_session_lifetime_is_30_days() -> None:
    settings = Settings(cookie_key=TEST_KEY)

    assert DEFAULT_SESSION_TTL_HOURS == 720
    assert DEFAULT_SESSION_TTL_SECONDS == 2_592_000
    assert settings.session_ttl_seconds == DEFAULT_SESSION_TTL_SECONDS
    assert settings.onboard_invite_code == "INVITEME"
    assert settings.onboard_invite_codes == ("INVITEME",)
    assert settings.background_job_threads == 2


def test_settings_load_onboard_invite_code_from_env(tmp_path, monkeypatch) -> None:
    env_key = Fernet.generate_key().decode("ascii")
    (tmp_path / ".env").write_text(
        f"SAFEBOX_COOKIE_KEY={env_key}\n"
        "SAFEBOX_ONBOARD_INVITE_CODE=COMMUNITY42,PARTNER7\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SAFEBOX_COOKIE_KEY", raising=False)
    monkeypatch.delenv("SAFEBOX_ONBOARD_INVITE_CODE", raising=False)

    settings = Settings.from_env()

    assert settings.onboard_invite_code == "COMMUNITY42"
    assert settings.onboard_invite_codes == ("COMMUNITY42", "PARTNER7")


def test_settings_load_comma_delimited_ws_relay_allowlist(
    tmp_path, monkeypatch
) -> None:
    env_key = Fernet.generate_key().decode("ascii")
    (tmp_path / ".env").write_text(
        f"SAFEBOX_COOKIE_KEY={env_key}\n"
        "SAFEBOX_ALLOWED_WS_RELAYS=ws://localhost:8735, ws://beelink:7777\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SAFEBOX_COOKIE_KEY", raising=False)
    monkeypatch.delenv("SAFEBOX_ALLOWED_WS_RELAYS", raising=False)

    settings = Settings.from_env()

    assert settings.allowed_ws_relays == (
        "ws://localhost:8735",
        "ws://beelink:7777",
    )


def test_ws_relay_requires_exact_allowlist_entry() -> None:
    with pytest.raises(ValueError, match="SAFEBOX_ALLOWED_WS_RELAYS"):
        normalize_bootstrap_relay("ws://localhost:8735")

    assert normalize_bootstrap_relay(
        "ws://LOCALHOST:8735",
        ("ws://localhost:8735",),
    ) == "ws://localhost:8735"


def test_ws_relay_allowlist_requires_explicit_port() -> None:
    with pytest.raises(ValueError, match="explicit port"):
        normalize_bootstrap_relay(
            "ws://localhost",
            ("ws://localhost",),
        )


def test_connect_accepts_exactly_allowlisted_ws_relay() -> None:
    settings = replace(
        TEST_SETTINGS,
        allowed_ws_relays=("ws://localhost:8735",),
    )
    client = TestClient(
        create_app(settings),
        base_url="https://safebox.example",
    )

    response = client.post(
        "/connect",
        data={
            "csrf_token": CsrfProtector(settings).issue(),
            "secret_type": "nsec",
            "secret": TEST_NSEC,
            "bootstrap_relay": "ws://LOCALHOST:8735",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    token = client.cookies.get(SECURE_COOKIE_NAME)
    assert token is not None
    credentials = SessionCipher(settings).decode(token)
    assert credentials.bootstrap_relay == "ws://localhost:8735"


def test_legacy_session_lifetime_seconds_remains_supported(
    tmp_path, monkeypatch
) -> None:
    env_key = Fernet.generate_key().decode("ascii")
    (tmp_path / ".env").write_text(
        f"SAFEBOX_COOKIE_KEY={env_key}\nSAFEBOX_SESSION_TTL_SECONDS=7200\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SAFEBOX_COOKIE_KEY", raising=False)
    monkeypatch.delenv("SAFEBOX_SESSION_TTL_HOURS", raising=False)
    monkeypatch.delenv("SAFEBOX_SESSION_TTL_SECONDS", raising=False)

    settings = Settings.from_env()

    assert settings.session_ttl_seconds == 7200


def test_non_loopback_http_is_rejected() -> None:
    client = TestClient(create_app(TEST_SETTINGS), base_url="http://safebox.example")

    response = client.get("/health")

    assert response.status_code == 400
    assert "HTTPS is required" in response.json()["detail"]


def test_direct_127001_http_is_allowed() -> None:
    client = TestClient(
        create_app(TEST_SETTINGS),
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50123),
    )

    response = client.get("/health")

    assert response.status_code == 200


def test_onboard_page_displays_acorn_safebox_relationship_visual() -> None:
    response = make_https_client().get("/onboard/INVITEME")

    assert response.status_code == 200
    assert 'aria-label="Safebox page tools"' in response.text
    assert 'aria-label="About and advisory notice"' in response.text
    assert 'aria-label="About Acorn"' in response.text
    assert 'aria-label="About Safebox"' in response.text
    assert "Detailed advisories can be found at the bottom" in response.text
    assert "Acorn</strong> is the user-controlled component" in response.text
    assert "Safebox</strong> is the web interface" in response.text
    assert "relationship-arrow" not in response.text
    assert response.text.count('name="header-explanation"') == 3
    assert "User-controlled component" not in response.text
    assert "User-controlled session" not in response.text
    assert "Web service surface" not in response.text


def test_root_redirects_valid_existing_session_to_wallet() -> None:
    client = make_https_client()
    client.cookies.set(
        SECURE_COOKIE_NAME,
        SessionCipher(TEST_SETTINGS).encode(
            SessionCredentials(
                nsec=TEST_NSEC,
                bootstrap_relay="wss://relay.example.com",
            )
        ),
        domain="safebox.example",
        path="/",
    )

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/wallet"


def test_root_renders_existing_acorn_connection_without_a_session() -> None:
    response = make_https_client().get("/", follow_redirects=False)

    assert response.status_code == 200
    assert "Connect an Acorn" in response.text
    assert 'action="/connect"' in response.text
    assert 'class="page-navigation"' not in response.text
    assert "Create a new Acorn" in response.text
    assert 'href="/rates">View Exchange Rates</a>' in response.text


def test_public_rates_page_uses_cached_rows_without_a_session(tmp_path) -> None:
    settings = replace(
        database_settings(tmp_path),
        currency_rates_enabled=True,
        currency_rate_stale_seconds=3_600,
    )
    app = create_app(settings)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with TestClient(app, base_url="https://safebox.example") as client:
        with Session(app.state.database_engine) as session:
            from app.models import CurrencyRate

            session.add_all(
                [
                    CurrencyRate(
                        currency_code="USD",
                        fiat_per_btc=100_000.5,
                        currency_symbol="$",
                        currency_description="United States dollar",
                        source="https://rates.example/ticker",
                        fetched_at=now,
                        updated_at=now,
                    ),
                    CurrencyRate(
                        currency_code="CAD",
                        fiat_per_btc=140_000.0,
                        currency_symbol="$",
                        currency_description="Canadian dollar",
                        source="https://rates.example/ticker",
                        fetched_at=now - timedelta(hours=2),
                        updated_at=now - timedelta(hours=2),
                    ),
                ]
            )
            session.commit()
        response = client.get("/rates")

    assert response.status_code == 200
    assert "Exchange Rates" in response.text
    assert "$140,000.00" in response.text
    assert "$100,000.50" in response.text
    assert response.text.index("CAD") < response.text.index("USD")
    assert response.text.count("Possibly stale") == 1
    assert "Acorn connection required" not in response.text
    assert "set-cookie" not in response.headers
    assert response.text.index("<h1>Exchange Rates</h1>") < response.text.index(
        'class="page-navigation"'
    )
    assert response.text.count('class="page-navigation') == 2
    assert response.text.index("<summary>Advisories</summary>") < response.text.rindex(
        'class="page-navigation'
    )


def test_public_rates_page_explains_when_cache_is_empty(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/rates")

    assert response.status_code == 200
    assert "not enabled by this Safebox operator" in response.text


def test_root_clears_invalid_session_and_renders_existing_acorn_connection() -> None:
    client = make_https_client()
    client.cookies.set(
        SECURE_COOKIE_NAME,
        "invalid-session",
        domain="safebox.example",
        path="/",
    )

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 200
    assert "Connect an Acorn" in response.text
    assert SECURE_COOKIE_NAME not in client.cookies


def test_disconnect_clears_session_and_redirects_to_root() -> None:
    client = make_https_client()
    client.cookies.set(
        SECURE_COOKIE_NAME,
        SessionCipher(TEST_SETTINGS).encode(
            SessionCredentials(
                nsec=TEST_NSEC,
                bootstrap_relay="wss://relay.example.com",
            )
        ),
        domain="safebox.example",
        path="/",
    )

    response = client.post(
        "/disconnect",
        data={"csrf_token": valid_csrf_token(), "confirmed": "yes"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert SECURE_COOKIE_NAME not in client.cookies


def test_disconnect_requires_recovery_confirmation() -> None:
    client = make_https_client()
    client.cookies.set(
        SECURE_COOKIE_NAME,
        SessionCipher(TEST_SETTINGS).encode(
            SessionCredentials(
                nsec=TEST_NSEC,
                bootstrap_relay="wss://relay.example.com",
            )
        ),
        domain="safebox.example",
        path="/",
    )

    response = client.post(
        "/disconnect",
        data={"csrf_token": valid_csrf_token()},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert "Confirm that you have your recovery information" in response.text
    assert SECURE_COOKIE_NAME in client.cookies


def test_disconnect_page_can_clear_an_unloadable_session() -> None:
    client = make_https_client()
    client.cookies.set(
        SECURE_COOKIE_NAME,
        SessionCipher(TEST_SETTINGS).encode(
            SessionCredentials(
                nsec=TEST_NSEC,
                bootstrap_relay="wss://relay.example.com",
            )
        ),
        domain="safebox.example",
        path="/",
    )

    page = client.get("/disconnect")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)

    assert page.status_code == 200
    assert token is not None
    assert "Clear Session and Disconnect" in page.text
    assert SECURE_COOKIE_NAME in client.cookies

    response = client.post(
        "/disconnect",
        data={"csrf_token": token.group(1), "confirmed": "yes"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert SECURE_COOKIE_NAME not in client.cookies


def test_legacy_session_routes_redirect_to_connect_and_disconnect() -> None:
    client = make_https_client()

    connect_page = client.get("/login", follow_redirects=False)
    connect_submission = client.post("/login", follow_redirects=False)
    disconnect_submission = client.post("/logout", follow_redirects=False)

    assert connect_page.status_code == 303
    assert connect_page.headers["location"] == "/connect"
    assert connect_submission.status_code == 307
    assert connect_submission.headers["location"] == "/connect"
    assert disconnect_submission.status_code == 307
    assert disconnect_submission.headers["location"] == "/disconnect"


def test_browser_balance_load_failure_offers_session_recovery() -> None:
    app = create_app(TEST_SETTINGS)

    async def unavailable_wallet():
        raise HTTPException(
            status_code=502,
            detail="Unable to load the Acorn wallet from its bootstrap relay",
        )

    app.dependency_overrides[get_loaded_acorn] = unavailable_wallet
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/transactions", headers={"Accept": "text/html"})

    assert response.status_code == 502
    assert "Unable to Load Acorn" in response.text
    assert "Try Different Recovery Material" in response.text
    assert "Clear Session and Disconnect" in response.text


def test_api_balance_load_failure_remains_json() -> None:
    app = create_app(TEST_SETTINGS)

    async def unavailable_wallet():
        raise HTTPException(
            status_code=502,
            detail="Unable to load the Acorn wallet from its bootstrap relay",
        )

    app.dependency_overrides[get_loaded_acorn] = unavailable_wallet
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/transactions", headers={"Accept": "application/json"})

    assert response.status_code == 502
    assert response.json() == {
        "detail": "Unable to load the Acorn wallet from its bootstrap relay"
    }


def test_onboard_offers_single_confirmed_fast_creation_path() -> None:
    response = make_https_client().get("/onboard/INVITEME")

    assert response.status_code == 200
    assert "Welcome to Safebox" in response.text
    assert 'action="/onboard/INVITEME"' in response.text
    assert 'name="defer_recovery"' not in response.text
    assert 'name="assign_default_handle"' not in response.text
    assert 'name="mnemonic_words"' in response.text
    assert '<option value="24" selected>24 words</option>' in response.text
    assert '<option value="12">12 words</option>' in response.text
    assert 'name="confirmed"' not in response.text
    assert 'name="confirmed" type="checkbox"' not in response.text
    assert "Create My Acorn" in response.text
    assert 'href="/connect"' not in response.text
    assert "Use an Existing Acorn" not in response.text
    assert "12- or 24-word recovery mnemonic" in response.text
    assert "defers the backup ceremony" in response.text
    assert "Protected Records can be enabled" in response.text


def test_default_handle_uses_bip39_words_and_sub_1000_suffix() -> None:
    assert main_module.default_handle_from_pubkey("00000000" + "11" * 28) == (
        "abandonabandon0"
    )
    assert main_module.default_handle_from_pubkey("ffffffff" + "11" * 28) == (
        "zoozoo23"
    )
    assert main_module.default_handle_from_pubkey(
        "00000000" + "11" * 28,
        attempt=1,
    ) == "abandonabandon1"


def test_quick_onboarding_assigns_available_default_handle(
    tmp_path,
    monkeypatch,
) -> None:
    FakeCreatedAcorn.instances.clear()
    monkeypatch.setattr(main_module, "Acorn", FakeCreatedAcorn)
    monkeypatch.setattr(
        main_module,
        "generate_seed_phrase_and_nsec",
        lambda strength=128: (TEST_MNEMONIC, TEST_NSEC),
    )
    settings = database_settings(tmp_path)
    app = create_app(settings)

    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.post(
            "/onboard/INVITEME",
            data={"csrf_token": CsrfProtector(settings).issue()},
            follow_redirects=False,
        )

    assert response.status_code == 303
    with sqlite3.connect(tmp_path / "database.db") as connection:
        row = connection.execute(
            "SELECT claimed_handle, npub, home_relay FROM claimed_handle"
        ).fetchone()
    assert row == (
        "abandonabandon0",
        "npub1newcomponent",
        settings.default_bootstrap_relay,
    )


def test_quick_onboarding_error_stays_on_streamlined_page() -> None:
    response = make_https_client().post(
        "/onboard/INVITEME",
        data={"csrf_token": "invalid"},
    )

    assert response.status_code == 403
    assert "Welcome to Safebox" in response.text
    assert "form token is invalid or expired" in response.text
    assert 'action="/onboard/INVITEME"' in response.text
    assert 'name="home_relay"' in response.text
    assert 'name="home_mint"' in response.text
    assert TEST_SETTINGS.default_bootstrap_relay in response.text
    assert TEST_SETTINGS.default_home_mint in response.text


def test_quick_onboarding_accepts_custom_home_relay_and_mint(
    tmp_path,
    monkeypatch,
) -> None:
    FakeCreatedAcorn.instances.clear()
    monkeypatch.setattr(main_module, "Acorn", FakeCreatedAcorn)
    monkeypatch.setattr(
        main_module,
        "generate_seed_phrase_and_nsec",
        lambda strength=128: (TEST_MNEMONIC, TEST_NSEC),
    )
    settings = database_settings(tmp_path)
    app = create_app(settings)

    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.post(
            "/onboard/INVITEME",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "home_relay": "relay.known.example",
                "home_mint": "mint.known.example/",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    created = FakeCreatedAcorn.instances[0]
    assert created.kwargs["home_relay"] == "wss://relay.known.example"
    assert created.kwargs["relays"] == ["wss://relay.known.example"]
    assert created.kwargs["mints"] == ["https://mint.known.example"]


def test_quick_onboarding_passes_selected_24_word_mnemonic_strength(
    tmp_path,
    monkeypatch,
) -> None:
    generated_strengths: list[int] = []
    FakeCreatedAcorn.instances.clear()
    monkeypatch.setattr(main_module, "Acorn", FakeCreatedAcorn)

    def generate_mnemonic(*, strength: int):
        generated_strengths.append(strength)
        return TEST_MNEMONIC, TEST_NSEC

    monkeypatch.setattr(
        main_module,
        "generate_seed_phrase_and_nsec",
        generate_mnemonic,
    )
    settings = database_settings(tmp_path)
    app = create_app(settings)

    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.post(
            "/onboard/INVITEME",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "mnemonic_words": "24",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/wallet"
    assert generated_strengths == [256]


def test_quick_onboarding_retries_transient_relay_readback(
    tmp_path,
    monkeypatch,
) -> None:
    class FlakyReadbackAcorn(FakeCreatedAcorn):
        load_attempts = 0

        async def load_data(self) -> None:
            self.__class__.load_attempts += 1
            if self.__class__.load_attempts == 1:
                raise RuntimeError("relay readback not visible yet")
            await super().load_data()

    FakeCreatedAcorn.instances.clear()
    monkeypatch.setattr(main_module, "Acorn", FlakyReadbackAcorn)
    monkeypatch.setattr(
        main_module,
        "generate_seed_phrase_and_nsec",
        lambda strength=128: (TEST_MNEMONIC, TEST_NSEC),
    )
    settings = database_settings(tmp_path)
    app = create_app(settings)

    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.post(
            "/onboard/INVITEME",
            data={"csrf_token": CsrfProtector(settings).issue()},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/wallet"
    assert FlakyReadbackAcorn.load_attempts == 2
    assert FakeCreatedAcorn.instances[0].loaded is True


def test_quick_onboarding_retries_transient_deferred_recovery_readback(
    tmp_path,
    monkeypatch,
) -> None:
    class FlakyDeferredRecoveryAcorn(FakeCreatedAcorn):
        deferred_attempts = 0

        async def store_deferred_recovery(self, **kwargs) -> dict:
            self.__class__.deferred_attempts += 1
            self.deferred_recovery_store_calls.append(kwargs)
            if self.__class__.deferred_attempts == 1:
                return {"status": "PENDING", "pending": True, "verified": False}
            return {"status": "PENDING", "pending": True, "verified": True}

    FakeCreatedAcorn.instances.clear()
    monkeypatch.setattr(main_module, "Acorn", FlakyDeferredRecoveryAcorn)
    monkeypatch.setattr(
        main_module,
        "generate_seed_phrase_and_nsec",
        lambda strength=256: (TEST_MNEMONIC, TEST_NSEC),
    )
    settings = database_settings(tmp_path)
    app = create_app(settings)

    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.post(
            "/onboard/INVITEME",
            data={"csrf_token": CsrfProtector(settings).issue()},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/wallet"
    assert FlakyDeferredRecoveryAcorn.deferred_attempts == 2
    assert len(FakeCreatedAcorn.instances[0].deferred_recovery_store_calls) == 2


def test_quick_onboarding_rejects_invalid_mnemonic_length() -> None:
    response = make_https_client().post(
        "/onboard/INVITEME",
        data={
            "csrf_token": valid_csrf_token(),
            "mnemonic_words": "18",
        },
    )

    assert response.status_code == 400
    assert "Choose a 12- or 24-word Safebox Acorn mnemonic" in response.text
    assert 'action="/onboard/INVITEME"' in response.text


def test_ordinary_creation_assigns_default_handle(
    tmp_path,
    monkeypatch,
) -> None:
    FakeCreatedAcorn.instances.clear()
    monkeypatch.setattr(main_module, "Acorn", FakeCreatedAcorn)
    monkeypatch.setattr(
        main_module,
        "generate_seed_phrase_and_nsec",
        lambda strength=128: (TEST_MNEMONIC, TEST_NSEC),
    )
    settings = database_settings(tmp_path)
    app = create_app(settings)

    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.post(
            "/create",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "home_relay": "relay.example.com",
                "home_mint": "mint.example.com",
                "assign_default_handle": "yes",
                "confirmed": "yes",
            },
        )

    assert response.status_code == 201
    with sqlite3.connect(tmp_path / "database.db") as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM claimed_handle"
        ).fetchone()[0]
        handle = connection.execute(
            "SELECT claimed_handle FROM claimed_handle"
        ).fetchone()[0]
    assert count == 1
    assert handle == "abandonabandon0"


def test_default_handle_collision_advances_numeric_suffix(
    tmp_path,
    monkeypatch,
) -> None:
    FakeCreatedAcorn.instances.clear()
    monkeypatch.setattr(main_module, "Acorn", FakeCreatedAcorn)
    monkeypatch.setattr(
        main_module,
        "generate_seed_phrase_and_nsec",
        lambda strength=128: (TEST_MNEMONIC, TEST_NSEC),
    )
    settings = database_settings(tmp_path)
    app = create_app(settings)

    with TestClient(app, base_url="https://safebox.example") as client:
        with sqlite3.connect(tmp_path / "database.db") as connection:
            connection.execute(
                "INSERT INTO claimed_handle "
                "(claimed_handle, npub, home_relay) VALUES (?, ?, ?)",
                (
                    "abandonabandon0",
                    "npub1somebodyelse",
                    "wss://relay.other.example",
                ),
            )
            connection.commit()
        response = client.post(
            "/create",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "home_relay": "relay.example.com",
                "home_mint": "mint.example.com",
                "defer_recovery": "yes",
                "assign_default_handle": "yes",
                "confirmed": "yes",
            },
            follow_redirects=False,
        )

    assert response.status_code == 303
    with sqlite3.connect(tmp_path / "database.db") as connection:
        handles = connection.execute(
            "SELECT claimed_handle FROM claimed_handle ORDER BY id"
        ).fetchall()
    assert handles == [("abandonabandon0",), ("abandonabandon1",)]


def test_onboard_redirects_to_default_invite_path() -> None:
    response = make_https_client().get("/onboard", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/onboard/INVITEME"


def test_custom_onboard_invite_code_controls_onboarding_path() -> None:
    settings = replace(TEST_SETTINGS, onboard_invite_codes=("COMMUNITY42", "PARTNER7"))
    client = TestClient(create_app(settings), base_url="https://safebox.example")

    response = client.get("/onboard", follow_redirects=False)
    invited = client.get("/onboard/COMMUNITY42")
    secondary = client.get("/onboard/partner7")

    assert response.status_code == 303
    assert response.headers["location"] == "/onboard/COMMUNITY42"
    assert invited.status_code == 200
    assert 'action="/onboard/COMMUNITY42"' in invited.text
    assert secondary.status_code == 200
    assert 'action="/onboard/PARTNER7"' in secondary.text


def test_unknown_onboard_invite_code_is_not_accepted() -> None:
    response = make_https_client().get("/onboard/WRONGCODE")

    assert response.status_code == 404
    assert "Invite code not found." in response.text


def test_friend_is_not_a_special_onboarding_route() -> None:
    response = make_https_client().get("/onboard/friend")

    assert response.status_code == 404
    assert "Invite code not found." in response.text


def test_friend_can_only_work_as_configured_invite_code() -> None:
    settings = replace(TEST_SETTINGS, onboard_invite_codes=("INVITEME", "FRIEND"))
    client = TestClient(create_app(settings), base_url="https://safebox.example")

    response = client.get("/onboard/friend")

    assert response.status_code == 200
    assert 'action="/onboard/FRIEND"' in response.text


def test_connected_wallet_can_show_invite_qr(monkeypatch) -> None:
    qr_payloads: list[tuple[str, bool]] = []

    def recording_qr_svg(payload: str, *, include_acorn: bool = False) -> str:
        qr_payloads.append((payload, include_acorn))
        return "<svg id=\"invite-qr\"></svg>"

    monkeypatch.setattr(main_module, "_qr_svg", recording_qr_svg)
    client = make_https_client()
    client.cookies.set(
        SECURE_COOKIE_NAME,
        SessionCipher(TEST_SETTINGS).encode(
            SessionCredentials(
                nsec=TEST_NSEC,
                bootstrap_relay="wss://relay.example.com",
            )
        ),
        domain="safebox.example",
        path="/",
    )

    response = client.get("/invite")

    assert response.status_code == 200
    assert "Invite" in response.text
    assert 'id="invite-qr"' in response.text
    assert "https://safebox.example/onboard/INVITEME" in response.text
    assert qr_payloads == [("https://safebox.example/onboard/INVITEME", True)]
    assert "not included in" in response.text
    assert "only the public Safebox onboarding link" in response.text


def test_invite_qr_requires_connected_acorn() -> None:
    response = make_https_client().get("/invite")

    assert response.status_code == 401


def test_protected_browser_page_redirects_when_connection_is_missing() -> None:
    response = make_https_client().get(
        "/transactions",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_protected_browser_page_redirects_to_root_when_session_is_invalid() -> None:
    client = make_https_client()
    client.cookies.set(
        SECURE_COOKIE_NAME,
        "not-a-valid-session",
        domain="safebox.example",
        path="/",
    )

    response = client.get(
        "/pay",
        headers={"Accept": "text/html"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_onboard_redirects_valid_existing_session_to_wallet() -> None:
    client = make_https_client()
    client.cookies.set(
        SECURE_COOKIE_NAME,
        SessionCipher(TEST_SETTINGS).encode(
            SessionCredentials(
                nsec=TEST_NSEC,
                bootstrap_relay="wss://relay.example.com",
            )
        ),
        domain="safebox.example",
        path="/",
    )

    response = client.get("/onboard/INVITEME", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/wallet"


def test_onboard_ignores_invalid_session_and_offers_fast_creation() -> None:
    client = make_https_client()
    client.cookies.set(
        SECURE_COOKIE_NAME,
        "invalid-session",
        domain="safebox.example",
        path="/",
    )

    response = client.get("/onboard/INVITEME", follow_redirects=False)

    assert response.status_code == 200
    assert "Create My Acorn" in response.text
    assert "Use an Existing Acorn" not in response.text


def test_progress_script_is_served_from_same_origin() -> None:
    client = make_https_client()
    response = client.get("/static/forms.js")
    page = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert '<p id="page-progress" class="page-progress"' in page.text
    assert 'form.setAttribute("aria-busy", "true")' in response.text
    assert 'form.hasAttribute("data-progress-form")' in response.text
    assert 'document.getElementById("page-progress")' in response.text
    assert 'event.target.closest("a[href]")' in response.text
    assert 'destination.origin !== window.location.origin' in response.text
    assert 'pageProgress.textContent = "Opening…"' in response.text
    assert 'button.dataset.progressLabel' in response.text
    assert 'status.hidden = false' in response.text
    assert "button.disabled = true" in response.text
    assert "navigator.clipboard.writeText(target.value)" in response.text
    assert 'event.target.closest("button[data-address-copy]")' in response.text
    assert "disclosure.open = false" not in response.text
    assert "copied to the clipboard" in response.text
    assert 'document.execCommand("copy")' in response.text


def test_theme_defaults_to_dark_and_script_is_served() -> None:
    page = make_https_client().get("/")
    script = make_https_client().get("/static/theme.js")

    assert page.status_code == 200
    assert '<html lang="en" data-theme="dark">' in page.text
    assert 'data-theme-toggle' in page.text
    assert 'aria-label="Use light mode"' in page.text
    assert 'data-theme-icon="sun"' in page.text
    assert 'data-theme-icon="moon"' in page.text
    assert 'src="/static/theme.js"' in page.text
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert 'theme === "dark" ? "light" : "dark"' in script.text
    assert "safebox_theme=" in script.text


def test_pages_declare_web_app_icons_and_manifest() -> None:
    client = make_https_client()
    page = client.get("/")

    assert page.status_code == 200
    assert '<meta name="theme-color" content="#151915">' in page.text
    assert '<link rel="icon" href="/favicon.ico" sizes="any">' in page.text
    assert 'href="/static/favicon-32.png"' in page.text
    assert 'href="/static/apple-touch-icon.png"' in page.text
    assert 'href="/static/site.webmanifest"' in page.text


def test_web_app_icons_and_manifest_are_served() -> None:
    client = make_https_client()

    favicon = client.get("/favicon.ico")
    png_icon = client.get("/static/favicon-32.png")
    touch_icon = client.get("/static/apple-touch-icon.png")
    app_icon = client.get("/static/safebox-icon-192.png")
    manifest = client.get("/static/site.webmanifest")

    assert favicon.status_code == 200
    assert favicon.headers["content-type"].startswith("image/x-icon")
    assert png_icon.status_code == 200
    assert png_icon.headers["content-type"].startswith("image/png")
    assert touch_icon.status_code == 200
    assert touch_icon.headers["content-type"].startswith("image/png")
    assert app_icon.status_code == 200
    assert app_icon.headers["content-type"].startswith("image/png")
    assert manifest.status_code == 200
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert manifest.json()["name"] == "Safebox"
    assert {icon["sizes"] for icon in manifest.json()["icons"]} == {"192x192", "512x512"}


def test_pages_include_mobile_layout_safeguards() -> None:
    client = make_https_client()
    response = client.get("/")
    stylesheet = client.get("/static/styles.css")

    assert response.status_code == 200
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in response.text
    assert 'href="/static/styles.css"' in response.text
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert ".page-tools { display: grid; grid-template-columns: 1fr auto auto 1fr;" in stylesheet.text
    assert ".header-icon, .theme-icon { width: 1.35rem; height: 1.35rem;" in stylesheet.text
    assert "-webkit-text-size-adjust: 100%" in stylesheet.text
    assert "min-height: 2.75rem" in stylesheet.text
    assert "@media (max-width: 36rem)" in stylesheet.text
    assert "@media (max-width: 24rem)" in stylesheet.text
    assert ".transaction-details { grid-template-columns: 1fr; }" in stylesheet.text
    assert ".safekeeping-message" in stylesheet.text
    assert ".nav-button { display: inline-flex; box-sizing: border-box; width: 100%;" in stylesheet.text
    assert ".page-navigation .nav-button { width: 100%; }" in stylesheet.text
    assert ".wallet-address-disclosure { box-sizing: border-box; width: 100%; margin: 0; }" in stylesheet.text
    assert ".wallet-address-panel button, .wallet-address-panel .nav-button { width: 100%; margin-top: 1rem; }" in stylesheet.text
    assert ".record-add { display: inline-flex; box-sizing: border-box; width: 100%;" in stylesheet.text
    assert ".clear-section-heading button { width: 100%; margin: 0; }" in stylesheet.text
    assert ".pdf-controls button { width: 100%; min-width: 0; margin: 0; }" in stylesheet.text


def test_secondary_pages_have_consistent_top_level_navigation() -> None:
    client = make_https_client()
    connection = client.get("/connect")
    connection_nav = re.search(
        r'<nav class="page-navigation".*?</nav>',
        connection.text,
        flags=re.DOTALL,
    )

    assert connection.status_code == 200
    assert connection_nav is not None
    assert 'href="/">Home</a>' in connection_nav.group(0)
    assert "Back to" not in connection_nav.group(0)
    assert connection.text.index("<h1>Connect an Acorn</h1>") < connection.text.index(
        'class="page-navigation"'
    )


def test_record_page_navigation_links_home_and_parent_records() -> None:
    app = create_app(TEST_SETTINGS)
    app.dependency_overrides[get_record_acorn] = lambda: FakeBlobAcorn(
        existing_labels={"Private Notes"}
    )
    response = TestClient(app, base_url="https://safebox.example").get(
        "/record", params={"label": "Private Notes"}
    )
    navigation = re.search(
        r'<nav class="page-navigation".*?</nav>',
        response.text,
        flags=re.DOTALL,
    )

    assert response.status_code == 200
    assert navigation is not None
    assert 'href="/">Home</a>' in navigation.group(0)
    assert 'href="/records">Back to Records</a>' in navigation.group(0)
    assert response.text.index("<h1>Private Notes</h1>") < response.text.index(
        'class="page-navigation"'
    )
    assert response.text.count('class="page-navigation') == 2


def test_wallet_navigation_links_are_presented_as_action_buttons(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    fake = FakeLoadedAcorn()
    app.dependency_overrides[get_acorn] = lambda: fake
    app.dependency_overrides[get_loaded_acorn] = lambda: fake
    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/wallet")

    assert response.status_code == 200
    assert '<nav class="wallet-actions" aria-label="Safebox resources">' in response.text
    assert '<h1 class="wallet-headline">Safebox is Connected</h1>' in response.text
    assert '<p class="continuity-mode">Connected Mode</p>' in response.text
    assert 'class="page-navigation"' not in response.text
    assert '<a href="/balances">Manage Balances</a>' in response.text
    assert '<a href="/records">Manage Records</a>' in response.text
    assert response.text.index("Clear Balances") < response.text.index(
        "Manage Balances"
    ) < response.text.index("Manage Records")
    assert "Claim a Custom Address" not in response.text
    assert '<a href="/invite">Invite someone to create an Acorn</a>' in response.text
    assert "Scan a Code" not in response.text
    assert 'href="/record-protection/enable"' in response.text
    assert "Protected Records" in response.text
    assert '<a class="wallet-balance" href="/transactions"' in response.text
    assert '<a class="wallet-balance clear-balance" href="/clear"' in response.text
    assert "321 <span>sats</span>" in response.text
    assert "verification, transaction history, and pending transfers" in response.text
    assert "View transaction history" not in response.text
    assert "Before disconnecting, make sure you have your" in response.text
    assert "recovery information" in response.text
    assert 'name="confirmed" type="checkbox" value="yes" required' in response.text
    assert response.text.index("wallet-actions") < response.text.index("Component public key")
    assert response.text.index("Disconnect") < response.text.index("Advisories")


def test_manage_balances_is_the_parent_for_balance_actions(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    app.dependency_overrides[get_session_credentials] = lambda: SessionCredentials(
        nsec=TEST_NSEC,
        bootstrap_relay="wss://relay.example",
    )

    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/balances")

    assert response.status_code == 200
    assert "<h1>Manage Balances</h1>" in response.text
    assert 'href="/receive-funds">Receive</a>' in response.text
    assert 'href="/pay">Transfer a Balance</a>' in response.text
    assert 'href="/transactions">Cash Balance and Transactions</a>' in response.text
    assert 'href="/clear">Clear Balances and Transactions</a>' in response.text
    assert 'href="/wallet">Back to Wallet</a>' in response.text


def test_wallet_prominently_warns_while_recovery_backup_is_pending(tmp_path) -> None:
    settings = database_settings(tmp_path)
    app = create_app(settings)
    fake = FakeLoadedAcorn()
    app.dependency_overrides[get_acorn] = lambda: fake
    app.dependency_overrides[get_loaded_acorn] = lambda: fake

    with TestClient(app, base_url="https://safebox.example") as client:
        client.cookies.set(
            SECURE_COOKIE_NAME,
            SessionCipher(settings).encode(
                SessionCredentials(
                    nsec=TEST_NSEC,
                    bootstrap_relay="wss://relay.example.com",
                    deferred_acorn_mnemonic=TEST_MNEMONIC,
                )
            ),
            domain="safebox.example",
            path="/",
        )
        response = client.get("/wallet")
        client.cookies.set(
            SECURE_COOKIE_NAME,
            SessionCipher(settings).encode(
                SessionCredentials(
                    nsec=TEST_NSEC,
                    bootstrap_relay="wss://relay.example.com",
                )
            ),
            domain="safebox.example",
            path="/",
        )
        completed_response = client.get("/wallet")

    assert response.status_code == 200
    assert "Recovery Backup Required" in response.text
    assert '<details class="recovery-warning">' in response.text
    assert "<summary>Recovery Backup Required</summary>" in response.text
    assert 'href="/recovery"' in response.text
    assert "Copy Recovery Words" in response.text
    assert response.text.index("Recovery Backup Required") < response.text.index(
        "wallet-actions"
    )
    assert completed_response.status_code == 200
    assert "Recovery Backup Required" not in completed_response.text
    assert "Copy Recovery Words" not in completed_response.text


def test_wallet_shows_collapsible_silent_payment_address_and_qr(tmp_path) -> None:
    client = TestClient(
        create_app(database_settings(tmp_path)),
        base_url="https://safebox.example",
    )
    fake = FakeLoadedAcorn()
    client.app.dependency_overrides[get_loaded_acorn] = lambda: fake
    client.app.dependency_overrides[get_acorn] = lambda: fake

    with client:
        response = client.get("/wallet")

    assert response.status_code == 200
    assert (
        '<details class="receive-payment-card silent-payment-card">'
        in response.text
    )
    assert "Receive Silent Payment" in response.text
    assert "sp1qqt0uh8dlt9yp" in response.text
    assert 'aria-label="Silent Payment address QR code"' in response.text
    assert '<div class="silent-payment-qr"' in response.text
    assert "<svg" in response.text
    assert 'id="qr-background"' in response.text
    assert 'fill="#ffffff"' in response.text
    assert 'id="qr-path" fill="#000000"' in response.text
    assert 'action="/bitcoin/silent-payment/detect"' in response.text
    assert "Check Payment" in response.text


def test_silent_payment_detection_shows_available_sweep_form(
    tmp_path, monkeypatch
) -> None:
    txid = "ab" * 32
    settings = database_settings(tmp_path)
    app = create_app(settings)
    app.dependency_overrides[get_session_credentials] = lambda: SessionCredentials(
        nsec=TEST_NSEC,
        bootstrap_relay="wss://relay.example.com",
    )

    def fake_detect(**kwargs):
        assert kwargs["nsec"] == TEST_NSEC
        assert kwargs["txid"] == txid
        return {
            "txid": txid,
            "silent_payment_address": "sp1example",
            "matches": [
                {
                    "txid": txid,
                    "vout": 1,
                    "value": 21_000,
                    "source_address": "bc1psource",
                    "confirmed": True,
                    "block_height": 900_000,
                    "availability": "available",
                }
            ],
        }

    monkeypatch.setattr(main_module, "detect_silent_payment_receipts", fake_detect)
    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.post(
            "/bitcoin/silent-payment/detect",
            data={"csrf_token": valid_csrf_token(), "txid": txid},
        )

    assert response.status_code == 200
    assert "21,000 sats" in response.text
    assert "Confirmed and currently reported as unspent" in response.text
    assert 'action="/bitcoin/silent-payment/sweep/preview"' in response.text
    assert 'name="destination_address"' in response.text
    assert TEST_NSEC not in response.text


def test_silent_payment_sweep_requires_review_then_confirmation(
    tmp_path, monkeypatch
) -> None:
    txid = "ab" * 32
    signed_txid = "cd" * 32
    settings = database_settings(tmp_path)
    app = create_app(settings)
    app.dependency_overrides[get_session_credentials] = lambda: SessionCredentials(
        nsec=TEST_NSEC,
        bootstrap_relay="wss://relay.example.com",
    )
    preview = {
        "txid": signed_txid,
        "receipt_txid": txid,
        "vout": 1,
        "source_address": "bc1psource",
        "destination_address": "bc1pdestination",
        "matched_value": 21_000,
        "amount_sats": 20_800,
        "fee_sats": 200,
        "fee_rate": 2.0,
        "vsize": 100,
    }
    calls = []

    def fake_preview(**kwargs):
        calls.append(("preview", kwargs))
        return preview

    def fake_broadcast(**kwargs):
        calls.append(("broadcast", kwargs))
        return {**preview, "broadcast_txid": signed_txid}

    monkeypatch.setattr(
        main_module,
        "create_silent_payment_sweep_preview",
        fake_preview,
    )
    monkeypatch.setattr(
        main_module,
        "broadcast_silent_payment_sweep",
        fake_broadcast,
    )

    with TestClient(app, base_url="https://safebox.example") as client:
        review = client.post(
            "/bitcoin/silent-payment/sweep/preview",
            data={
                "csrf_token": valid_csrf_token(),
                "txid": txid,
                "vout": "1",
                "destination_address": "bc1pdestination",
            },
        )
        unconfirmed = client.post(
            "/bitcoin/silent-payment/sweep",
            data={
                "csrf_token": valid_csrf_token(),
                "txid": txid,
                "vout": "1",
                "destination_address": "bc1pdestination",
            },
        )
        broadcast = client.post(
            "/bitcoin/silent-payment/sweep",
            data={
                "csrf_token": valid_csrf_token(),
                "txid": txid,
                "vout": "1",
                "destination_address": "bc1pdestination",
                "confirmed": "yes",
            },
        )

    assert review.status_code == 200
    assert "20,800 sats" in review.text
    assert "200 sats at 2.0 sat/vB" in review.text
    assert "Receive Funds" in review.text
    assert "signed-transaction" not in review.text
    assert unconfirmed.status_code == 400
    assert "Confirm that the transaction is irreversible" in unconfirmed.text
    assert broadcast.status_code == 200
    assert "received successfully" in broadcast.text
    assert signed_txid in broadcast.text
    assert [name for name, _ in calls] == ["preview", "broadcast"]


def test_pages_use_external_jinja_layout_assets() -> None:
    response = make_https_client().get("/")

    assert response.status_code == 200
    assert response.text.count("<!doctype html>") == 1
    assert "<style>" not in response.text
    assert "style-src 'self'" in response.headers["content-security-policy"]


def test_connect_page_links_to_new_acorn_creation() -> None:
    response = make_https_client().get("/connect")

    assert response.status_code == 200
    assert response.text.index('value="mnemonic"') < response.text.index('value="nsec"')
    assert 'href="/onboard/INVITEME"' in response.text
    assert "Create a new Acorn" in response.text
    assert "Restore protected record access" in response.text
    assert 'name="record_protection_recovery"' in response.text
    assert 'name="record_protection_entropy"' in response.text


def test_create_form_displays_default_relay_and_mint() -> None:
    response = make_https_client().get("/create")

    assert response.status_code == 200
    assert 'name="home_relay"' in response.text
    assert 'name="assign_default_handle" value="yes"' in response.text
    assert TEST_SETTINGS.default_bootstrap_relay in response.text
    assert 'name="home_mint"' in response.text
    assert TEST_SETTINGS.default_home_mint in response.text
    assert 'name="mnemonic_words"' in response.text
    assert '<option value="24" selected>' in response.text
    assert '<option value="12">' in response.text
    assert "Bring your own entropy" in response.text
    assert 'name="use_external_entropy" type="checkbox"' in response.text
    assert 'name="entropy_hex" type="password"' in response.text
    assert 'name="entropy_confirmation" type="password"' in response.text
    assert 'autocomplete="new-password"' in response.text
    assert 'pattern="[0-9A-Fa-f]{64}"' not in response.text
    assert "Bring your own record-protection entropy" not in response.text
    assert 'name="defer_recovery" type="checkbox"' in response.text
    assert "complete the recovery backup later" in response.text
    assert 'name="confirmed"' in response.text


def test_create_acorn_initializes_relay_state_and_starts_session(monkeypatch) -> None:
    FakeCreatedAcorn.instances.clear()
    generated_strengths = []
    monkeypatch.setattr(main_module, "Acorn", FakeCreatedAcorn)
    monkeypatch.setattr(
        main_module,
        "generate_seed_phrase_and_nsec",
        lambda strength=128: (
            generated_strengths.append(strength) or TEST_MNEMONIC,
            TEST_NSEC,
        ),
    )
    client = make_https_client()

    response = client.post(
        "/create",
        data={
            "csrf_token": valid_csrf_token(),
            "home_relay": "relay.example.com",
            "home_mint": "mint.example.com/",
            "mnemonic_words": "24",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 201
    assert "New Acorn created" in response.text
    assert TEST_MNEMONIC in response.text
    assert TEST_NSEC in response.text
    assert TEST_RPK_PHRASE not in response.text
    assert "Safebox Acorn safekeeping message" in response.text
    assert "Safebox Acorn mnemonic:" in response.text
    assert "Protected record mnemonic:" not in response.text
    assert "Protected records: not enabled" in response.text
    assert "Bootstrap relay: wss://relay.example.com" in response.text
    assert 'data-copy-target="safekeeping-message"' in response.text
    assert "wss://relay.example.com" in response.text
    assert "https://mint.example.com" in response.text
    created = FakeCreatedAcorn.instances[0]
    assert created.kwargs == {
        "nsec": TEST_NSEC,
        "home_relay": "wss://relay.example.com",
        "relays": ["wss://relay.example.com"],
        "mints": ["https://mint.example.com"],
    }
    assert created.created_seed_phrase == TEST_MNEMONIC
    assert created.retain_seed_phrase is False
    assert created.loaded is True
    assert created.deferred_recovery_store_calls == []
    assert generated_strengths == [256]

    token = client.cookies.get(SECURE_COOKIE_NAME)
    assert token is not None
    assert TEST_NSEC not in token
    assert TEST_MNEMONIC not in token
    assert TEST_RPK not in token
    credentials = SessionCipher(TEST_SETTINGS).decode(token)
    assert credentials.nsec == TEST_NSEC
    assert credentials.bootstrap_relay == "wss://relay.example.com"
    assert credentials.deferred_acorn_mnemonic is None
    assert credentials.record_protection_key is None
    assert credentials.record_protection_backup_confirmed is False


def test_create_acorn_can_defer_recovery_and_open_wallet(monkeypatch) -> None:
    FakeCreatedAcorn.instances.clear()
    monkeypatch.setattr(main_module, "Acorn", FakeCreatedAcorn)
    monkeypatch.setattr(
        main_module,
        "generate_seed_phrase_and_nsec",
        lambda strength=128: (TEST_MNEMONIC, TEST_NSEC),
    )
    client = make_https_client()

    response = client.post(
        "/create",
        data={
            "csrf_token": valid_csrf_token(),
            "home_relay": "relay.example.com",
            "home_mint": "mint.example.com",
            "defer_recovery": "yes",
            "confirmed": "yes",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/wallet"
    assert TEST_MNEMONIC not in response.text
    created = FakeCreatedAcorn.instances[0]
    assert created.retain_seed_phrase is False
    assert created.deferred_recovery_store_calls == [{}]
    token = client.cookies.get(SECURE_COOKIE_NAME)
    credentials = SessionCipher(TEST_SETTINGS).decode(token)
    assert credentials.deferred_acorn_mnemonic == TEST_MNEMONIC
    assert credentials.record_protection_key is None


def test_deferred_recovery_display_and_completion_updates_session() -> None:
    app = create_app(TEST_SETTINGS)
    fake = FakeLoadedAcorn()
    fake.deferred_recovery_state = {"status": "PENDING", "pending": True}
    credentials = SessionCredentials(
        nsec=TEST_NSEC,
        bootstrap_relay="wss://relay.example.com",
        deferred_acorn_mnemonic=TEST_MNEMONIC,
        record_protection_key=None,
        record_protection_backup_confirmed=False,
    )
    app.dependency_overrides[get_loaded_acorn] = lambda: fake
    app.dependency_overrides[get_session_credentials] = lambda: credentials
    client = TestClient(app, base_url="https://safebox.example")

    warning = client.get("/recovery")
    assert warning.status_code == 200
    assert "Your recovery backup is not complete" in warning.text
    assert TEST_MNEMONIC not in warning.text

    displayed = client.post(
        "/recovery/display",
        data={"csrf_token": valid_csrf_token(), "confirmed": "yes"},
    )
    assert displayed.status_code == 200
    assert TEST_MNEMONIC in displayed.text
    assert TEST_RPK_PHRASE not in displayed.text
    assert "Protected records: not enabled" in displayed.text
    assert displayed.headers["cache-control"] == "no-store"

    completed = client.post(
        "/recovery/confirm",
        data={"csrf_token": valid_csrf_token(), "confirmed": "yes"},
        follow_redirects=False,
    )
    assert completed.status_code == 303
    assert completed.headers["location"] == "/wallet"
    assert fake.deferred_recovery_complete_calls == 1
    token = client.cookies.get(SECURE_COOKIE_NAME)
    updated = SessionCipher(TEST_SETTINGS).decode(token)
    assert updated.deferred_acorn_mnemonic is None
    assert updated.record_protection_key is None


def test_record_protection_recovery_display_requires_confirmation_and_marks_backup() -> None:
    client = make_https_client()
    credentials = SessionCredentials(
        nsec=TEST_NSEC,
        bootstrap_relay="wss://relay.example.com",
        record_protection_key=TEST_RPK,
    )
    client.cookies.set(
        SECURE_COOKIE_NAME,
        SessionCipher(TEST_SETTINGS).encode(credentials),
        domain="safebox.example",
        path="/",
    )

    warning = client.get("/record-protection/recovery")
    assert warning.status_code == 200
    assert "Sensitive recovery material is about to be displayed" in warning.text
    assert TEST_RPK_PHRASE not in warning.text
    assert warning.headers["cache-control"] == "no-store"

    rejected = client.post(
        "/record-protection/recovery",
        data={"csrf_token": valid_csrf_token()},
    )
    assert rejected.status_code == 403
    assert TEST_RPK_PHRASE not in rejected.text

    displayed = client.post(
        "/record-protection/recovery",
        data={
            "csrf_token": valid_csrf_token(),
            "confirmed": "yes",
        },
    )
    assert displayed.status_code == 200
    assert TEST_RPK_PHRASE in displayed.text
    assert TEST_RPK not in displayed.text
    assert displayed.headers["cache-control"] == "no-store"

    confirmed = client.post(
        "/record-protection/confirm",
        data={
            "csrf_token": valid_csrf_token(),
            "confirmed": "yes",
        },
        follow_redirects=False,
    )
    assert confirmed.status_code == 303
    updated_token = client.cookies.get(SECURE_COOKIE_NAME)
    updated = SessionCipher(TEST_SETTINGS).decode(updated_token)
    assert updated.record_protection_key == TEST_RPK
    assert updated.record_protection_backup_confirmed is True


def test_connect_restores_record_protection_key_from_recovery_phrase() -> None:
    client = make_https_client()

    response = client.post(
        "/connect",
        data={
            "csrf_token": valid_csrf_token(),
            "secret_type": "nsec",
            "secret": TEST_NSEC,
            "bootstrap_relay": "relay.example.com",
            "record_protection_recovery": TEST_RPK_PHRASE,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    token = client.cookies.get(SECURE_COOKIE_NAME)
    assert token is not None
    assert TEST_RPK_PHRASE not in token
    assert TEST_RPK not in token
    credentials = SessionCipher(TEST_SETTINGS).decode(token)
    assert credentials.record_protection_key == TEST_RPK
    assert credentials.record_protection_backup_confirmed is True


def test_connect_restores_record_protection_key_from_external_entropy() -> None:
    entropy = "17" * 32
    expected_rpk = record_protection_key_from_entropy(entropy)
    client = make_https_client()

    response = client.post(
        "/connect",
        data={
            "csrf_token": valid_csrf_token(),
            "secret_type": "nsec",
            "secret": TEST_NSEC,
            "bootstrap_relay": "relay.example.com",
            "record_protection_entropy": entropy,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    token = client.cookies.get(SECURE_COOKIE_NAME)
    assert token is not None
    assert entropy not in token
    assert expected_rpk not in token
    credentials = SessionCipher(TEST_SETTINGS).decode(token)
    assert credentials.record_protection_key == expected_rpk
    assert credentials.record_protection_backup_confirmed is True


def test_connect_rejects_invalid_record_protection_phrase_without_echoing_it() -> None:
    invalid_phrase = "abandon " * 23 + "abandon"
    client = make_https_client()

    response = client.post(
        "/connect",
        data={
            "csrf_token": valid_csrf_token(),
            "secret_type": "nsec",
            "secret": TEST_NSEC,
            "bootstrap_relay": "relay.example.com",
            "record_protection_recovery": invalid_phrase,
        },
    )

    assert response.status_code == 400
    assert "Protected record mnemonic is not valid" in response.text
    assert invalid_phrase not in response.text
    assert SECURE_COOKIE_NAME not in response.headers.get("set-cookie", "")


def test_record_protection_activation_uses_external_entropy(monkeypatch) -> None:
    rpk_entropy = "03" * 32
    calls = []
    monkeypatch.setattr(
        main_module,
        "record_protection_key_from_entropy",
        lambda supplied: calls.append(supplied) or TEST_RPK,
    )
    monkeypatch.setattr(
        main_module,
        "generate_record_protection_key",
        lambda: (_ for _ in ()).throw(
            AssertionError("random RPK generation must not be used")
        ),
    )
    app = create_app(TEST_SETTINGS)
    fake = FakeLoadedAcorn()
    credentials = SessionCredentials(
        nsec=TEST_NSEC,
        bootstrap_relay="wss://relay.example.com",
    )
    app.dependency_overrides[get_loaded_acorn] = lambda: fake
    app.dependency_overrides[get_session_credentials] = lambda: credentials
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/record-protection/enable",
        data={
            "csrf_token": valid_csrf_token(),
            "use_external_entropy": "yes",
            "entropy_hex": rpk_entropy,
            "entropy_confirmation": rpk_entropy,
            "confirmed": "yes",
        },
    )

    assert response.status_code == 200
    assert calls == [rpk_entropy]
    assert rpk_entropy not in response.text
    assert TEST_RPK_PHRASE in response.text
    assert fake.record_protection_activation_calls == [TEST_RPK]
    token = client.cookies.get(SECURE_COOKIE_NAME)
    assert token is not None
    credentials = SessionCipher(TEST_SETTINGS).decode(token)
    assert credentials.record_protection_key == TEST_RPK


def test_record_protection_activation_rejects_mismatched_entropy() -> None:
    entropy = "04" * 32
    app = create_app(TEST_SETTINGS)
    fake = FakeLoadedAcorn()
    app.dependency_overrides[get_loaded_acorn] = lambda: fake
    app.dependency_overrides[get_session_credentials] = lambda: SessionCredentials(
        nsec=TEST_NSEC,
        bootstrap_relay="wss://relay.example.com",
    )
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/record-protection/enable",
        data={
            "csrf_token": valid_csrf_token(),
            "use_external_entropy": "yes",
            "entropy_hex": entropy,
            "entropy_confirmation": "05" * 32,
            "confirmed": "yes",
        },
    )

    assert response.status_code == 400
    assert "external entropy values do not match" in response.text
    assert entropy not in response.text
    assert fake.record_protection_activation_calls == []


def test_record_protection_activation_generates_independent_key(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "generate_record_protection_key", lambda: TEST_RPK)
    app = create_app(TEST_SETTINGS)
    fake = FakeLoadedAcorn()
    credentials = SessionCredentials(
        nsec=TEST_NSEC,
        bootstrap_relay="wss://relay.example.com",
    )
    app.dependency_overrides[get_loaded_acorn] = lambda: fake
    app.dependency_overrides[get_session_credentials] = lambda: credentials
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/record-protection/enable",
        data={
            "csrf_token": valid_csrf_token(),
            "confirmed": "yes",
        },
    )

    assert response.status_code == 200
    assert TEST_RPK_PHRASE in response.text
    assert fake.record_protection_activation_calls == [TEST_RPK]


def test_create_acorn_requires_confirmation_before_generating(monkeypatch) -> None:
    generated = False

    def must_not_generate():
        nonlocal generated
        generated = True
        return TEST_MNEMONIC, TEST_NSEC

    monkeypatch.setattr(main_module, "generate_seed_phrase_and_nsec", must_not_generate)
    client = make_https_client()

    response = client.post(
        "/create",
        data={
            "csrf_token": valid_csrf_token(),
            "home_relay": "relay.example.com",
            "home_mint": "mint.example.com",
        },
    )

    assert response.status_code == 400
    assert "Explicit confirmation is required" in response.text
    assert generated is False
    assert SECURE_COOKIE_NAME not in response.headers.get("set-cookie", "")


def test_create_acorn_uses_24_words_by_default(monkeypatch) -> None:
    FakeCreatedAcorn.instances.clear()
    generated_strengths = []
    monkeypatch.setattr(main_module, "Acorn", FakeCreatedAcorn)

    def generate(strength=128):
        generated_strengths.append(strength)
        return TEST_MNEMONIC, TEST_NSEC

    monkeypatch.setattr(main_module, "generate_seed_phrase_and_nsec", generate)
    client = make_https_client()

    response = client.post(
        "/create",
        data={
            "csrf_token": valid_csrf_token(),
            "home_relay": "relay.example.com",
            "home_mint": "mint.example.com",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 201
    assert generated_strengths == [256]


def test_create_acorn_ignores_unselected_autofilled_entropy(monkeypatch) -> None:
    FakeCreatedAcorn.instances.clear()
    monkeypatch.setattr(main_module, "Acorn", FakeCreatedAcorn)
    monkeypatch.setattr(
        main_module,
        "generate_seed_phrase_and_nsec",
        lambda strength=128: (TEST_MNEMONIC, TEST_NSEC),
    )
    monkeypatch.setattr(
        main_module,
        "seed_phrase_and_nsec_from_entropy",
        lambda supplied: (_ for _ in ()).throw(
            AssertionError("unchecked wallet entropy must be ignored")
        ),
    )
    client = make_https_client()

    response = client.post(
        "/create",
        data={
            "csrf_token": valid_csrf_token(),
            "home_relay": "relay.example.com",
            "home_mint": "mint.example.com",
            "entropy_hex": "password-manager-wallet-value",
            "entropy_confirmation": "different-wallet-value",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 201
    assert TEST_MNEMONIC in response.text
    token = client.cookies.get(SECURE_COOKIE_NAME)
    assert token is not None
    credentials = SessionCipher(TEST_SETTINGS).decode(token)
    assert credentials.record_protection_key is None


def test_create_acorn_uses_confirmed_external_entropy(monkeypatch) -> None:
    FakeCreatedAcorn.instances.clear()
    entropy_hex = "02" * 32
    entropy_calls = []
    monkeypatch.setattr(main_module, "Acorn", FakeCreatedAcorn)
    monkeypatch.setattr(
        main_module,
        "seed_phrase_and_nsec_from_entropy",
        lambda supplied: (
            entropy_calls.append(supplied) or TEST_MNEMONIC,
            TEST_NSEC,
        ),
    )
    monkeypatch.setattr(
        main_module,
        "generate_seed_phrase_and_nsec",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("random generation must not be used")
        ),
    )
    client = make_https_client()

    response = client.post(
        "/create",
        data={
            "csrf_token": valid_csrf_token(),
            "home_relay": "relay.example.com",
            "home_mint": "mint.example.com",
            "mnemonic_words": "12",
            "use_external_entropy": "yes",
            "entropy_hex": entropy_hex,
            "entropy_confirmation": entropy_hex,
            "confirmed": "yes",
        },
    )

    assert response.status_code == 201
    assert entropy_calls == [entropy_hex]
    assert TEST_MNEMONIC in response.text
    assert entropy_hex not in response.text
    assert FakeCreatedAcorn.instances[0].created_seed_phrase == TEST_MNEMONIC


def test_create_acorn_rejects_mismatched_external_entropy(monkeypatch) -> None:
    entropy_hex = "02" * 32
    monkeypatch.setattr(
        main_module,
        "seed_phrase_and_nsec_from_entropy",
        lambda supplied: (_ for _ in ()).throw(
            AssertionError("mismatched entropy must not be derived")
        ),
    )
    client = make_https_client()

    response = client.post(
        "/create",
        data={
            "csrf_token": valid_csrf_token(),
            "home_relay": "relay.example.com",
            "home_mint": "mint.example.com",
            "use_external_entropy": "yes",
            "entropy_hex": entropy_hex,
            "entropy_confirmation": "03" * 32,
            "confirmed": "yes",
        },
    )

    assert response.status_code == 400
    assert "external entropy values do not match" in response.text
    assert entropy_hex not in response.text


def test_create_acorn_rejects_invalid_external_entropy() -> None:
    invalid_entropy = "not-hex"
    client = make_https_client()

    response = client.post(
        "/create",
        data={
            "csrf_token": valid_csrf_token(),
            "home_relay": "relay.example.com",
            "home_mint": "mint.example.com",
            "use_external_entropy": "yes",
            "entropy_hex": invalid_entropy,
            "entropy_confirmation": invalid_entropy,
            "confirmed": "yes",
        },
    )

    assert response.status_code == 400
    assert "exactly 64 hexadecimal characters" in response.text
    assert invalid_entropy not in response.text


def test_create_acorn_rejects_invalid_mnemonic_length(monkeypatch) -> None:
    generated = False

    def must_not_generate(strength=128):
        nonlocal generated
        generated = True
        return TEST_MNEMONIC, TEST_NSEC

    monkeypatch.setattr(main_module, "generate_seed_phrase_and_nsec", must_not_generate)
    client = make_https_client()

    response = client.post(
        "/create",
        data={
            "csrf_token": valid_csrf_token(),
            "home_relay": "relay.example.com",
            "home_mint": "mint.example.com",
            "mnemonic_words": "18",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 400
    assert "Choose a 12- or 24-word Safebox Acorn mnemonic" in response.text
    assert generated is False


def test_create_acorn_rejects_insecure_remote_mint() -> None:
    client = make_https_client()

    response = client.post(
        "/create",
        data={
            "csrf_token": valid_csrf_token(),
            "home_relay": "relay.example.com",
            "home_mint": "http://mint.example.com",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 400
    assert "allowed only on loopback" in response.text
    assert SECURE_COOKIE_NAME not in response.headers.get("set-cookie", "")


def test_loaded_acorn_dependency_loads_request_scoped_state() -> None:
    acorn = FakeLoadedAcorn()

    result = asyncio.run(get_loaded_acorn(acorn, TEST_SETTINGS))

    assert result is acorn
    assert acorn.loaded is True


def test_record_acorn_dependency_does_not_load_funds_state() -> None:
    acorn = FakeLoadedAcorn()

    result = get_record_acorn(acorn)

    assert result is acorn
    assert acorn.loaded is False


def test_wallet_page_shows_snapshot_but_defers_verification_and_transfer_checks(tmp_path) -> None:
    settings = database_settings(tmp_path)
    app = create_app(settings)
    acorn = FakeLoadedAcorn(balance=12_345)

    async def unexpected_live_check(*args, **kwargs):
        raise AssertionError("wallet landing page performed a deferred live check")

    acorn.check_proofs = unexpected_live_check
    acorn.get_deferred_recovery_status = unexpected_live_check
    acorn.get_continuity_receipts = unexpected_live_check
    acorn.sweep_ecash_transfers = unexpected_live_check
    acorn.get_clear_receipts = unexpected_live_check
    acorn.sweep_clear_transfers = unexpected_live_check
    app.dependency_overrides[get_acorn] = lambda: acorn
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/wallet")

    assert response.status_code == 200
    assert "12,345 <span>sats</span>" in response.text
    assert "Relay-visible proof total" not in response.text
    assert "Confirmed cash balance" not in response.text
    assert "Previously confirmed balance" in response.text
    assert "verification, transaction history, and pending transfers" in response.text
    assert "wss://relay.example.com" in response.text
    assert "not stored" in response.text
    assert "NIP-05 address" not in response.text
    assert acorn.balance_snapshot_reads == 1
    assert acorn.loaded is False
    assert acorn.balance_snapshot_publishes == 0


def test_wallet_bootstraps_missing_balance_snapshot_from_authoritative_state(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    acorn = FakeLoadedAcorn(balance=777)
    acorn.clear_balances = [
        {
            "mint": "https://clear.example",
            "unit": "cmu-bootstrap",
            "amount": 12,
            "proof_count": 2,
        }
    ]
    acorn.get_balance_snapshot = AsyncMock(return_value=None)
    acorn.publish_balance_snapshot = AsyncMock(return_value={"status": "OK"})
    app.dependency_overrides[get_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/wallet")

    assert response.status_code == 200
    assert "777 <span>sats</span>" in response.text
    assert "12 cmu-bootstrap" in response.text
    assert acorn.loaded is True
    acorn.publish_balance_snapshot.assert_awaited_once_with(
        clear_balances=acorn.clear_balances,
        verify=False,
    )


def test_wallet_renders_without_full_load_when_snapshot_is_unavailable_during_finalization(
    tmp_path,
) -> None:
    settings = replace(
        database_settings(tmp_path),
        wallet_home_snapshot_timeout_seconds=0.01,
    )
    app = create_app(settings)
    acorn = FakeLoadedAcorn(balance=777)
    acorn.get_balance_snapshot = AsyncMock(side_effect=TimeoutError)
    app.dependency_overrides[get_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        claimed, _owner_token, _job = claim_finalization_job(
            app.state.database_engine,
            acorn.pubkey_bech32,
            worker_id=app.state.worker_id,
        )
        assert claimed is True
        response = client.get("/wallet")

    assert response.status_code == 200
    assert "Updating…" in response.text
    assert "Funds finalization is running in the background." in response.text
    assert acorn.loaded is False


def test_wallet_displays_cached_currency_estimate_without_mint_verification(tmp_path) -> None:
    settings = replace(
        database_settings(tmp_path),
        currency_rates_enabled=True,
        default_display_currency="CAD",
    )
    app = create_app(settings)
    fake = FakeLoadedAcorn(balance=50_000)
    app.dependency_overrides[get_acorn] = lambda: fake
    app.dependency_overrides[get_loaded_acorn] = lambda: fake
    with TestClient(app, base_url="https://safebox.example") as client:
        with Session(app.state.database_engine) as session:
            from app.models import CurrencyRate

            session.add(
                CurrencyRate(
                    currency_code="CAD",
                    fiat_per_btc=200_000.0,
                    currency_symbol="$",
                    currency_description="Canadian dollar",
                    source="test",
                    fetched_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    updated_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
            )
            session.commit()
        response = client.get("/wallet")

    assert response.status_code == 200
    assert 'class="wallet-balance-amount">≈ $100.00 <span>CAD</span>' in response.text
    assert 'class="wallet-balance-sats">50,000 sats' in response.text
    assert "Cached rate may be stale" not in response.text


def test_wallet_clear_snapshot_uses_friendly_cached_mint_metadata(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    acorn = FakeLoadedAcorn()
    acorn.clear_balances = [
        {
            "mint": "https://clear.example",
            "unit": "cmu-friendly",
            "amount": 150,
            "proof_count": 4,
        }
    ]
    app.state.clear_mint_metadata_cache[(
        "https://clear.example",
        "cmu-friendly",
    )] = (
        time.monotonic(),
        {
            "display_name": "Community Credits",
            "display_unit": "credits",
            "metadata_resolved": True,
        },
    )
    app.dependency_overrides[get_acorn] = lambda: acorn
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/wallet")

    assert response.status_code == 200
    assert "Community Credits</strong>: 150 credits." in response.text
    assert "cmu-friendly · https://clear.example" in response.text


def test_transaction_page_warns_when_relay_total_exceeds_mint_confirmed_balance(tmp_path) -> None:
    settings = database_settings(tmp_path)
    app = create_app(settings)
    acorn = FakeLoadedAcorn(
        balance=33_926,
        verified_balance=52,
        verification_status="repair-recommended",
    )
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/transactions?check=1")

    assert response.status_code == 200
    assert "Relay-visible proof total: <strong>33,926 sats" in response.text
    assert "Confirmed cash balance: <strong>52 sats" in response.text
    assert "33,874 sats that are not confirmed" in response.text
    assert "Do not make a transfer" in response.text


def test_clear_page_shows_pending_clear_transfers_separately(tmp_path) -> None:
    settings = database_settings(tmp_path)
    app = create_app(settings)
    acorn = FakeLoadedAcorn(balance=9_836)
    acorn.clear_receipts = [
        {
            "event_id": "clear-1",
            "status": "pending",
            "amount": 25,
            "unit": "cmu-00ce29eeaf094301",
            "mint": "http://127.0.0.1:3338",
        }
    ]
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/clear")

    assert response.status_code == 200
    assert "Clear Balances" in response.text
    assert "1 pending transfer across 1 Clear balance." in response.text
    assert "25 pending in 1 transfer" in response.text
    assert "Pending Cash transfers: 25 sats" not in response.text


def test_clear_page_defers_new_transfer_scan_until_requested(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    acorn = FakeLoadedAcorn(balance=9_836)
    event_id = "7" * 64
    acorn.clear_preview = {
        "previewed_count": 1,
        "previewed_amount": 40,
        "previewed": [{
            "event_id": event_id,
            "sender_pubkey": "sender-pubkey",
            "amount": 40,
            "unit": "cmu-preview",
            "mints": ["http://127.0.0.1:3339"],
            "timestamp": 1_786_430_400,
            "comment": "relay preview",
        }],
    }
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/clear")

    assert response.status_code == 200
    assert "1 pending transfer across 1 Clear balance." not in response.text
    assert "40 pending in 1 transfer" not in response.text
    assert "Check for Clear Transfers" in response.text
    assert acorn.clear_preview_calls == 0
    assert acorn.clear_sweep_calls == 0
    assert acorn.clear_receipts == []


def test_startup_migrates_a_new_sqlite_database(tmp_path) -> None:
    settings = database_settings(tmp_path)
    database_path = tmp_path / "database.db"

    with TestClient(
        create_app(settings), base_url="https://safebox.example"
    ) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/handle").status_code == 401

    assert database_path.is_file()
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
        handle_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(claimed_handle)")
        }
        payment_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(provider_payment)")
        }
        provider_identity_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(provider_identity)")
        }
        provider_zap_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(provider_zap)")
        }
        currency_rate_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(currency_rate)")
        }
        finalization_job_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(funds_finalization_job)"
            )
        }
        clear_acceptance_job_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(clear_acceptance_job)"
            )
        }
        worker_heartbeat_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(web_worker_heartbeat)"
            )
        }
    assert {
        "alembic_version",
        "claimed_handle",
        "provider_identity",
        "provider_payment",
        "provider_zap",
        "currency_rate",
        "funds_finalization_job",
        "clear_acceptance_job",
        "web_worker_heartbeat",
    }.issubset(tables)
    assert revision == ("20260823_0008",)
    assert handle_columns == {"id", "claimed_handle", "npub", "home_relay"}
    assert {
        "id",
        "payment_id",
        "claimed_handle",
        "recipient_npub",
        "recipient_relay",
        "amount_msat",
        "amount_sat",
        "comment",
        "lnurl_metadata",
        "status",
        "mint",
        "mint_quote",
        "invoice",
        "delivery_event_id",
        "error",
        "attempts",
        "delivery_attempts",
        "created_at",
        "updated_at",
        "next_check_at",
    } == payment_columns
    assert {"name", "nostr_pubkey", "updated_at"} == provider_identity_columns
    assert {
        "id",
        "payment_id",
        "request_event_id",
        "request_json",
        "receipt_relays_json",
        "receipt_event_id",
        "receipt_json",
        "receipt_error",
    } == provider_zap_columns
    assert {
        "currency_code",
        "fiat_per_btc",
        "currency_symbol",
        "currency_description",
        "source",
        "fetched_at",
        "updated_at",
    } == currency_rate_columns
    assert {
        "npub",
        "status",
        "owner_token",
        "owner_worker_id",
        "phase",
        "discovered_count",
        "discovered_amount",
        "confirmed_count",
        "confirmed_amount",
        "pending_count",
        "pending_amount",
        "error",
        "started_at",
        "updated_at",
        "lease_expires_at",
    } == finalization_job_columns
    assert {
        "npub",
        "event_id",
        "status",
        "owner_token",
        "owner_worker_id",
        "phase",
        "amount",
        "mint",
        "unit",
        "error",
        "started_at",
        "updated_at",
        "lease_expires_at",
    } == clear_acceptance_job_columns
    assert {
        "worker_id",
        "started_at",
        "heartbeat_at",
    } == worker_heartbeat_columns


def test_web_lifespan_does_not_own_the_service_acorn(tmp_path) -> None:
    settings = replace(
        database_settings(tmp_path),
        service_acorn_enabled=True,
        service_acorn_state_file=str(tmp_path / "service-acorn.json"),
    )
    app = create_app(settings)

    with TestClient(app, base_url="https://safebox.example") as client:
        assert client.get("/health").status_code == 200
        assert not hasattr(app.state, "service_acorn_runtime")
        assert not hasattr(app.state, "service_acorn")

    assert not (tmp_path / "service-acorn.json").exists()


def test_connected_acorn_can_claim_and_resolve_a_nip05_handle(tmp_path) -> None:
    settings = database_settings(tmp_path)
    app = create_app(settings)
    acorn = main_module.Acorn(
        nsec=TEST_NSEC,
        home_relay="wss://relay.one.example",
        relays=["wss://relay.one.example"],
    )
    stub_deferred_recovery_status(acorn)
    app.dependency_overrides[get_acorn] = lambda: acorn
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        form = client.get("/handle")
        assert form.status_code == 200
        assert "Claim a NIP-05 handle" in form.text
        assert "alice@safebox.example" not in form.text

        claim = client.post(
            "/handle",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "claimed_handle": "Alice",
            },
            follow_redirects=False,
        )
        assert claim.status_code == 303
        assert claim.headers["location"] == "/handle"

        claimed = client.get("/handle")
        assert claimed.status_code == 200
        assert "alice@safebox.example" in claimed.text
        assert acorn.pubkey_bech32 in claimed.text

        wallet_page = client.get("/wallet")
        assert wallet_page.status_code == 200
        assert "NIP-05 address" in wallet_page.text
        assert "alice@safebox.example" in wallet_page.text
        assert '<a href="/handle">alice@safebox.example</a>' in wallet_page.text

        resolution = client.get(
            "/.well-known/nostr.json", params={"name": "alice"}
        )
        assert resolution.status_code == 200
        assert resolution.headers["access-control-allow-origin"] == "*"
        assert resolution.json() == {
            "names": {"alice": acorn.pubkey_hex},
            "relays": {acorn.pubkey_hex: ["wss://relay.one.example"]},
        }

        # The same component can idempotently refresh its current relay.
        acorn.home_relay = "wss://relay.two.example"
        refreshed = client.post(
            "/handle",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "claimed_handle": "alice",
            },
            follow_redirects=False,
        )
        assert refreshed.status_code == 303
        assert client.get(
            "/.well-known/nostr.json", params={"name": "alice"}
        ).json()["relays"] == {
            acorn.pubkey_hex: ["wss://relay.two.example"]
        }

        unconfirmed_remove = client.post(
            "/handle/remove",
            data={"csrf_token": CsrfProtector(settings).issue()},
        )
        assert unconfirmed_remove.status_code == 400
        assert "Explicit removal confirmation is required" in unconfirmed_remove.text

        removed = client.post(
            "/handle/remove",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "confirmed": "yes",
            },
            follow_redirects=False,
        )
        assert removed.status_code == 303
        assert removed.headers["location"] == "/handle"
        assert client.get(
            "/.well-known/nostr.json", params={"name": "alice"}
        ).status_code == 404
        assert "Claim a NIP-05 handle" in client.get("/handle").text
        assert "NIP-05 address" not in client.get("/wallet").text


def test_nip05_handle_can_advertise_clear_receive_support(tmp_path) -> None:
    settings = replace(
        database_settings(tmp_path),
        clear_receive_enabled=True,
    )
    app = create_app(settings)
    acorn = main_module.Acorn(
        nsec=TEST_NSEC,
        home_relay="wss://relay.one.example",
        relays=["wss://relay.one.example"],
    )
    stub_deferred_recovery_status(acorn)
    app.dependency_overrides[get_acorn] = lambda: acorn
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        claim = client.post(
            "/handle",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "claimed_handle": "Alice",
            },
            follow_redirects=False,
        )
        assert claim.status_code == 303

        resolution = client.get(
            "/.well-known/nostr.json", params={"name": "alice"}
        )

    assert resolution.status_code == 200
    assert resolution.json() == {
        "names": {"alice": acorn.pubkey_hex},
        "relays": {acorn.pubkey_hex: ["wss://relay.one.example"]},
        "clear": {
            "alice": {
                "protocols": ["clear-token-transfer"],
                "transports": ["nip59"],
                "kinds": [7379],
            }
        },
    }


def test_wallet_shows_plain_address_with_lnurl_qr(
    tmp_path,
    monkeypatch,
) -> None:
    qr_payloads: list[tuple[str, bool]] = []
    original_qr_svg = main_module._qr_svg

    def recording_qr_svg(payload: str, *, include_acorn: bool = False) -> str:
        qr_payloads.append((payload, include_acorn))
        return original_qr_svg(payload, include_acorn=include_acorn)

    monkeypatch.setattr(main_module, "_qr_svg", recording_qr_svg)
    settings = replace(database_settings(tmp_path), service_acorn_enabled=True)
    app = create_app(settings)
    acorn = main_module.Acorn(
        nsec=TEST_NSEC,
        home_relay="wss://relay.one.example",
        relays=["wss://relay.one.example"],
    )
    stub_deferred_recovery_status(acorn)
    app.dependency_overrides[get_acorn] = lambda: acorn
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        claim = client.post(
            "/handle",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "claimed_handle": "alice",
            },
            follow_redirects=False,
        )
        assert claim.status_code == 303

        wallet_page = client.get("/wallet")
        stylesheet = client.get("/static/styles.css")

    assert wallet_page.status_code == 200
    assert "alice@safebox.example" in wallet_page.text
    assert '<section class="wallet-address" aria-label="Transfer address">' in wallet_page.text
    assert 'class="wallet-address-value"' not in wallet_page.text
    assert '<div class="wallet-address-actions">' in wallet_page.text
    assert '<a href="/scan/lightning">Scan</a>' in wallet_page.text
    assert '<details class="wallet-address-disclosure">' in wallet_page.text
    assert "Show QR Code" not in wallet_page.text
    assert "Hide QR" not in wallet_page.text
    assert 'class="wallet-address-summary-icon wallet-address-summary-qr"' in wallet_page.text
    assert 'class="wallet-address-summary-icon wallet-address-summary-at"' in wallet_page.text
    assert '<span class="wallet-address-summary-value">alice@safebox.example</span>' in wallet_page.text
    assert wallet_page.text.index('href="/scan/lightning">Scan') < wallet_page.text.index("wallet-address-disclosure")
    assert 'class="wallet-address-qr"' in wallet_page.text
    assert 'aria-label="Balance transfer QR code"' in wallet_page.text
    assert 'data-address-copy="alice@safebox.example"' in wallet_page.text
    assert "Copy transfer address" in wallet_page.text
    assert '<a class="nav-button" href="/handle">Change Address</a>' in wallet_page.text
    assert wallet_page.text.index("Copy transfer address") < wallet_page.text.index(
        '<a class="nav-button" href="/handle">Change Address</a>'
    )
    address_disclosure = re.search(
        r'<details class="wallet-address-disclosure">.*?</details>',
        wallet_page.text,
        flags=re.DOTALL,
    )
    assert address_disclosure is not None
    assert "Change Address" in address_disclosure.group(0)
    assert "Scan for Balance Transfer." in wallet_page.text
    expected_lnurl = main_module.encode_lnurl(
        "https://safebox.example/.well-known/lnurlp/alice"
    )
    assert (expected_lnurl, True) in qr_payloads
    assert "<svg" in wallet_page.text
    assert 'id="qr-background"' in wallet_page.text
    assert 'fill="#ffffff"' in wallet_page.text
    assert 'id="qr-path" fill="#000000"' in wallet_page.text
    assert 'id="acorn-qr-mark"' in wallet_page.text
    assert '<details class="general-advisories">' in wallet_page.text
    assert "<summary>Advisories</summary>" in wallet_page.text
    assert "Funds transfer message retention" in wallet_page.text
    assert "for 1 week after publication" in wallet_page.text
    assert "Relay enforcement and physical deletion can vary" in wallet_page.text
    assert wallet_page.text.index("alice@safebox.example") < wallet_page.text.index("Balance")
    assert wallet_page.text.index("Balance") < wallet_page.text.index("Receive Silent Payment")
    assert wallet_page.text.index("Disconnect") < wallet_page.text.index("Advisories")

    assert stylesheet.status_code == 200
    assert ".wallet-address-summary-at { display: none; }" in stylesheet.text
    assert ".wallet-address-disclosure[open] .wallet-address-summary-qr { display: none; }" in stylesheet.text
    assert ".wallet-address-disclosure[open] .wallet-address-summary-at { display: inline-flex; }" in stylesheet.text


def test_invoice_qr_is_black_and_white_without_centre_mark() -> None:
    svg = main_module._invoice_svg("lnbc1pytest")

    assert 'class="qr-code"' in svg
    assert 'id="qr-background"' in svg
    assert 'fill="#ffffff"' in svg
    assert 'id="qr-path" fill="#000000"' in svg
    assert 'id="acorn-qr-mark"' not in svg


def test_wallet_hides_address_qr_when_payment_provider_is_disabled(tmp_path) -> None:
    settings = database_settings(tmp_path)
    app = create_app(settings)
    acorn = main_module.Acorn(
        nsec=TEST_NSEC,
        home_relay="wss://relay.one.example",
        relays=["wss://relay.one.example"],
    )
    stub_deferred_recovery_status(acorn)
    app.dependency_overrides[get_acorn] = lambda: acorn
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        client.post(
            "/handle",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "claimed_handle": "alice",
            },
        )
        wallet_page = client.get("/wallet")

    assert "NIP-05 address" in wallet_page.text
    assert "Receive Lightning" not in wallet_page.text
    assert '<section class="wallet-address"' not in wallet_page.text
    assert '<div class="wallet-address-qr"' not in wallet_page.text


def test_handle_and_component_uniqueness_are_enforced(tmp_path) -> None:
    settings = database_settings(tmp_path)
    app = create_app(settings)
    first = main_module.Acorn(
        nsec=TEST_NSEC,
        home_relay="wss://relay.one.example",
        relays=["wss://relay.one.example"],
    )
    _second_mnemonic, second_nsec = main_module.generate_seed_phrase_and_nsec()
    second = main_module.Acorn(
        nsec=second_nsec,
        home_relay="wss://relay.two.example",
        relays=["wss://relay.two.example"],
    )
    active = {"acorn": first}
    app.dependency_overrides[get_acorn] = lambda: active["acorn"]

    with TestClient(app, base_url="https://safebox.example") as client:
        first_claim = client.post(
            "/handle",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "claimed_handle": "taken",
            },
            follow_redirects=False,
        )
        assert first_claim.status_code == 303

        active["acorn"] = second
        competing_claim = client.post(
            "/handle",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "claimed_handle": "taken",
            },
        )
        assert competing_claim.status_code == 409
        assert "already been claimed" in competing_claim.text

        active["acorn"] = first
        changed_handle = client.post(
            "/handle",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "claimed_handle": "another",
            },
            follow_redirects=False,
        )
        assert changed_handle.status_code == 303
        assert client.get(
            "/.well-known/nostr.json", params={"name": "taken"}
        ).status_code == 404
        changed_resolution = client.get(
            "/.well-known/nostr.json", params={"name": "another"}
        )
        assert changed_resolution.status_code == 200
        assert changed_resolution.json()["names"] == {
            "another": first.pubkey_hex
        }

        # Renaming releases the old handle for another authenticated Acorn.
        active["acorn"] = second
        released_claim = client.post(
            "/handle",
            data={
                "csrf_token": CsrfProtector(settings).issue(),
                "claimed_handle": "taken",
            },
            follow_redirects=False,
        )
        assert released_claim.status_code == 303

        assert client.get(
            "/.well-known/nostr.json", params={"name": "missing"}
        ).status_code == 404


def test_transaction_history_renders_mobile_friendly_journal_cards(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    acorn = FakeLoadedAcorn(
        transaction_history=[
            {
                "create_time": "2026-08-03 18:42:10",
                "tx_type": "C",
                "amount": 21,
                "comment": "safebox web deposit <confirmed>",
                "tendered_amount": 21.0,
                "tendered_currency": "SAT",
                "fees": 0,
                "current_balance": 52,
                "invoice": "invoice-is-not-rendered",
                "preimage": "preimage-is-not-rendered",
            },
            {
                "create_time": "2026-08-03 18:45:00",
                "tx_type": "D",
                "amount": 5,
                "comment": "coffee",
                "tendered_amount": 5.0,
                "tendered_currency": "SAT",
                "fees": 1,
                "current_balance": 46,
            },
        ]
    )
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/transactions")

    assert response.status_code == 200
    assert '<h1 class="transaction-headline">Cash Transactions</h1>' in response.text
    assert '<a class="wallet-balance transaction-balance" href="/wallet"' in response.text
    assert "Mint verification and incoming-transfer discovery run only when requested" in response.text
    assert "Check Balance and Incoming Transfers" in response.text
    assert acorn.proof_check_calls == 0
    assert acorn.preview_calls == 0
    assert "Incoming ecash" not in response.text
    assert response.text.index(
        '<h1 class="transaction-headline">Cash Transactions</h1>'
    ) < response.text.index(">Home</a>")
    assert response.text.index(">Home</a>") < response.text.index(
        'class="wallet-balance transaction-balance"'
    )
    assert response.text.index(">Back to Balances</a>") < response.text.index(
        'class="wallet-balance transaction-balance"'
    )
    assert response.text.index('class="wallet-balance transaction-balance"') < response.text.index(
        'aria-label="Cash transaction finalization"'
    )
    assert response.text.index('aria-label="Cash transaction finalization"') < response.text.index(
        'aria-label="Cash transaction history"'
    )
    assert 'aria-label="Cash transaction history"' in response.text
    assert 'class="transaction-card credit"' in response.text
    assert 'class="transaction-card debit"' in response.text
    assert "+21 sats" in response.text
    assert "−5 sats" in response.text
    assert response.text.index("−5 sats") < response.text.index("+21 sats")
    assert "52 sats" in response.text
    assert "safebox web deposit &lt;confirmed&gt;" in response.text
    assert "invoice-is-not-rendered" not in response.text
    assert "preimage-is-not-rendered" not in response.text
    assert 'href="/static/styles.css"' in response.text
    assert 'action="/transactions/receive"' in response.text
    assert 'name="csrf_token"' in response.text
    assert "Finalize Cash Transactions" in response.text
    assert "Check and receive ecash" not in response.text
    assert "Finalizing…" in response.text
    assert '<details class="transaction-advisories">' in response.text
    assert "<summary>Advisories</summary>" in response.text
    assert response.text.index('aria-label="Cash transaction history"') < response.text.index(
        "Pending Cash transfers are added to the confirmed balance"
    )
    assert response.text.index("Force Finalization") > response.text.index(
        "<summary>Advisories</summary>"
    )
    assert response.text.count('class="page-navigation') == 2
    assert response.text.rindex("<summary>Advisories</summary>") < response.text.rindex(
        'class="page-navigation'
    )


def test_transaction_history_has_an_empty_state(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    app.dependency_overrides[get_loaded_acorn] = lambda: FakeLoadedAcorn()
    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/transactions")

    assert response.status_code == 200
    assert "No transaction history was found" in response.text


def test_transaction_history_displays_cached_currency_estimate(tmp_path) -> None:
    settings = replace(
        database_settings(tmp_path),
        currency_rates_enabled=True,
        default_display_currency="USD",
    )
    app = create_app(settings)
    app.dependency_overrides[get_loaded_acorn] = lambda: FakeLoadedAcorn(
        balance=50_000
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with TestClient(app, base_url="https://safebox.example") as client:
        with Session(app.state.database_engine) as session:
            from app.models import CurrencyRate

            session.add(
                CurrencyRate(
                    currency_code="USD",
                    fiat_per_btc=100_000.0,
                    currency_symbol="$",
                    currency_description="United States dollar",
                    source="https://rates.example/ticker",
                    fetched_at=now,
                    updated_at=now,
                )
            )
            session.commit()
        response = client.get("/transactions")

    assert response.status_code == 200
    assert 'class="wallet-balance-amount">≈ $50.00 <span>USD</span>' in response.text
    assert 'class="wallet-balance-sats">50,000 sats' in response.text


def test_wallet_uses_manage_balances_as_the_balance_navigation(tmp_path) -> None:
    settings = database_settings(tmp_path)
    app = create_app(settings)
    fake = FakeLoadedAcorn()
    app.dependency_overrides[get_acorn] = lambda: fake
    app.dependency_overrides[get_loaded_acorn] = lambda: fake
    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/wallet")

    assert response.status_code == 200
    assert '<a href="/balances">Manage Balances</a>' in response.text
    assert '<a class="wallet-balance" href="/transactions"' in response.text


def test_cash_transactions_do_not_include_clear_transfers(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    acorn = FakeLoadedAcorn(balance=100)
    acorn.clear_receipts = [
        {
            "event_id": "clear-1",
            "status": "pending",
            "amount": 16,
            "unit": "cmu-test",
        },
        {
            "event_id": "clear-2",
            "status": "pending",
            "amount": 9,
            "unit": "cmu-test",
        },
    ]
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/transactions")

    assert response.status_code == 200
    balance_pane = response.text.split(
        '<a class="wallet-balance transaction-balance"', 1
    )[1].split("</a>", 1)[0]
    assert "Cash Balance" in balance_pane
    assert "Clear Balances" not in response.text
    assert "cmu-test" not in response.text
    assert "Pending Cash transfers: 25 sats" not in response.text


def test_clear_page_shows_balances_and_receipt_history(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    acorn = FakeLoadedAcorn(balance=100)
    acorn.clear_receipts = [
        {
            "event_id": "clear-event-1234567890",
            "sender_pubkey": "sender-pubkey-1234567890",
            "status": "pending",
            "amount": 25,
            "unit": "cmu-test",
            "mint": "http://clear.example",
            "comment": "community supplies",
            "timestamp": 1_786_430_400,
            "keyset_ids": ["keyset-test"],
        }
    ]
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/clear")

    assert response.status_code == 200
    assert '<h1 class="transaction-headline">Clear Transactions</h1>' in response.text
    assert 'action="/clear/receive"' in response.text
    assert "Check for Clear Transfers" in response.text
    assert '<h2 id="clear-balances-heading">Clear Balances</h2>' in response.text
    assert '<h2 id="clear-pending-heading">Pending Clear Transfers</h2>' in response.text
    assert "1 pending transfer across 1 Clear balance." in response.text
    assert "0 <span>cmu-test</span>" in response.text
    assert "25 pending in 1 transfer" in response.text
    assert '<h2 id="clear-history-heading">Clear Transaction History</h2>' in response.text
    assert "Pending Clear Transfer" in response.text
    assert "+25 cmu-test" in response.text
    assert "community supplies" in response.text
    assert "clear-event" in response.text
    assert "sender-pubke" in response.text
    assert "keyset-test" in response.text
    assert "Delete pending transfer" in response.text
    assert "Accept Clear Transfer" in response.text
    assert 'action="/clear/receipts/accept"' in response.text
    assert 'action="/clear/receipts/delete"' in response.text
    assert response.text.index("Clear Balances") < response.text.index(
        "Pending Clear Transfers"
    ) < response.text.index("Clear Transaction History")
    history_section = response.text.split("Clear Transaction History", 1)[1]
    assert "Pending Clear Transfer" not in history_section
    assert "No completed Clear transactions found." in history_section
    assert response.text.count('class="page-navigation') == 2
    assert "Confirmed balance" in response.text
    assert "spendable" not in response.text.lower()


def test_user_can_check_for_new_clear_transfers(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    acorn = FakeLoadedAcorn(balance=100)
    event_id = "a" * 64
    acorn.clear_sweep_receipts = [
        {
            "event_id": event_id,
            "status": "pending",
            "amount": 100,
            "unit": "cmu-test",
            "mint": "https://clear.example",
            "timestamp": 1_786_430_400,
        }
    ]
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        page = client.get("/clear")
        token_match = re.search(
            r'name="csrf_token" value="([^"]+)"',
            page.text,
        )
        assert token_match is not None
        response = client.post(
            "/clear/receive",
            data={"csrf_token": token_match.group(1)},
            follow_redirects=False,
        )
        result = client.get(response.headers["location"])

    assert response.status_code == 303
    assert response.headers["location"] == "/clear?received=1"
    assert acorn.clear_sweep_calls == 1
    assert "Received 1 new Clear transfer." in result.text
    assert "0 <span>cmu-test</span>" in result.text
    assert "100 pending in 1 transfer" in result.text
    assert "Pending Clear Transfer" in result.text


def test_clear_transfer_check_reports_when_nothing_new(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    acorn = FakeLoadedAcorn(balance=100)
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        page = client.get("/clear")
        token_match = re.search(
            r'name="csrf_token" value="([^"]+)"',
            page.text,
        )
        assert token_match is not None
        response = client.post(
            "/clear/receive",
            data={"csrf_token": token_match.group(1)},
            follow_redirects=False,
        )
        result = client.get(response.headers["location"])

    assert response.status_code == 303
    assert response.headers["location"] == "/clear?received=0"
    assert acorn.clear_sweep_calls == 1
    assert "No new Clear transfers found." in result.text


def test_user_can_accept_clear_transfer_into_spendable_balance(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    acorn = FakeLoadedAcorn(balance=100)
    event_id = "b" * 64
    acorn.clear_receipts = [{
        "event_id": event_id,
        "sender_pubkey": "sender-pubkey",
        "status": "pending",
        "amount": 25,
        "unit": "cmu-test",
        "mint": "https://clear.example",
        "comment": "guest passes",
        "timestamp": 1_786_430_400,
    }]
    app.dependency_overrides[get_acorn] = lambda: acorn
    app.dependency_overrides[get_background_acorn_factory] = lambda: lambda: acorn
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        page = client.get("/clear")
        token_match = re.search(
            r'name="csrf_token" value="([^"]+)"',
            page.text,
        )
        assert token_match is not None
        response = client.post(
            "/clear/receipts/accept",
            data={"csrf_token": token_match.group(1), "event_id": event_id},
            follow_redirects=False,
        )
        deadline = time.monotonic() + 2
        job = None
        while time.monotonic() < deadline:
            job = get_clear_acceptance_job(
                app.state.database_engine,
                acorn.pubkey_bech32,
            )
            if job and job["status"] == "COMPLETE":
                break
            time.sleep(0.01)
        status_page = client.get(response.headers["location"])
        result = client.get("/clear")

    assert response.status_code == 303
    assert response.headers["location"] == "/clear/acceptance-status"
    assert job is not None
    assert job["status"] == "COMPLETE"
    assert status_page.status_code == 200
    assert "Clear transfer acceptance completed." in status_page.text
    assert "Confirmed 25 cmu-test." in status_page.text
    assert 'href="/clear">Open Clear Transactions</a>' in status_page.text
    assert acorn.loaded is True
    assert acorn.accepted_clear_receipts == [event_id]
    assert "Clear transfer acceptance completed." in result.text
    assert "Confirmed 25 cmu-test." in result.text
    assert "25 <span>cmu-test</span>" in result.text
    assert "Accept Clear Transaction" in result.text
    assert "guest passes" in result.text
    assert "Accept Clear Transfer" not in result.text


def test_clear_acceptance_response_does_not_wait_for_wallet_load(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    event_id = "7" * 64
    acorn = FakeLoadedAcorn(balance=100)
    acorn.clear_receipts = [{
        "event_id": event_id,
        "sender_pubkey": "sender-pubkey",
        "status": "pending",
        "amount": 9,
        "unit": "cmu-background",
        "mint": "https://clear.example",
        "comment": "background wallet load",
        "timestamp": 1_786_430_400,
    }]
    load_started = threading.Event()
    allow_load_to_finish = threading.Event()
    background_thread_names: list[str] = []

    async def delayed_load_data() -> None:
        background_thread_names.append(threading.current_thread().name)
        load_started.set()
        await asyncio.to_thread(allow_load_to_finish.wait, 2)
        acorn.loaded = True

    acorn.load_data = delayed_load_data
    app.dependency_overrides[get_acorn] = lambda: acorn
    app.dependency_overrides[get_background_acorn_factory] = lambda: lambda: acorn
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        page = client.get("/clear")
        token_match = re.search(
            r'name="csrf_token" value="([^"]+)"',
            page.text,
        )
        assert token_match is not None
        response = client.post(
            "/clear/receipts/accept",
            data={"csrf_token": token_match.group(1), "event_id": event_id},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/clear/acceptance-status"
        assert load_started.wait(0.5) is True
        running = get_clear_acceptance_job(
            app.state.database_engine,
            acorn.pubkey_bech32,
        )
        assert running is not None
        assert running["status"] == "RUNNING"
        assert running["phase"] == "LOADING"
        status_page = client.get(response.headers["location"])
        assert status_page.status_code == 200
        assert "Clear transfer acceptance is running in the background." in status_page.text
        assert "Current phase: loading." in status_page.text
        assert "Check Status" in status_page.text

        allow_load_to_finish.set()
        deadline = time.monotonic() + 2
        completed = None
        while time.monotonic() < deadline:
            completed = get_clear_acceptance_job(
                app.state.database_engine,
                acorn.pubkey_bech32,
            )
            if completed and completed["status"] == "COMPLETE":
                break
            time.sleep(0.01)

    assert completed is not None
    assert completed["status"] == "COMPLETE"
    assert acorn.loaded is True
    assert background_thread_names
    assert background_thread_names[0].startswith("safebox-wallet-job")


def test_user_can_check_then_accept_relay_clear_transfer(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    acorn = FakeLoadedAcorn(balance=100)
    event_id = "8" * 64
    acorn.clear_sweep_receipts = [{
            "event_id": event_id,
            "sender_pubkey": "sender-pubkey",
            "amount": 12,
            "unit": "cmu-preview",
            "mints": ["http://127.0.0.1:3339"],
            "comment": "preview acceptance",
            "timestamp": 1_786_430_400,
        }]
    app.dependency_overrides[get_acorn] = lambda: acorn
    app.dependency_overrides[get_background_acorn_factory] = lambda: lambda: acorn
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        page = client.get("/clear")
        token_match = re.search(
            r'name="csrf_token" value="([^"]+)"',
            page.text,
        )
        assert token_match is not None
        checked = client.post(
            "/clear/receive",
            data={"csrf_token": token_match.group(1)},
            follow_redirects=True,
        )
        page = checked
        token_match = re.search(
            r'name="csrf_token" value="([^"]+)"',
            page.text,
        )
        assert token_match is not None
        assert "12 pending in 1 transfer" in page.text
        assert "Accept Clear Transfer" in page.text
        assert "Delete pending transfer" in page.text
        response = client.post(
            "/clear/receipts/accept",
            data={"csrf_token": token_match.group(1), "event_id": event_id},
            follow_redirects=False,
        )
        deadline = time.monotonic() + 2
        job = None
        while time.monotonic() < deadline:
            job = get_clear_acceptance_job(
                app.state.database_engine,
                acorn.pubkey_bech32,
            )
            if job and job["status"] == "COMPLETE":
                break
            time.sleep(0.01)
        result = client.get("/clear")

    assert response.status_code == 303
    assert response.headers["location"] == "/clear/acceptance-status"
    assert job is not None
    assert job["status"] == "COMPLETE"
    assert acorn.loaded is True
    assert acorn.clear_sweep_calls == 1
    assert acorn.accepted_clear_receipts == [event_id]
    assert "12 <span>cmu-preview</span>" in result.text
    assert "Accept Clear Transfer" not in result.text


def test_stored_clear_receipt_survives_history_lookup_failure(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    acorn = FakeLoadedAcorn(balance=100)
    acorn.clear_receipts = [{
            "event_id": "9" * 64,
            "sender_pubkey": "sender-pubkey",
            "amount": 30,
            "unit": "cmu-preview",
            "mint": "http://127.0.0.1:3339",
            "status": "pending",
            "timestamp": 1_786_430_400,
        }]

    async def broken_history():
        raise RuntimeError("history unavailable")

    acorn.get_clear_transaction_history = broken_history
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/clear")

    assert response.status_code == 200
    assert "30 pending in 1 transfer" in response.text
    assert "Pending Clear Transfer" in response.text
    assert "No completed Clear transactions found." in response.text


def test_clear_page_ignores_invalid_receive_notice_count(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    acorn = FakeLoadedAcorn(balance=100)
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/clear?received=invalid")

    assert response.status_code == 200
    assert "Received invalid" not in response.text


def test_user_can_delete_pending_clear_receipt(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    acorn = FakeLoadedAcorn(balance=100)
    event_id = "e" * 64
    acorn.clear_receipts = [
        {
            "event_id": event_id,
            "status": "pending",
            "amount": 25,
            "unit": "cmu-test",
            "mint": "https://clear.example",
            "timestamp": 1_786_430_400,
        }
    ]
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        page = client.get("/clear")
        token_match = re.search(
            r'name="csrf_token" value="([^"]+)"',
            page.text,
        )
        assert token_match is not None
        response = client.post(
            "/clear/receipts/delete",
            data={
                "csrf_token": token_match.group(1),
                "event_id": event_id,
                "confirmed": "yes",
            },
            follow_redirects=False,
        )
        result = client.get(response.headers["location"])

    assert response.status_code == 303
    assert response.headers["location"] == "/clear?receipt_deleted=1"
    assert acorn.deleted_clear_receipts == [event_id]
    assert "Pending Clear transfer deleted." in result.text
    assert "No pending Clear transfers." in result.text
    assert "No completed Clear transactions found." in result.text


def test_clear_receipt_deletion_requires_confirmation(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    acorn = FakeLoadedAcorn(balance=100)
    event_id = "f" * 64
    acorn.clear_receipts = [
        {
            "event_id": event_id,
            "status": "pending",
            "amount": 10,
            "unit": "cmu-test",
            "mint": "https://clear.example",
        }
    ]
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        page = client.get("/clear")
        token_match = re.search(
            r'name="csrf_token" value="([^"]+)"',
            page.text,
        )
        assert token_match is not None
        response = client.post(
            "/clear/receipts/delete",
            data={
                "csrf_token": token_match.group(1),
                "event_id": event_id,
            },
        )

    assert response.status_code == 400
    assert acorn.deleted_clear_receipts == []


def test_clear_page_resolves_aliases_without_summing_distinct_balances(
    tmp_path,
    monkeypatch,
) -> None:
    metadata = {
        "https://clear.safebox.dev/v1/info": {
            "mint_url": "https://clear.safebox.dev",
            "currency": {
                "name": "Clear Lab Credit Program",
                "unit": "cmu-new",
                "friendly_alias": "Clear Lab Credits",
                "friendly_unit_alias": "credits",
            },
        },
        "https://harbour.example/v1/info": {
            "mint_url": "https://harbour.example",
            "currency": {
                "name": "Harbour Program",
                "unit": "cmu-old",
                "friendly_alias": "Harbour Lab Credits",
                "friendly_unit_alias": "smiles",
            },
        },
    }
    requested_urls = []

    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.payload = payload
            self.content = json.dumps(payload).encode()

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self.payload

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url):
            requested_urls.append(url)
            return FakeResponse(metadata[url])

    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeClient)
    app = create_app(database_settings(tmp_path))
    acorn = FakeLoadedAcorn(balance=9_836)
    acorn.clear_receipts = [
        {
            "event_id": "clear-new",
            "status": "pending",
            "amount": 25,
            "unit": "cmu-new",
            "mint": "https://clear.safebox.dev",
        },
        {
            "event_id": "clear-old",
            "status": "pending",
            "amount": 25,
            "unit": "cmu-old",
            "mint": "https://harbour.example",
        },
    ]
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/clear")

    assert response.status_code == 200
    assert "2 pending transfers across 2 Clear balances." in response.text
    assert "Clear Lab Credits" in response.text
    assert "Harbour Lab Credits" in response.text
    assert "25 pending in 1 transfer" in response.text
    assert "cmu-new" in response.text
    assert "cmu-old" in response.text
    assert sorted(requested_urls) == sorted(metadata)


def test_transaction_page_shows_persisted_payment_awaiting_confirmation(tmp_path) -> None:
    settings = database_settings(tmp_path)
    app = create_app(settings)
    acorn = FakeLoadedAcorn(balance=100)
    acorn.continuity_receipts = [
        {
            "event_id": "continuity-event-1",
            "amount": 5,
            "timestamp": 1_786_430_400,
            "comment": "local market",
            "status": "provisional",
        }
    ]
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/transactions?check=1")

    assert response.status_code == 200
    assert "Pending Cash transfers: 5 sats in 1 transfer." in response.text
    assert "100 <span>sats</span>" in response.text


def test_transaction_page_previews_unprocessed_incoming_payments_without_receiving(
    tmp_path,
) -> None:
    settings = database_settings(tmp_path)
    app = create_app(settings)
    acorn = FakeLoadedAcorn(balance=100)
    acorn.incoming_preview = {"previewed_count": 2, "previewed_amount": 7}
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/transactions?check=1")

    assert response.status_code == 200
    assert "Pending Cash transfers: 7 sats in 2 transfers." in response.text
    assert "100 <span>sats</span>" in response.text
    assert acorn.preview_calls == 1
    assert acorn.receive_calls == 0


def test_transaction_history_sums_all_pending_payments(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    acorn = FakeLoadedAcorn(balance=100)
    acorn.continuity_receipts = [
        {
            "event_id": "continuity-event-1",
            "amount": 5,
            "timestamp": 1_786_430_400,
            "comment": "local market",
            "status": "provisional",
        }
    ]
    acorn.incoming_preview = {
        "previewed_count": 2,
        "previewed_amount": 7,
        "previewed": [
            {
                "event_id": "incoming-event-2",
                "sender_pubkey": "sender-two-pubkey",
                "timestamp": 1_786_430_500,
                "amount": 4,
                "comment": "Community supplies",
            },
            {
                "event_id": "incoming-event-3",
                "sender_pubkey": "sender-three-pubkey",
                "timestamp": 1_786_430_450,
                "amount": 3,
            },
        ],
    }
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/transactions?check=1")

    assert response.status_code == 200
    assert "Pending Cash transfers: 12 sats in 3 transfers." in response.text
    assert "100 <span>sats</span>" in response.text
    assert "Pending Cash Transfers" in response.text
    assert "These Cash transfers have arrived for this Acorn" in response.text
    assert "Awaiting mint confirmation" in response.text
    assert "Received on relay; finalization pending" in response.text
    assert "+5 sats" in response.text
    assert "+4 sats" in response.text
    assert "+3 sats" in response.text
    assert "local market" in response.text
    assert "Community supplies" in response.text
    assert "<strong>Detail:</strong>" in response.text
    assert "sender-two-p" in response.text
    assert response.text.index("+4 sats") < response.text.index("+3 sats")
    assert response.text.index("+3 sats") < response.text.index("+5 sats")
    assert "No transaction history was found" in response.text


def test_pending_transaction_list_deduplicates_staged_event(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    acorn = FakeLoadedAcorn(balance=100)
    acorn.continuity_receipts = [
        {
            "event_id": "same-event",
            "sender_pubkey": "sender-pubkey",
            "amount": 9,
            "timestamp": 1_786_430_400,
            "status": "provisional",
        }
    ]
    acorn.incoming_preview = {
        "previewed_count": 1,
        "previewed_amount": 9,
        "previewed": [
            {
                "event_id": "same-event",
                "sender_pubkey": "sender-pubkey",
                "amount": 9,
                "timestamp": 1_786_430_400,
            }
        ],
    }
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/transactions?check=1")

    assert response.status_code == 200
    assert "Pending Cash transfers: 9 sats in 1 transfer." in response.text
    assert response.text.count("+9 sats") == 1
    assert "Awaiting mint confirmation" in response.text


def test_transaction_finalization_runs_in_background(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    acorn = FakeLoadedAcorn(
        receive_result={
            "queried": 3,
            "accepted_count": 3,
            "accepted_amount": 50,
            "provisional_count": 3,
            "provisional_amount": 50,
        }
    )
    acorn.continuity_reconciliation = {
        "confirmed_count": 3,
        "confirmed_amount": 50,
        "pending_count": 0,
        "pending_amount": 0,
        "terminal_error_count": 0,
        "terminal_error_amount": 0,
    }
    background_thread_names: list[str] = []
    original_load_data = acorn.load_data

    async def recording_load_data() -> None:
        background_thread_names.append(threading.current_thread().name)
        await original_load_data()

    acorn.load_data = recording_load_data
    app.dependency_overrides[get_receive_acorn] = lambda: acorn
    app.dependency_overrides[get_loaded_acorn] = lambda: acorn
    app.dependency_overrides[get_acorn] = lambda: acorn
    app.dependency_overrides[get_background_acorn_factory] = lambda: lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.post(
            "/transactions/finalize-background",
            data={"csrf_token": valid_csrf_token()},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/transactions?finalization=started"

        deadline = time.monotonic() + 2
        job = None
        while time.monotonic() < deadline:
            job = get_finalization_job(
                app.state.database_engine,
                acorn.pubkey_bech32,
            )
            if job and job["status"] == "COMPLETE":
                break
            time.sleep(0.01)

        page = client.get("/transactions")

    assert job is not None
    assert job["status"] == "COMPLETE"
    assert job["confirmed_count"] == 3
    assert job["confirmed_amount"] == 50
    assert acorn.receive_calls == 1
    assert acorn.receive_finalize_values == [False]
    assert background_thread_names
    assert background_thread_names[0].startswith("safebox-wallet-job")
    assert "Cash transaction finalization completed." in page.text
    assert "Finalized 50 sats from 3 transfers." in page.text


def test_transaction_history_can_receive_incoming_ecash() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(
        receive_result={
            "queried": 1,
            "accepted_count": 1,
            "accepted_amount": 3,
        },
        transaction_history=[
            {
                "create_time": "2026-08-04 12:00:00",
                "tx_type": "C",
                "amount": 3,
                "comment": "ecash transfer received",
                "current_balance": 324,
            }
        ],
    )
    app.dependency_overrides[get_receive_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/transactions/receive",
        data={"csrf_token": valid_csrf_token()},
    )

    assert response.status_code == 200
    assert acorn.receive_calls == 1
    assert acorn.receive_finalize_values == [False]
    assert "Finalized 3 sats." in response.text
    assert '<a class="wallet-balance transaction-balance" href="/wallet"' in response.text
    assert "+3 sats" in response.text
    assert "funds transfer received" in response.text


def test_receive_incoming_ecash_warns_when_credit_history_is_missing() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(
        receive_result={
            "queried": 1,
            "accepted_count": 1,
            "accepted_amount": 3,
        },
        transaction_history=[],
    )
    app.dependency_overrides[get_receive_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/transactions/receive",
        data={"csrf_token": valid_csrf_token()},
    )

    assert response.status_code == 200
    assert "Finalized 3 sats." in response.text
    assert "wallet balance may already reflect the accepted funds" in response.text
    assert "Reload transaction history before relying on the journal" in response.text


def test_receive_continuity_payment_remains_pending_when_mint_is_unavailable(
    monkeypatch,
) -> None:
    async def verification_must_not_run_after_failed_reconciliation(_acorn, _timeout):
        raise AssertionError("Pending-only results should not verify the wallet balance")

    monkeypatch.setattr(
        main_module,
        "_read_proof_verification",
        verification_must_not_run_after_failed_reconciliation,
    )
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(
        receive_result={
            "queried": 1,
            "accepted_count": 1,
            "confirmed_count": 0,
            "accepted_amount": 0,
            "provisional_count": 1,
            "provisional_amount": 5,
        },
        transaction_history=[],
    )
    acorn.continuity_reconciliation = {
        "confirmed_count": 0,
        "confirmed_amount": 0,
        "pending_count": 1,
        "pending_amount": 5,
    }
    app.dependency_overrides[get_receive_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/transactions/receive",
        data={"csrf_token": valid_csrf_token()},
    )

    assert response.status_code == 200
    assert "5 sats remain pending." in response.text
    assert "Received 0 sats" not in response.text
    assert "wallet balance may already reflect" not in response.text


def test_receive_continuity_payment_confirms_pending_proofs() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(
        balance=105,
        receive_result={
            "queried": 0,
            "accepted_count": 0,
            "confirmed_count": 0,
            "accepted_amount": 0,
            "provisional_count": 0,
            "provisional_amount": 0,
        },
        transaction_history=[
            {
                "create_time": "2026-08-12 12:00:00",
                "tx_type": "C",
                "amount": 5,
                "comment": "continuity payment confirmed: market",
                "current_balance": 105,
            }
        ],
    )
    acorn.continuity_reconciliation = {
        "confirmed_count": 1,
        "confirmed_amount": 5,
        "pending_count": 0,
        "pending_amount": 0,
    }
    app.dependency_overrides[get_receive_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/transactions/receive",
        data={"csrf_token": valid_csrf_token()},
    )

    assert response.status_code == 200
    assert "Finalized 5 sats." in response.text
    assert "+5 sats" in response.text
    assert "continuity payment confirmed: market" in response.text


def test_receive_records_terminal_spent_token_error_and_clears_pending_notice() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(
        balance=100,
        receive_result={
            "queried": 0,
            "accepted_count": 0,
            "accepted_amount": 0,
            "provisional_count": 0,
            "provisional_amount": 0,
        },
        transaction_history=[
            {
                "create_time": "2026-08-12 17:56:28",
                "tx_type": "X",
                "amount": 21,
                "comment": (
                    "Incoming ecash was not credited: the issuing mint reports "
                    "the token was already spent."
                ),
                "current_balance": 100,
            }
        ],
    )
    acorn.continuity_reconciliation = {
        "confirmed_count": 0,
        "confirmed_amount": 0,
        "pending_count": 0,
        "pending_amount": 0,
        "terminal_error_count": 1,
        "terminal_error_amount": 21,
    }
    app.dependency_overrides[get_receive_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/transactions/receive",
        data={"csrf_token": valid_csrf_token()},
    )

    assert response.status_code == 200
    assert "Recorded 1 failed transaction totaling 21 sats" in response.text
    assert "no balance was credited" in response.text
    assert '<span class="transaction-kind">Error</span>' in response.text
    assert "21 sats remain pending" not in response.text


def test_receive_incoming_ecash_retries_until_credit_history_is_visible() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(
        receive_result={
            "queried": 1,
            "accepted_count": 1,
            "accepted_amount": 23,
        },
        delayed_transaction_history=[
            [],
            [
                {
                    "create_time": "2026-08-04 12:00:00",
                    "tx_type": "C",
                    "amount": 23,
                    "comment": "ecash transfer received",
                    "current_balance": 344,
                }
            ],
        ],
    )
    app.dependency_overrides[get_receive_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/transactions/receive",
        data={"csrf_token": valid_csrf_token()},
    )

    assert response.status_code == 200
    assert "Finalized 23 sats." in response.text
    assert "+23 sats" in response.text
    assert "wallet balance may already reflect the accepted funds" not in response.text


def test_receive_incoming_ecash_stale_proofs_repairs_and_requires_fresh_check() -> None:
    class StaleReceiveAcorn(FakeLoadedAcorn):
        async def sweep_ecash_transfers(self, *, finalize: bool = True) -> dict:
            self.receive_calls += 1
            self.receive_finalize_values.append(finalize)
            raise RuntimeError(
                "Unable to accept token safely: Local wallet proof state is stale"
            )

    app = create_app(TEST_SETTINGS)
    acorn = StaleReceiveAcorn()
    app.dependency_overrides[get_receive_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/transactions/receive",
        data={"csrf_token": valid_csrf_token()},
    )

    assert response.status_code == 409
    assert acorn.receive_calls == 1
    assert acorn.receive_finalize_values == [False]
    assert acorn.repair_calls == 1
    assert acorn.repair_force_values == [False]
    assert "Safebox repaired stale proofs" in response.text
    assert "Finalize pending transactions again" in response.text


def test_transaction_history_offers_explicit_force_finalization(tmp_path) -> None:
    app = create_app(database_settings(tmp_path))
    app.dependency_overrides[get_loaded_acorn] = lambda: FakeLoadedAcorn()
    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/transactions")

    assert response.status_code == 200
    assert '<input type="hidden" name="force" value="true">' in response.text
    assert 'name="force_confirm" value="acknowledged" required' in response.text
    assert "Force Finalization" in response.text
    assert "remove only those the mint" in response.text
    assert "Usable proofs are not refreshed or swapped" in response.text


def test_force_finalization_repairs_all_proofs_before_receiving() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(
        receive_result={
            "queried": 1,
            "accepted_count": 1,
            "accepted_amount": 3,
        },
        transaction_history=[
            {
                "create_time": "2026-08-12 12:00:00",
                "tx_type": "C",
                "amount": 3,
                "comment": "ecash transfer received",
                "current_balance": 324,
            }
        ],
    )
    acorn.stale_reconciliation_result = {
        "status": "OK",
        "operation": "reconcile-stale-proofs",
        "removed": 2,
        "amount": 5,
        "balance": 316,
    }
    app.dependency_overrides[get_receive_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/transactions/receive",
        data={
            "csrf_token": valid_csrf_token(),
            "force": "true",
            "force_confirm": "acknowledged",
        },
    )

    assert response.status_code == 200
    assert acorn.repair_calls == 0
    assert acorn.stale_reconciliation_calls == 1
    assert acorn.receive_calls == 1
    assert "Removed 5 sats in 2 stale proofs. Finalized 3 sats." in response.text


def test_force_finalization_requires_explicit_acknowledgement() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn()
    app.dependency_overrides[get_receive_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/transactions/receive",
        data={"csrf_token": valid_csrf_token(), "force": "true"},
    )

    assert response.status_code == 400
    assert acorn.repair_calls == 0
    assert acorn.stale_reconciliation_calls == 0
    assert acorn.receive_calls == 0
    assert "Force finalization can permanently remove" in response.text


def test_receive_incoming_ecash_rejects_invalid_csrf() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn()
    app.dependency_overrides[get_receive_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/transactions/receive",
        data={"csrf_token": "invalid"},
    )

    assert response.status_code == 403
    assert acorn.receive_calls == 0


def test_payment_form_displays_balance_and_confirmation() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/pay")

    assert response.status_code == 200
    assert "Transfer a Balance" in response.text
    assert "500 sats" in response.text
    assert "Transfer Address" in response.text
    assert "Transfer From" in response.text
    assert "Balance Transfer Mode" in response.text
    assert "Cash Transfer Mode" not in response.text
    assert 'name="csrf_token"' in response.text
    assert 'name="confirmed"' in response.text
    assert 'name="payment_mode"' in response.text
    assert 'value="confirmed"' in response.text
    assert 'value="continuity"' in response.text
    assert "Balance transfer in progress. Please wait" in response.text
    assert "Transferring…" in response.text
    assert "<summary>Advisories</summary>" in response.text
    assert "fee reserve" in response.text
    assert "Scan a Lightning address or invoice QR code" not in response.text
    assert "spendable" not in response.text.lower()


def test_payment_form_lists_each_clear_balance_without_combining_it() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    acorn.clear_balances = [
        {
            "mint": "http://clear.one",
            "unit": "cmu-one",
            "amount": 25,
            "proof_count": 3,
        },
        {
            "mint": "http://clear.two",
            "unit": "cmu-two",
            "amount": 40,
            "proof_count": 2,
        },
    ]
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/pay")

    assert response.status_code == 200
    assert '<select id="payment_asset" name="payment_asset"' in response.text
    assert "Cash Balance" in response.text
    assert "cmu-one — 25 cmu-one" in response.text
    assert "cmu-two — 40 cmu-two" in response.text


def test_clear_payment_sends_exact_mint_and_cmu_to_compatible_address(
    monkeypatch,
) -> None:
    recipient_hex = "11" * 32
    recipient_npub = main_module.Keys.hex_to_bech32(recipient_hex, prefix="npub")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "names": {"alice": recipient_hex},
                "relays": {recipient_hex: ["wss://recipient-relay.example"]},
                "clear": {
                    "alice": {
                        "protocols": ["clear-token-transfer"],
                        "transports": ["nip59"],
                        "kinds": [7379],
                        "mints": ["http://clear.one"],
                        "units": ["cmu-one"],
                    }
                },
            }

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url, params):
            assert url == "https://example.com/.well-known/nostr.json"
            assert params == {"name": "alice"}
            return FakeResponse()

    async def cash_mint_must_not_be_checked(_acorn, _timeout):
        raise AssertionError("A Clear payment must not check the Cash balance")

    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        main_module,
        "_read_proof_verification",
        cash_mint_must_not_be_checked,
    )
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    acorn.clear_balances = [
        {
            "mint": "http://clear.one",
            "unit": "cmu-one",
            "amount": 25,
            "proof_count": 3,
        }
    ]
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/pay",
        data={
            "csrf_token": valid_csrf_token(),
            "payment_asset": main_module._encode_clear_payment_asset(
                "http://clear.one",
                "cmu-one",
            ),
            "lightning_address": "alice@example.com",
            "amount": "5",
            "comment": "meeting room",
            "payment_mode": "confirmed",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 200
    assert "Clear balance transferred" in response.text
    assert "5 cmu-one" in response.text
    assert "recipient must accept" in response.text
    assert acorn.payments == []
    assert acorn.ecash_transfers == []
    assert acorn.clear_transfers == [
        {
            "amount": 5,
            "recipient": recipient_npub,
            "relay": "wss://recipient-relay.example",
            "mint": "http://clear.one",
            "unit": "cmu-one",
            "comment": "meeting room",
        }
    ]


def test_clear_payment_rejects_address_without_compatible_advertisement(
    monkeypatch,
) -> None:
    recipient_hex = "11" * 32

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "names": {"alice": recipient_hex},
                "relays": {recipient_hex: ["wss://recipient-relay.example"]},
            }

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url, params):
            return FakeResponse()

    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeClient)
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    acorn.clear_balances = [
        {
            "mint": "http://clear.one",
            "unit": "cmu-one",
            "amount": 25,
            "proof_count": 3,
        }
    ]
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/pay",
        data={
            "csrf_token": valid_csrf_token(),
            "payment_asset": main_module._encode_clear_payment_asset(
                "http://clear.one",
                "cmu-one",
            ),
            "lightning_address": "alice@example.com",
            "amount": "5",
            "comment": "must not send",
            "payment_mode": "confirmed",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 422
    assert "does not advertise support for this Clear Balance" in response.text
    assert "No value was sent" in response.text
    assert acorn.clear_transfers == []
    assert acorn.payments == []


def test_lightning_address_scanner_is_authenticated_and_self_contained() -> None:
    app = create_app(TEST_SETTINGS)
    app.dependency_overrides[get_session_credentials] = lambda: SessionCredentials(
        nsec=TEST_NSEC,
        bootstrap_relay="wss://relay.example.com",
    )
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/scan/lightning")

    assert response.status_code == 200
    assert 'data-lightning-scanner' in response.text
    assert 'src="/static/lightning-scan.js"' in response.text
    assert 'method="post" action="/scan/lightning"' in response.text
    assert "data-scanner-form" in response.text
    assert '<button type="button" data-scanner-start hidden>' in response.text
    assert "Requesting camera access…" in response.text
    assert '<details class="general-advisories scanner-advisories">' in response.text
    assert response.text.index('data-lightning-scanner') < response.text.index(
        "Point the camera at a Safebox"
    )
    assert "Camera scanning requires JavaScript" in response.text
    assert "worker-src 'self' blob:" in response.headers["content-security-policy"]

    script = client.get("/static/lightning-scan.js")
    assert script.status_code == 200
    assert "const start = async () =>" in script.text
    assert "void start();" in script.text
    assert "startButton.hidden = false;" in script.text


def test_scanned_lightning_address_prefills_payment_review() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/scan/lightning",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_payment": "LIGHTNING:alice@example.com",
        },
    )

    assert response.status_code == 200
    assert "Transfer a Balance" in response.text
    assert 'value="alice@example.com"' in response.text


def test_scanned_lnurl_pay_qr_derives_lightning_address() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")
    lnurl = main_module.encode_lnurl(
        "https://safebox.example/.well-known/lnurlp/alice"
    )

    response = client.post(
        "/scan/lightning",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_payment": lnurl,
        },
    )

    assert response.status_code == 200
    assert "Transfer a Balance" in response.text
    assert 'value="alice@safebox.example"' in response.text
    assert lnurl not in response.text


def test_scanned_arbitrary_lnurl_endpoint_is_not_treated_as_address() -> None:
    app = create_app(TEST_SETTINGS)
    app.dependency_overrides[get_payment_acorn] = lambda: FakeLoadedAcorn()
    client = TestClient(app, base_url="https://safebox.example")
    lnurl = main_module.encode_lnurl("https://example.com/custom/callback")

    response = client.post(
        "/scan/lightning",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_payment": lnurl,
        },
    )

    assert response.status_code == 400
    assert "supported Lightning address" in response.text


def test_scanner_recognizes_record_transfer_descriptor() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn()
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/scan/lightning",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_payment": TEST_TRANSFER_DESCRIPTOR,
        },
    )

    assert response.status_code == 200
    assert "A valid Acorn record-sharing code was recognized" in response.text
    assert "Shared Notes" in response.text
    assert 'action="/record/import"' in response.text
    assert 'type="checkbox"' not in response.text
    assert 'data-progress-form' in response.text
    assert 'data-progress-label="Importing record…"' in response.text
    assert TEST_TRANSFER_DESCRIPTOR in response.text
    assert acorn.record_transfer_inspect_calls == [
        {
            "descriptor": TEST_TRANSFER_DESCRIPTOR,
            "allowed_servers": [TEST_SETTINGS.blossom_home_server],
        }
    ]
    assert acorn.payments == []


def test_scanner_browser_module_recognizes_presentation_before_transfer() -> None:
    response = make_https_client().get("/static/lightning-scan.js")

    assert response.status_code == 200
    presentation_check = response.text.index(
        'lowerValue.startsWith("acorn:record-presentation:")'
    )
    transfer_check = response.text.index(
        'lowerValue.startsWith("acorn:record-transfer:")'
    )
    assert presentation_check < transfer_check
    assert "Record presentation acquired" in response.text
    assert "scanForm.requestSubmit()" in response.text


def test_scanned_non_lightning_qr_is_rejected_without_payment() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/scan/lightning",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_payment": "https://example.com/not-a-lightning-address",
        },
    )

    assert response.status_code == 400
    assert "does not contain a supported Lightning address or fixed-amount" in response.text
    assert acorn.payments == []


@pytest.mark.parametrize(
    "payload",
    (
        "https://alice@example.com",
        "alice@example",
        "alice@@example.com",
        "alice example@example.com",
    ),
)
def test_lightning_address_normalizer_rejects_non_address_payloads(payload: str) -> None:
    assert main_module._normalize_lightning_address(payload) is None


def test_real_bolt11_invoice_is_decoded_for_review() -> None:
    bolt11_module = main_module.bolt11
    invoice = bolt11_module.Bolt11(
        currency="bc",
        date=int(time.time()),
        amount_msat=bolt11_module.MilliSatoshi(21_000),
        tags=bolt11_module.Tags(
            [
                bolt11_module.Tag(bolt11_module.TagChar.payment_hash, "00" * 32),
                bolt11_module.Tag(bolt11_module.TagChar.payment_secret, "11" * 32),
                bolt11_module.Tag(bolt11_module.TagChar.description, "Test invoice"),
            ]
        ),
    )
    encoded = bolt11_module.encode(invoice, private_key="12" * 32)

    decoded = main_module._decode_lightning_invoice(f"lightning:{encoded}")

    assert decoded is not None
    assert decoded["amount"] == 21
    assert decoded["description"] == "Test invoice"


def test_invoice_review_state_rejects_tampering() -> None:
    cipher = InvoicePaymentCipher(TEST_SETTINGS)
    token = cipher.encode(
        InvoicePaymentState(invoice="lnbc210n1pytestinvoice", amount=21)
    )

    with pytest.raises(ValueError, match="invalid or expired"):
        cipher.decode(token[:-2] + "xx")


def test_scanned_invoice_renders_review_without_exposing_raw_invoice(monkeypatch) -> None:
    invoice = "lnbc210n1pytestinvoice"
    monkeypatch.setattr(
        main_module,
        "_decode_lightning_invoice",
        lambda value: {
            "invoice": invoice,
            "amount": 21,
            "description": "Coffee",
            "expiry": "2026-08-06 23:59:00",
            "payment_hash": "ab" * 32,
        }
        if str(value).removeprefix("lightning:") == invoice
        else None,
    )
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/scan/lightning",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_payment": f"lightning:{invoice}",
        },
    )

    assert response.status_code == 200
    assert "Review Lightning invoice" in response.text
    assert "21 sats" in response.text
    assert "Coffee" in response.text
    assert 'name="invoice_state"' in response.text
    assert invoice not in response.text
    assert acorn.invoice_payments == []


def test_confirmed_scanned_invoice_delegates_to_acorn(monkeypatch) -> None:
    invoice = "lnbc210n1pytestinvoice"
    decoded = {
        "invoice": invoice,
        "amount": 21,
        "description": "Coffee",
        "expiry": "2026-08-06 23:59:00",
        "payment_hash": "ab" * 32,
    }
    monkeypatch.setattr(main_module, "_decode_lightning_invoice", lambda value: decoded)
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")
    state_token = InvoicePaymentCipher(TEST_SETTINGS).encode(
        InvoicePaymentState(invoice=invoice, amount=21)
    )

    response = client.post(
        "/pay/invoice",
        data={
            "csrf_token": valid_csrf_token(),
            "invoice_state": state_token,
            "comment": "pytest invoice",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 200
    assert "Invoice payment successful" in response.text
    assert acorn.invoice_payments == [
        {"invoice": invoice, "comment": "pytest invoice"}
    ]


def test_receive_funds_form_displays_home_mint_and_amount_field() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_deposit_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/receive-funds")

    assert response.status_code == 200
    assert "500 sats" in response.text
    assert "https://mint.example.com" in response.text
    assert 'name="amount"' in response.text
    assert (
        "Create a transfer request for the balance you want to receive."
        in response.text
    )
    assert (
        'name="payment_method" type="radio" value="lightning" checked'
        in response.text
    )
    assert "Creating a transfer request. Please wait." in response.text
    assert "Creating request…" in response.text
    assert "Create Transfer Request" in response.text
    assert "Clear Mint Unit" in response.text


def test_receive_funds_rejects_unavailable_payment_method() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_deposit_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/receive-funds",
        data={
            "csrf_token": valid_csrf_token(),
            "amount": "21",
            "payment_method": "clear-cmu",
        },
    )

    assert response.status_code == 400
    assert "transfer-request method is not available yet" in response.text
    assert acorn.deposit_calls == []


@pytest.mark.parametrize(
    ("method", "legacy_path", "canonical_path", "status_code"),
    [
        ("GET", "/deposit", "/receive-funds", 303),
        ("POST", "/deposit", "/receive-funds", 307),
        ("POST", "/deposit/check", "/receive-funds/check", 307),
    ],
)
def test_legacy_deposit_routes_redirect_to_receive_funds(
    method: str,
    legacy_path: str,
    canonical_path: str,
    status_code: int,
) -> None:
    app = create_app(TEST_SETTINGS)
    client = TestClient(app, base_url="https://safebox.example")

    response = client.request(method, legacy_path, follow_redirects=False)

    assert response.status_code == status_code
    assert response.headers["location"] == canonical_path


def test_receive_funds_creates_lightning_request_without_polling() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_deposit_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/receive-funds",
        data={
            "csrf_token": valid_csrf_token(),
            "amount": "21",
            "payment_method": "lightning",
        },
    )

    assert response.status_code == 200
    assert "Lightning Payment Request" in response.text
    assert "21 sats" in response.text
    assert "lnbc21n1pytestinvoice" in response.text
    assert '<div class="invoice-qr"><svg' in response.text
    assert 'action="/receive-funds/check"' in response.text
    assert "Checking and finalizing the received funds. Please wait" in response.text
    assert "Checking transfer…" in response.text
    assert acorn.deposit_calls == [21]
    assert acorn.quote_checks == []

    token_match = re.search(
        r'name="deposit_token" value="([^"]+)"', response.text
    )
    assert token_match is not None
    deposit_token = unescape(token_match.group(1))
    assert "pytest-deposit-quote" not in deposit_token
    state = DepositQuoteCipher(TEST_SETTINGS).decode(deposit_token)
    assert state == DepositQuoteState(
        quote="pytest-deposit-quote",
        amount=21,
        mint="https://mint.example.com",
        invoice="lnbc21n1pytestinvoice",
    )


def test_paid_deposit_is_finalized_and_redirects_to_updated_wallet() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500, deposit_paid=True)
    app.dependency_overrides[get_deposit_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")
    state = DepositQuoteState(
        quote="pytest-deposit-quote",
        amount=21,
        mint=acorn.home_mint,
        invoice="lnbc21n1pytestinvoice",
    )

    response = client.post(
        "/receive-funds/check",
        data={
            "csrf_token": valid_csrf_token(),
            "deposit_token": DepositQuoteCipher(TEST_SETTINGS).encode(state),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/wallet"
    assert acorn.quote_checks == [("pytest-deposit-quote", 21)]
    assert acorn.balance == 521
    assert acorn.history_entries == [
        {"tx_type": "C", "amount": 21, "comment": "safebox web funds received"}
    ]


def test_unpaid_deposit_keeps_same_invoice_available_for_recheck() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500, deposit_paid=False)
    app.dependency_overrides[get_deposit_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")
    state = DepositQuoteState(
        quote="pytest-deposit-quote",
        amount=21,
        mint=acorn.home_mint,
        invoice="lnbc21n1pytestinvoice",
    )

    response = client.post(
        "/receive-funds/check",
        data={
            "csrf_token": valid_csrf_token(),
            "deposit_token": DepositQuoteCipher(TEST_SETTINGS).encode(state),
        },
    )

    assert response.status_code == 409
    assert "has not confirmed settlement yet" in response.text
    assert "lnbc21n1pytestinvoice" in response.text
    assert 'action="/receive-funds/check"' in response.text
    assert acorn.history_entries == []


def test_confirmed_lightning_payment_delegates_to_acorn() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/pay",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_address": "alice@example.com",
            "amount": "21",
            "comment": "pytest web payment",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 200
    assert "Balance transferred" in response.text
    assert "21 sats" in response.text
    assert "Fee: <strong>1 sat" in response.text
    assert acorn.payments == [
        {
            "amount": 21,
            "lnaddress": "alice@example.com",
            "comment": "pytest web payment",
        }
    ]


def test_safebox_lightning_address_prefers_direct_ecash_transfer(
    monkeypatch,
) -> None:
    recipient_hex = "11" * 32
    recipient_npub = main_module.Keys.hex_to_bech32(recipient_hex, prefix="npub")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "names": {"alice": recipient_hex},
                "relays": {recipient_hex: ["wss://recipient-relay.example"]},
            }

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url, params):
            assert url == "https://example.com/.well-known/nostr.json"
            assert params == {"name": "alice"}
            return FakeResponse()

    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeClient)
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/pay",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_address": "alice@example.com",
            "amount": "21",
            "comment": "direct please",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 200
    assert "Balance transferred" in response.text
    assert "Direct Safebox funds transfer sent" in response.text
    assert "Fee: <strong>0 sats" in response.text
    assert acorn.payments == []
    assert acorn.ecash_transfers == [
        {
            "amount": 21,
            "recipient": recipient_npub,
            "relay": "wss://recipient-relay.example",
            "comment": "direct please",
        }
    ]


def test_safebox_direct_ecash_transfer_requires_confirmed_result(
    monkeypatch,
) -> None:
    recipient_hex = "11" * 32

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "names": {"alice": recipient_hex},
                "relays": {recipient_hex: ["wss://recipient-relay.example"]},
            }

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url, params):
            return FakeResponse()

    class UnconfirmedDirectAcorn(FakeLoadedAcorn):
        async def send_ecash_transfer(self, **kwargs):
            self.ecash_transfers.append(kwargs)
            return {"status": "ERROR", "error": "relay publish not verified"}

    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeClient)
    app = create_app(TEST_SETTINGS)
    acorn = UnconfirmedDirectAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/pay",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_address": "alice@example.com",
            "amount": "21",
            "comment": "direct please",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 502
    assert "did not return a confirmed successful result" in response.text
    assert acorn.payments == []
    assert len(acorn.ecash_transfers) == 1


def test_safebox_direct_ecash_transfer_exception_shows_safe_reason(
    monkeypatch,
) -> None:
    recipient_hex = "11" * 32

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "names": {"alice": recipient_hex},
                "relays": {recipient_hex: ["wss://recipient-relay.example"]},
            }

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url, params):
            return FakeResponse()

    class FailingDirectAcorn(FakeLoadedAcorn):
        async def send_ecash_transfer(self, **kwargs):
            self.ecash_transfers.append(kwargs)
            raise RuntimeError("Mint swap unavailable <retry later>")

    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeClient)
    app = create_app(TEST_SETTINGS)
    acorn = FailingDirectAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/pay",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_address": "alice@example.com",
            "amount": "21",
            "comment": "direct please",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 502
    assert "funds delivery could not be completed" in response.text
    assert "Mint swap unavailable &lt;retry later&gt;" in response.text
    assert "<retry later>" not in response.text
    assert acorn.payments == []
    assert len(acorn.ecash_transfers) == 1


def test_lightning_payment_falls_back_when_nip05_is_not_safebox(
    monkeypatch,
) -> None:
    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url, params):
            raise main_module.httpx.HTTPError("not found")

    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeClient)
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/pay",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_address": "alice@example.com",
            "amount": "21",
            "comment": "lightning please",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 200
    assert acorn.ecash_transfers == []
    assert acorn.payments == [
        {
            "amount": 21,
            "lnaddress": "alice@example.com",
            "comment": "lightning please",
        }
    ]


def test_lightning_payment_failure_shows_safe_reason(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url, params):
            raise main_module.httpx.HTTPError("not found")

    class FailingLightningAcorn(FakeLoadedAcorn):
        async def pay_multi(self, amount: int, lnaddress: str, comment: str):
            self.payments.append(
                {"amount": amount, "lnaddress": lnaddress, "comment": comment}
            )
            raise RuntimeError("Melt quote failed <inspect>")

    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeClient)
    app = create_app(TEST_SETTINGS)
    acorn = FailingLightningAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/pay",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_address": "alice@example.com",
            "amount": "21",
            "comment": "lightning please",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 502
    assert "Safebox did not receive a confirmed successful result" in response.text
    assert "Melt quote failed &lt;inspect&gt;" in response.text
    assert "<inspect>" not in response.text
    assert acorn.ecash_transfers == []
    assert len(acorn.payments) == 1


def test_lightning_payment_stale_proofs_repairs_and_returns_to_review(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url, params):
            raise main_module.httpx.HTTPError("not found")

    class StaleProofAcorn(FakeLoadedAcorn):
        async def pay_multi(self, amount: int, lnaddress: str, comment: str):
            self.payments.append(
                {"amount": amount, "lnaddress": lnaddress, "comment": comment}
            )
            raise RuntimeError(
                "Payment could not proceed because the wallet contains stale proofs."
            )

    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeClient)
    app = create_app(TEST_SETTINGS)
    acorn = StaleProofAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/pay",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_address": "alice@example.com",
            "amount": "21",
            "comment": "lightning please",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 409
    assert acorn.repair_calls == 1
    assert len(acorn.payments) == 1
    assert "Safebox repaired stale proofs" in response.text
    assert "confirm the transfer again" in response.text
    assert 'value="alice@example.com"' in response.text
    assert 'value="21"' in response.text
    assert 'value="lightning please"' in response.text
    assert "<code>acorn reconcile-payments</code>" not in response.text
    assert "wallet contains stale proofs" in response.text


def test_payment_requires_explicit_confirmation_before_calling_acorn() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/pay",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_address": "alice@example.com",
            "amount": "21",
            "comment": "must not run",
        },
    )

    assert response.status_code == 400
    assert "Explicit transfer confirmation is required" in response.text
    assert acorn.payments == []


def test_payment_is_blocked_when_mint_verification_is_not_clean() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(
        balance=33_926,
        verified_balance=52,
        verification_status="repair-recommended",
    )
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/pay",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_address": "alice@example.com",
            "amount": "21",
            "comment": "must not run",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 409
    assert "transfer is blocked" in response.text
    assert acorn.payments == []


def test_confirmed_payment_still_blocks_when_mint_is_unavailable(
    monkeypatch,
) -> None:
    async def unavailable_mint(_acorn, _timeout):
        return None, "Mint verification was unavailable."

    monkeypatch.setattr(main_module, "_read_proof_verification", unavailable_mint)
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/pay",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_address": "alice@example.com",
            "amount": "21",
            "comment": "must not run",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 503
    assert "a mint is unavailable" in response.text
    assert "Continuity mode" in response.text
    assert acorn.payments == []
    assert acorn.ecash_transfers == []


def test_continuity_payment_sends_only_to_safebox_without_mint_check(
    monkeypatch,
) -> None:
    recipient_hex = "11" * 32
    recipient_npub = main_module.Keys.hex_to_bech32(recipient_hex, prefix="npub")

    async def mint_must_not_be_called(_acorn, _timeout):
        raise AssertionError("Continuity mode must not contact the mint")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "names": {"alice": recipient_hex},
                "relays": {recipient_hex: ["ws://spurline.local:8080"]},
            }

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url, params):
            return FakeResponse()

    monkeypatch.setattr(main_module, "_read_proof_verification", mint_must_not_be_called)
    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeClient)
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/pay",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_address": "alice@example.com",
            "amount": "21",
            "comment": "local market",
            "payment_mode": "continuity",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 200
    assert "Continuity Transfer sent" in response.text
    assert "mint was not contacted" in response.text
    assert acorn.payments == []
    assert acorn.ecash_transfers == [
        {
            "amount": 21,
            "recipient": recipient_npub,
            "relay": "ws://spurline.local:8080",
            "comment": "local market",
            "payment_mode": "continuity",
        }
    ]


def test_continuity_payment_rejects_external_lightning_address(
    monkeypatch,
) -> None:
    async def mint_must_not_be_called(_acorn, _timeout):
        raise AssertionError("Continuity mode must not contact the mint")

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def get(self, url, params):
            raise main_module.httpx.HTTPError("not a Safebox address")

    monkeypatch.setattr(main_module, "_read_proof_verification", mint_must_not_be_called)
    monkeypatch.setattr(main_module.httpx, "AsyncClient", FakeClient)
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn(balance=500)
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/pay",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_address": "outside@example.com",
            "amount": "21",
            "comment": "must stay local",
            "payment_mode": "continuity",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 422
    assert "only be sent to another Safebox address" in response.text
    assert "No Lightning transfer was attempted" in response.text
    assert 'value="continuity"' in response.text
    assert acorn.payments == []
    assert acorn.ecash_transfers == []


def test_record_index_links_encoded_labels() -> None:
    app = create_app(TEST_SETTINGS)
    app.dependency_overrides[get_acorn] = lambda: FakeLoadedAcorn()
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/records")

    assert response.status_code == 200
    assert "Field Notes" in response.text
    assert "/record?label=Field+Notes" in response.text
    assert "/record?label=Travel%2F2026" in response.text
    assert "/record?label=A+%26+B" in response.text
    assert 'href="/record/edit"' in response.text
    assert 'href="/blob/upload"' not in response.text
    assert '<h1>Manage Records</h1>' in response.text
    assert '<nav class="record-list" aria-label="Records">' in response.text
    assert response.text.count('class="record-list-item"') == 3
    assert ">Open</a>" not in response.text


def test_record_index_uses_relay_catalog_without_loading_wallet_or_records() -> None:
    class CatalogAcorn(FakeLoadedAcorn):
        async def get_record_catalog(self) -> dict:
            return {
                "records": [
                    {"label": "Fast Record", "modified_at": 200},
                    {"label": "Older Record", "modified_at": 100},
                ]
            }

    app = create_app(TEST_SETTINGS)
    acorn = CatalogAcorn()
    acorn.get_user_records = AsyncMock(
        side_effect=AssertionError("authoritative records should not be scanned")
    )
    app.dependency_overrides[get_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/records")

    assert response.status_code == 200
    assert response.text.index("Fast Record") < response.text.index("Older Record")
    assert acorn.loaded is False
    acorn.get_user_records.assert_not_awaited()


def test_record_index_does_not_rebuild_missing_catalog_during_page_load() -> None:
    class MissingCatalogAcorn(FakeLoadedAcorn):
        def __init__(self):
            super().__init__()
            self.rebuild_calls = 0

        async def get_record_catalog(self):
            return None

        async def rebuild_record_catalog(self):
            self.rebuild_calls += 1
            return {
                "records": [
                    {"label": "Recovered Catalog", "modified_at": 123}
                ]
            }

    app = create_app(TEST_SETTINGS)
    acorn = MissingCatalogAcorn()
    app.dependency_overrides[get_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        response = client.get("/records")

    assert response.status_code == 200
    assert "record catalog is missing" in response.text
    assert "Recovered Catalog" not in response.text
    assert acorn.rebuild_calls == 0
    assert acorn.loaded is False


def test_record_index_offers_explicit_catalog_rebuild() -> None:
    class RefreshableCatalogAcorn(FakeLoadedAcorn):
        async def get_record_catalog(self):
            return {"records": []}

        async def rebuild_record_catalog(self):
            return {
                "records": [
                    {"label": "Refreshed Record", "modified_at": 321}
                ]
            }

    app = create_app(TEST_SETTINGS)
    acorn = RefreshableCatalogAcorn()
    acorn.rebuild_record_catalog = AsyncMock(
        wraps=acorn.rebuild_record_catalog
    )
    app.dependency_overrides[get_acorn] = lambda: acorn

    with TestClient(app, base_url="https://safebox.example") as client:
        page = client.get("/records")
        response = client.post(
            "/records/rebuild",
            data={"csrf_token": valid_csrf_token()},
            follow_redirects=False,
        )

    assert page.status_code == 200
    assert "Refresh Record List" in page.text
    assert response.status_code == 303
    assert response.headers["location"] == "/records?catalog=rebuilt"
    acorn.rebuild_record_catalog.assert_awaited_once_with()


def test_record_index_orders_unique_labels_by_last_modified_descending() -> None:
    class TimestampedRecordAcorn(FakeLoadedAcorn):
        async def get_user_records(self, **_kwargs) -> list[dict]:
            return [
                {"tag": ["Older Record"], "timestamp": 100},
                {"tag": ["Recently Updated"], "timestamp": 200},
                {"tag": ["Newest Record"], "timestamp": 300},
                {"tag": ["Recently Updated"], "timestamp": 250},
            ]

    app = create_app(TEST_SETTINGS)
    app.dependency_overrides[get_acorn] = lambda: TimestampedRecordAcorn()
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/records")

    assert response.status_code == 200
    assert response.text.count("Recently Updated") == 1
    newest_position = response.text.index("Newest Record")
    updated_position = response.text.index("Recently Updated")
    older_position = response.text.index("Older Record")
    assert newest_position < updated_position < older_position


def test_record_index_paginates_ten_clickable_panels_at_a_time() -> None:
    class PaginatedRecordAcorn(FakeLoadedAcorn):
        async def get_user_records(self, **_kwargs) -> list[dict]:
            return [
                {
                    "tag": [f"Record {number:02d}"],
                    "timestamp": number,
                }
                for number in range(1, 26)
            ]

    app = create_app(TEST_SETTINGS)
    app.dependency_overrides[get_acorn] = lambda: PaginatedRecordAcorn()
    client = TestClient(app, base_url="https://safebox.example")

    first = client.get("/records")
    second = client.get("/records", params={"page": "2"})
    third = client.get("/records", params={"page": "3"})

    assert first.status_code == second.status_code == third.status_code == 200
    assert first.text.count('class="record-list-item"') == 10
    assert second.text.count('class="record-list-item"') == 10
    assert third.text.count('class="record-list-item"') == 5
    assert "Record 25" in first.text
    assert "Record 16" in first.text
    assert "Record 15" not in first.text
    assert "Record 15" in second.text
    assert "Record 06" in second.text
    assert "Record 05" in third.text
    assert "Page 1 of 3 · 25 records" in first.text
    assert 'rel="next" href="/records?page=2"' in first.text
    assert 'rel="prev" href="/records?page=1"' in second.text
    assert 'rel="next" href="/records?page=3"' in second.text
    assert 'rel="prev" href="/records?page=2"' in third.text
    assert 'rel="next"' not in third.text
    assert first.text.index("<h1>Manage Records</h1>") < first.text.index(
        'class="page-navigation"'
    )
    assert first.text.count('class="page-navigation') == 2
    assert ">Home</a>" in first.text[first.text.rindex('class="page-navigation'):]
    assert ">Back to Wallet</a>" in first.text[first.text.rindex('class="page-navigation'):]


def test_record_index_rejects_invalid_page_and_clamps_excessive_page() -> None:
    app = create_app(TEST_SETTINGS)
    app.dependency_overrides[get_acorn] = lambda: FakeLoadedAcorn()
    client = TestClient(app, base_url="https://safebox.example")

    invalid = client.get("/records", params={"page": "not-a-number"})
    excessive = client.get("/records", params={"page": "999"})

    assert invalid.status_code == 400
    assert "requested records page is invalid" in invalid.text
    assert excessive.status_code == 200
    assert "Page 1 of 1 · 3 records" in excessive.text


def test_record_index_folder_view_groups_paths_and_preserves_flat_records() -> None:
    class FolderRecordAcorn(FakeLoadedAcorn):
        async def get_user_records(self, **_kwargs) -> list[dict]:
            return [
                {"tag": ["Health/Passport"], "timestamp": 400},
                {"tag": ["Health/Labs/2026"], "timestamp": 300},
                {"tag": ["Finance/Taxes"], "timestamp": 200},
                {"tag": ["Emergency Contacts"], "timestamp": 100},
            ]

    app = create_app(TEST_SETTINGS)
    app.dependency_overrides[get_acorn] = lambda: FolderRecordAcorn()
    client = TestClient(app, base_url="https://safebox.example")

    root = client.get("/records", params={"view": "folders"})
    health = client.get(
        "/records",
        params={"view": "folders", "folder": "Health"},
    )
    labs = client.get(
        "/records",
        params={"view": "folders", "folder": "Health/Labs"},
    )

    assert root.status_code == health.status_code == labs.status_code == 200
    assert 'aria-current="page">Folders</a>' in root.text
    assert 'href="/records?view=folders&amp;folder=Finance"' in root.text
    assert 'href="/records?view=folders&amp;folder=Health"' in root.text
    assert 'href="/record?label=Emergency+Contacts"' in root.text
    assert "Passport" not in root.text
    assert 'href="/record?label=Health%2FPassport"' in health.text
    assert ">Passport</a>" in health.text
    assert 'href="/records?view=folders&amp;folder=Health%2FLabs"' in health.text
    assert "2026" not in health.text
    assert 'aria-label="Record folder breadcrumbs"' in labs.text
    assert 'href="/records?view=folders">Records</a>' in labs.text
    assert 'href="/records?view=folders&amp;folder=Health">Health</a>' in labs.text
    assert 'href="/record?label=Health%2FLabs%2F2026"' in labs.text
    assert ">2026</a>" in labs.text
    assert "Page 1 of" not in root.text


def test_record_index_rejects_unknown_view_and_handles_empty_folder() -> None:
    app = create_app(TEST_SETTINGS)
    app.dependency_overrides[get_acorn] = lambda: FakeLoadedAcorn()
    client = TestClient(app, base_url="https://safebox.example")

    invalid = client.get("/records", params={"view": "grid"})
    empty = client.get(
        "/records",
        params={"view": "folders", "folder": "Unknown"},
    )

    assert invalid.status_code == 400
    assert "requested records view is invalid" in invalid.text
    assert empty.status_code == 200
    assert "No records or folders were found here." in empty.text


def test_record_share_creates_base64url_qr_from_explicit_action() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn()
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    warning = client.get("/record/share", params={"label": "Field Notes"})
    created = client.post(
        "/record/share",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Field Notes",
        },
    )

    assert warning.status_code == 200
    assert "Create Sharing Code" in warning.text
    assert created.status_code == 200
    assert 'aria-label="Record sharing QR code"' in created.text
    assert TEST_TRANSFER_DESCRIPTOR in created.text
    assert "Expires:" in created.text
    assert 'action="/record/share/stop"' in created.text
    assert "Stop Sharing" in created.text
    assert "Keep this page open while sharing" in created.text
    assert 'type="checkbox"' not in warning.text
    assert 'type="checkbox"' not in created.text
    assert 'src="/static/record-share.js"' in created.text
    assert acorn.record_transfer_create_calls == [
        {
            "record_name": "Field Notes",
            "expires_in": 3600,
            "blossom_transfer_server": TEST_SETTINGS.blossom_home_server,
        }
    ]


def test_record_share_page_serves_scoped_navigation_warning() -> None:
    script = make_https_client().get("/static/record-share.js")

    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert 'window.addEventListener("beforeunload"' in script.text
    assert 'stopForm.addEventListener("submit"' in script.text
    assert 'window.removeEventListener("beforeunload"' in script.text


def test_record_presentation_creates_distinct_qr_capability() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn()
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    warning = client.get("/record/present", params={"label": "Field Notes"})
    response = client.post(
        "/record/present",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Field Notes",
        },
    )

    assert warning.status_code == 200
    assert 'type="checkbox"' not in warning.text
    assert response.status_code == 200
    assert 'aria-label="Record presentation QR code"' in response.text
    assert TEST_PRESENTATION_DESCRIPTOR in response.text
    assert "Stop Presenting" in response.text
    assert 'action="/record/present/stop"' in response.text
    assert 'type="checkbox"' not in response.text
    assert acorn.record_presentation_create_calls == [
        {
            "record_name": "Field Notes",
            "expires_in": 3600,
            "blossom_transfer_server": TEST_SETTINGS.blossom_home_server,
        }
    ]


def test_scanned_presentation_displays_record_and_history_without_import(monkeypatch) -> None:
    async def fake_history(digest, relays, **kwargs):
        return {
            "digest": digest,
            "relays": tuple(relays),
            "origin": {
                "id": "01" * 32,
                "author": "npub1issuer",
                "created_at": "2026-08-10 12:00 UTC",
                "content": "Attested original",
            },
            "controls": [],
            "warnings": [],
            "error": None,
        }

    monkeypatch.setattr(main_module, "query_openetr_history", fake_history)
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn()
    app.dependency_overrides[get_payment_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/scan/lightning",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_payment": TEST_PRESENTATION_DESCRIPTOR,
        },
    )

    assert response.status_code == 200
    assert "Presented Credential" in response.text
    assert "verified copy" in response.text
    assert "Control History" in response.text
    assert "Attested original" in response.text
    assert ">Done</button>" in response.text
    assert "Import Record" not in response.text
    assert 'action="/record/import"' not in response.text
    assert acorn.record_transfer_accept_calls == []
    assert acorn.record_presentation_inspect_calls == [
        {
            "descriptor": TEST_PRESENTATION_DESCRIPTOR,
            "allowed_servers": [TEST_SETTINGS.blossom_home_server],
        }
    ]


def test_scanner_opens_real_encrypted_presentation_capability(monkeypatch) -> None:
    import acorn.acorn as acorn_module

    secret = b"p" * 32
    ciphertext, _ = encrypt_record_transfer_envelope(
        RecordTransferEnvelope(
            label="Emergency Credential",
            record_type="generic",
            payload={"status": "presented"},
            blob_data=b"presented image bytes",
            blob_type="image/png",
            capability="presentation",
        ),
        secret=secret,
    )
    digest = __import__("hashlib").sha256(ciphertext).hexdigest()
    descriptor = encode_record_presentation_descriptor(
        RecordTransferDescriptor(
            blob_url=f"https://blossom.getsafebox.app/{digest}",
            ciphertext_sha256=digest,
            secret=secret,
            expires_at=2_000_000_000,
        )
    )

    class RetrievedBlob:
        def get_bytes(self) -> bytes:
            return ciphertext

    class FakeBlossomClient:
        def __init__(self, **kwargs) -> None:
            pass

        def get_blob(self, *, server: str, sha256: str):
            assert server == "https://blossom.getsafebox.app"
            assert sha256 == digest
            return RetrievedBlob()

    monkeypatch.setattr(acorn_module, "BlossomClient", FakeBlossomClient)
    receiver = object.__new__(Acorn)
    app = create_app(TEST_SETTINGS)
    app.dependency_overrides[get_payment_acorn] = lambda: receiver

    response = TestClient(app, base_url="https://safebox.example").post(
        "/scan/lightning",
        data={
            "csrf_token": valid_csrf_token(),
            "lightning_payment": descriptor,
        },
    )

    assert response.status_code == 200
    assert "Emergency Credential" in response.text
    assert "presented" in response.text
    assert "Presented image" in response.text
    assert "Import Record" not in response.text


def test_recipient_done_deletes_presentation_without_importing() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn()
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/record/presentation/done",
        data={
            "csrf_token": valid_csrf_token(),
            "descriptor": TEST_PRESENTATION_DESCRIPTOR,
        },
    )

    assert response.status_code == 200
    assert "temporary presentation was deleted" in response.text
    assert "No record was imported" in response.text
    assert acorn.record_transfer_accept_calls == []
    assert acorn.record_transfer_delete_calls == [
        {
            "descriptor": TEST_PRESENTATION_DESCRIPTOR,
            "allowed_servers": [TEST_SETTINGS.blossom_home_server],
        }
    ]


def test_presenter_can_stop_presentation_without_confirmation_checkbox() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn()
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/record/present/stop",
        data={
            "csrf_token": valid_csrf_token(),
            "descriptor": TEST_PRESENTATION_DESCRIPTOR,
            "label": "Field Notes",
        },
    )

    assert response.status_code == 200
    assert "Presentation of Field Notes has stopped" in response.text
    assert "No record was imported" in response.text
    assert acorn.record_transfer_delete_calls == [
        {
            "descriptor": TEST_PRESENTATION_DESCRIPTOR,
            "allowed_servers": [TEST_SETTINGS.blossom_home_server],
        }
    ]


def test_originator_can_stop_record_sharing_from_explicit_action() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn()
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/record/share/stop",
        data={
            "csrf_token": valid_csrf_token(),
            "descriptor": TEST_TRANSFER_DESCRIPTOR,
            "label": "Field Notes",
        },
    )

    assert response.status_code == 200
    assert "Sharing of Field Notes has stopped" in response.text
    assert "original Acorn record was not" in response.text
    assert acorn.record_transfer_delete_calls == [
        {
            "descriptor": TEST_TRANSFER_DESCRIPTOR,
            "allowed_servers": [TEST_SETTINGS.blossom_home_server],
        }
    ]


def test_stop_record_sharing_requires_valid_csrf_token() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn()
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/record/share/stop",
        data={
            "csrf_token": "invalid",
            "descriptor": TEST_TRANSFER_DESCRIPTOR,
            "label": "Field Notes",
        },
    )

    assert response.status_code == 403
    assert "form token is invalid or expired" in response.text
    assert acorn.record_transfer_delete_calls == []


def test_record_transfer_import_action_stores_then_deletes_transfer() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn()
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/record/import",
        data={
            "csrf_token": valid_csrf_token(),
            "descriptor": TEST_TRANSFER_DESCRIPTOR,
            "label": "Imported Notes",
        },
    )

    assert response.status_code == 200
    assert "Imported Notes was imported and stored" in response.text
    assert "temporary sharing copy was deleted" in response.text
    assert acorn.record_transfer_accept_calls == [
        {
            "descriptor": TEST_TRANSFER_DESCRIPTOR,
            "record_name": "Imported Notes",
            "allowed_servers": [TEST_SETTINGS.blossom_home_server],
            "delete_transfer": True,
        }
    ]


def test_legacy_blob_upload_page_redirects_to_unified_record_form() -> None:
    app = create_app(TEST_SETTINGS)
    app.dependency_overrides[get_record_acorn] = lambda: FakeBlobAcorn()
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/blob/upload", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/record/edit"


def test_blob_upload_passes_plaintext_to_acorn_encryption_boundary() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn()
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/blob/upload",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Private Notes",
            "description": "Encrypted attachment",
            "confirmed": "yes",
        },
        files={"blob": ("notes.txt", b"private blob contents", "text/plain")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/record?label=Private+Notes&saved=1"
    assert acorn.record_put_calls == [
        {
            "record_name": "Private Notes",
            "record_value": {
                "description": "Encrypted attachment",
                "filename": "notes.txt",
                "content_type": "text/plain",
                "size": 21,
            },
            "record_type": "blob",
            "record_kind": 37375,
            "blob_data": b"private blob contents",
            "blob_type": "text/plain",
            "return_result": True,
        }
    ]


def test_blob_upload_follow_redirect_renders_saved_record() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn()
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/blob/upload",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Private Notes",
            "description": "Encrypted attachment",
            "confirmed": "yes",
        },
        files={"blob": ("notes.txt", b"private blob contents", "text/plain")},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert str(response.url).endswith("/record?label=Private+Notes&saved=1")
    assert "Record saved and verified." in response.text
    assert "Safebox stored Original Record type for notes.txt: text/plain." in response.text
    assert '<a class="record-capability" href="/record/blob?label=Private+Notes">Original</a>' in response.text


def test_pkpass_blob_upload_records_wallet_pass_media_type() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn()
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")
    pkpass_data = (
        PKPASS_FIXTURE.read_bytes()
        if PKPASS_FIXTURE.exists()
        else b"fake pkpass bytes"
    )

    response = client.post(
        "/blob/upload",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Example Pass",
            "description": "Wallet pass",
            "confirmed": "yes",
        },
        files={"blob": ("Example.pkpass", pkpass_data, "application/zip")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert acorn.record_put_calls[0]["record_value"]["filename"] == "Example.pkpass"
    assert acorn.record_put_calls[0]["record_value"]["content_type"] == PKPASS_MIME_TYPE
    assert acorn.record_put_calls[0]["blob_data"] == pkpass_data
    assert acorn.record_put_calls[0]["blob_type"] == PKPASS_MIME_TYPE


def test_pkpass_blob_upload_follow_redirect_renders_preview() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn()
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")
    pkpass_data = (
        PKPASS_FIXTURE.read_bytes()
        if PKPASS_FIXTURE.exists()
        else b"fake pkpass bytes"
    )

    response = client.post(
        "/blob/upload",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Example Pass",
            "description": "Wallet pass",
            "confirmed": "yes",
        },
        files={"blob": ("Example.pkpass", pkpass_data, "application/zip")},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert str(response.url).endswith("/record?label=Example+Pass&saved=1")
    assert "Record saved and verified." in response.text
    assert "Open/Add Wallet Pass" in response.text
    assert "Wallet pass preview" in response.text
    assert "Safebox recognized Original Record type for Example.pkpass" in response.text


def test_effective_mime_resolver_identifies_verifiable_credential_json() -> None:
    upload = SimpleNamespace(filename="credential.json", content_type="application/json")
    credential_data = W3C_DEGREE_FIXTURE.read_bytes()

    resolution = main_module._resolve_upload_effective_mime(
        upload,
        credential_data,
    )

    assert resolution.effective_mime == "application/vc"
    assert resolution.source == "inferred"
    assert resolution.confidence == "high"
    assert resolution.requires_confirmation is False
    assert "json includes W3C credentials context" in resolution.evidence


def test_effective_mime_resolver_identifies_verifiable_presentation_json() -> None:
    upload = SimpleNamespace(filename="presentation.json", content_type="application/json")
    presentation = {
        "@context": ["https://www.w3.org/ns/credentials/v2"],
        "type": ["VerifiablePresentation"],
        "verifiableCredential": [],
    }

    resolution = main_module._resolve_upload_effective_mime(
        upload,
        json.dumps(presentation).encode("utf-8"),
    )

    assert resolution.effective_mime == "application/vp"
    assert resolution.confidence == "high"


def test_effective_mime_resolver_identifies_vc_jwt_and_sd_jwt_vc() -> None:
    def segment(value: dict) -> str:
        encoded = base64.urlsafe_b64encode(json.dumps(value).encode("utf-8")).decode(
            "ascii"
        )
        return encoded.rstrip("=")

    vc_jwt = ".".join(
        [
            segment({"alg": "ES256", "typ": "vc+jwt"}),
            segment(
                {
                    "iss": "did:example:issuer",
                    "vc": {"type": ["VerifiableCredential"]},
                }
            ),
            "signature",
        ]
    )
    sd_jwt = ".".join(
        [
            segment({"alg": "ES256", "typ": "vc+sd-jwt"}),
            segment({"iss": "did:example:issuer", "vct": "ExampleCredential"}),
            "signature",
        ]
    ) + "~disclosure"

    jwt_resolution = main_module._resolve_upload_effective_mime(
        SimpleNamespace(filename="credential.jwt", content_type="text/plain"),
        vc_jwt.encode("ascii"),
    )
    sd_jwt_resolution = main_module._resolve_upload_effective_mime(
        SimpleNamespace(filename="credential.txt", content_type="text/plain"),
        sd_jwt.encode("ascii"),
    )

    assert jwt_resolution.effective_mime == "application/vc+jwt"
    assert jwt_resolution.requires_confirmation is True
    assert sd_jwt_resolution.effective_mime == "application/vc+sd-jwt"
    assert sd_jwt_resolution.requires_confirmation is True


def test_effective_mime_resolver_identifies_mdoc_filename() -> None:
    resolution = main_module._resolve_upload_effective_mime(
        SimpleNamespace(filename="example_mdl.mdoc", content_type="application/cbor"),
        MDL_FIXTURE.read_bytes(),
    )

    assert resolution.effective_mime == "application/mdoc+cbor"
    assert resolution.confidence == "medium"
    assert resolution.requires_confirmation is True
    assert "application/cbor" in resolution.alternatives


def test_mdoc_upload_follow_redirect_records_effective_mime() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn()
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")
    mdl_data = MDL_FIXTURE.read_bytes()

    response = client.post(
        "/blob/upload",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Example mDL",
            "description": "Synthetic ISO mdoc fixture",
            "confirmed": "yes",
        },
        files={"blob": ("example_mdl.mdoc", mdl_data, "application/cbor")},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert str(response.url).endswith("/record?label=Example+mDL&saved=1")
    assert "Record saved and verified." in response.text
    assert (
        "Safebox stored Original Record type for example_mdl.mdoc: "
        "application/mdoc+cbor."
    ) in response.text
    assert 'aria-label="mDL preview"' in response.text
    assert "<h3>Mobile Driving Licence</h3>" in response.text
    assert (
        "Preview only. Safebox decoded this mdoc container but has not verified "
        "issuer signatures, device signatures, or digest bindings."
        in response.text
    )
    assert "<dt>Document type</dt>" in response.text
    assert "<code>org.iso.18013.5.1.mDL</code>" in response.text
    assert (
        "<dt>documents[0].issuerSigned.nameSpaces."
        "org.iso.18013.5.1[0].elementIdentifier</dt>"
        in response.text
    )
    assert "<dd>family_name</dd>" in response.text
    assert "<dt>Family name</dt>" in response.text
    assert "<dd>Johnson</dd>" in response.text
    assert "<dt>Given name</dt>" in response.text
    assert "<dd>Alex</dd>" in response.text
    assert "<dt>Over 18</dt>" in response.text
    assert "<dd>true</dd>" in response.text
    assert "<summary>Technical mdoc fields</summary>" in response.text
    assert (
        acorn.record_put_calls[0]["record_value"]["content_type"]
        == "application/mdoc+cbor"
    )
    assert acorn.record_put_calls[0]["blob_type"] == "application/mdoc+cbor"


def test_mdoc_preview_decodes_cbor_fixture() -> None:
    preview = main_module._mdoc_preview(MDL_FIXTURE.read_bytes())

    assert preview is not None
    assert preview["doc_type"] == "org.iso.18013.5.1.mDL"
    assert preview["credential_kind"] == "mdl"
    assert preview["document_count"] == 1
    assert preview["status"] == "0"
    assert {
        "identifier": "family_name",
        "label": "Family name",
        "value": "Johnson",
    } in preview["credential_fields"]
    assert {
        "identifier": "age_over_18",
        "label": "Over 18",
        "value": "true",
    } in preview["credential_fields"]
    assert {
        "key": "documents[0].issuerSigned.nameSpaces.org.iso.18013.5.1[2].elementValue",
        "value": "true",
        "depth": 6,
    } in preview["rows"]


def test_mdoc_preview_decodes_synthetic_eudi_pid_fixture() -> None:
    preview = main_module._mdoc_preview(EUDI_PID_FIXTURE.read_bytes())

    assert preview is not None
    assert preview["doc_type"] == "eu.europa.ec.eudi.pid.1"
    assert preview["credential_kind"] == "eudi_pid"
    assert preview["document_count"] == 1
    assert preview["status"] == "0"
    assert {
        "identifier": "family_name",
        "label": "Family name",
        "value": "MUSTERMANN",
    } in preview["credential_fields"]
    assert {
        "identifier": "place_of_birth",
        "label": "Place of birth",
        "value": "country: DE, locality: BERLIN",
    } in preview["credential_fields"]
    assert {
        "key": (
            "documents[0].issuerSigned.nameSpaces.eu.europa.ec.eudi.pid.1[0]."
            "elementIdentifier"
        ),
        "value": "family_name",
        "depth": 6,
    } in preview["rows"]
    assert any(
        row["key"].endswith("elementValue") and row["value"] == "MUSTERMANN"
        for row in preview["rows"]
    )


def test_eudi_pid_upload_renders_semantic_preview() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn()
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/blob/upload",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Example EUDI PID",
            "description": "Synthetic EUDI PID fixture",
            "confirmed": "yes",
        },
        files={
            "blob": (
                "eudi_pid_synthetic.mdoc",
                EUDI_PID_FIXTURE.read_bytes(),
                "application/cbor",
            )
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert 'aria-label="EUDI PID preview"' in response.text
    assert "<h3>EU Digital Identity PID</h3>" in response.text
    assert "<dt>Family name</dt>" in response.text
    assert "<dd>MUSTERMANN</dd>" in response.text
    assert "<dt>Place of birth</dt>" in response.text
    assert "<dd>country: DE, locality: BERLIN</dd>" in response.text
    assert "<summary>Technical mdoc fields</summary>" in response.text


def test_blob_upload_records_verifiable_credential_effective_mime() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn()
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")
    credential_data = W3C_DEGREE_FIXTURE.read_bytes()

    response = client.post(
        "/blob/upload",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Example Credential",
            "description": "VC",
            "confirmed": "yes",
        },
        files={"blob": ("credential.json", credential_data, "application/json")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert acorn.record_put_calls[0]["record_value"]["content_type"] == "application/vc"
    assert acorn.record_put_calls[0]["blob_type"] == "application/vc"


def test_verifiable_credential_upload_follow_redirect_renders_record() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn()
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")
    credential_data = W3C_DEGREE_FIXTURE.read_bytes()

    response = client.post(
        "/blob/upload",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "W3C Degree",
            "description": "W3C example credential",
            "confirmed": "yes",
        },
        files={"blob": ("w3c_degree.json", credential_data, "application/json")},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert str(response.url).endswith("/record?label=W3C+Degree&saved=1")
    assert "Record saved and verified." in response.text
    assert "Safebox stored Original Record type for w3c_degree.json: application/vc." in response.text
    assert 'aria-label="Credential preview"' in response.text
    assert "<h3>Example Degree Credential</h3>" in response.text
    assert (
        "Preview only. Safebox decoded this credential but has not verified its "
        "issuer proof, credential status, holder binding, or presentation."
        in response.text
    )
    assert "<dt>Issuer</dt>" in response.text
    assert "<dd>https://university.example/issuers/565049</dd>" in response.text
    assert "<dt>Subject</dt>" in response.text
    assert "<dd>did:example:ebfeb1f712ebc6f1c276e12ec21</dd>" in response.text
    assert "<dt>Degree type</dt>" in response.text
    assert "<dd>ExampleBachelorDegree</dd>" in response.text
    assert "<dt>Degree name</dt>" in response.text
    assert "<dd>Bachelor of Science and Arts</dd>" in response.text
    assert "<summary>Technical credential fields</summary>" in response.text
    assert "<dt>type[0]</dt>" in response.text
    assert "<dd>VerifiableCredential</dd>" in response.text
    assert "<dt>credentialSubject.degree.name</dt>" in response.text
    assert "<dd>Bachelor of Science and Arts</dd>" in response.text


def test_record_detail_shows_determined_original_record_type() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(
        existing_labels={"Example Credential"},
        blob_type="application/vc",
        payload={
            "filename": "credential.json",
            "content_type": "application/vc",
            "size": 123,
        },
    )
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get(
        "/record",
        params={"label": "Example Credential", "saved": "1"},
    )

    assert response.status_code == 200
    assert "Record saved and verified." in response.text
    assert (
        "Safebox stored Original Record type for credential.json: application/vc."
        in response.text
    )


def test_verifiable_credential_blob_download_uses_json_extension() -> None:
    credential_data = W3C_DEGREE_FIXTURE.read_bytes()
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(
        existing_labels={"W3C Degree"},
        downloaded_type="application/vc",
        downloaded_data=credential_data,
        blob_type="application/vc",
        payload={
            "filename": "w3c_degree.json",
            "content_type": "application/vc",
            "size": len(credential_data),
        },
    )
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/record/blob", params={"label": "W3C Degree"})

    assert response.status_code == 200
    assert response.content == credential_data
    assert response.headers["content-type"].startswith("application/vc")
    assert 'filename="W3C_Degree.json"' in response.headers["content-disposition"]
    assert "filename*=UTF-8''W3C%20Degree.json" in response.headers[
        "content-disposition"
    ]


def test_mdoc_blob_download_uses_mdoc_extension() -> None:
    mdl_data = MDL_FIXTURE.read_bytes()
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(
        existing_labels={"Example mDL"},
        downloaded_type="application/mdoc+cbor",
        downloaded_data=mdl_data,
        blob_type="application/mdoc+cbor",
        payload={
            "filename": "example_mdl.mdoc",
            "content_type": "application/mdoc+cbor",
            "size": len(mdl_data),
        },
    )
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/record/blob", params={"label": "Example mDL"})

    assert response.status_code == 200
    assert response.content == mdl_data
    assert response.headers["content-type"].startswith("application/mdoc+cbor")
    assert 'filename="Example_mDL.mdoc"' in response.headers["content-disposition"]
    assert "filename*=UTF-8''Example%20mDL.mdoc" in response.headers[
        "content-disposition"
    ]


def test_blob_upload_rejects_existing_label_without_orphaning_blob() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(existing_labels={"Private Notes"})
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/blob/upload",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Private Notes",
            "confirmed": "yes",
        },
        files={"blob": ("notes.txt", b"do not upload", "text/plain")},
    )

    assert response.status_code == 409
    assert "already exists" in response.text
    assert acorn.record_put_calls == []


def test_blob_upload_fails_closed_when_existing_record_cannot_be_read() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(
        record_lookup_error=ValueError("Could not decrypt private record")
    )
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/blob/upload",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Uncertain Label",
            "confirmed": "yes",
        },
        files={"blob": ("notes.txt", b"do not upload", "text/plain")},
    )

    assert response.status_code == 502
    assert "could not confirm" in response.text
    assert acorn.record_put_calls == []


def test_blob_upload_enforces_configured_size_limit() -> None:
    settings = replace(TEST_SETTINGS, max_blob_bytes=4)
    app = create_app(settings)
    acorn = FakeBlobAcorn()
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/blob/upload",
        data={
            "csrf_token": CsrfProtector(settings).issue(),
            "label": "Too Large",
            "confirmed": "yes",
        },
        files={"blob": ("large.bin", b"12345", "application/octet-stream")},
    )

    assert response.status_code == 413
    assert "exceeds the 4-byte upload limit" in response.text
    assert acorn.record_put_calls == []


def test_blob_record_download_returns_decrypted_attachment() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(existing_labels={"Private Notes"})
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    detail = client.get("/record", params={"label": "Private Notes"})
    response = client.get("/record/blob", params={"label": "Private Notes"})

    assert detail.status_code == 200
    assert 'class="record-fingerprint"' in detail.text
    assert "Fingerprint:</span> <code>1EA23F2B</code>" in detail.text
    assert '<nav class="record-capabilities" aria-label="Record actions">' in detail.text
    assert 'href="/record/blob?label=Private+Notes">Original</a>' in detail.text
    assert 'href="/record/share?label=Private+Notes">Share</a>' in detail.text
    assert 'href="/record/present?label=Private+Notes">Present</a>' in detail.text
    assert ">Issue</button>" in detail.text
    assert ">Verify</button>" in detail.text
    assert "/record/blob?label=Private+Notes" in detail.text
    assert response.status_code == 200
    assert response.content == b"private blob contents"
    assert response.headers["content-type"].startswith("text/plain")
    assert "attachment" in response.headers["content-disposition"]
    assert acorn.blob_reads == ["Private Notes"]


def test_pkpass_blob_download_uses_wallet_pass_headers_from_metadata() -> None:
    pkpass_data = (
        PKPASS_FIXTURE.read_bytes()
        if PKPASS_FIXTURE.exists()
        else b"fake pkpass bytes"
    )
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(
        existing_labels={"Example Pass"},
        downloaded_type="application/zip",
        downloaded_data=pkpass_data,
        blob_type="application/zip",
        payload={
            "filename": "Example.pkpass",
            "description": "Wallet pass",
            "content_type": PKPASS_MIME_TYPE,
            "size": len(pkpass_data),
        },
    )
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    detail = client.get("/record", params={"label": "Example Pass"})
    response = client.get("/record/blob", params={"label": "Example Pass"})
    attempted_inline = client.get(
        "/record/blob", params={"label": "Example Pass", "inline": "1"}
    )

    assert detail.status_code == 200
    assert "Open/Add Wallet Pass" in detail.text
    assert "Wallet pass preview" in detail.text
    assert "Tim Oliver" in detail.text
    assert "Hanzo Main" in detail.text
    assert "downloads.timoliver.com.au" in detail.text
    assert (
        "Safebox recognized Original Record type for Example.pkpass: application/vnd.apple.pkpass."
        in detail.text
    )
    assert 'src="data:image/png;base64,' in detail.text
    assert "&#34;data:image" not in detail.text
    assert "img-src 'self' data:" in detail.headers["content-security-policy"]
    assert response.status_code == 200
    assert response.content == pkpass_data
    assert response.headers["content-type"].startswith(PKPASS_MIME_TYPE)
    assert 'filename="Example_Pass.pkpass"' in response.headers["content-disposition"]
    assert "attachment" in attempted_inline.headers["content-disposition"]
    assert acorn.blob_reads == ["Example Pass", "Example Pass", "Example Pass"]


def test_pkpass_preview_renders_aztec_boarding_pass_barcode() -> None:
    pass_json = {
        "description": "Porter Airlines",
        "organizationName": "Porter Airlines",
        "serialNumber": "OB6Z6A-0-20260323_PD_179_YYZYOW",
        "barcodes": [
            {
                "format": "PKBarcodeFormatAztec",
                "message": (
                    "M1BOUMA/TIMOTHY       EOB6Z6A YYZYOWPD 0179 082Y028B0025 "
                    "148>5180Ms6081BPD 00000000000002A4516025526441 0"
                ),
                "messageEncoding": "ISO-8859-1",
                "altText": "Conf. OB6Z6A    025",
            }
        ],
        "boardingPass": {
            "headerFields": [
                {"key": "flightnumber", "label": "FLIGHT", "value": "PD179"}
            ],
            "primaryFields": [
                {"key": "origin", "label": "Toronto-Pearson, ON", "value": "YYZ"},
                {"key": "destination", "label": "Ottawa, ON", "value": "YOW"},
            ],
        },
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("pass.json", json.dumps(pass_json))
    pkpass_data = buffer.getvalue()
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(
        existing_labels={"Boarding Pass"},
        downloaded_type=PKPASS_MIME_TYPE,
        downloaded_data=pkpass_data,
        blob_type=PKPASS_MIME_TYPE,
        payload={
            "filename": "boarding.pkpass",
            "content_type": PKPASS_MIME_TYPE,
            "size": len(pkpass_data),
        },
    )
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    detail = client.get("/record", params={"label": "Boarding Pass"})

    assert detail.status_code == 200
    assert "Porter Airlines" in detail.text
    assert "PD179" in detail.text
    assert "Conf. OB6Z6A    025" in detail.text
    assert '<div class="pkpass-barcode-image" aria-label="Aztec barcode">' in detail.text
    assert '<svg class="pkpass-barcode-symbol"' in detail.text
    assert "M1BOUMA/TIMOTHY" not in detail.text


def test_record_offers_control_history_button_without_querying(monkeypatch) -> None:
    queried = False

    async def fake_query(*args, **kwargs):
        nonlocal queried
        queried = True
        return {}

    monkeypatch.setattr(main_module, "query_openetr_history", fake_query)
    app = create_app(TEST_SETTINGS)
    app.dependency_overrides[get_record_acorn] = lambda: FakeBlobAcorn(
        existing_labels={"Receipt"}
    )
    response = TestClient(app, base_url="https://safebox.example").get(
        "/record", params={"label": "Receipt"}
    )

    assert response.status_code == 200
    assert '<details class="openetr-history"' not in response.text
    assert '>Control History</a>' in response.text
    assert "openetr=1" in response.text.replace("&amp;", "&")
    assert queried is False


def test_record_renders_openetr_origin_and_control_events(monkeypatch) -> None:
    digest = "1ea23f2b" + "0" * 56
    query_args = {}

    async def fake_query(the_digest, relays, **kwargs):
        query_args.update(digest=the_digest, relays=relays, kwargs=kwargs)
        return {
            "digest": the_digest,
            "relays": tuple(relays),
            "origin": {
                "id": "01" * 32,
                "author": "npub1issuer",
                "created_at": "2026-08-10 12:00 UTC",
                "content": "Issued warehouse receipt",
                "kind": 1415,
            },
            "controls": [
                {
                    "id": "02" * 32,
                    "author": "npub1controller",
                    "created_at": "2026-08-10 13:00 UTC",
                    "action_label": "Transfer initiated",
                    "prior_event_id": "01" * 32,
                    "participant": "npub1recipient",
                    "content": "Transfer to recipient",
                    "kind": 1416,
                }
            ],
            "warnings": [],
            "error": None,
        }

    monkeypatch.setattr(main_module, "query_openetr_history", fake_query)
    settings = replace(
        TEST_SETTINGS,
        openetr_relays=("wss://relay.openetr.example",),
        openetr_query_timeout_seconds=2,
        openetr_query_limit=25,
    )
    app = create_app(settings)
    app.dependency_overrides[get_record_acorn] = lambda: FakeBlobAcorn(
        existing_labels={"Receipt"}, orig_sha256=digest
    )
    response = TestClient(app, base_url="https://safebox.example").get(
        "/record", params={"label": "Receipt", "openetr": "1"}
    )

    assert response.status_code == 200
    assert '<section class="openetr-history-content"' in response.text
    assert '<details class="openetr-history"' not in response.text
    assert 'href="/record?label=Receipt">Back to Record</a>' in response.text
    assert "Origin Event" in response.text
    assert digest in response.text
    assert "Issued warehouse receipt" in response.text
    assert "Transfer initiated" in response.text
    assert "npub1recipient" in response.text
    assert query_args == {
        "digest": digest,
        "relays": ("wss://relay.openetr.example",),
        "kwargs": {"timeout": 2, "limit": 25},
    }


def test_image_blob_uses_native_authenticated_inline_preview() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(
        existing_labels={"Photo"},
        downloaded_type="image/png",
        downloaded_data=b"png bytes",
        blob_type="image/png",
    )
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    detail = client.get("/record", params={"label": "Photo"})
    preview = client.get(
        "/record/blob", params={"label": "Photo", "inline": "1"}
    )

    assert detail.status_code == 200
    assert '<img src="/record/blob?label=Photo&amp;inline=1"' in detail.text
    assert "fetch(" not in detail.text
    assert "URL.createObjectURL" not in detail.text
    assert preview.status_code == 200
    assert preview.headers["content-disposition"].startswith("inline;")
    assert preview.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in preview.headers["content-security-policy"]


def test_pdf_blob_uses_pdfjs_progressive_viewer_with_download_fallback() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(
        existing_labels={"Report"},
        downloaded_type="application/pdf",
        downloaded_data=b"%PDF test",
        blob_type="application/pdf",
    )
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/record", params={"label": "Report"})

    assert response.status_code == 200
    assert 'class="blob-preview blob-preview-pdf" data-pdf-viewer' in response.text
    assert 'src="/static/pdf-viewer.js"' in response.text
    assert "data-pdf-previous" in response.text
    assert "data-pdf-next" in response.text
    assert 'href="/record/blob?label=Report&amp;inline=1"' in response.text
    assert "Open PDF full screen" in response.text
    assert "JavaScript is required for the inline PDF preview" in response.text
    assert 'href="/record/blob?label=Report">Original</a>' in response.text


def test_blob_fingerprint_is_hidden_when_plaintext_digest_is_invalid() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(
        existing_labels={"Legacy blob"},
        orig_sha256="not-a-sha256",
    )
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/record", params={"label": "Legacy blob"})

    assert response.status_code == 200
    assert "Blob fingerprint:" not in response.text


def test_blob_record_delete_form_requires_explicit_confirmation() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(existing_labels={"Report"})
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    detail = client.get("/record", params={"label": "Report"})
    response = client.post(
        "/record/delete",
        data={"csrf_token": valid_csrf_token(), "label": "Report"},
    )

    assert 'action="/record/delete"' in detail.text
    assert "Delete this record and Original Record" in detail.text
    assert response.status_code == 400
    assert "Explicit deletion confirmation is required" in response.text
    assert acorn.record_delete_calls == []


def test_blob_record_delete_removes_record_and_requests_blob_cleanup() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(existing_labels={"Report"})
    acorn.record_delete_result = {
        "status": "DELETE_REQUESTED",
        "hidden_on": [acorn.home_relay],
        "blob_cleanup": {
            "requested": True,
            "deleted": True,
            "sha256": "abc123",
        },
        "index_error": None,
    }
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/record/delete",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Report",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 200
    assert "Deletion was requested for Report" in response.text
    assert "accepted the Original Record deletion request" in response.text
    assert acorn.record_delete_calls == [
        {
            "label": "Report",
            "record_kind": 37375,
            "delete_blob": True,
        }
    ]


def test_blob_record_delete_reports_partial_blob_cleanup() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(existing_labels={"Report"})
    acorn.record_delete_result = {
        "status": "DELETE_REQUESTED",
        "hidden_on": [],
        "blob_cleanup": {"requested": True, "deleted": False},
        "index_error": None,
    }
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/record/delete",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Report",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 200
    assert "could not confirm deletion of the Original Record" in response.text
    assert "could not confirm that the original record is already hidden" in response.text


def test_unsafe_blob_type_cannot_be_forced_inline() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeBlobAcorn(
        existing_labels={"Markup"},
        downloaded_type="text/html",
        downloaded_data=b"<script>alert(1)</script>",
        blob_type="text/html",
    )
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    detail = client.get("/record", params={"label": "Markup"})
    attempted_preview = client.get(
        "/record/blob", params={"label": "Markup", "inline": "1"}
    )

    assert "No inline preview is available" in detail.text
    assert "<object" not in detail.text
    assert attempted_preview.headers["content-disposition"].startswith("attachment;")
    assert attempted_preview.headers["x-frame-options"] == "DENY"


def test_record_detail_renders_escaped_payload() -> None:
    app = create_app(TEST_SETTINGS)
    app.dependency_overrides[get_record_acorn] = lambda: FakeLoadedAcorn()
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/record", params={"label": "Field Notes"})

    assert response.status_code == 200
    assert "Field Notes" in response.text
    assert "&lt;script&gt;" in response.text
    assert "<script>" not in response.text
    assert "/record/edit?label=Field+Notes" in response.text


def test_record_edit_form_loads_and_escapes_existing_payload() -> None:
    app = create_app(TEST_SETTINGS)
    app.dependency_overrides[get_record_acorn] = lambda: FakeLoadedAcorn()
    client = TestClient(app, base_url="https://safebox.example")

    response = client.get("/record/edit", params={"label": "Field Notes"})

    assert response.status_code == 200
    assert "Update Record" in response.text
    assert 'value="Field Notes"' in response.text
    assert 'value="Field Notes" readonly' in response.text
    assert '<option value="json" selected>JSON</option>' in response.text
    assert "&lt;script&gt;alert" in response.text
    assert "<script>alert" not in response.text
    assert 'enctype="multipart/form-data"' in response.text
    assert 'name="attachment" type="file"' in response.text
    assert "Original Record (optional)" in response.text
    assert "Select a file to attach it to this record" in response.text


def test_record_save_encrypts_publishes_and_verifies() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn()
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/record/save",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Field Notes",
            "payload": "A private update",
            "confirmed": "yes",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/record?label=Field+Notes&saved=1"
    assert acorn.record_put_calls == [
        {
            "record_name": "Field Notes",
            "record_value": "A private update",
            "record_type": "generic",
            "record_kind": 37375,
            "blob_data": None,
            "blob_type": None,
            "preserve_existing_blob": True,
            "return_result": True,
        }
    ]


def test_record_save_can_include_an_encrypted_file_attachment() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn()
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/record/save",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Travel Documents",
            "payload": "Boarding pass",
            "confirmed": "yes",
        },
        files={"attachment": ("pass.pdf", b"private pdf", "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert acorn.record_put_calls == [
        {
            "record_name": "Travel Documents",
            "record_value": "Boarding pass",
            "record_type": "generic",
            "record_kind": 37375,
            "blob_data": b"private pdf",
            "blob_type": "application/pdf",
            "preserve_existing_blob": True,
            "return_result": True,
        }
    ]


def test_record_save_allows_a_file_only_private_record() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn()
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/record/save",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Passport",
            "payload": "",
            "confirmed": "yes",
        },
        files={"attachment": ("passport.pdf", b"private pdf", "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert acorn.record_put_calls[0]["record_value"] == ""
    assert acorn.record_put_calls[0]["blob_data"] == b"private pdf"


def test_record_save_requires_confirmation_without_mutating() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn()
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/record/save",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Field Notes",
            "payload": "Do not store",
        },
    )

    assert response.status_code == 400
    assert "Explicit confirmation is required" in response.text
    assert acorn.record_put_calls == []


def test_record_save_preserves_structured_json_payload() -> None:
    app = create_app(TEST_SETTINGS)
    acorn = FakeLoadedAcorn()
    app.dependency_overrides[get_record_acorn] = lambda: acorn
    client = TestClient(app, base_url="https://safebox.example")

    response = client.post(
        "/record/save",
        data={
            "csrf_token": valid_csrf_token(),
            "label": "Preferences",
            "payload": '{"theme":"green","alerts":true}',
            "payload_format": "json",
            "confirmed": "yes",
        },
    )

    assert response.status_code == 200
    assert acorn.record_put_calls[0]["record_value"] == {
        "theme": "green",
        "alerts": True,
    }


def test_loopback_connect_accepts_matching_browser_origin() -> None:
    client = TestClient(
        create_app(TEST_SETTINGS),
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50123),
    )

    response = client.post(
        "/connect",
        headers={"Origin": "http://127.0.0.1:8000/"},
        data={
            "csrf_token": valid_csrf_token(),
            "secret_type": "nsec",
            "secret": TEST_NSEC,
            "bootstrap_relay": "relay.example.com",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_loopback_connect_accepts_null_origin_with_valid_form_token() -> None:
    client = TestClient(
        create_app(TEST_SETTINGS),
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50123),
    )

    response = client.post(
        "/connect",
        headers={"Origin": "null"},
        data={
            "csrf_token": valid_csrf_token(),
            "secret_type": "nsec",
            "secret": TEST_NSEC,
            "bootstrap_relay": "relay.example.com",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_null_origin_without_valid_form_token_is_rejected() -> None:
    client = TestClient(
        create_app(TEST_SETTINGS),
        base_url="http://127.0.0.1:8000",
        client=("127.0.0.1", 50123),
    )

    response = client.post(
        "/connect",
        headers={"Origin": "null"},
        data={
            "csrf_token": "invalid",
            "secret_type": "nsec",
            "secret": TEST_NSEC,
            "bootstrap_relay": "relay.example.com",
        },
    )

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")
    assert "form token is invalid" in response.text.lower()


def test_connect_rejects_cross_origin_submission() -> None:
    client = make_https_client()

    response = client.post(
        "/connect",
        headers={"Origin": "https://attacker.example"},
        data={
            "csrf_token": valid_csrf_token(),
            "secret_type": "nsec",
            "secret": TEST_NSEC,
            "bootstrap_relay": "relay.example.com",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Origin rejected"


def test_nsec_connection_uses_encrypted_secure_cookie_and_dependency() -> None:
    client = make_https_client()

    response = client.post(
        "/connect",
        data={
            "csrf_token": valid_csrf_token(),
            "secret_type": "nsec",
            "secret": TEST_NSEC,
            "bootstrap_relay": "relay.example.com",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    set_cookie = response.headers["set-cookie"]
    assert f"{SECURE_COOKIE_NAME}=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert f"Max-Age={TEST_SETTINGS.session_ttl_seconds}" in set_cookie
    assert "expires=" in set_cookie.lower()

    token = client.cookies.get(SECURE_COOKIE_NAME)
    assert token is not None
    assert token.startswith("v2.")
    assert TEST_NSEC not in token
    assert "relay.example.com" not in token

    credentials = SessionCipher(TEST_SETTINGS).decode(token)
    assert credentials.nsec == TEST_NSEC
    assert credentials.bootstrap_relay == "wss://relay.example.com"

    session_response = client.get("/api/session")
    assert session_response.status_code == 200
    payload = session_response.json()
    assert payload["authenticated"] is True
    assert payload["bootstrap_relay"] == "wss://relay.example.com"
    assert payload["npub"].startswith(TEST_NPUB)
    assert "nsec" not in payload
    assert "deferred_acorn_mnemonic" not in payload
    assert "record_protection_key" not in payload


def test_session_cipher_uses_randomized_aes_256_gcm_tokens() -> None:
    cipher = SessionCipher(TEST_SETTINGS)
    credentials = SessionCredentials(
        nsec=TEST_NSEC,
        bootstrap_relay="wss://relay.example.com",
        record_protection_key=TEST_RPK,
    )

    first = cipher.encode(credentials)
    second = cipher.encode(credentials)

    assert first.startswith("v2.")
    assert second.startswith("v2.")
    assert first != second
    assert cipher.decode(first) == credentials
    assert cipher.decode(second) == credentials


def test_session_cipher_protects_and_validates_deferred_acorn_mnemonic() -> None:
    cipher = SessionCipher(TEST_SETTINGS)
    credentials = SessionCredentials(
        nsec=TEST_NSEC,
        bootstrap_relay="wss://relay.example.com",
        deferred_acorn_mnemonic=TEST_MNEMONIC,
    )

    token = cipher.encode(credentials)

    assert TEST_MNEMONIC not in token
    assert cipher.decode(token) == credentials


def test_session_cipher_rejects_deferred_mnemonic_for_another_key() -> None:
    other_mnemonic, _other_nsec = main_module.generate_seed_phrase_and_nsec()

    with pytest.raises(ValueError, match="does not match the Acorn key"):
        SessionCipher(TEST_SETTINGS).encode(
            SessionCredentials(
                nsec=TEST_NSEC,
                bootstrap_relay="wss://relay.example.com",
                deferred_acorn_mnemonic=other_mnemonic,
            )
        )


def test_aes_256_gcm_session_rejects_tampering() -> None:
    cipher = SessionCipher(TEST_SETTINGS)
    token = cipher.encode(
        SessionCredentials(
            nsec=TEST_NSEC,
            bootstrap_relay="wss://relay.example.com",
        )
    )
    replacement = "A" if token[-2] != "A" else "B"
    tampered = token[:-2] + replacement + token[-1]

    with pytest.raises(ValueError, match="invalid or expired"):
        cipher.decode(tampered)


def test_aes_256_gcm_session_rejects_invalid_record_protection_key() -> None:
    cipher = SessionCipher(TEST_SETTINGS)
    with pytest.raises(ValueError, match="record protection key is invalid"):
        cipher.encode(
            SessionCredentials(
                nsec=TEST_NSEC,
                bootstrap_relay="wss://relay.example.com",
                record_protection_key="not-a-key",
            )
        )


def test_session_rejects_confirmed_backup_without_record_protection_key() -> None:
    cipher = SessionCipher(TEST_SETTINGS)

    with pytest.raises(ValueError, match="cannot confirm a missing"):
        cipher.encode(
            SessionCredentials(
                nsec=TEST_NSEC,
                bootstrap_relay="wss://relay.example.com",
                record_protection_backup_confirmed=True,
            )
        )


def test_aes_256_gcm_session_enforces_absolute_expiry(monkeypatch) -> None:
    cipher = SessionCipher(TEST_SETTINGS)
    monkeypatch.setattr(security_module, "time", lambda: 1_000_000)
    token = cipher.encode(
        SessionCredentials(
            nsec=TEST_NSEC,
            bootstrap_relay="wss://relay.example.com",
        )
    )

    monkeypatch.setattr(
        security_module,
        "time",
        lambda: 1_000_000 + TEST_SETTINGS.session_ttl_seconds + 1,
    )

    with pytest.raises(ValueError, match="invalid or expired"):
        cipher.decode(token)


def test_session_cipher_accepts_unexpired_legacy_fernet_cookie() -> None:
    credentials = SessionCredentials(
        nsec=TEST_NSEC,
        bootstrap_relay="wss://relay.example.com",
    )
    legacy_payload = json.dumps(
        {
            "bootstrap_relay": credentials.bootstrap_relay,
            "nsec": credentials.nsec,
            "version": credentials.version,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    legacy_token = Fernet(TEST_KEY.encode("ascii")).encrypt(legacy_payload).decode("ascii")

    assert SessionCipher(TEST_SETTINGS).decode(legacy_token) == credentials


def test_offline_mnemonic_connection_derives_nsec_without_storing_phrase() -> None:
    client = make_https_client()

    response = client.post(
        "/connect",
        data={
            "csrf_token": valid_csrf_token(),
            "secret_type": "mnemonic",
            "secret": TEST_MNEMONIC,
            "bootstrap_relay": "wss://relay.example.com",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    token = client.cookies.get(SECURE_COOKIE_NAME)
    assert token is not None
    assert "abandon" not in token
    credentials = SessionCipher(TEST_SETTINGS).decode(token)
    assert credentials.nsec == TEST_NSEC
    assert not hasattr(credentials, "mnemonic")


def test_invalid_secret_is_rejected_without_echoing_it() -> None:
    client = make_https_client()
    bad_secret = "not-a-real-private-key"

    response = client.post(
        "/connect",
        data={
            "csrf_token": valid_csrf_token(),
            "secret_type": "nsec",
            "secret": bad_secret,
            "bootstrap_relay": "relay.example.com",
        },
    )

    assert response.status_code == 400
    assert bad_secret not in response.text
    assert SECURE_COOKIE_NAME not in response.headers.get("set-cookie", "")


def test_tampered_cookie_is_rejected() -> None:
    client = make_https_client()
    client.cookies.set(
        SECURE_COOKIE_NAME,
        "not-a-valid-session",
        domain="safebox.example",
        path="/",
    )

    response = client.get("/api/session")

    assert response.status_code == 401
    assert response.json()["detail"] == "Acorn connection is invalid or expired"


def test_unlisted_ws_relay_is_rejected() -> None:
    client = make_https_client()

    response = client.post(
        "/connect",
        data={
            "csrf_token": valid_csrf_token(),
            "secret_type": "nsec",
            "secret": TEST_NSEC,
            "bootstrap_relay": "ws://relay.example.com:8735",
        },
    )

    assert response.status_code == 400
    assert "SAFEBOX_ALLOWED_WS_RELAYS" in response.text
