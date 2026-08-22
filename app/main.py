"""Minimal stateless Safebox web shell."""

from __future__ import annotations

import asyncio
import base64
import io
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from html import escape
import inspect
import json
import logging
import mimetypes
from pathlib import Path
import re
from time import monotonic
from urllib.parse import quote, urlencode, urlsplit
import zipfile

from aztec_code_generator import AztecCode
import bolt11
import cbor2
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
import httpx
from monstr.encrypt import Keys
import qrcode
import qrcode.image.svg
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from acorn import (
    Acorn,
    RECORD_PRESENTATION_PREFIX,
    RECORD_TRANSFER_PREFIX,
    RecordTransferError,
    decode_record_presentation_descriptor,
    decode_record_transfer_descriptor,
    generate_record_protection_key,
    record_protection_key_from_entropy,
    record_protection_key_from_recovery_phrase,
    record_protection_recovery_phrase,
)
from acorn.func_utils import (
    generate_seed_phrase_and_nsec,
    npub_to_hex,
    seed_phrase_and_nsec_from_entropy,
)

from app.config import Settings
from acorn import (
    BitcoinCapabilityError,
    broadcast_silent_payment_sweep,
    create_silent_payment_sweep_preview,
    derive_nostr_silent_payment_address,
    detect_silent_payment_receipts,
)
from app.database import create_database_engine, run_migrations
from app.currency_rates import currency_balance_estimate
from app.dependencies import (
    AcornDependency,
    CredentialsDependency,
    DatabaseSessionDependency,
    DepositAcornDependency,
    LoadedAcornDependency,
    PaymentAcornDependency,
    ReceiveAcornDependency,
    RecordAcornDependency,
)
from app.models import ClaimedHandle, CurrencyRate
from app.funds_finalization import (
    claim_finalization_job,
    get_finalization_job,
    run_finalization_job,
)
from app.clear_acceptance import (
    claim_clear_acceptance_job,
    get_clear_acceptance_job,
    run_clear_acceptance_job,
)
from app.worker_liveness import (
    new_worker_id,
    start_worker_heartbeat,
    stop_worker_heartbeat,
)
from app.handles import default_handle_from_pubkey
from app.openetr import query_openetr_history
from app.lnurl_pay import (
    encode_lnurl,
    lightning_address_from_lnurl,
    router as lnurl_pay_router,
)
from app.security import (
    LOOPBACK_COOKIE_NAME,
    SECURE_COOKIE_NAME,
    CsrfProtector,
    DepositQuoteCipher,
    DepositQuoteState,
    InvoicePaymentCipher,
    InvoicePaymentState,
    SessionCipher,
    cookie_name_for_request,
    credentials_from_connection,
    is_allowed_transport,
    is_loopback_http_request,
    is_same_origin,
    normalize_bootstrap_relay,
    normalize_home_mint,
    set_session_cookie,
)


from app.templating import render_template


logger = logging.getLogger("safebox_web.security")
BITCOIN_TXID_PATTERN = re.compile(r"[0-9a-fA-F]{64}")
RECORDS_PAGE_SIZE = 10


def _humanize_retention(seconds: int) -> str:
    """Present a configured retention period in an intuitive exact unit."""

    units = (
        (30 * 24 * 60 * 60, "month"),
        (7 * 24 * 60 * 60, "week"),
        (24 * 60 * 60, "day"),
        (60 * 60, "hour"),
    )
    for unit_seconds, unit_name in units:
        if seconds >= unit_seconds and seconds % unit_seconds == 0:
            quantity = seconds // unit_seconds
            suffix = "" if quantity == 1 else "s"
            return f"{quantity} {unit_name}{suffix}"

    hours = seconds / (60 * 60)
    quantity = f"{hours:.2f}".rstrip("0").rstrip(".")
    return f"{quantity} hours"


def _ecash_retention_notice(settings: Settings) -> str:
    """Explain the provider gift-wrap retention policy to wallet users."""

    if not settings.service_acorn_enabled:
        return ""
    retention = settings.service_acorn_gift_wrap_retention_seconds
    if retention is None:
        message = (
            "Safebox does not request automatic expiration for private funds "
            "delivery messages. Relays may retain them according to their own policy."
        )
    else:
        duration = _humanize_retention(retention)
        message = (
            "Safebox asks compatible relays to retain private funds delivery "
            f"messages for {duration} after publication, then expire and delete "
            "them. Receive incoming funds before this period ends. Relay "
            "enforcement and physical deletion can vary."
        )
    return (
        '<aside class="retention-notice" aria-labelledby="retention-heading">'
        '<h3 id="retention-heading">Funds transfer message retention</h3>'
        f"<p>{escape(message)}</p>"
        "</aside>"
    )


def _acorn_safekeeping_message(
    *,
    acorn_mnemonic: str,
    npub: str,
    home_relay: str,
    home_mint: str,
) -> str:
    """Build the recovery message for an Acorn without record protection."""

    return "\n".join(
        (
            "SAFEBOX ACORN RECOVERY MESSAGE",
            "Keep this message private and offline.",
            "",
            "Safebox Acorn mnemonic:",
            acorn_mnemonic,
            "",
            f"Bootstrap relay: {home_relay}",
            f"Home mint: {home_mint}",
            f"Component public key: {npub}",
            "",
            "Protected records: not enabled",
        )
    )


def _page(title: str, body: str) -> str:
    """Render a generic result or error page through the shared Jinja layout."""

    return render_template("page.html", title=title, body=body)


def _connect_form(
    default_relay: str,
    csrf_token: str,
    error: str | None = None,
    *,
    onboard_path: str = "/onboard/INVITEME",
    show_page_navigation: bool | None = None,
) -> str:
    context = {
        "title": "Connect an Acorn",
        "default_relay": default_relay,
        "csrf_token": csrf_token,
        "onboard_path": onboard_path,
        "error": error,
    }
    if show_page_navigation is not None:
        context["show_page_navigation"] = show_page_navigation
    return render_template(
        "connect.html",
        **context,
    )


def _create_form(
    default_relay: str,
    default_mint: str,
    csrf_token: str,
    error: str | None = None,
    mnemonic_words: str = "24",
    use_external_entropy: bool = False,
    defer_recovery: bool = False,
) -> str:
    return render_template(
        "create.html",
        title="Create a new Acorn",
        default_relay=default_relay,
        default_mint=default_mint,
        csrf_token=csrf_token,
        error=error,
        mnemonic_words=mnemonic_words,
        use_external_entropy=use_external_entropy,
        defer_recovery=defer_recovery,
    )


def _payment_form(
    balance: int,
    csrf_token: str,
    error: str | None = None,
    balance_status: str | None = None,
    lightning_address: str = "",
    amount: str = "",
    comment: str = "Transferred from Safebox Web",
    payment_mode: str = "confirmed",
    payment_asset: str = "cash",
    clear_balances: list[dict] | None = None,
) -> str:
    if balance_status is None:
        balance_status = (
            f"<p>Relay-visible proof total: <strong>{int(balance):,} sats</strong></p>"
        )
    return render_template(
        "pay.html",
        title="Transfer a Balance",
        balance_status=balance_status,
        csrf_token=csrf_token,
        error=error,
        lightning_address=lightning_address,
        amount=amount,
        comment=comment,
        payment_mode=payment_mode,
        payment_asset=payment_asset,
        clear_balances=clear_balances or [],
    )


def _lightning_scan_form(
    csrf_token: str,
    error: str | None = None,
    lightning_address: str = "",
) -> str:
    return render_template(
        "scan_lightning_address.html",
        title="Scan a Code",
        csrf_token=csrf_token,
        error=error,
        lightning_address=lightning_address,
    )


def _normalize_lightning_address(value: str) -> str | None:
    """Return a conservative Lightning address extracted from scanner input."""

    recipient = str(value or "").strip()
    if recipient[:10].lower() == "lightning:":
        recipient = recipient[10:].strip()
    if (
        not recipient
        or len(recipient) > 320
        or recipient.count("@") != 1
        or any(character.isspace() for character in recipient)
    ):
        return None
    local_part, domain = recipient.split("@", 1)
    if (
        not re.fullmatch(r"[A-Za-z0-9._+~-]{1,64}", local_part)
        or local_part.startswith(".")
        or local_part.endswith(".")
        or ".." in local_part
        or len(domain) > 253
    ):
        return None
    labels = domain.split(".")
    if len(labels) < 2 or any(
        not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in labels
    ):
        return None
    return f"{local_part}@{domain.lower()}"


async def _resolve_safebox_lightning_recipient(
    lightning_address: str,
    *,
    timeout: float,
) -> dict[str, str] | None:
    """Resolve a Lightning address to a Safebox NIP-05 recipient if possible."""

    try:
        local_part, domain = lightning_address.split("@", 1)
    except ValueError:
        return None
    if not local_part or not domain:
        return None

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)),
            follow_redirects=True,
        ) as client:
            response = await client.get(
                f"https://{domain}/.well-known/nostr.json",
                params={"name": local_part},
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.info(
            "safebox recipient nip05 resolution skipped address=%s error_type=%s",
            lightning_address,
            type(exc).__name__,
        )
        return None

    if not isinstance(payload, dict):
        return None
    names = payload.get("names")
    relays = payload.get("relays")
    if not isinstance(names, dict) or not isinstance(relays, dict):
        return None

    pubkey_hex = names.get(local_part) or names.get(local_part.lower())
    if not isinstance(pubkey_hex, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}",
        pubkey_hex,
    ):
        return None
    recipient_relays = relays.get(pubkey_hex) or relays.get(pubkey_hex.lower())
    if not isinstance(recipient_relays, list) or not recipient_relays:
        return None
    relay = str(recipient_relays[0]).strip()
    if not relay.startswith(("wss://", "ws://")):
        return None

    try:
        recipient_npub = Keys.hex_to_bech32(pubkey_hex.lower(), prefix="npub")
    except Exception:
        return None
    return {"npub": recipient_npub, "relay": relay}


async def _resolve_safebox_clear_recipient(
    payment_address: str,
    *,
    mint: str,
    unit: str,
    timeout: float,
) -> dict[str, str] | None:
    """Resolve a NIP-05 address advertising compatible Clear receipt support."""

    try:
        local_part, domain = payment_address.split("@", 1)
    except ValueError:
        return None
    if not local_part or not domain:
        return None
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)),
            follow_redirects=True,
        ) as client:
            response = await client.get(
                f"https://{domain}/.well-known/nostr.json",
                params={"name": local_part},
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.info(
            "Clear recipient NIP-05 resolution skipped address=%s error_type=%s",
            payment_address,
            type(exc).__name__,
        )
        return None
    if not isinstance(payload, dict):
        return None
    names = payload.get("names")
    relays = payload.get("relays")
    clear = payload.get("clear")
    if (
        not isinstance(names, dict)
        or not isinstance(relays, dict)
        or not isinstance(clear, dict)
    ):
        return None
    pubkey_hex = names.get(local_part) or names.get(local_part.lower())
    descriptor = clear.get(local_part) or clear.get(local_part.lower())
    if (
        not isinstance(pubkey_hex, str)
        or not re.fullmatch(r"[0-9a-fA-F]{64}", pubkey_hex)
        or not isinstance(descriptor, dict)
    ):
        return None
    if "clear-token-transfer" not in (descriptor.get("protocols") or []):
        return None
    if "nip59" not in (descriptor.get("transports") or []):
        return None
    try:
        advertised_kinds = {int(kind) for kind in descriptor.get("kinds") or []}
    except (TypeError, ValueError):
        return None
    if 7379 not in advertised_kinds:
        return None
    advertised_mints = {
        str(candidate).rstrip("/") for candidate in descriptor.get("mints") or []
    }
    advertised_units = {
        str(candidate).strip() for candidate in descriptor.get("units") or []
    }
    if advertised_mints and str(mint).rstrip("/") not in advertised_mints:
        return None
    if advertised_units and str(unit).strip() not in advertised_units:
        return None
    recipient_relays = relays.get(pubkey_hex) or relays.get(pubkey_hex.lower())
    if not isinstance(recipient_relays, list) or not recipient_relays:
        return None
    relay = str(recipient_relays[0]).strip()
    if not relay.startswith(("wss://", "ws://")):
        return None
    try:
        recipient_npub = Keys.hex_to_bech32(pubkey_hex.lower(), prefix="npub")
    except Exception:
        return None
    return {"npub": recipient_npub, "relay": relay}


def _decode_lightning_invoice(value: str) -> dict[str, object] | None:
    """Validate a fixed-amount, unexpired mainnet BOLT11 invoice."""

    invoice = str(value or "").strip()
    if invoice[:10].lower() == "lightning:":
        invoice = invoice[10:].strip()
    if len(invoice) > 4096 or not invoice.lower().startswith("lnbc"):
        return None
    try:
        decoded = bolt11.decode(invoice)
    except Exception:
        return None
    if not decoded.is_mainnet() or decoded.has_expired() or decoded.amount_msat is None:
        return None
    amount_msat = int(decoded.amount_msat)
    if amount_msat <= 0 or amount_msat % 1000 != 0:
        return None
    return {
        "invoice": invoice,
        "amount": amount_msat // 1000,
        "description": str(decoded.description or ""),
        "expiry": datetime.fromtimestamp(
            decoded.expiry_time,
            tz=timezone.utc,
        ).isoformat(sep=" ", timespec="seconds"),
        "payment_hash": str(decoded.payment_hash),
    }


def _invoice_payment_form(
    *,
    csrf_token: str,
    state_token: str,
    amount: int,
    description: str,
    expiry: str,
    payment_hash: str,
    error: str | None = None,
) -> str:
    return render_template(
        "pay_invoice.html",
        title="Review Lightning invoice",
        csrf_token=csrf_token,
        state_token=state_token,
        amount=amount,
        description=description,
        expiry=expiry,
        payment_hash=payment_hash,
        error=error,
    )


async def _read_proof_verification(acorn, timeout: float) -> tuple[dict | None, str | None]:
    """Return a read-only mint-state report for the currently loaded proofs."""

    try:
        report = await asyncio.wait_for(acorn.check_proofs(), timeout=timeout)
        return report, None
    except TimeoutError:
        logger.warning("proof balance verification timed out")
        return None, "Mint verification timed out."
    except Exception as exc:
        logger.warning(
            "proof balance verification failed error_type=%s",
            type(exc).__name__,
        )
        return None, "Mint verification was unavailable."


def _balance_status_html(
    relay_balance: int,
    proof_count: int,
    verification: dict | None,
    verification_error: str | None,
) -> str:
    relay_html = (
        f"<p>Relay-visible proof total: <strong>{int(relay_balance):,} sats</strong> "
        f"in {int(proof_count):,} proofs</p>"
    )
    if verification is None:
        return (
            relay_html
            + '<p class="error"><strong>Confirmed balance not verified.</strong> '
            + escape(verification_error or "Mint verification was unavailable.")
            + " Do not rely on the relay-visible total for a payment.</p>"
        )

    confirmed = verification.get("mint_confirmed_unspent", {})
    confirmed_amount = int(confirmed.get("amount", 0))
    confirmed_count = int(confirmed.get("proof_count", 0))
    status = str(verification.get("status", "inconclusive"))
    confirmed_html = (
        "<p>Confirmed cash balance: "
        f"<strong>{confirmed_amount:,} sats</strong> in {confirmed_count:,} proofs</p>"
    )
    if status != "clean" or confirmed_amount != int(relay_balance):
        difference = max(0, int(relay_balance) - confirmed_amount)
        warning = (
            '<p class="error"><strong>Proof state requires attention.</strong> '
            f"Verification status: {escape(status)}. "
        )
        if difference:
            warning += f"The relay total includes {difference:,} sats that are not confirmed. "
        warning += "Do not make a payment until the proof state has been reviewed.</p>"
        return relay_html + confirmed_html + warning
    return relay_html + confirmed_html


def _unchecked_balance_status_html(relay_balance: int, proof_count: int) -> str:
    """Describe relay-visible state without implying a mint check occurred."""

    return (
        f"<p>Relay-visible proof total: <strong>{int(relay_balance):,} sats</strong> "
        f"in {int(proof_count):,} proofs</p>"
        "<p>Mint verification has not been run for this page load. Use "
        "<strong>Check Balance and Incoming Transfers</strong> when you need "
        "current mint-confirmed and pending-transfer status.</p>"
    )


def _wallet_balance_summary(
    relay_balance: int,
    verification: dict | None,
) -> tuple[int, bool]:
    """Choose the safest concise balance for the wallet's primary display."""

    if verification is None:
        return int(relay_balance), False
    confirmed = verification.get("mint_confirmed_unspent", {})
    confirmed_amount = int(confirmed.get("amount", 0))
    verified = (
        str(verification.get("status", "inconclusive")) == "clean"
        and confirmed_amount == int(relay_balance)
    )
    return confirmed_amount, verified


def _transaction_history_view(entries: list[dict]) -> list[dict]:
    """Normalize Acorn journal entries for the transaction template."""

    cards: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue

        tx_type = str(entry.get("tx_type") or "").upper()
        direction, sign, style = {
            "C": ("Credit", "+", "credit"),
            "D": ("Debit", "−", "debit"),
            "X": ("Error", "", "advisory"),
        }.get(tx_type, (tx_type or "Transaction", "", "advisory"))
        amount = str(entry.get("amount", 0))
        created = str(entry.get("create_time") or "Unknown time")
        fees = str(entry.get("fees") or 0)
        current_balance = entry.get("current_balance")
        balance = "—" if current_balance is None else str(current_balance)
        tendered_amount = entry.get("tendered_amount")
        tendered_currency = str(entry.get("tendered_currency") or "SAT")
        tender = (
            "—"
            if tendered_amount is None
            else f"{tendered_amount} {tendered_currency}"
        )
        comment = str(entry.get("comment") or "").strip()
        # Preserve old journal data while presenting protocol-neutral language.
        comment = comment.replace("ecash transfer received", "funds transfer received")
        comment = comment.replace("Ecash transfer received", "Funds transfer received")
        comment = comment.replace("Incoming ecash", "Incoming funds")
        cards.append(
            {
                "direction": direction,
                "sign": sign,
                "style": style,
                "amount": amount,
                "created": created,
                "fees": fees,
                "balance": balance,
                "tender": tender,
                "comment": comment,
            }
        )
    return cards


def _pending_transaction_view(
    continuity_receipts: list[dict],
    incoming_preview: dict,
) -> list[dict]:
    """Normalize relay-visible arrivals without presenting them as spendable."""

    cards: list[dict] = []
    seen_event_ids: set[str] = set()

    def append_card(item: dict, *, stage: str) -> None:
        if not isinstance(item, dict):
            return
        event_id = str(item.get("event_id") or "").strip()
        if event_id and event_id in seen_event_ids:
            return
        if event_id:
            seen_event_ids.add(event_id)
        try:
            amount = int(item.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0
        try:
            timestamp = int(item.get("timestamp") or 0)
        except (TypeError, ValueError):
            timestamp = 0
        created = (
            datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            if timestamp > 0
            else "Arrival time unavailable"
        )
        sender = str(item.get("sender_pubkey") or "").strip()
        cards.append(
            {
                "event_id": event_id,
                "event_short": event_id[:12],
                "amount": amount,
                "created": created,
                "timestamp": timestamp,
                "sender_short": sender[:12],
                "comment": str(item.get("comment") or "").strip(),
                "stage": stage,
            }
        )

    for receipt in continuity_receipts:
        if str(receipt.get("status") or "provisional") == "provisional":
            append_card(receipt, stage="Awaiting mint confirmation")

    previewed = incoming_preview.get("previewed", [])
    if isinstance(previewed, list):
        for arrival in previewed:
            append_card(arrival, stage="Received on relay; finalization pending")

    return sorted(
        cards,
        key=lambda card: (card["timestamp"], card["event_id"]),
        reverse=True,
    )


def _pending_transaction_totals(
    continuity_receipts: list[dict],
    incoming_preview: dict,
) -> tuple[int, int]:
    """Prefer exact deduplicated entries while supporting older Acorn results."""

    if isinstance(incoming_preview.get("previewed"), list):
        cards = _pending_transaction_view(continuity_receipts, incoming_preview)
        return sum(int(card["amount"]) for card in cards), len(cards)
    provisional = [
        receipt
        for receipt in continuity_receipts
        if str(receipt.get("status") or "provisional") == "provisional"
    ]
    return (
        sum(int(receipt.get("amount") or 0) for receipt in provisional)
        + int(incoming_preview.get("previewed_amount", 0)),
        len(provisional) + int(incoming_preview.get("previewed_count", 0)),
    )


async def _read_clear_receipts(
    acorn,
    timeout: float,
    *,
    status: str | None = "pending",
) -> list[dict]:
    reader = getattr(acorn, "get_clear_receipts", None)
    if reader is None:
        return []
    try:
        awaitable = reader() if status is None else reader(status=status)
    except TypeError:
        awaitable = reader()
    receipts = await asyncio.wait_for(awaitable, timeout=timeout)
    return receipts if isinstance(receipts, list) else []


async def _read_clear_balances(acorn, timeout: float) -> list[dict]:
    reader = getattr(acorn, "get_clear_balances", None)
    if reader is None:
        return []
    balances = await asyncio.wait_for(reader(), timeout=timeout)
    return balances if isinstance(balances, list) else []


def _encode_clear_payment_asset(mint: str, unit: str) -> str:
    payload = json.dumps(
        {"mint": str(mint).rstrip("/"), "unit": str(unit)},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "clear:" + base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_clear_payment_asset(value: str) -> tuple[str, str] | None:
    encoded = str(value or "").strip()
    if not encoded.startswith("clear:") or len(encoded) > 1024:
        return None
    token = encoded[6:]
    try:
        payload = json.loads(
            base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        )
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    mint = str(payload.get("mint") or "").rstrip("/")
    unit = str(payload.get("unit") or "").strip()
    if (
        not mint
        or not unit.startswith("cmu-")
        or len(mint) > 512
        or len(unit) > 128
        or any(character.isspace() for character in unit)
    ):
        return None
    return mint, unit


async def _payment_clear_balances(request: Request, acorn) -> list[dict]:
    """Return spendable Clear balances prepared for an unambiguous form choice."""

    settings = request.app.state.settings
    balances = await _read_clear_balances(
        acorn,
        settings.wallet_load_timeout_seconds,
    )
    summary = _clear_balance_summary([], balances)
    try:
        summary = await _resolve_clear_aliases(
            summary,
            timeout=settings.wallet_load_timeout_seconds,
            configured_mints=settings.clear_mints,
            cache=request.app.state.clear_mint_metadata_cache,
        )
    except Exception as exc:
        logger.info(
            "Clear payment alias lookup skipped error_type=%s",
            type(exc).__name__,
        )
    options: list[dict] = []
    for balance in summary.get("balances") or []:
        if not isinstance(balance, dict):
            continue
        amount = max(0, int(balance.get("amount") or 0))
        if amount <= 0:
            continue
        mint = str(balance.get("mint") or "").rstrip("/")
        unit = str(balance.get("unit") or "").strip()
        if not mint or not unit:
            continue
        options.append(
            {
                **balance,
                "amount": amount,
                "asset_id": _encode_clear_payment_asset(mint, unit),
            }
        )
    return options


async def _read_clear_history(acorn, timeout: float) -> list[dict]:
    reader = getattr(acorn, "get_clear_transaction_history", None)
    if reader is None:
        return []
    history = await asyncio.wait_for(reader(), timeout=timeout)
    return history if isinstance(history, list) else []


async def _preview_incoming_clear(acorn, timeout: float) -> dict:
    scanner = getattr(acorn, "sweep_clear_transfers", None)
    if scanner is None:
        return {"previewed_count": 0, "previewed_amount": 0, "previewed": []}
    try:
        awaitable = scanner(preview_only=True, advance_cursor=False)
    except TypeError:
        return {"previewed_count": 0, "previewed_amount": 0, "previewed": []}
    try:
        result = await asyncio.wait_for(awaitable, timeout=timeout)
    except Exception as exc:
        logger.warning(
            "incoming Clear preview failed error_type=%s",
            type(exc).__name__,
        )
        return {"previewed_count": 0, "previewed_amount": 0, "previewed": []}
    return result if isinstance(result, dict) else {}


def _merge_clear_pending(receipts: list[dict], preview: dict) -> list[dict]:
    """Merge relay previews with stored pending receipts without duplication."""

    known_event_ids = {
        str(receipt.get("event_id") or "")
        for receipt in receipts
        if isinstance(receipt, dict)
    }
    pending = [
        dict(receipt)
        for receipt in receipts
        if isinstance(receipt, dict)
        and str(receipt.get("status") or "pending") == "pending"
    ]
    for item in preview.get("previewed") or []:
        if not isinstance(item, dict):
            continue
        event_id = str(item.get("event_id") or "")
        if not event_id or event_id in known_event_ids:
            continue
        mints = [str(mint).rstrip("/") for mint in (item.get("mints") or [])]
        if len(mints) != 1:
            continue
        pending.append({
            **item,
            "event_id": event_id,
            "mint": mints[0],
            "status": "pending",
            "relay_preview": True,
        })
        known_event_ids.add(event_id)
    return pending


def _clear_balance_summary(
    receipts: list[dict],
    spendable_balances: list[dict] | None = None,
) -> dict:
    pending = [
        receipt
        for receipt in receipts
        if isinstance(receipt, dict)
        and str(receipt.get("status") or "pending") == "pending"
    ]
    by_balance: dict[tuple[str, str], dict] = {}
    for balance in spendable_balances or []:
        if not isinstance(balance, dict):
            continue
        unit = str(balance.get("unit") or "unknown")
        mint = str(balance.get("mint") or "unknown").rstrip("/")
        try:
            amount = max(0, int(balance.get("amount") or 0))
        except (TypeError, ValueError):
            amount = 0
        by_balance[(mint, unit)] = {
            "mint": mint,
            "unit": unit,
            "amount": amount,
            "proof_count": max(0, int(balance.get("proof_count") or 0)),
            "pending_amount": 0,
            "count": 0,
            "display_name": unit,
            "display_unit": unit,
            "metadata_resolved": False,
        }
    for receipt in pending:
        unit = str(receipt.get("unit") or "unknown")
        mint = str(receipt.get("mint") or "unknown").rstrip("/")
        row = by_balance.setdefault(
            (mint, unit),
            {
                "mint": mint,
                "unit": unit,
                "amount": 0,
                "proof_count": 0,
                "pending_amount": 0,
                "count": 0,
                "display_name": unit,
                "display_unit": unit,
                "metadata_resolved": False,
            },
        )
        try:
            row["pending_amount"] += int(receipt.get("amount") or 0)
        except (TypeError, ValueError):
            pass
        row["count"] += 1
    return {
        "pending": bool(pending),
        "count": len(pending),
        "pending_balance_count": len({
            (
                str(receipt.get("mint") or "unknown").rstrip("/"),
                str(receipt.get("unit") or "unknown"),
            )
            for receipt in pending
        }),
        "balance_count": len(by_balance),
        "spendable": any(row["amount"] > 0 for row in by_balance.values()),
        "balances": sorted(
            by_balance.values(),
            key=lambda row: (row["unit"], row["mint"]),
        ),
    }


def _pending_clear_summary(receipts: list[dict]) -> dict:
    """Compatibility wrapper for callers that only need pending receipts."""

    return _clear_balance_summary(receipts)


def _clear_transaction_view(
    receipts: list[dict],
    summary: dict,
    history: list[dict] | None = None,
) -> list[dict]:
    """Present Clear receipts without combining distinct mint-unit balances."""

    metadata = {
        (str(balance["mint"]), str(balance["unit"])): balance
        for balance in summary.get("balances", [])
        if isinstance(balance, dict)
        and balance.get("mint") is not None
        and balance.get("unit") is not None
    }
    cards: list[dict] = []
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        mint = str(receipt.get("mint") or "unknown").rstrip("/")
        unit = str(receipt.get("unit") or "unknown")
        display = metadata.get((mint, unit), {})
        try:
            amount = int(receipt.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0
        try:
            timestamp = int(receipt.get("timestamp") or 0)
        except (TypeError, ValueError):
            timestamp = 0
        created = (
            datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC"
            )
            if timestamp > 0
            else "Arrival time unavailable"
        )
        status = str(receipt.get("status") or "pending").strip().lower()
        event_id = str(receipt.get("event_id") or "").strip()
        sender = str(receipt.get("sender_pubkey") or "").strip()
        cards.append(
            {
                "amount": amount,
                "mint": mint,
                "unit": unit,
                "display_name": str(display.get("display_name") or unit),
                "display_unit": str(display.get("display_unit") or unit),
                "status": status,
                "status_label": status.replace("_", " ").title(),
                "created": created,
                "timestamp": timestamp,
                "event_id": event_id,
                "event_short": event_id[:12],
                "sender_short": sender[:12],
                "comment": str(receipt.get("comment") or "").strip(),
                "keyset_ids": [
                    str(keyset_id)
                    for keyset_id in (receipt.get("keyset_ids") or [])
                ],
                "direction": "in",
                "operation": "receive",
                "relay_preview": bool(receipt.get("relay_preview")),
            }
        )
    for entry in history or []:
        if not isinstance(entry, dict):
            continue
        mint = str(entry.get("mint") or "unknown").rstrip("/")
        unit = str(entry.get("unit") or "unknown")
        display = metadata.get((mint, unit), {})
        timestamp = max(0, int(entry.get("timestamp") or 0))
        direction = str(entry.get("direction") or "in")
        operation = str(entry.get("operation") or "transfer")
        event_id = str(entry.get("event_id") or "")
        cards.append({
            "amount": max(0, int(entry.get("amount") or 0)),
            "mint": mint,
            "unit": unit,
            "display_name": str(display.get("display_name") or unit),
            "display_unit": str(display.get("display_unit") or unit),
            "status": "completed",
            "status_label": operation.title(),
            "created": (
                datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime(
                    "%Y-%m-%d %H:%M UTC"
                )
                if timestamp > 0
                else "Transaction time unavailable"
            ),
            "timestamp": timestamp,
            "event_id": event_id,
            "event_short": event_id[:12],
            "sender_short": str(entry.get("counterparty") or "")[:12],
            "comment": str(entry.get("memo") or "").strip(),
            "keyset_ids": [],
            "direction": direction,
            "operation": operation,
        })
    return sorted(
        cards,
        key=lambda card: (card["timestamp"], card["event_id"]),
        reverse=True,
    )


def _clear_page_notice(query_params) -> str | None:
    if query_params.get("acceptance") == "started":
        return "Clear transfer acceptance started. You may leave this page."
    if query_params.get("acceptance") == "running":
        return "A Clear transfer acceptance is already running for this Acorn."
    if query_params.get("receipt_accepted") == "1":
        return "Clear transfer accepted into your Clear balance."
    if query_params.get("receipt_deleted") == "1":
        return "Pending Clear transfer deleted."
    raw_received = query_params.get("received")
    if raw_received is None:
        return None
    try:
        received = max(0, int(raw_received))
    except (TypeError, ValueError):
        return None
    if received == 0:
        return "No new Clear transfers found."
    suffix = "" if received == 1 else "s"
    return f"Received {received:,} new Clear transfer{suffix}."


def _clear_metadata_url(mint: str, configured_mints: tuple[str, ...]) -> str | None:
    normalized = mint.rstrip("/")
    parsed = urlsplit(normalized)
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    trusted_mints = {value.rstrip("/") for value in configured_mints}
    if parsed.scheme != "https" and normalized not in trusted_mints:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    return f"{normalized}/v1/info"


def _clear_display_metadata(payload: object, *, mint: str, unit: str) -> dict | None:
    if not isinstance(payload, dict):
        return None
    advertised_mint = str(payload.get("mint_url") or mint).rstrip("/")
    currency = payload.get("currency")
    if advertised_mint != mint or not isinstance(currency, dict):
        return None
    if str(currency.get("unit") or "") != unit:
        return None

    friendly_name = currency.get("friendly_alias") or currency.get("name")
    friendly_unit = currency.get("friendly_unit_alias")
    display_name = str(friendly_name or unit).strip()
    display_unit = str(friendly_unit or unit).strip()
    if not display_name or len(display_name) > 120:
        display_name = unit
    if not display_unit or len(display_unit) > 40:
        display_unit = unit
    return {
        "display_name": display_name,
        "display_unit": display_unit,
        "metadata_resolved": True,
    }


async def _resolve_clear_aliases(
    summary: dict,
    *,
    timeout: float,
    configured_mints: tuple[str, ...],
    cache: dict[tuple[str, str], tuple[float, dict]],
) -> dict:
    balances = summary.get("balances")
    if not isinstance(balances, list) or not balances:
        return summary

    async def resolve(client: httpx.AsyncClient, balance: dict) -> dict | None:
        mint = str(balance["mint"])
        unit = str(balance["unit"])
        cached = cache.get((mint, unit))
        if cached is not None and monotonic() - cached[0] < 300:
            return cached[1]
        metadata_url = _clear_metadata_url(mint, configured_mints)
        if metadata_url is None:
            return None
        try:
            response = await client.get(metadata_url)
            response.raise_for_status()
            if len(response.content) > 64 * 1024:
                return None
            metadata = _clear_display_metadata(
                response.json(),
                mint=mint,
                unit=unit,
            )
            if metadata is not None:
                cache[(mint, unit)] = (monotonic(), metadata)
            return metadata
        except Exception as exc:
            logger.info(
                "clear mint alias lookup skipped mint=%s error_type=%s",
                mint,
                type(exc).__name__,
            )
            return None

    metadata_timeout = max(0.1, min(float(timeout), 3.0))
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(
            metadata_timeout,
            connect=min(metadata_timeout, 2.0),
        ),
        follow_redirects=False,
    ) as client:
        resolved = await asyncio.gather(
            *(resolve(client, balance) for balance in balances)
        )
    for balance, metadata in zip(balances, resolved, strict=True):
        if metadata is not None:
            balance.update(metadata)
    return summary


async def _read_continuity_receipts(acorn, timeout: float) -> list[dict]:
    reader = getattr(acorn, "get_continuity_receipts", None)
    if reader is None:
        return []
    try:
        awaitable = reader(status="provisional")
    except TypeError:
        awaitable = reader()
    receipts = await asyncio.wait_for(awaitable, timeout=timeout)
    return receipts if isinstance(receipts, list) else []


async def _reconcile_continuity_receipts(acorn, timeout: float) -> dict:
    reconciler = getattr(acorn, "reconcile_continuity_receipts", None)
    if reconciler is None:
        return {"supported": False}
    result = await asyncio.wait_for(reconciler(), timeout=timeout)
    if not isinstance(result, dict):
        return {"supported": False}
    return {**result, "supported": True}


async def _preview_incoming_payments(acorn, timeout: float) -> dict:
    scanner = getattr(acorn, "sweep_ecash_transfers", None)
    if scanner is None:
        return {"previewed_count": 0, "previewed_amount": 0}
    try:
        awaitable = scanner(preview_only=True)
    except TypeError:
        return {"previewed_count": 0, "previewed_amount": 0}
    result = await asyncio.wait_for(awaitable, timeout=timeout)
    return result if isinstance(result, dict) else {}


def _transactions_page(
    entries: list[dict],
    csrf_token: str,
    notice: str | None = None,
    retention_notice: str = "",
    wallet_balance: int | None = None,
    wallet_balance_verified: bool = False,
    balance_status: str = "",
    pending_amount: int = 0,
    pending_count: int = 0,
    fiat_estimate: dict | None = None,
    finalization_job: dict | None = None,
    pending_transactions: list[dict] | None = None,
    checks_performed: bool = False,
) -> str:
    """Render transaction history with an explicit incoming funds check."""

    return render_template(
        "transactions.html",
        title="Cash Transactions",
        headline_class="transaction-headline",
        entries=_transaction_history_view(entries),
        csrf_token=csrf_token,
        notice=notice,
        retention_notice=retention_notice,
        wallet_balance=wallet_balance,
        wallet_balance_verified=wallet_balance_verified,
        balance_status=balance_status,
        pending_amount=int(pending_amount),
        pending_count=int(pending_count),
        fiat_estimate=fiat_estimate,
        finalization_job=finalization_job,
        pending_transactions=pending_transactions or [],
        checks_performed=checks_performed,
    )


async def _record_index_entries(acorn, timeout: float) -> list[dict]:
    """Load unique record labels ordered by newest relay event first."""

    records_reader = getattr(acorn, "get_user_records", None)
    if callable(records_reader):
        records = await asyncio.wait_for(
            records_reader(record_kind=37375, reverse=True),
            timeout=timeout,
        )
        if isinstance(records, list):
            newest_by_label: dict[str, int] = {}
            for record in records:
                if not isinstance(record, dict):
                    continue
                tag = record.get("tag")
                if isinstance(tag, list) and tag:
                    label = str(tag[0]).strip()
                elif isinstance(tag, str):
                    label = tag.strip()
                else:
                    continue
                if not label:
                    continue
                try:
                    modified_at = int(
                        record.get("timestamp")
                        or record.get("created_at")
                        or 0
                    )
                except (TypeError, ValueError):
                    modified_at = 0
                newest_by_label[label] = max(
                    newest_by_label.get(label, 0),
                    modified_at,
                )
            return [
                {"label": label, "modified_at": modified_at}
                for label, modified_at in sorted(
                    newest_by_label.items(),
                    key=lambda item: (-item[1], item[0].casefold(), item[0]),
                )
            ]

    labels = await asyncio.wait_for(
        acorn.get_user_record_labels(),
        timeout=timeout,
    )
    unique_labels = {str(label).strip() for label in labels if str(label).strip()}
    return [
        {"label": label, "modified_at": 0}
        for label in sorted(unique_labels, key=lambda item: (item.casefold(), item))
    ]


def _history_has_receive_credit(
    entries: list[dict],
    *,
    accepted_amount: int,
    accepted_count: int,
) -> bool:
    """Return whether a post-receive history lookup includes the expected credit."""

    if accepted_count <= 0:
        return True
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("tx_type") or "").upper() != "C":
            continue
        if int(entry.get("amount") or 0) != accepted_amount:
            continue
        comment = str(entry.get("comment") or "").lower()
        if (
            "funds transfer received" in comment
            or "ecash transfer received" in comment
        ):
            return True
    return False


def _is_stale_proof_error(message: str) -> bool:
    normalized = str(message or "").lower()
    return "stale proofs" in normalized or "proof state is stale" in normalized


async def _read_receive_history_with_retry(
    acorn,
    *,
    accepted_amount: int,
    accepted_count: int,
    timeout: float,
    attempts: int = 4,
    delay_seconds: float = 0.35,
) -> list[dict]:
    """Read transaction history, allowing brief relay read-after-write lag."""

    last_history: list[dict] = []
    for attempt in range(max(1, attempts)):
        history = await asyncio.wait_for(acorn.get_tx_history(), timeout=timeout)
        last_history = history if isinstance(history, list) else []
        if _history_has_receive_credit(
            last_history,
            accepted_amount=accepted_amount,
            accepted_count=accepted_count,
        ):
            return last_history
        if accepted_count <= 0 or attempt == attempts - 1:
            return last_history
        await asyncio.sleep(delay_seconds)
    return last_history


def _record_form(
    csrf_token: str,
    max_blob_bytes: int,
    *,
    label: str = "",
    payload: str = "",
    payload_format: str = "text",
    updating: bool = False,
    has_blob: bool = False,
    error: str | None = None,
) -> str:
    """Render the add/update form without retaining record data server-side."""

    title = "Update Record" if updating else "Add a Record"
    page_back_url = (
        f'/record?{urlencode({"label": label})}' if updating and label else "/records"
    )
    return render_template(
        "record_form.html",
        title=title,
        page_back_url=page_back_url,
        page_back_label="Back to Record" if updating and label else "Back to Records",
        csrf_token=csrf_token,
        label=label,
        payload=payload,
        payload_format=payload_format,
        updating=updating,
        has_blob=has_blob,
        max_blob_megabytes=f"{max_blob_bytes / (1024 * 1024):g}",
        error=error,
    )


def _validate_record_label(value: str) -> str:
    """Validate a private record label shared by text and blob records."""

    label = str(value).strip()
    if not label:
        raise ValueError("Record label is required.")
    if len(label) > 200 or any(character in label for character in ("\x00", "\r", "\n")):
        raise ValueError(
            "Record label must be 200 characters or fewer and remain on one line."
        )
    return label


def _blob_upload_form(
    csrf_token: str,
    max_blob_bytes: int,
    *,
    label: str = "",
    description: str = "",
    error: str | None = None,
) -> str:
    return render_template(
        "blob_upload.html",
        title="Store an Original Record",
        csrf_token=csrf_token,
        max_blob_megabytes=f"{max_blob_bytes / (1024 * 1024):g}",
        label=label,
        description=description,
        error=error,
    )


INLINE_BLOB_IMAGE_TYPES = frozenset(
    {
        "image/avif",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)
PKPASS_MIME_TYPE = "application/vnd.apple.pkpass"
VC_MIME_TYPE = "application/vc"
VP_MIME_TYPE = "application/vp"
VC_JWT_MIME_TYPE = "application/vc+jwt"
VC_SD_JWT_MIME_TYPE = "application/vc+sd-jwt"
VC_COSE_MIME_TYPE = "application/vc+cose"
MDOC_CBOR_MIME_TYPE = "application/mdoc+cbor"
EUDI_PID_DOC_TYPE = "eu.europa.ec.eudi.pid.1"
MDL_DOC_TYPE = "org.iso.18013.5.1.mDL"
MDL_NAMESPACE = "org.iso.18013.5.1"
JSON_CREDENTIAL_PREVIEW_TYPES = frozenset({VC_MIME_TYPE, VP_MIME_TYPE})
JSON_CREDENTIAL_PREVIEW_MAX_BYTES = 1024 * 1024
JSON_CREDENTIAL_PREVIEW_MAX_ROWS = 80
JSON_CREDENTIAL_PREVIEW_MAX_CLAIMS = 40
MDOC_PREVIEW_MAX_BYTES = 1024 * 1024
MDOC_PREVIEW_MAX_ROWS = 100
EUDI_PID_FIELD_LABELS = {
    "family_name": "Family name",
    "given_name": "Given name",
    "birth_date": "Date of birth",
    "place_of_birth": "Place of birth",
    "nationality": "Nationality",
    "expiry_date": "Expiry date",
    "issuing_authority": "Issuing authority",
    "issuing_country": "Issuing country",
    "issuance_date": "Issuance date",
    "resident_address": "Address",
    "resident_country": "Country of residence",
    "resident_state": "State or region",
    "resident_city": "City",
    "resident_postal_code": "Postal code",
    "resident_street": "Street",
    "resident_house_number": "House number",
    "document_number": "Document number",
    "personal_administrative_number": "Administrative number",
    "family_name_birth": "Family name at birth",
    "given_name_birth": "Given name at birth",
    "sex": "Sex",
    "email_address": "Email address",
    "mobile_phone_number": "Mobile phone number",
}
MDL_FIELD_LABELS = {
    "family_name": "Family name",
    "given_name": "Given name",
    "birth_date": "Date of birth",
    "issue_date": "Issue date",
    "expiry_date": "Expiry date",
    "issuing_country": "Issuing country",
    "issuing_authority": "Issuing authority",
    "document_number": "Licence number",
    "portrait": "Portrait",
    "driving_privileges": "Driving privileges",
    "un_distinguishing_sign": "Distinguishing sign",
    "age_over_18": "Over 18",
    "age_over_21": "Over 21",
    "age_in_years": "Age in years",
    "age_birth_year": "Birth year",
    "nationality": "Nationality",
    "sex": "Sex",
    "place_of_birth": "Place of birth",
    "resident_address": "Address",
    "resident_city": "City",
    "resident_state": "State or region",
    "resident_postal_code": "Postal code",
    "resident_country": "Country of residence",
    "height": "Height",
    "weight": "Weight",
    "eye_colour": "Eye colour",
    "hair_colour": "Hair colour",
    "family_name_national_character": "Family name (national characters)",
    "given_name_national_character": "Given name (national characters)",
    "signature_usual_mark": "Signature",
}
EFFECTIVE_MIME_DOWNLOAD_EXTENSIONS = {
    VC_MIME_TYPE: ".json",
    VP_MIME_TYPE: ".json",
    VC_JWT_MIME_TYPE: ".jwt",
    "application/vp+jwt": ".jwt",
    VC_SD_JWT_MIME_TYPE: ".sd-jwt",
    "application/vp+sd-jwt": ".sd-jwt",
    VC_COSE_MIME_TYPE: ".cose",
    "application/vp+cose": ".cose",
    MDOC_CBOR_MIME_TYPE: ".mdoc",
    PKPASS_MIME_TYPE: ".pkpass",
}
PKPASS_PREVIEW_MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
PKPASS_PREVIEW_MAX_MEMBER_BYTES = 2 * 1024 * 1024
PKPASS_PREVIEW_MAX_TOTAL_BYTES = 12 * 1024 * 1024
PKPASS_PREVIEW_IMAGE_NAMES = (
    "strip.png",
    "strip@2x.png",
    "thumbnail.png",
    "thumbnail@2x.png",
    "logo.png",
    "logo@2x.png",
    "icon.png",
    "icon@2x.png",
)


@dataclass(frozen=True)
class EffectiveMimeResolution:
    effective_mime: str | None
    source: str
    confidence: str
    detected_mime: str | None = None
    evidence: tuple[str, ...] = ()
    requires_confirmation: bool = False
    alternatives: tuple[str, ...] = ()


def _normalize_media_type(media_type: str | None) -> str | None:
    normalized = str(media_type or "").split(";", 1)[0].strip().lower()
    return normalized or None


def _upload_original_filename(upload: UploadFile | None) -> str:
    if upload is None:
        return ""
    return str(upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1]


def _json_has_type(value, expected_type: str) -> bool:
    document_types = value.get("type")
    if isinstance(document_types, str):
        return document_types == expected_type
    if isinstance(document_types, list):
        return expected_type in document_types
    return False


def _vc_media_type_from_json(
    blob_data: bytes | None,
) -> tuple[str, tuple[str, ...]] | None:
    if not blob_data or len(blob_data) > 1024 * 1024:
        return None
    try:
        document = json.loads(blob_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    context = document.get("@context")
    if isinstance(context, str):
        contexts = [context]
    elif isinstance(context, list):
        contexts = [item for item in context if isinstance(item, str)]
    else:
        contexts = []
    has_vc_context = any(
        item
        in (
            "https://www.w3.org/ns/credentials/v2",
            "https://www.w3.org/2018/credentials/v1",
        )
        for item in contexts
    )
    if _json_has_type(document, "VerifiablePresentation"):
        evidence = ["json type includes VerifiablePresentation"]
        if has_vc_context:
            evidence.append("json includes W3C credentials context")
        return VP_MIME_TYPE, tuple(evidence)
    if _json_has_type(document, "VerifiableCredential"):
        evidence = ["json type includes VerifiableCredential"]
        if has_vc_context:
            evidence.append("json includes W3C credentials context")
        return VC_MIME_TYPE, tuple(evidence)
    return None


def _base64url_json(segment: str) -> dict | None:
    if not segment:
        return None
    padding = "=" * (-len(segment) % 4)
    try:
        decoded = base64.urlsafe_b64decode((segment + padding).encode("ascii"))
        value = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _vc_media_type_from_token(
    blob_data: bytes | None,
) -> tuple[str, tuple[str, ...]] | None:
    if not blob_data or len(blob_data) > 1024 * 1024:
        return None
    try:
        token = blob_data.decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    if not token or any(character.isspace() for character in token):
        return None
    first_token = token.split("~", 1)[0]
    parts = first_token.split(".")
    if len(parts) < 2:
        return None
    header = _base64url_json(parts[0])
    payload = _base64url_json(parts[1])
    typ = str((header or {}).get("typ") or "").lower()
    if "vc+sd-jwt" in typ or (
        "~" in token and isinstance(payload, dict) and "vct" in payload
    ):
        return VC_SD_JWT_MIME_TYPE, ("token indicates SD-JWT VC",)
    if "vc+jwt" in typ or "vc" in typ:
        return VC_JWT_MIME_TYPE, ("jwt header typ indicates VC",)
    if isinstance(payload, dict) and (
        isinstance(payload.get("vc"), dict) or isinstance(payload.get("vp"), dict)
    ):
        return VC_JWT_MIME_TYPE, ("jwt payload contains vc/vp claim",)
    return None


def _resolve_upload_effective_mime(
    upload: UploadFile | None,
    blob_data: bytes | None = None,
) -> EffectiveMimeResolution:
    """Resolve the artifact MIME type for an uploaded Original Record."""

    if upload is None:
        return EffectiveMimeResolution(None, "missing", "none")
    filename = _upload_original_filename(upload)
    lower_filename = filename.lower()
    declared_mime = _normalize_media_type(upload.content_type)
    extension_map = {
        ".pkpass": PKPASS_MIME_TYPE,
        ".vc": VC_MIME_TYPE,
        ".vp": VP_MIME_TYPE,
        ".vc-jwt": VC_JWT_MIME_TYPE,
        ".vc.jwt": VC_JWT_MIME_TYPE,
        ".sd-jwt": VC_SD_JWT_MIME_TYPE,
        ".vc+sd-jwt": VC_SD_JWT_MIME_TYPE,
        ".vc+cose": VC_COSE_MIME_TYPE,
        ".mdoc": MDOC_CBOR_MIME_TYPE,
        ".mdl": MDOC_CBOR_MIME_TYPE,
    }
    for suffix, media_type in extension_map.items():
        if lower_filename.endswith(suffix):
            return EffectiveMimeResolution(
                media_type,
                "inferred",
                "high" if suffix == ".pkpass" else "medium",
                detected_mime=declared_mime,
                evidence=(f"filename ends {suffix}",),
                requires_confirmation=suffix != ".pkpass",
                alternatives=tuple(
                    value
                    for value in (declared_mime, media_type, "application/octet-stream")
                    if value
                ),
            )
    if token_result := _vc_media_type_from_token(blob_data):
        media_type, evidence = token_result
        return EffectiveMimeResolution(
            media_type,
            "inferred",
            "medium",
            detected_mime=declared_mime,
            evidence=evidence,
            requires_confirmation=True,
            alternatives=tuple(
                value
                for value in (declared_mime, media_type, "application/octet-stream")
                if value
            ),
        )
    if json_result := _vc_media_type_from_json(blob_data):
        media_type, evidence = json_result
        return EffectiveMimeResolution(
            media_type,
            "inferred",
            "high" if "json includes W3C credentials context" in evidence else "medium",
            detected_mime=declared_mime,
            evidence=evidence,
            requires_confirmation="json includes W3C credentials context" not in evidence,
            alternatives=tuple(
                value
                for value in (declared_mime, media_type, "application/octet-stream")
                if value
            ),
        )
    return EffectiveMimeResolution(
        declared_mime,
        "declared" if declared_mime else "default",
        "low" if declared_mime else "none",
        detected_mime=declared_mime,
        evidence=(f"upload declared {declared_mime}",) if declared_mime else (),
        alternatives=(declared_mime, "application/octet-stream")
        if declared_mime and declared_mime != "application/octet-stream"
        else (),
    )


def _record_original_filename(record_value) -> str:
    payload = getattr(record_value, "payload", None)
    if isinstance(payload, dict):
        return str(payload.get("filename") or "")
    return ""


def _original_record_type_notice(record_value, blob_type: str | None) -> str | None:
    if not getattr(record_value, "blobref", None):
        return None
    effective_mime = _normalize_media_type(blob_type) or "application/octet-stream"
    filename = _record_original_filename(record_value)
    payload = getattr(record_value, "payload", None)
    stored_mime = (
        _normalize_media_type(payload.get("content_type"))
        if isinstance(payload, dict)
        else None
    )
    source = "determined"
    if stored_mime and stored_mime == effective_mime:
        source = "stored"
    if filename.lower().endswith(".pkpass") and effective_mime == PKPASS_MIME_TYPE:
        source = "recognized"
    filename_text = f" for {filename}" if filename else ""
    return f"Safebox {source} Original Record type{filename_text}: {effective_mime}."


def _effective_blob_media_type(media_type: str | None, record_value=None) -> str:
    """Prefer explicit Original Record metadata when byte sniffing is ambiguous."""

    normalized = _normalize_media_type(media_type) or "application/octet-stream"
    if record_value is not None:
        payload = getattr(record_value, "payload", None)
        if isinstance(payload, dict):
            payload_type = _normalize_media_type(payload.get("content_type"))
            if payload_type == PKPASS_MIME_TYPE:
                return PKPASS_MIME_TYPE
        if _record_original_filename(record_value).lower().endswith(".pkpass"):
            return PKPASS_MIME_TYPE
    return normalized or "application/octet-stream"


def _pkpass_asset_data_url(archive: zipfile.ZipFile, name: str) -> str | None:
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > PKPASS_PREVIEW_MAX_MEMBER_BYTES:
        return None
    data = archive.read(info)
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        media_type = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        media_type = "image/jpeg"
    else:
        return None
    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"


def _pkpass_field_rows(pass_json: dict) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    pass_sections = (
        "boardingPass",
        "coupon",
        "eventTicket",
        "generic",
        "storeCard",
    )
    field_sections = (
        "headerFields",
        "primaryFields",
        "secondaryFields",
        "auxiliaryFields",
        "backFields",
    )
    for pass_section in pass_sections:
        section = pass_json.get(pass_section)
        if not isinstance(section, dict):
            continue
        for field_section in field_sections:
            fields = section.get(field_section)
            if not isinstance(fields, list):
                continue
            for field in fields:
                if not isinstance(field, dict):
                    continue
                value = field.get("value")
                if value is None:
                    continue
                label = str(field.get("label") or field.get("key") or "").strip()
                rows.append(
                    {
                        "label": label[:80],
                        "value": str(value).strip()[:240],
                    }
                )
                if len(rows) >= 10:
                    return rows
    return rows


def _pkpass_barcode_svg(format_name: str, message: str, encoding: str) -> str | None:
    if format_name == "QR":
        return _qr_svg(message)
    if format_name == "Aztec":
        try:
            svg = AztecCode(message, encoding=encoding or None).svg()
        except (LookupError, TypeError, ValueError):
            return None
        svg = re.sub(r"^<\?xml[^>]*\?>", "", svg, count=1)
        return svg.replace("<svg ", '<svg class="pkpass-barcode-symbol" ', 1)
    return None


def _pkpass_barcode(pass_json: dict) -> dict[str, str | None] | None:
    candidates = pass_json.get("barcodes")
    if isinstance(candidates, list) and candidates:
        barcode = candidates[0]
    else:
        barcode = pass_json.get("barcode")
    if not isinstance(barcode, dict):
        return None
    message = str(barcode.get("message") or "").strip()
    if not message:
        return None
    format_name = str(barcode.get("format") or "barcode").replace(
        "PKBarcodeFormat", ""
    )
    encoding = str(barcode.get("messageEncoding") or "").strip()
    alt_text = str(barcode.get("altText") or "").strip()
    return {
        "format": format_name,
        "message": message[:240],
        "alt_text": alt_text[:120],
        "symbol": _pkpass_barcode_svg(format_name, message, encoding),
    }


def _pkpass_preview(blob_data: bytes | None) -> dict | None:
    if not blob_data or len(blob_data) > PKPASS_PREVIEW_MAX_ARCHIVE_BYTES:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(blob_data)) as archive:
            total_size = 0
            for info in archive.infolist():
                name = info.filename
                if name.startswith("/") or ".." in Path(name).parts:
                    return None
                total_size += max(0, int(info.file_size))
                if (
                    info.file_size > PKPASS_PREVIEW_MAX_MEMBER_BYTES
                    or total_size > PKPASS_PREVIEW_MAX_TOTAL_BYTES
                ):
                    return None
            pass_info = archive.getinfo("pass.json")
            if pass_info.file_size > PKPASS_PREVIEW_MAX_MEMBER_BYTES:
                return None
            pass_json = json.loads(archive.read(pass_info).decode("utf-8"))
            if not isinstance(pass_json, dict):
                return None
            images = {
                name.split(".", 1)[0].replace("@2x", ""): data_url
                for name in PKPASS_PREVIEW_IMAGE_NAMES
                if (data_url := _pkpass_asset_data_url(archive, name))
            }
            return {
                "organization": str(pass_json.get("organizationName") or "").strip(),
                "description": str(pass_json.get("description") or "").strip(),
                "logo_text": str(pass_json.get("logoText") or "").strip(),
                "serial": str(pass_json.get("serialNumber") or "").strip(),
                "fields": _pkpass_field_rows(pass_json),
                "barcode": _pkpass_barcode(pass_json),
                "images": images,
            }
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile):
        return None


def _json_credential_preview_rows(
    value,
    *,
    prefix: str = "",
    depth: int = 0,
    rows: list[dict[str, str | int | None]] | None = None,
) -> list[dict[str, str | int | None]]:
    if rows is None:
        rows = []
    if len(rows) >= JSON_CREDENTIAL_PREVIEW_MAX_ROWS:
        return rows
    if isinstance(value, dict):
        for key, child in value.items():
            if len(rows) >= JSON_CREDENTIAL_PREVIEW_MAX_ROWS:
                break
            label = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(child, (dict, list)):
                rows.append({"key": label, "value": None, "depth": min(depth, 6)})
                _json_credential_preview_rows(
                    child,
                    prefix=label,
                    depth=depth + 1,
                    rows=rows,
                )
            else:
                rows.append(
                    {
                        "key": label,
                        "value": _json_preview_scalar(child),
                        "depth": min(depth, 6),
                    }
                )
        return rows
    if isinstance(value, list):
        for index, child in enumerate(value):
            if len(rows) >= JSON_CREDENTIAL_PREVIEW_MAX_ROWS:
                break
            label = f"{prefix}[{index}]" if prefix else f"[{index}]"
            if isinstance(child, (dict, list)):
                rows.append({"key": label, "value": None, "depth": min(depth, 6)})
                _json_credential_preview_rows(
                    child,
                    prefix=label,
                    depth=depth + 1,
                    rows=rows,
                )
            else:
                rows.append(
                    {
                        "key": label,
                        "value": _json_preview_scalar(child),
                        "depth": min(depth, 6),
                    }
                )
        return rows
    rows.append(
        {
            "key": prefix or "value",
            "value": _json_preview_scalar(value),
            "depth": min(depth, 6),
        }
    )
    return rows


def _json_preview_scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)[:500]


def _json_credential_label(value: object) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value))
    text = re.sub(r"[_-]+", " ", text).strip()
    return (text[:1].upper() + text[1:])[:120] if text else "Claim"


def _json_credential_display_value(value) -> str:
    if isinstance(value, dict):
        identifier = value.get("id")
        name = value.get("name")
        if name and identifier:
            return f"{_json_preview_scalar(name)} ({_json_preview_scalar(identifier)})"
        if name or identifier:
            return _json_preview_scalar(name or identifier)
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))[:500]
    if isinstance(value, list):
        return ", ".join(_json_preview_scalar(item) for item in value)[:500]
    return _json_preview_scalar(value)


def _json_credential_claim_label(prefix: tuple[str, ...]) -> str:
    if not prefix:
        return "Claim"
    return " ".join(
        [
            prefix[0],
            *(value[:1].lower() + value[1:] for value in prefix[1:]),
        ]
    )


def _json_credential_claim_rows(
    value,
    *,
    prefix: tuple[str, ...] = (),
    rows: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    if rows is None:
        rows = []
    if len(rows) >= JSON_CREDENTIAL_PREVIEW_MAX_CLAIMS:
        return rows
    if isinstance(value, dict):
        for key, child in value.items():
            if len(rows) >= JSON_CREDENTIAL_PREVIEW_MAX_CLAIMS:
                break
            if str(key) == "id":
                continue
            _json_credential_claim_rows(
                child,
                prefix=(*prefix, _json_credential_label(key)),
                rows=rows,
            )
        return rows
    if isinstance(value, list):
        if all(not isinstance(child, (dict, list)) for child in value):
            rows.append({
                "label": _json_credential_claim_label(prefix),
                "value": _json_credential_display_value(value),
            })
            return rows
        for index, child in enumerate(value, start=1):
            if len(rows) >= JSON_CREDENTIAL_PREVIEW_MAX_CLAIMS:
                break
            item_prefix = prefix if len(value) == 1 else (*prefix, str(index))
            _json_credential_claim_rows(child, prefix=item_prefix, rows=rows)
        return rows
    rows.append({
        "label": _json_credential_claim_label(prefix),
        "value": _json_credential_display_value(value),
    })
    return rows


def _json_credential_types(document: dict) -> list[str]:
    raw_types = document.get("type")
    if isinstance(raw_types, list):
        return [str(value) for value in raw_types if str(value).strip()]
    if raw_types is not None and str(raw_types).strip():
        return [str(raw_types)]
    return []


def _json_credential_title(document: dict, *, kind: str) -> str:
    generic_type = "VerifiablePresentation" if kind == "vp" else "VerifiableCredential"
    specific_types = [
        value for value in _json_credential_types(document) if value != generic_type
    ]
    if specific_types:
        return _json_credential_label(specific_types[-1])
    return "W3C Verifiable Presentation" if kind == "vp" else "W3C Verifiable Credential"


def _json_credential_preview(blob_data: bytes | None) -> dict | None:
    if not blob_data or len(blob_data) > JSON_CREDENTIAL_PREVIEW_MAX_BYTES:
        return None
    try:
        document = json.loads(blob_data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    rows = _json_credential_preview_rows(document)
    credential_types = _json_credential_types(document)
    kind = (
        "vp"
        if "VerifiablePresentation" in credential_types
        or "verifiableCredential" in document
        else "vc"
    )
    summary = []

    def add_summary(label: str, value) -> None:
        if value is None or value == "" or value == []:
            return
        summary.append({
            "label": label,
            "value": _json_credential_display_value(value),
        })

    if kind == "vp":
        add_summary("Holder", document.get("holder"))
        credentials = document.get("verifiableCredential")
        if isinstance(credentials, list):
            add_summary("Credentials", len(credentials))
        elif credentials is not None:
            add_summary("Credentials", 1)
    else:
        add_summary("Issuer", document.get("issuer"))
        add_summary("Credential ID", document.get("id"))
        add_summary("Valid from", document.get("validFrom") or document.get("issuanceDate"))
        add_summary("Valid until", document.get("validUntil") or document.get("expirationDate"))
        subjects = document.get("credentialSubject")
        subject_values = subjects if isinstance(subjects, list) else [subjects]
        subject_ids = [
            subject.get("id")
            for subject in subject_values
            if isinstance(subject, dict) and subject.get("id")
        ]
        add_summary("Subject", subject_ids)
    claims_source = document.get("credentialSubject") if kind == "vc" else None
    claims = (
        _json_credential_claim_rows(claims_source)
        if claims_source is not None
        else []
    )
    return {
        "kind": kind,
        "title": _json_credential_title(document, kind=kind),
        "summary": summary,
        "claims": claims,
        "rows": rows,
        "truncated": len(rows) >= JSON_CREDENTIAL_PREVIEW_MAX_ROWS,
        "claims_truncated": len(claims) >= JSON_CREDENTIAL_PREVIEW_MAX_CLAIMS,
    }


def _mdoc_preview_scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, bytes):
        prefix = value[:16].hex()
        suffix = "..." if len(value) > 16 else ""
        return f"bytes({len(value)}) 0x{prefix}{suffix}"
    if isinstance(value, cbor2.CBORTag):
        return f"CBOR tag {value.tag}: {_mdoc_preview_scalar(value.value)}"
    return str(value)[:500]


def _mdoc_embedded_map(value) -> dict | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, cbor2.CBORTag):
        value = value.value
    if not isinstance(value, bytes):
        return None
    try:
        decoded = cbor2.loads(value)
    except (cbor2.CBORDecodeError, ValueError, TypeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _mdoc_display_value(value) -> str:
    if isinstance(value, cbor2.CBORTag):
        return _mdoc_display_value(value.value)
    if isinstance(value, bytes):
        return f"Binary data ({len(value)} bytes)"
    if isinstance(value, dict):
        return ", ".join(
            f"{_mdoc_preview_scalar(key)}: {_mdoc_display_value(child)}"
            for key, child in value.items()
        )[:500]
    if isinstance(value, (list, tuple)):
        return ", ".join(_mdoc_display_value(child) for child in value)[:500]
    return _mdoc_preview_scalar(value)


def _mdoc_preview_fields(
    first_document: dict | None,
    namespace: str,
    labels: dict[str, str],
) -> list[dict[str, str]]:
    if not isinstance(first_document, dict):
        return []
    issuer_signed = first_document.get("issuerSigned")
    if not isinstance(issuer_signed, dict):
        return []
    namespaces = issuer_signed.get("nameSpaces")
    if not isinstance(namespaces, dict):
        return []
    elements = namespaces.get(namespace)
    if not isinstance(elements, (list, tuple)):
        return []

    fields = []
    for encoded_element in elements:
        element = _mdoc_embedded_map(encoded_element)
        if element is None:
            continue
        identifier = str(element.get("elementIdentifier") or "").strip()
        if not identifier or "elementValue" not in element:
            continue
        fields.append(
            {
                "identifier": identifier,
                "label": labels.get(
                    identifier, identifier.replace("_", " ").capitalize()
                ),
                "value": _mdoc_display_value(element["elementValue"]),
            }
        )
    return fields


def _mdoc_preview_rows(
    value,
    *,
    prefix: str = "",
    depth: int = 0,
    rows: list[dict[str, str | int | None]] | None = None,
) -> list[dict[str, str | int | None]]:
    if rows is None:
        rows = []
    if len(rows) >= MDOC_PREVIEW_MAX_ROWS:
        return rows
    if isinstance(value, cbor2.CBORTag):
        label = prefix or f"tag({value.tag})"
        rows.append(
            {
                "key": f"{label} tag",
                "value": str(value.tag),
                "depth": min(depth, 6),
            }
        )
        return _mdoc_preview_rows(
            value.value,
            prefix=f"{label}.value",
            depth=depth + 1,
            rows=rows,
        )
    if isinstance(value, dict):
        for key, child in value.items():
            if len(rows) >= MDOC_PREVIEW_MAX_ROWS:
                break
            key_label = _mdoc_preview_scalar(key)
            label = f"{prefix}.{key_label}" if prefix else key_label
            if isinstance(child, (dict, list, tuple, cbor2.CBORTag)):
                rows.append({"key": label, "value": None, "depth": min(depth, 6)})
                _mdoc_preview_rows(
                    child,
                    prefix=label,
                    depth=depth + 1,
                    rows=rows,
                )
            else:
                rows.append(
                    {
                        "key": label,
                        "value": _mdoc_preview_scalar(child),
                        "depth": min(depth, 6),
                    }
                )
        return rows
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            if len(rows) >= MDOC_PREVIEW_MAX_ROWS:
                break
            label = f"{prefix}[{index}]" if prefix else f"[{index}]"
            if isinstance(child, (dict, list, tuple, cbor2.CBORTag)):
                rows.append({"key": label, "value": None, "depth": min(depth, 6)})
                _mdoc_preview_rows(
                    child,
                    prefix=label,
                    depth=depth + 1,
                    rows=rows,
                )
            else:
                rows.append(
                    {
                        "key": label,
                        "value": _mdoc_preview_scalar(child),
                        "depth": min(depth, 6),
                    }
                )
        return rows
    rows.append(
        {
            "key": prefix or "value",
            "value": _mdoc_preview_scalar(value),
            "depth": min(depth, 6),
        }
    )
    return rows


def _mdoc_preview(blob_data: bytes | None) -> dict | None:
    if not blob_data or len(blob_data) > MDOC_PREVIEW_MAX_BYTES:
        return None
    try:
        document = cbor2.loads(blob_data)
    except (cbor2.CBORDecodeError, ValueError, TypeError):
        return None
    documents = document.get("documents") if isinstance(document, dict) else None
    document_count = len(documents) if isinstance(documents, list) else None
    first_document = (
        documents[0]
        if isinstance(documents, list) and documents and isinstance(documents[0], dict)
        else None
    )
    doc_type = (
        str(first_document.get("docType") or "")
        if isinstance(first_document, dict)
        else ""
    )
    status = document.get("status") if isinstance(document, dict) else None
    rows = _mdoc_preview_rows(document)
    field_labels = (
        EUDI_PID_FIELD_LABELS
        if doc_type == EUDI_PID_DOC_TYPE
        else MDL_FIELD_LABELS
        if doc_type == MDL_DOC_TYPE
        else None
    )
    field_namespace = (
        EUDI_PID_DOC_TYPE
        if doc_type == EUDI_PID_DOC_TYPE
        else MDL_NAMESPACE
        if doc_type == MDL_DOC_TYPE
        else None
    )
    return {
        "doc_type": doc_type,
        "credential_kind": (
            "eudi_pid"
            if doc_type == EUDI_PID_DOC_TYPE
            else "mdl"
            if doc_type == MDL_DOC_TYPE
            else "mdoc"
        ),
        "document_count": document_count,
        "status": _mdoc_preview_scalar(status) if status is not None else None,
        "credential_fields": (
            _mdoc_preview_fields(first_document, field_namespace, field_labels)
            if field_namespace is not None and field_labels is not None
            else []
        ),
        "rows": rows,
        "truncated": len(rows) >= MDOC_PREVIEW_MAX_ROWS,
    }


def _callable_accepts_keyword(func, keyword: str) -> bool:
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return True
    return keyword in signature.parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )


async def _put_acorn_record(acorn: Acorn, **kwargs):
    if (
        "blob_type" in kwargs
        and not _callable_accepts_keyword(acorn.put_record, "blob_type")
    ):
        if kwargs.get("blob_type") is not None:
            logger.warning(
                "Acorn put_record does not support blob_type; effective MIME metadata was not stored."
            )
        kwargs = dict(kwargs)
        kwargs.pop("blob_type", None)
    return await acorn.put_record(**kwargs)


def _blob_preview_kind(media_type: str | None) -> str | None:
    """Return a narrowly allowlisted browser-native preview type."""

    normalized = str(media_type or "").split(";", 1)[0].strip().lower()
    if normalized in INLINE_BLOB_IMAGE_TYPES:
        return "image"
    if normalized == "application/pdf":
        return "pdf"
    return None


def _blob_recognition_fingerprint(digest: str | None) -> str | None:
    """Return an eight-character display fingerprint for a plaintext SHA-256."""

    normalized = str(digest or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{64}", normalized) is None:
        return None
    return normalized[:8].upper()


def _blob_download_headers(
    label: str,
    media_type: str | None,
    *,
    inline: bool = False,
) -> dict[str, str]:
    """Return an injection-safe filename with UTF-8 label support."""

    normalized_media_type = _normalize_media_type(media_type)
    extension = (
        EFFECTIVE_MIME_DOWNLOAD_EXTENSIONS.get(normalized_media_type or "")
        or mimetypes.guess_extension(normalized_media_type or "")
        or ".bin"
    )
    filename = label if label.lower().endswith(extension.lower()) else label + extension
    fallback_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("._") or "acorn-blob"
    fallback = (
        fallback_stem
        if fallback_stem.lower().endswith(extension.lower())
        else fallback_stem + extension
    )
    disposition = "inline" if inline else "attachment"
    return {
        "Content-Disposition": (
            f'{disposition}; filename="{fallback[:180]}"; '
            f"filename*=UTF-8''{quote(filename, safe='')}"
        )
    }


HANDLE_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")


def _normalize_handle(value: str) -> str:
    """Normalize a NIP-05 local name to a conservative URL-safe form."""

    handle = str(value or "").strip().lower()
    if (
        not HANDLE_PATTERN.fullmatch(handle)
        or ".." in handle
        or handle == "_"
    ):
        raise ValueError(
            "Use 1–64 lowercase letters, numbers, dots, underscores, or hyphens. "
            "The handle must begin and end with a letter or number."
        )
    return handle


def _assign_default_handle(
    session,
    *,
    npub: str,
    pubkey_hex: str,
    home_relay: str,
) -> str | None:
    """Claim a deterministic onboarding handle without replacing another user."""

    existing = session.exec(
        select(ClaimedHandle).where(ClaimedHandle.npub == npub)
    ).first()
    if existing is not None:
        existing.home_relay = home_relay
        session.add(existing)
        session.commit()
        return existing.claimed_handle

    for attempt in range(1000):
        candidate = default_handle_from_pubkey(pubkey_hex, attempt=attempt)
        claimed = session.exec(
            select(ClaimedHandle).where(
                ClaimedHandle.claimed_handle == candidate
            )
        ).first()
        if claimed is not None:
            continue
        registration = ClaimedHandle(
            claimed_handle=candidate,
            npub=npub,
            home_relay=home_relay,
        )
        try:
            session.add(registration)
            session.commit()
        except IntegrityError:
            # A concurrent onboarding request may have claimed this candidate.
            session.rollback()
            concurrent = session.exec(
                select(ClaimedHandle).where(ClaimedHandle.npub == npub)
            ).first()
            if concurrent is not None:
                return concurrent.claimed_handle
            continue
        return candidate
    return None


def _handle_form(
    csrf_token: str,
    existing: ClaimedHandle | None = None,
    hostname: str | None = None,
    error: str | None = None,
) -> str:
    host = hostname or "this service"
    return render_template(
        "handle.html",
        title="NIP-05 handle" if existing is not None else "Claim a NIP-05 handle",
        csrf_token=csrf_token,
        existing=existing,
        host=host,
        error=error,
    )


def _receive_funds_form(
    balance: int,
    home_mint: str,
    csrf_token: str,
    error: str | None = None,
    balance_status: str | None = None,
) -> str:
    if balance_status is None:
        balance_status = (
            f"<p>Relay-visible proof total: <strong>{int(balance):,} sats</strong></p>"
        )
    return render_template(
        "receive_funds.html",
        title="Receive Funds",
        home_mint=home_mint,
        csrf_token=csrf_token,
        error=error,
        balance_status=balance_status,
    )


def _qr_svg(payload: str, *, include_acorn: bool = False) -> str:
    """Render a high-contrast QR, optionally with a protected Acorn centre mark."""

    qr = qrcode.QRCode(
        version=None,
        error_correction=(
            qrcode.constants.ERROR_CORRECT_H
            if include_acorn
            else qrcode.constants.ERROR_CORRECT_M
        ),
        box_size=8,
        # ISO/IEC 18004 specifies a four-module quiet zone. Record capability
        # descriptors are relatively dense, so the full quiet zone materially
        # improves camera acquisition on small mobile screens.
        border=4,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    svg = image.to_string(encoding="unicode")
    svg = svg.replace("<svg ", '<svg class="qr-code" ', 1)
    svg = svg.replace(
        "><path",
        '><rect id="qr-background" width="100%" height="100%" fill="#ffffff" />'
        '<path shape-rendering="crispEdges"',
        1,
    )

    if not include_acorn:
        return svg

    view_box = re.search(
        r'viewBox="0 0 ([0-9.]+) ([0-9.]+)"',
        svg,
    )
    if view_box is None:
        raise RuntimeError("generated QR SVG does not contain a usable viewBox")

    width = float(view_box.group(1))
    height = float(view_box.group(2))
    extent = min(width, height)
    backing_size = extent * 0.21
    logo_size = extent * 0.15
    backing_x = (width - backing_size) / 2
    backing_y = (height - backing_size) / 2
    logo_x = (width - logo_size) / 2
    logo_y = (height - logo_size) / 2
    logo_scale = logo_size / 88
    radius = backing_size * 0.16

    acorn_mark = f"""
<g id="acorn-qr-mark" aria-hidden="true">
  <rect x="{backing_x:.4f}" y="{backing_y:.4f}"
        width="{backing_size:.4f}" height="{backing_size:.4f}"
        rx="{radius:.4f}" fill="#ffffff" />
  <g transform="translate({logo_x:.4f} {logo_y:.4f}) scale({logo_scale:.6f})">
    <path fill="#000000" d="M18 34c0-14 12-25 26-25s26 11 26 25H18Z"/>
    <path fill="#000000" d="M42 12 48 0l10 5-7 12Z"/>
    <path fill="#000000" d="M20 38h48c0 25-9 39-24 50C29 77 20 63 20 38Z"/>
    <path d="M25 42h15v11h13v12H42v17" fill="none"
          stroke="#ffffff" stroke-width="5" stroke-linejoin="round"/>
  </g>
</g>"""
    return svg.replace("</svg>", f"{acorn_mark}</svg>", 1)


def _invoice_svg(invoice: str) -> str:
    return _qr_svg(invoice)


def _onboard_invite_code(settings: Settings, value: str) -> str | None:
    folded = str(value or "").strip().casefold()
    for invite_code in settings.onboard_invite_codes:
        if invite_code.casefold() == folded:
            return invite_code
    return None


def _onboard_path(settings: Settings, invite_code: str | None = None) -> str:
    code = settings.onboard_invite_code if invite_code is None else invite_code
    return f"/onboard/{quote(code, safe='')}"


async def _load_new_acorn_with_retry(
    acorn: Acorn,
    *,
    timeout: float,
    attempts: int = 3,
) -> None:
    """Verify a newly created Acorn, allowing transient relay readback misses."""

    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            await asyncio.wait_for(acorn.load_data(), timeout=timeout)
            return
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            logger.info(
                "new acorn relay readback retrying attempt=%s max_attempts=%s error_type=%s",
                attempt,
                attempts,
                type(exc).__name__,
            )
            await asyncio.sleep(0.25)
    if last_error is not None:
        raise last_error


async def _store_deferred_recovery_with_retry(
    acorn: Acorn,
    *,
    timeout: float,
    attempts: int = 3,
) -> dict:
    """Store deferred recovery, allowing transient relay verification misses."""

    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            deferred_state = await asyncio.wait_for(
                acorn.store_deferred_recovery(),
                timeout=timeout,
            )
            if not (
                deferred_state.get("pending")
                and deferred_state.get("verified")
            ):
                raise RuntimeError("deferred recovery readback was not verified")
            return deferred_state
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            logger.info(
                "deferred recovery relay readback retrying attempt=%s max_attempts=%s error_type=%s",
                attempt,
                attempts,
                type(exc).__name__,
            )
            await asyncio.sleep(0.25)
    if last_error is not None:
        raise last_error
    raise RuntimeError("deferred recovery readback was not verified")


def _lightning_payment_request_page(
    state: DepositQuoteState,
    state_token: str,
    csrf_token: str,
    message: str | None = None,
) -> str:
    return render_template(
        "receive_funds_request.html",
        title="Lightning Payment Request",
        state=state,
        state_token=state_token,
        csrf_token=csrf_token,
        message=message,
        invoice_svg=_invoice_svg(state.invoice),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or Settings.from_env()
    static_directory = Path(__file__).resolve().parent / "static"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        run_migrations(runtime_settings.database_url)
        app.state.database_engine = create_database_engine(
            runtime_settings.database_url
        )
        app.state.worker_id = new_worker_id()
        (
            app.state.worker_heartbeat_stop,
            app.state.worker_heartbeat_thread,
        ) = start_worker_heartbeat(
            app.state.database_engine,
            app.state.worker_id,
        )
        app.state.finalization_tasks = {}
        app.state.clear_acceptance_tasks = {}
        try:
            yield
        finally:
            tasks = [
                *app.state.finalization_tasks.values(),
                *app.state.clear_acceptance_tasks.values(),
            ]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            stop_worker_heartbeat(
                app.state.database_engine,
                app.state.worker_id,
                app.state.worker_heartbeat_stop,
                app.state.worker_heartbeat_thread,
            )
            app.state.database_engine.dispose()

    app = FastAPI(title="Safebox Web", version="0.1.0", lifespan=lifespan)
    app.state.settings = runtime_settings
    app.state.clear_mint_metadata_cache = {}
    app.include_router(lnurl_pay_router)
    app.mount(
        "/static",
        StaticFiles(directory=static_directory),
        name="static",
    )

    @app.exception_handler(HTTPException)
    async def browser_session_error(request: Request, exc: HTTPException):
        session_errors = {
            "Acorn connection required",
            "Acorn connection is invalid or expired",
        }
        accepts_html = "text/html" in request.headers.get("accept", "").lower()
        if (
            exc.status_code == 401
            and str(exc.detail) in session_errors
            and request.method in {"GET", "HEAD"}
            and accepts_html
        ):
            return RedirectResponse("/", status_code=303)
        if (
            exc.status_code == 502
            and str(exc.detail)
            == "Unable to load the Acorn wallet from its bootstrap relay"
            and request.method in {"GET", "HEAD"}
            and accepts_html
        ):
            return HTMLResponse(
                render_template(
                    "disconnect.html",
                    title="Unable to Load Acorn",
                    message=(
                        "Safebox could not find or load wallet state for this "
                        "key and bootstrap relay. The recovery material or relay "
                        "may be incorrect, or the relay may be unavailable."
                    ),
                    csrf_token=CsrfProtector(request.app.state.settings).issue(),
                    show_page_navigation=False,
                ),
                status_code=502,
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=exc.headers,
        )

    @app.middleware("http")
    async def security_boundary(request: Request, call_next):
        if not is_allowed_transport(request):
            return JSONResponse(
                status_code=400,
                content={
                    "detail": (
                        "HTTPS is required. Plain HTTP is allowed only for direct "
                        "development access at http://127.0.0.1:<port>."
                    )
                },
            )

        origin = request.headers.get("origin")
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and origin
            and origin.lower() != "null"
        ):
            if not is_same_origin(request, origin):
                request_origin = f"{request.url.scheme}://{request.url.netloc}"
                logger.warning(
                    "origin rejected received=%r request_origin=%r client=%r",
                    origin,
                    request_origin,
                    request.client.host if request.client else None,
                )
                return JSONResponse(status_code=403, content={"detail": "Origin rejected"})

        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        content_security_policy = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        if request.url.path == "/record":
            content_security_policy += "; img-src 'self' data:"
        if request.url.path == "/scan/lightning":
            # qr-scanner uses a same-origin module that creates its decoder
            # worker from a blob URL when the browser's native BarcodeDetector
            # is unavailable or unsuitable.
            content_security_policy += "; worker-src 'self' blob:"
            content_security_policy += "; img-src 'self' data:; connect-src 'self' data:"
        response.headers["Content-Security-Policy"] = content_security_policy
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if (
            request.url.path == "/record/blob"
            and response.headers.get("Content-Disposition", "").startswith("inline;")
        ):
            # Only allow the narrowly typed decrypted image/PDF response to be
            # embedded by this application. Other pages remain non-frameable.
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; frame-ancestors 'self'; sandbox"
            )
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.get("/health", response_class=JSONResponse)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/rates", response_class=HTMLResponse)
    async def public_rates(
        request: Request,
        session: DatabaseSessionDependency,
    ) -> HTMLResponse:
        """Show cached informational rates without requiring an Acorn session."""

        settings = request.app.state.settings
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        stale_after = timedelta(seconds=settings.currency_rate_stale_seconds)
        rows = session.exec(
            select(CurrencyRate).order_by(CurrencyRate.currency_code)
        ).all()
        rates = [
            {
                "currency_code": row.currency_code,
                "currency_symbol": row.currency_symbol,
                "currency_description": row.currency_description,
                "fiat_per_btc": row.fiat_per_btc,
                "fetched_at": row.fetched_at,
                "source": row.source,
                "stale": now - row.fetched_at > stale_after,
            }
            for row in rows
        ]
        return HTMLResponse(
            render_template(
                "rates.html",
                title="Exchange Rates",
                rates=rates,
                rates_enabled=settings.currency_rates_enabled,
            )
        )

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        return FileResponse(static_directory / "favicon.ico", media_type="image/x-icon")

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        settings = request.app.state.settings
        session_token = request.cookies.get(cookie_name_for_request(request))
        if session_token:
            try:
                SessionCipher(settings).decode(session_token)
            except ValueError:
                pass
            else:
                return RedirectResponse("/wallet", status_code=303)
        response = HTMLResponse(
            _connect_form(
                settings.default_bootstrap_relay,
                CsrfProtector(settings).issue(),
                onboard_path=_onboard_path(settings),
                show_page_navigation=False,
            )
        )
        if session_token:
            response.delete_cookie(
                SECURE_COOKIE_NAME,
                path="/",
                secure=True,
                httponly=True,
            )
            response.delete_cookie(LOOPBACK_COOKIE_NAME, path="/", httponly=True)
        return response

    @app.get("/onboard", response_class=HTMLResponse)
    async def onboard_default(request: Request):
        """Redirect to the configured invite-code onboarding entry point."""

        settings = request.app.state.settings
        return RedirectResponse(_onboard_path(settings), status_code=303)

    @app.get("/invite", response_class=HTMLResponse)
    async def onboard_invite(
        request: Request,
        credentials: CredentialsDependency,
    ) -> HTMLResponse:
        """Present the configured onboarding entry point as a scannable QR code."""

        _ = credentials
        settings = request.app.state.settings
        onboarding_url = str(
            request.url_for(
                "onboard",
                invite_code=settings.onboard_invite_code,
            )
        )
        return HTMLResponse(
            render_template(
                "onboard_invite.html",
                title="Invite",
                onboarding_url=onboarding_url,
                onboarding_qr=_qr_svg(onboarding_url, include_acorn=True),
            ),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/onboard/{invite_code}", response_class=HTMLResponse)
    async def onboard(request: Request, invite_code: str):
        """Provide the invite-code entry point for creating a new Acorn."""

        settings = request.app.state.settings
        canonical_invite_code = _onboard_invite_code(settings, invite_code)
        if canonical_invite_code is None:
            return HTMLResponse("Invite code not found.", status_code=404)
        session_token = request.cookies.get(cookie_name_for_request(request))
        if session_token:
            try:
                SessionCipher(settings).decode(session_token)
            except ValueError:
                pass
            else:
                return RedirectResponse("/wallet", status_code=303)
        return render_template(
            "onboard.html",
            title="Welcome to Safebox",
            csrf_token=CsrfProtector(settings).issue(),
            onboard_path=_onboard_path(settings, canonical_invite_code),
            default_relay=settings.default_bootstrap_relay,
            default_mint=settings.default_home_mint,
        )

    @app.get("/connect", response_class=HTMLResponse)
    async def connect_form(request: Request) -> str:
        settings = request.app.state.settings
        return _connect_form(
            settings.default_bootstrap_relay,
            CsrfProtector(settings).issue(),
            onboard_path=_onboard_path(settings),
        )

    @app.get("/create", response_class=HTMLResponse)
    async def create_form(request: Request) -> str:
        settings = request.app.state.settings
        return _create_form(
            settings.default_bootstrap_relay,
            settings.default_home_mint,
            CsrfProtector(settings).issue(),
        )

    @app.post("/create", response_class=HTMLResponse)
    async def create_acorn(
        request: Request,
        csrf_token: str = Form(...),
        home_relay: str = Form(...),
        home_mint: str = Form(...),
        mnemonic_words: str = Form("24"),
        use_external_entropy: str | None = Form(None),
        entropy_hex: str = Form(""),
        entropy_confirmation: str = Form(""),
        defer_recovery: str | None = Form(None),
        assign_default_handle: str | None = Form(None),
        confirmed: str | None = Form(None),
    ):
        settings = request.app.state.settings
        form_token = CsrfProtector(settings)

        def creation_error(message: str, status_code: int = 400) -> HTMLResponse:
            if getattr(request.state, "streamlined_onboarding", False):
                return HTMLResponse(
                    render_template(
                        "onboard.html",
                        title="Welcome to Safebox",
                        csrf_token=form_token.issue(),
                        onboard_path=_onboard_path(settings),
                        default_relay=settings.default_bootstrap_relay,
                        default_mint=settings.default_home_mint,
                        mnemonic_words=mnemonic_words,
                        error=message,
                    ),
                    status_code=status_code,
                )
            return HTMLResponse(
                _create_form(
                    settings.default_bootstrap_relay,
                    settings.default_home_mint,
                    form_token.issue(),
                    message,
                    mnemonic_words=mnemonic_words,
                    use_external_entropy=(use_external_entropy == "yes"),
                    defer_recovery=(defer_recovery == "yes"),
                ),
                status_code=status_code,
            )

        if not form_token.verify(csrf_token):
            return creation_error(
                "The form token is invalid or expired. Review the choices again.",
                403,
            )
        if confirmed != "yes":
            return creation_error("Explicit confirmation is required.")

        uses_external_entropy = use_external_entropy == "yes"
        supplied_entropy = (
            str(entropy_hex).strip() if uses_external_entropy else ""
        )
        repeated_entropy = (
            str(entropy_confirmation).strip() if uses_external_entropy else ""
        )
        if uses_external_entropy:
            if not supplied_entropy or not repeated_entropy:
                return creation_error(
                    "Enter the external entropy in both fields."
                )
            if supplied_entropy != repeated_entropy:
                return creation_error(
                    "The external entropy values do not match."
                )
            try:
                seed_phrase, generated_nsec = seed_phrase_and_nsec_from_entropy(
                    supplied_entropy
                )
            except ValueError as exc:
                return creation_error(f"Invalid external entropy: {exc}")
        else:
            if mnemonic_words not in {"12", "24"}:
                return creation_error(
                    "Choose a 12- or 24-word Safebox Acorn mnemonic."
                )
            mnemonic_strength = 128 if mnemonic_words == "12" else 256
            seed_phrase, generated_nsec = generate_seed_phrase_and_nsec(
                strength=mnemonic_strength
            )

        try:
            normalized_relay = normalize_bootstrap_relay(
                home_relay,
                settings.allowed_ws_relays,
            )
            normalized_mint = normalize_home_mint(home_mint)
        except ValueError as exc:
            return creation_error(str(exc))
        acorn = Acorn(
            nsec=generated_nsec,
            home_relay=normalized_relay,
            relays=[normalized_relay],
            mints=[normalized_mint],
        )
        try:
            await asyncio.wait_for(
                acorn.create_instance(
                    seed_phrase=seed_phrase,
                    retain_seed_phrase=False,
                ),
                timeout=settings.wallet_load_timeout_seconds,
            )
            await _load_new_acorn_with_retry(
                acorn,
                timeout=settings.wallet_load_timeout_seconds,
            )
        except TimeoutError:
            logger.warning("acorn creation timed out relay=%s", normalized_relay)
            return creation_error(
                "Wallet initialization timed out before relay readback was verified. "
                "No session was started; try a different relay.",
                504,
            )
        except Exception as exc:
            logger.warning(
                "acorn creation failed relay=%s error_type=%s",
                normalized_relay,
                type(exc).__name__,
            )
            return creation_error(
                "Safebox could not initialize and verify the new Acorn on that "
                "relay. No session was started; try a different relay.",
                502,
            )

        assigned_handle = None
        handle_assignment_requested = assign_default_handle == "yes"
        if handle_assignment_requested:
            try:
                with Session(request.app.state.database_engine) as session:
                    assigned_handle = _assign_default_handle(
                        session,
                        npub=acorn.pubkey_bech32,
                        pubkey_hex=acorn.pubkey_hex,
                        home_relay=normalized_relay,
                    )
            except Exception as exc:
                logger.warning(
                    "default handle assignment failed npub=%s error_type=%s error=%s",
                    acorn.pubkey_bech32,
                    type(exc).__name__,
                    exc,
                )
            else:
                if assigned_handle is None:
                    logger.warning(
                        "default handle namespace exhausted npub=%s",
                        acorn.pubkey_bech32,
                    )
                else:
                    logger.info(
                        "default handle assigned handle=%s npub=%s",
                        assigned_handle,
                        acorn.pubkey_bech32,
                    )

        credentials = credentials_from_connection(
            secret_type="nsec",
            secret=generated_nsec,
            bootstrap_relay=normalized_relay,
            deferred_acorn_mnemonic=(
                seed_phrase if defer_recovery == "yes" else None
            ),
            allowed_ws_relays=settings.allowed_ws_relays,
        )
        deferred_recovery_error = None
        if defer_recovery == "yes":
            try:
                await _store_deferred_recovery_with_retry(
                    acorn,
                    timeout=settings.wallet_load_timeout_seconds,
                )
            except TimeoutError:
                logger.warning(
                    "deferred recovery storage timed out relay=%s",
                    normalized_relay,
                )
                deferred_recovery_error = (
                    "Safebox could not verify temporary recovery storage. "
                    "Save the recovery material on this page before continuing."
                )
            except Exception as exc:
                logger.warning(
                    "deferred recovery storage failed relay=%s error_type=%s",
                    normalized_relay,
                    type(exc).__name__,
                )
                deferred_recovery_error = (
                    "Safebox could not safely defer recovery. Save the recovery "
                    "material on this page before continuing."
                )
            else:
                wallet_location = (
                    "/wallet"
                    if not handle_assignment_requested or assigned_handle is not None
                    else "/wallet?handle_assignment=failed"
                )
                response = RedirectResponse(wallet_location, status_code=303)
                set_session_cookie(
                    response,
                    request=request,
                    settings=settings,
                    credentials=credentials,
                )
                return response
        response = HTMLResponse(
            render_template(
                "new_acorn.html",
                title="New Acorn created",
                seed_phrase=seed_phrase,
                nsec=generated_nsec,
                npub=acorn.pubkey_bech32,
                home_relay=normalized_relay,
                home_mint=normalized_mint,
                safekeeping_message=_acorn_safekeeping_message(
                    acorn_mnemonic=seed_phrase,
                    npub=acorn.pubkey_bech32,
                    home_relay=normalized_relay,
                    home_mint=normalized_mint,
                ),
                recovery_csrf_token=CsrfProtector(settings).issue(),
                deferred_recovery=False,
                assigned_handle=assigned_handle,
                handle_assignment_failed=(
                    handle_assignment_requested and assigned_handle is None
                ),
                error=deferred_recovery_error,
            ),
            status_code=201,
        )
        set_session_cookie(
            response,
            request=request,
            settings=settings,
            credentials=credentials,
        )
        return response

    @app.post("/onboard", response_class=HTMLResponse)
    async def create_default_streamlined_onboarded_acorn(
        request: Request,
        csrf_token: str = Form(...),
        mnemonic_words: str = Form("24"),
    ):
        """Accept legacy onboarding posts through the configured invite code."""

        settings = request.app.state.settings
        return await create_streamlined_onboarded_acorn(
            request=request,
            invite_code=settings.onboard_invite_code,
            csrf_token=csrf_token,
            mnemonic_words=mnemonic_words,
        )

    @app.post("/onboard/{invite_code}", response_class=HTMLResponse)
    async def create_streamlined_onboarded_acorn(
        request: Request,
        invite_code: str,
        csrf_token: str = Form(...),
        mnemonic_words: str = Form("24"),
    ):
        """Create an Acorn using server-selected onboarding defaults."""

        settings = request.app.state.settings
        canonical_invite_code = _onboard_invite_code(settings, invite_code)
        if canonical_invite_code is None:
            return HTMLResponse("Invite code not found.", status_code=404)
        request.state.streamlined_onboarding = True
        return await create_acorn(
            request=request,
            csrf_token=csrf_token,
            home_relay=settings.default_bootstrap_relay,
            home_mint=settings.default_home_mint,
            mnemonic_words=mnemonic_words,
            use_external_entropy=None,
            entropy_hex="",
            entropy_confirmation="",
            defer_recovery="yes",
            assign_default_handle="yes",
            confirmed="yes",
        )

    @app.post("/connect")
    async def connect(
        request: Request,
        csrf_token: str = Form(...),
        secret_type: str = Form(...),
        secret: str = Form(...),
        bootstrap_relay: str = Form(...),
        record_protection_recovery: str = Form(""),
        record_protection_entropy: str = Form(""),
    ):
        settings = request.app.state.settings
        if not CsrfProtector(settings).verify(csrf_token):
            return HTMLResponse(
                _connect_form(
                    settings.default_bootstrap_relay,
                    CsrfProtector(settings).issue(),
                    "The form token is invalid or expired. Reload and try again.",
                    onboard_path=_onboard_path(settings),
                ),
                status_code=403,
            )
        try:
            recovery_phrase = str(record_protection_recovery).strip()
            recovery_entropy = str(record_protection_entropy).strip()
            if recovery_phrase and recovery_entropy:
                raise ValueError(
                    "enter either the protected-record recovery phrase or "
                    "external entropy, not both"
                )
            if recovery_phrase:
                record_protection_key = (
                    record_protection_key_from_recovery_phrase(recovery_phrase)
                )
            elif recovery_entropy:
                record_protection_key = record_protection_key_from_entropy(
                    recovery_entropy
                )
            else:
                record_protection_key = None
            credentials = credentials_from_connection(
                secret_type=secret_type,
                secret=secret,
                bootstrap_relay=bootstrap_relay,
                record_protection_key=record_protection_key,
                record_protection_backup_confirmed=(
                    record_protection_key is not None
                ),
                allowed_ws_relays=settings.allowed_ws_relays,
            )
        except ValueError as exc:
            return HTMLResponse(
                _connect_form(
                    settings.default_bootstrap_relay,
                    CsrfProtector(settings).issue(),
                    str(exc),
                    onboard_path=_onboard_path(settings),
                ),
                status_code=400,
            )

        response = RedirectResponse("/wallet", status_code=303)
        set_session_cookie(
            response,
            request=request,
            settings=settings,
            credentials=credentials,
        )
        return response

    @app.get("/recovery", response_class=HTMLResponse)
    async def deferred_recovery_warning(
        request: Request,
        credentials: CredentialsDependency,
        acorn: LoadedAcornDependency,
    ) -> HTMLResponse:
        settings = request.app.state.settings
        try:
            state = await asyncio.wait_for(
                acorn.get_deferred_recovery_status(),
                timeout=settings.wallet_load_timeout_seconds,
            )
        except Exception as exc:
            logger.warning(
                "deferred recovery status failed error_type=%s",
                type(exc).__name__,
            )
            return HTMLResponse(
                _page(
                    "Recovery status unavailable",
                    "<p>Safebox could not read the deferred recovery state. "
                    "Try again before disconnecting this Acorn.</p>"
                    '<p><a class="nav-button" href="/wallet">Return to wallet</a></p>',
                ),
                status_code=502,
                headers={"Cache-Control": "no-store"},
            )
        if not state.get("pending"):
            return HTMLResponse(
                _page(
                    "Recovery backup is not pending",
                    "<p>This Acorn does not have a pending temporary recovery bundle.</p>"
                    '<p><a class="nav-button" href="/wallet">Return to wallet</a></p>',
                ),
                headers={"Cache-Control": "no-store"},
            )
        if credentials.deferred_acorn_mnemonic is None:
            return HTMLResponse(
                _page(
                    "Recovery material unavailable",
                    "<p>The relay reports a pending backup, but this browser no "
                    "longer has the temporary Acorn mnemonic. Do not disconnect; "
                    "export and protect the current nsec from a trusted interface.</p>"
                    '<p><a class="nav-button" href="/wallet">Return to wallet</a></p>',
                ),
                status_code=409,
                headers={"Cache-Control": "no-store"},
            )
        return HTMLResponse(
            render_template(
                "deferred_recovery_warning.html",
                title="Complete Recovery Backup",
                csrf_token=CsrfProtector(settings).issue(),
            ),
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/recovery/display", response_class=HTMLResponse)
    async def display_deferred_recovery(
        request: Request,
        credentials: CredentialsDependency,
        acorn: LoadedAcornDependency,
        csrf_token: str = Form(...),
        confirmed: str | None = Form(None),
    ) -> HTMLResponse:
        settings = request.app.state.settings
        if not CsrfProtector(settings).verify(csrf_token) or confirmed != "yes":
            return HTMLResponse(
                _page(
                    "Recovery material not displayed",
                    "<p>Valid confirmation is required.</p>"
                    '<p><a class="nav-button" href="/recovery">Return</a></p>',
                ),
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        if credentials.deferred_acorn_mnemonic is None:
            return HTMLResponse(
                _page(
                    "Recovery material unavailable",
                    "<p>This browser session no longer contains the temporary "
                    "Safebox Acorn mnemonic.</p>"
                    '<p><a class="nav-button" href="/wallet">Return to wallet</a></p>',
                ),
                status_code=409,
                headers={"Cache-Control": "no-store"},
            )
        return HTMLResponse(
            render_template(
                "deferred_recovery.html",
                title="Recovery Material",
                safekeeping_message=_acorn_safekeeping_message(
                    acorn_mnemonic=credentials.deferred_acorn_mnemonic,
                    npub=acorn.pubkey_bech32,
                    home_relay=acorn.home_relay,
                    home_mint=acorn.home_mint,
                ),
                csrf_token=CsrfProtector(settings).issue(),
            ),
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/recovery/confirm")
    async def confirm_deferred_recovery(
        request: Request,
        credentials: CredentialsDependency,
        acorn: LoadedAcornDependency,
        csrf_token: str = Form(...),
        confirmed: str | None = Form(None),
    ):
        settings = request.app.state.settings
        if not CsrfProtector(settings).verify(csrf_token) or confirmed != "yes":
            return HTMLResponse(
                _page(
                    "Recovery backup not confirmed",
                    "<p>Valid confirmation is required.</p>"
                    '<p><a class="nav-button" href="/recovery">Return</a></p>',
                ),
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        try:
            if credentials.deferred_acorn_mnemonic is None:
                raise ValueError("the deferred Acorn mnemonic is unavailable")
            result = await asyncio.wait_for(
                acorn.complete_deferred_recovery(),
                timeout=settings.payment_timeout_seconds,
            )
            if not result.get("completed"):
                raise RuntimeError("recovery cleanup was not completed")
        except Exception as exc:
            logger.warning(
                "deferred recovery completion failed error_type=%s",
                type(exc).__name__,
            )
            return HTMLResponse(
                _page(
                    "Recovery backup remains pending",
                    "<p>Safebox could not verify removal of the current recovery "
                    "material. The warning will remain; try again before "
                    "disconnecting.</p>"
                    '<p><a class="nav-button" href="/recovery">Return</a></p>',
                ),
                status_code=502,
                headers={"Cache-Control": "no-store"},
            )
        updated_credentials = replace(
            credentials,
            deferred_acorn_mnemonic=None,
        )
        response = RedirectResponse("/wallet", status_code=303)
        set_session_cookie(
            response,
            request=request,
            settings=settings,
            credentials=updated_credentials,
        )
        return response

    @app.post("/recovery/confirm-created")
    async def confirm_creation_recovery_backup(
        request: Request,
        credentials: CredentialsDependency,
        csrf_token: str = Form(...),
        confirmed: str | None = Form(None),
    ):
        settings = request.app.state.settings
        if not CsrfProtector(settings).verify(csrf_token) or confirmed != "yes":
            return HTMLResponse(
                _page(
                    "Recovery backup not confirmed",
                    "<p>Valid confirmation is required.</p>",
                ),
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        response = RedirectResponse("/wallet", status_code=303)
        set_session_cookie(
            response,
            request=request,
            settings=settings,
            credentials=replace(credentials, deferred_acorn_mnemonic=None),
        )
        return response

    @app.get("/record-protection/recovery", response_class=HTMLResponse)
    async def record_protection_recovery_warning(
        request: Request,
        credentials: CredentialsDependency,
    ):
        settings = request.app.state.settings
        if credentials.record_protection_key is None:
            return HTMLResponse(
                _page(
                    "Record protection is not attached",
                    "<p>This session does not contain a record protection key. "
                    "Disconnect and reconnect with the protected-record recovery "
                    "phrase or external entropy.</p>"
                    '<p><a href="/wallet">Back to wallet</a></p>',
                ),
                status_code=409,
                headers={"Cache-Control": "no-store"},
            )
        return HTMLResponse(
            render_template(
                "record_protection_warning.html",
                title="Protected record mnemonic",
                csrf_token=CsrfProtector(settings).issue(),
            ),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/record-protection/enable", response_class=HTMLResponse)
    async def enable_record_protection_form(
        request: Request,
        credentials: CredentialsDependency,
    ) -> HTMLResponse:
        if credentials.record_protection_key is not None:
            return RedirectResponse("/record-protection/recovery", status_code=303)
        return HTMLResponse(
            render_template(
                "record_protection_enable.html",
                title="Enable Protected Records",
                csrf_token=CsrfProtector(request.app.state.settings).issue(),
                error=None,
            ),
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/record-protection/enable", response_class=HTMLResponse)
    async def enable_record_protection(
        request: Request,
        credentials: CredentialsDependency,
        acorn: LoadedAcornDependency,
        csrf_token: str = Form(...),
        use_external_entropy: str | None = Form(None),
        entropy_hex: str = Form(""),
        entropy_confirmation: str = Form(""),
        confirmed: str | None = Form(None),
    ) -> HTMLResponse:
        settings = request.app.state.settings

        def activation_error(message: str, status_code: int = 400) -> HTMLResponse:
            return HTMLResponse(
                render_template(
                    "record_protection_enable.html",
                    title="Enable Protected Records",
                    csrf_token=CsrfProtector(settings).issue(),
                    error=message,
                ),
                status_code=status_code,
                headers={"Cache-Control": "no-store"},
            )

        if credentials.record_protection_key is not None:
            return RedirectResponse("/record-protection/recovery", status_code=303)
        if not CsrfProtector(settings).verify(csrf_token):
            return activation_error("The form token is invalid or expired.", 403)
        if confirmed != "yes":
            return activation_error("Explicit confirmation is required.")

        if use_external_entropy == "yes":
            supplied = str(entropy_hex).strip()
            repeated = str(entropy_confirmation).strip()
            if not supplied or not repeated:
                return activation_error("Enter the external entropy in both fields.")
            if supplied != repeated:
                return activation_error("The external entropy values do not match.")
            try:
                record_protection_key = record_protection_key_from_entropy(supplied)
            except ValueError as exc:
                return activation_error(f"Invalid record-protection entropy: {exc}")
        else:
            record_protection_key = generate_record_protection_key()

        try:
            activation = await asyncio.wait_for(
                acorn.activate_record_protection(
                    record_protection_key=record_protection_key,
                ),
                timeout=settings.wallet_load_timeout_seconds,
            )
            if not activation.get("active") or not activation.get("verified"):
                raise RuntimeError("record protection activation was not verified")
        except TimeoutError:
            logger.warning("record protection activation timed out")
            return activation_error(
                "Safebox could not verify record-protection activation on the relay.",
                504,
            )
        except Exception as exc:
            logger.warning(
                "record protection activation failed error_type=%s",
                type(exc).__name__,
            )
            return activation_error(
                "Safebox could not activate record protection. No recovery key "
                "was attached to this session.",
                502,
            )

        recovery_phrase = record_protection_recovery_phrase(record_protection_key)
        response = HTMLResponse(
            render_template(
                "record_protection_recovery.html",
                title="Protected record mnemonic",
                recovery_phrase=recovery_phrase,
                csrf_token=CsrfProtector(settings).issue(),
                activation=True,
            ),
            headers={"Cache-Control": "no-store"},
        )
        set_session_cookie(
            response,
            request=request,
            settings=settings,
            credentials=replace(
                credentials,
                record_protection_key=record_protection_key,
                record_protection_backup_confirmed=False,
            ),
        )
        return response

    @app.post("/record-protection/recovery", response_class=HTMLResponse)
    async def display_record_protection_recovery(
        request: Request,
        credentials: CredentialsDependency,
        csrf_token: str = Form(...),
        confirmed: str | None = Form(None),
    ):
        settings = request.app.state.settings
        if not CsrfProtector(settings).verify(csrf_token) or confirmed != "yes":
            return HTMLResponse(
                _page(
                    "Recovery material not displayed",
                    "<p>Valid confirmation is required.</p>"
                    '<p><a href="/record-protection/recovery">Try again</a></p>',
                ),
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        if credentials.record_protection_key is None:
            return HTMLResponse(
                _page(
                    "Record protection is not attached",
                    "<p>This session does not contain a record protection key.</p>"
                    '<p><a href="/wallet">Back to wallet</a></p>',
                ),
                status_code=409,
                headers={"Cache-Control": "no-store"},
            )
        recovery_phrase = record_protection_recovery_phrase(
            credentials.record_protection_key
        )
        return HTMLResponse(
            render_template(
                "record_protection_recovery.html",
                title="Protected record mnemonic",
                recovery_phrase=recovery_phrase,
                csrf_token=CsrfProtector(settings).issue(),
            ),
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/record-protection/confirm")
    async def confirm_record_protection_backup(
        request: Request,
        credentials: CredentialsDependency,
        csrf_token: str = Form(...),
        confirmed: str | None = Form(None),
    ):
        settings = request.app.state.settings
        if not CsrfProtector(settings).verify(csrf_token) or confirmed != "yes":
            return HTMLResponse(
                _page(
                    "Backup not confirmed",
                    "<p>Valid confirmation is required.</p>"
                    '<p><a href="/record-protection/recovery">Return</a></p>',
                ),
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        if credentials.record_protection_key is None:
            return HTMLResponse(
                _page(
                    "Record protection is not attached",
                    "<p>This session does not contain a record protection key.</p>"
                    '<p><a href="/wallet">Back to wallet</a></p>',
                ),
                status_code=409,
                headers={"Cache-Control": "no-store"},
            )
        updated_credentials = replace(
            credentials,
            record_protection_backup_confirmed=True,
        )
        response = RedirectResponse("/wallet", status_code=303)
        set_session_cookie(
            response,
            request=request,
            settings=settings,
            credentials=updated_credentials,
        )
        return response

    @app.post("/disconnect")
    async def disconnect(
        request: Request,
        csrf_token: str = Form(...),
        confirmed: str | None = Form(None),
    ):
        if not CsrfProtector(request.app.state.settings).verify(csrf_token):
            return HTMLResponse(
                _page(
                    "Unable to disconnect",
                    '<p class="error">The form token is invalid or expired.</p>'
                    '<p><a href="/wallet">Return to wallet</a></p>',
                ),
                status_code=403,
            )
        if confirmed != "yes":
            return HTMLResponse(
                _page(
                    "Unable to disconnect",
                    '<p class="error">Confirm that you have your recovery '
                    "information before disconnecting.</p>"
                    '<p><a href="/wallet">Return to wallet</a></p>',
                ),
                status_code=403,
            )
        # An explicit disconnect normally means the user may want to reconnect
        # an existing Acorn. Do not send them through the one-click new-wallet
        # onboarding path.
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(SECURE_COOKIE_NAME, path="/", secure=True, httponly=True)
        response.delete_cookie(LOOPBACK_COOKIE_NAME, path="/", httponly=True)
        return response

    @app.get("/login", include_in_schema=False)
    async def legacy_connect_get() -> RedirectResponse:
        return RedirectResponse("/connect", status_code=303)

    @app.post("/login", include_in_schema=False)
    async def legacy_connect_post() -> RedirectResponse:
        return RedirectResponse("/connect", status_code=307)

    @app.post("/logout", include_in_schema=False)
    async def legacy_disconnect_post() -> RedirectResponse:
        return RedirectResponse("/disconnect", status_code=307)

    @app.get("/disconnect", response_class=HTMLResponse)
    async def disconnect_form(request: Request) -> HTMLResponse:
        """Offer a safe cookie reset even when the attached Acorn cannot load."""

        return HTMLResponse(
            render_template(
                "disconnect.html",
                title="Disconnect Acorn",
                message=(
                    "Disconnecting removes the attached Acorn credentials from "
                    "this browser session. It does not delete relay data, records, "
                    "or funds."
                ),
                csrf_token=CsrfProtector(request.app.state.settings).issue(),
                show_page_navigation=False,
            )
        )

    @app.get("/wallet", response_class=HTMLResponse)
    async def wallet(
        request: Request,
        acorn: LoadedAcornDependency,
        session: DatabaseSessionDependency,
    ) -> str:
        settings = request.app.state.settings
        csrf_token = CsrfProtector(settings).issue()
        session_credentials = None
        session_token = request.cookies.get(cookie_name_for_request(request))
        if session_token:
            try:
                session_credentials = SessionCipher(settings).decode(session_token)
            except ValueError:
                # The normal LoadedAcorn dependency rejects invalid cookies.
                # This fallback exists for dependency-overridden test clients.
                session_credentials = None
        claimed_handle = session.exec(
            select(ClaimedHandle).where(
                ClaimedHandle.npub == acorn.pubkey_bech32
            )
        ).first()
        nip05_address = None
        lightning_lnurl = None
        address_qr = None
        if claimed_handle is not None:
            nip05_address = (
                f"{claimed_handle.claimed_handle}@{request.url.hostname}"
            ).lower()
            if settings.service_acorn_enabled:
                pay_endpoint = str(
                    request.url_for(
                        "lnurl_pay_resolve",
                        handle=claimed_handle.claimed_handle,
                    )
                )
                lightning_lnurl = encode_lnurl(pay_endpoint)
                address_qr = _qr_svg(lightning_lnurl, include_acorn=True)
        silent_payment_address = None
        silent_payment_qr = None
        try:
            silent_payment_address = derive_nostr_silent_payment_address(
                acorn.pubkey_bech32
            )
            silent_payment_qr = _qr_svg(silent_payment_address)
        except ValueError as exc:
            logger.warning(
                "silent payment public derivation unavailable error_type=%s",
                type(exc).__name__,
            )
        # Show lightweight relay-visible balance snapshots on the landing page.
        # Mint verification, journals, and pending-transfer scans remain scoped
        # to their dedicated detail pages. Clear aliases use the same bounded,
        # short-lived metadata cache as the Clear page.
        recovery_backup_pending = bool(
            session_credentials is not None
            and session_credentials.deferred_acorn_mnemonic
        )
        wallet_balance = acorn.get_balance()
        fiat_estimate = None
        if settings.currency_rates_enabled:
            fiat_estimate = currency_balance_estimate(
                session,
                sats=wallet_balance,
                currency_code=settings.default_display_currency,
                stale_seconds=settings.currency_rate_stale_seconds,
            )
        try:
            clear_balances = await _read_clear_balances(
                acorn,
                settings.wallet_load_timeout_seconds,
            )
        except Exception as exc:
            logger.warning(
                "wallet clear balance snapshot unavailable error_type=%s",
                type(exc).__name__,
            )
            clear_balances = []
        clear_summary = await _resolve_clear_aliases(
            _clear_balance_summary([], clear_balances),
            timeout=settings.wallet_load_timeout_seconds,
            configured_mints=settings.clear_mints,
            cache=request.app.state.clear_mint_metadata_cache,
        )
        return render_template(
            "wallet.html",
            title="Safebox is Connected",
            headline_class="wallet-headline",
            npub=acorn.pubkey_bech32,
            home_relay=acorn.home_relay,
            record_protection_available=(
                session_credentials is not None
                and session_credentials.record_protection_key is not None
            ),
            record_protection_backup_confirmed=(
                session_credentials is not None
                and session_credentials.record_protection_backup_confirmed
            ),
            recovery_backup_pending=recovery_backup_pending,
            handle_assignment_failed=(
                request.query_params.get("handle_assignment") == "failed"
            ),
            nip05_address=nip05_address,
            lightning_lnurl=lightning_lnurl,
            address_qr=address_qr,
            silent_payment_address=silent_payment_address,
            silent_payment_qr=silent_payment_qr,
            retention_notice=_ecash_retention_notice(settings),
            wallet_balance=wallet_balance,
            fiat_estimate=fiat_estimate,
            clear_summary=clear_summary,
            onboard_invite_path="/invite",
            csrf_token=csrf_token,
        )

    @app.get("/balances", response_class=HTMLResponse)
    async def balances(
        credentials: CredentialsDependency,
    ) -> str:
        """Present the balance domain without treating Cash as the top level."""

        return render_template(
            "balances.html",
            title="Manage Balances",
        )

    @app.post("/bitcoin/silent-payment/detect", response_class=HTMLResponse)
    async def bitcoin_silent_payment_detect(
        request: Request,
        credentials: CredentialsDependency,
        csrf_token: str = Form(...),
        txid: str = Form(...),
    ) -> HTMLResponse:
        settings = request.app.state.settings
        if not CsrfProtector(settings).verify(csrf_token):
            return HTMLResponse(
                render_template(
                    "silent_payment_receipts.html",
                    title="Check Silent Payment",
                    error="The form token is invalid or expired. Return to the wallet and try again.",
                    result=None,
                    csrf_token=CsrfProtector(settings).issue(),
                ),
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        normalized_txid = str(txid or "").strip().lower()
        if BITCOIN_TXID_PATTERN.fullmatch(normalized_txid) is None:
            return HTMLResponse(
                render_template(
                    "silent_payment_receipts.html",
                    title="Check Silent Payment",
                    error="Enter a 64-character hexadecimal transaction id.",
                    result=None,
                    csrf_token=CsrfProtector(settings).issue(),
                ),
                status_code=400,
                headers={"Cache-Control": "no-store"},
            )
        try:
            result = await asyncio.to_thread(
                detect_silent_payment_receipts,
                nsec=credentials.nsec,
                txid=normalized_txid,
                api_base=settings.bitcoin_api_base,
                timeout=settings.bitcoin_lookup_timeout_seconds,
            )
            error = None
        except BitcoinCapabilityError as exc:
            result = None
            error = str(exc)
        return HTMLResponse(
            render_template(
                "silent_payment_receipts.html",
                title="Incoming Silent Payment",
                error=error,
                result=result,
                csrf_token=CsrfProtector(settings).issue(),
            ),
            status_code=200 if error is None else 502,
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/bitcoin/silent-payment/sweep/preview", response_class=HTMLResponse)
    async def bitcoin_silent_payment_sweep_preview(
        request: Request,
        credentials: CredentialsDependency,
        csrf_token: str = Form(...),
        txid: str = Form(...),
        vout: int = Form(...),
        destination_address: str = Form(...),
    ) -> HTMLResponse:
        settings = request.app.state.settings
        if not CsrfProtector(settings).verify(csrf_token):
            return HTMLResponse(
                render_template(
                    "silent_payment_sweep_review.html",
                    title="Review Silent Payment",
                    error="The form token is invalid or expired.",
                    preview=None,
                    csrf_token=CsrfProtector(settings).issue(),
                ),
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        normalized_txid = str(txid or "").strip().lower()
        destination = str(destination_address or "").strip()
        if BITCOIN_TXID_PATTERN.fullmatch(normalized_txid) is None:
            error = "The receipt transaction id is invalid."
            preview = None
        elif vout < 0:
            error = "The receipt output index is invalid."
            preview = None
        elif not destination:
            error = "Enter a settlement address."
            preview = None
        else:
            try:
                preview = await asyncio.to_thread(
                    create_silent_payment_sweep_preview,
                    nsec=credentials.nsec,
                    txid=normalized_txid,
                    vout=vout,
                    destination_address=destination,
                    fee_rate=settings.bitcoin_sweep_fee_rate,
                    api_base=settings.bitcoin_api_base,
                    timeout=settings.bitcoin_lookup_timeout_seconds,
                )
                error = None
            except BitcoinCapabilityError as exc:
                preview = None
                error = str(exc)
        return HTMLResponse(
            render_template(
                "silent_payment_sweep_review.html",
                title="Review Silent Payment",
                error=error,
                preview=preview,
                csrf_token=CsrfProtector(settings).issue(),
            ),
            status_code=200 if error is None else 400,
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/bitcoin/silent-payment/sweep", response_class=HTMLResponse)
    async def bitcoin_silent_payment_sweep(
        request: Request,
        credentials: CredentialsDependency,
        csrf_token: str = Form(...),
        txid: str = Form(...),
        vout: int = Form(...),
        destination_address: str = Form(...),
        confirmed: str = Form(""),
    ) -> HTMLResponse:
        settings = request.app.state.settings
        error = None
        result = None
        if not CsrfProtector(settings).verify(csrf_token):
            error = "The form token is invalid or expired."
        elif confirmed != "yes":
            error = "Confirm that the transaction is irreversible."
        else:
            normalized_txid = str(txid or "").strip().lower()
            destination = str(destination_address or "").strip()
            if BITCOIN_TXID_PATTERN.fullmatch(normalized_txid) is None or vout < 0:
                error = "The receipt outpoint is invalid."
            elif not destination:
                error = "The settlement address is required."
            else:
                try:
                    result = await asyncio.to_thread(
                        broadcast_silent_payment_sweep,
                        nsec=credentials.nsec,
                        txid=normalized_txid,
                        vout=vout,
                        destination_address=destination,
                        fee_rate=settings.bitcoin_sweep_fee_rate,
                        api_base=settings.bitcoin_api_base,
                        timeout=settings.bitcoin_lookup_timeout_seconds,
                    )
                except BitcoinCapabilityError as exc:
                    error = str(exc)
        return HTMLResponse(
            render_template(
                "silent_payment_sweep_result.html",
                title="Silent Payment received",
                error=error,
                result=result,
            ),
            status_code=200 if error is None else 400,
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/handle", response_class=HTMLResponse)
    async def handle_form(
        request: Request,
        acorn: AcornDependency,
        session: DatabaseSessionDependency,
    ) -> str:
        existing = session.exec(
            select(ClaimedHandle).where(
                ClaimedHandle.npub == acorn.pubkey_bech32
            )
        ).first()
        return _handle_form(
            CsrfProtector(request.app.state.settings).issue(),
            existing=existing,
            hostname=request.url.hostname,
        )

    @app.post("/handle", response_class=HTMLResponse)
    async def claim_handle(
        request: Request,
        acorn: AcornDependency,
        session: DatabaseSessionDependency,
        csrf_token: str = Form(...),
        claimed_handle: str = Form(...),
    ):
        settings = request.app.state.settings
        form_token = CsrfProtector(settings)
        existing_for_npub = session.exec(
            select(ClaimedHandle).where(
                ClaimedHandle.npub == acorn.pubkey_bech32
            )
        ).first()

        def claim_error(message: str, status_code: int) -> HTMLResponse:
            return HTMLResponse(
                _handle_form(
                    form_token.issue(),
                    existing=existing_for_npub,
                    hostname=request.url.hostname,
                    error=message,
                ),
                status_code=status_code,
            )

        if not form_token.verify(csrf_token):
            return claim_error(
                "The form token is invalid or expired. Review the handle again.",
                403,
            )
        try:
            normalized_handle = _normalize_handle(claimed_handle)
        except ValueError as exc:
            return claim_error(str(exc), 400)

        existing_for_handle = session.exec(
            select(ClaimedHandle).where(
                ClaimedHandle.claimed_handle == normalized_handle
            )
        ).first()
        if (
            existing_for_handle is not None
            and existing_for_handle.npub != acorn.pubkey_bech32
        ):
            return claim_error("That handle has already been claimed.", 409)
        registration = existing_for_npub or existing_for_handle
        if registration is None:
            registration = ClaimedHandle(
                claimed_handle=normalized_handle,
                npub=acorn.pubkey_bech32,
                home_relay=acorn.home_relay,
            )
        else:
            # The authenticated component may rename its own mapping or submit
            # the same name idempotently to refresh its relay.
            registration.claimed_handle = normalized_handle
            registration.home_relay = acorn.home_relay

        try:
            session.add(registration)
            session.commit()
        except IntegrityError:
            # The unique constraints make simultaneous claims deterministic.
            session.rollback()
            return claim_error("That handle was claimed by another request.", 409)

        return RedirectResponse("/handle", status_code=303)

    @app.post("/handle/remove", response_class=HTMLResponse)
    async def remove_handle(
        request: Request,
        acorn: AcornDependency,
        session: DatabaseSessionDependency,
        csrf_token: str = Form(...),
        confirmed: str | None = Form(None),
    ):
        settings = request.app.state.settings
        form_token = CsrfProtector(settings)
        registration = session.exec(
            select(ClaimedHandle).where(
                ClaimedHandle.npub == acorn.pubkey_bech32
            )
        ).first()

        def removal_error(message: str, status_code: int) -> HTMLResponse:
            return HTMLResponse(
                _handle_form(
                    form_token.issue(),
                    existing=registration,
                    hostname=request.url.hostname,
                    error=message,
                ),
                status_code=status_code,
            )

        if not form_token.verify(csrf_token):
            return removal_error(
                "The form token is invalid or expired. Review the removal again.",
                403,
            )
        if confirmed != "yes":
            return removal_error("Explicit removal confirmation is required.", 400)
        if registration is None:
            return removal_error("This Acorn has no claimed handle.", 404)

        session.delete(registration)
        session.commit()
        return RedirectResponse("/handle", status_code=303)

    @app.get("/.well-known/nostr.json", response_class=JSONResponse)
    async def resolve_nip05(
        request: Request,
        name: str,
        session: DatabaseSessionDependency,
    ):
        try:
            normalized_name = _normalize_handle(name)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"detail": str(exc)})

        registration = session.exec(
            select(ClaimedHandle).where(
                ClaimedHandle.claimed_handle == normalized_name
            )
        ).first()
        if registration is None:
            return JSONResponse(
                status_code=404,
                content={"detail": f"{normalized_name} not found"},
                headers={"Access-Control-Allow-Origin": "*"},
            )

        try:
            pubkey_hex = npub_to_hex(registration.npub)
        except ValueError:
            logger.error(
                "invalid npub in claimed handle directory handle=%s",
                normalized_name,
            )
            return JSONResponse(
                status_code=500,
                content={"detail": "Stored handle public key is invalid"},
                headers={"Access-Control-Allow-Origin": "*"},
            )

        content = {
            "names": {normalized_name: pubkey_hex},
            "relays": {pubkey_hex: [registration.home_relay]},
        }
        settings = request.app.state.settings
        if settings.clear_receive_enabled:
            clear_descriptor = {
                "protocols": ["clear-token-transfer"],
                "transports": ["nip59"],
                "kinds": [7379],
            }
            if settings.clear_mints:
                clear_descriptor["mints"] = list(settings.clear_mints)
            if settings.clear_units:
                clear_descriptor["units"] = list(settings.clear_units)
            content["clear"] = {
                normalized_name: clear_descriptor,
            }

        return JSONResponse(
            content=content,
            headers={"Access-Control-Allow-Origin": "*"},
        )

    @app.get("/receive-funds", response_class=HTMLResponse)
    async def receive_funds_form(
        request: Request, acorn: DepositAcornDependency
    ) -> str:
        settings = request.app.state.settings
        verification, verification_error = await _read_proof_verification(
            acorn,
            settings.wallet_load_timeout_seconds,
        )
        return _receive_funds_form(
            acorn.get_balance(),
            acorn.home_mint,
            CsrfProtector(settings).issue(),
            balance_status=_balance_status_html(
                acorn.get_balance(),
                len(acorn.proofs),
                verification,
                verification_error,
            ),
        )

    @app.post("/receive-funds", response_class=HTMLResponse)
    async def create_payment_request(
        request: Request,
        acorn: DepositAcornDependency,
        csrf_token: str = Form(...),
        amount: str = Form(...),
        payment_method: str = Form("lightning"),
    ):
        settings = request.app.state.settings
        form_token = CsrfProtector(settings)

        def receive_error(message: str, status_code: int = 400) -> HTMLResponse:
            return HTMLResponse(
                _receive_funds_form(
                    acorn.get_balance(),
                    acorn.home_mint,
                    form_token.issue(),
                    message,
                ),
                status_code=status_code,
            )

        if not form_token.verify(csrf_token):
            return receive_error(
                "The form token is invalid or expired. Enter the amount again.",
                403,
            )
        if payment_method != "lightning":
            return receive_error(
                "That payment-request method is not available yet.",
                400,
            )
        try:
            amount_sats = int(str(amount).strip())
        except ValueError:
            return receive_error("The amount must be a whole number of sats.")
        if amount_sats <= 0:
            return receive_error("The amount must be greater than zero.")

        try:
            quote = await asyncio.wait_for(
                asyncio.to_thread(acorn.deposit, amount_sats),
                timeout=settings.payment_timeout_seconds,
            )
        except TimeoutError:
            logger.warning("deposit quote request timed out mint=%s", acorn.home_mint)
            return receive_error(
                "The home mint did not return a payment request before the request "
                "timed out.",
                504,
            )
        except Exception as exc:
            logger.warning(
                "deposit quote request failed mint=%s error_type=%s",
                acorn.home_mint,
                type(exc).__name__,
            )
            return receive_error(
                "Safebox could not create a Lightning payment request through the "
                "home mint.",
                502,
            )

        quote_id = str(quote.quote).strip()
        invoice = str(quote.invoice).strip()
        if not quote_id or not invoice or len(quote_id) > 512 or len(invoice) > 2048:
            return receive_error(
                "The home mint returned an invalid or oversized Lightning payment "
                "request.",
                502,
            )

        state = DepositQuoteState(
            quote=quote_id,
            amount=amount_sats,
            mint=str(acorn.home_mint).rstrip("/"),
            invoice=invoice,
        )
        state_token = DepositQuoteCipher(settings).encode(state)
        return _lightning_payment_request_page(
            state,
            state_token,
            form_token.issue(),
        )

    @app.post("/receive-funds/check")
    async def check_payment_request(
        request: Request,
        acorn: DepositAcornDependency,
        csrf_token: str = Form(...),
        deposit_token: str = Form(...),
    ):
        settings = request.app.state.settings
        form_token = CsrfProtector(settings)
        if not form_token.verify(csrf_token):
            return JSONResponse(
                status_code=403,
                content={"detail": "Form token is invalid or expired"},
            )
        try:
            state = DepositQuoteCipher(settings).decode(deposit_token)
        except ValueError as exc:
            return HTMLResponse(
                _page(
                    "Payment request expired",
                    f'<p class="error">{escape(str(exc))}.</p>'
                    '<p><a href="/receive-funds">Create a new payment request</a></p>',
                ),
                status_code=400,
            )

        if str(acorn.home_mint).rstrip("/") != state.mint:
            return HTMLResponse(
                _lightning_payment_request_page(
                    state,
                    deposit_token,
                    form_token.issue(),
                    "This Acorn's home mint no longer matches the invoice mint. "
                    "The quote was not checked.",
                ),
                status_code=409,
            )

        try:
            success, _ = await asyncio.wait_for(
                acorn.check_quote(state.quote, state.amount),
                timeout=settings.payment_timeout_seconds,
            )
        except TimeoutError:
            logger.warning("deposit confirmation timed out mint=%s", state.mint)
            return HTMLResponse(
                _lightning_payment_request_page(
                    state,
                    deposit_token,
                    form_token.issue(),
                    "Payment confirmation timed out. Do not create or complete "
                    "another request; wait and check this request again.",
                ),
                status_code=504,
            )
        except Exception as exc:
            logger.warning(
                "deposit confirmation failed mint=%s error_type=%s",
                state.mint,
                type(exc).__name__,
            )
            return HTMLResponse(
                _lightning_payment_request_page(
                    state,
                    deposit_token,
                    form_token.issue(),
                    "Safebox could not confirm the payment. Do not create or "
                    "complete another request; wait and check this request again.",
                ),
                status_code=502,
            )

        if not success:
            return HTMLResponse(
                _lightning_payment_request_page(
                    state,
                    deposit_token,
                    form_token.issue(),
                    "The mint has not confirmed payment yet. If you paid recently, "
                    "wait briefly and check this invoice again.",
                ),
                status_code=409,
            )

        try:
            await acorn.add_tx_history(
                tx_type="C",
                amount=state.amount,
                comment="safebox web funds received",
            )
        except Exception as exc:
            logger.warning(
                "deposit confirmed but transaction history write failed "
                "mint=%s error_type=%s",
                state.mint,
                type(exc).__name__,
            )
        return RedirectResponse("/wallet", status_code=303)

    @app.get("/deposit", include_in_schema=False)
    async def legacy_deposit_form() -> RedirectResponse:
        return RedirectResponse("/receive-funds", status_code=303)

    @app.post("/deposit", include_in_schema=False)
    async def legacy_create_deposit() -> RedirectResponse:
        return RedirectResponse("/receive-funds", status_code=307)

    @app.post("/deposit/check", include_in_schema=False)
    async def legacy_check_deposit() -> RedirectResponse:
        return RedirectResponse("/receive-funds/check", status_code=307)

    @app.get("/scan/lightning-address", include_in_schema=False)
    async def legacy_lightning_address_scanner() -> RedirectResponse:
        return RedirectResponse("/scan/lightning", status_code=303)

    @app.get("/scan/lightning", response_class=HTMLResponse)
    async def lightning_scanner(
        request: Request,
        credentials: CredentialsDependency,
    ) -> str:
        del credentials
        return _lightning_scan_form(
            CsrfProtector(request.app.state.settings).issue()
        )

    @app.post("/scan/lightning", response_class=HTMLResponse)
    async def accept_scanned_lightning_payment(
        request: Request,
        acorn: PaymentAcornDependency,
        csrf_token: str = Form(...),
        lightning_payment: str = Form(...),
    ) -> HTMLResponse:
        settings = request.app.state.settings
        form_token = CsrfProtector(settings)

        def scan_error(message: str, status_code: int = 400) -> HTMLResponse:
            return HTMLResponse(
                _lightning_scan_form(
                    form_token.issue(),
                    error=message,
                    lightning_address=str(lightning_payment).strip(),
                ),
                status_code=status_code,
            )

        if not form_token.verify(csrf_token):
            return scan_error("The form token is invalid or expired. Scan again.", 403)

        scanned_value = str(lightning_payment).strip()
        if scanned_value.lower().startswith(RECORD_PRESENTATION_PREFIX):
            try:
                # Validate the visible capability tag and compact descriptor
                # before retrieving any temporary content.
                decode_record_presentation_descriptor(scanned_value)
                presentation = await asyncio.wait_for(
                    acorn.inspect_record_presentation(
                        scanned_value,
                        allowed_servers=[settings.blossom_home_server],
                    ),
                    timeout=settings.wallet_load_timeout_seconds,
                )
            except RecordTransferError as exc:
                return scan_error(str(exc))
            except TimeoutError:
                return scan_error("The record presentation lookup timed out.", 504)
            except Exception as exc:
                logger.warning(
                    "record presentation inspection failed error_type=%s",
                    type(exc).__name__,
                )
                return scan_error("The temporary record presentation could not be loaded.", 502)

            payload = presentation.get("payload")
            rendered_payload = (
                payload
                if isinstance(payload, str)
                else json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True, default=str)
            )
            blob_data = presentation.get("blob_data")
            blob_type = _effective_blob_media_type(presentation.get("blob_type"))
            blob_sha256 = presentation.get("blob_sha256")
            blob_data_url = None
            if isinstance(blob_data, bytes):
                blob_data_url = (
                    f"data:{blob_type or 'application/octet-stream'};base64,"
                    + base64.b64encode(blob_data).decode("ascii")
                )
            control_history = None
            if blob_sha256:
                try:
                    control_history = await asyncio.wait_for(
                        query_openetr_history(
                            str(blob_sha256),
                            settings.openetr_relays,
                            timeout=settings.openetr_query_timeout_seconds,
                            limit=settings.openetr_query_limit,
                        ),
                        timeout=(settings.openetr_query_timeout_seconds * 2) + 1,
                    )
                except Exception as exc:
                    logger.warning(
                        "presentation control history failed error_type=%s",
                        type(exc).__name__,
                    )
                    control_history = {
                        "digest": str(blob_sha256),
                        "relays": settings.openetr_relays,
                        "origin": None,
                        "controls": [],
                        "warnings": [],
                        "error": "Control history is temporarily unavailable.",
                    }
            return HTMLResponse(
                render_template(
                    "record_presentation_view.html",
                    title=presentation["label"],
                    presentation=presentation,
                    payload=rendered_payload,
                    blob_type=blob_type,
                    blob_preview=_blob_preview_kind(blob_type),
                    blob_data_url=blob_data_url,
                    blob_fingerprint=(
                        str(blob_sha256)[:8].upper() if blob_sha256 else None
                    ),
                    control_history=control_history,
                    descriptor=scanned_value,
                    csrf_token=form_token.issue(),
                ),
                headers={"Cache-Control": "no-store"},
            )

        if scanned_value.lower().startswith(RECORD_TRANSFER_PREFIX):
            try:
                decode_record_transfer_descriptor(scanned_value)
                transfer = await asyncio.wait_for(
                    acorn.inspect_record_transfer(
                        scanned_value,
                        allowed_servers=[settings.blossom_home_server],
                    ),
                    timeout=settings.wallet_load_timeout_seconds,
                )
            except RecordTransferError as exc:
                return scan_error(str(exc))
            except TimeoutError:
                return scan_error("The record transfer lookup timed out.", 504)
            except Exception as exc:
                logger.warning(
                    "record transfer inspection failed error_type=%s",
                    type(exc).__name__,
                )
                return scan_error("The temporary record transfer could not be loaded.", 502)
            return HTMLResponse(
                render_template(
                    "record_transfer_review.html",
                    title="Import Record",
                    error=None,
                    transfer=transfer,
                    descriptor=scanned_value,
                    expires_at=datetime.fromtimestamp(
                        int(transfer["expires_at"]),
                        tz=timezone.utc,
                    ).strftime("%Y-%m-%d %H:%M UTC"),
                    csrf_token=form_token.issue(),
                ),
                headers={"Cache-Control": "no-store"},
            )

        recipient_input = str(lightning_payment).strip()
        lightning_payload = recipient_input
        if lightning_payload[:10].lower() == "lightning:":
            lightning_payload = lightning_payload[10:].strip()
        if lightning_payload.lower().startswith("lnurl1"):
            try:
                recipient_input = lightning_address_from_lnurl(lightning_payload)
            except ValueError:
                recipient_input = ""
        recipient = _normalize_lightning_address(recipient_input)
        invoice = _decode_lightning_invoice(lightning_payment)
        if recipient is None and invoice is None:
            return scan_error(
                "The QR code does not contain a supported Lightning address or fixed-amount mainnet invoice."
            )

        if invoice is not None:
            state_token = InvoicePaymentCipher(settings).encode(
                InvoicePaymentState(
                    invoice=str(invoice["invoice"]),
                    amount=int(invoice["amount"]),
                )
            )
            return HTMLResponse(
                _invoice_payment_form(
                    csrf_token=form_token.issue(),
                    state_token=state_token,
                    amount=int(invoice["amount"]),
                    description=str(invoice["description"]),
                    expiry=str(invoice["expiry"]),
                    payment_hash=str(invoice["payment_hash"]),
                )
            )

        try:
            clear_balances = await _payment_clear_balances(request, acorn)
        except Exception as exc:
            logger.warning(
                "Clear payment balance lookup failed after scan error_type=%s",
                type(exc).__name__,
            )
            clear_balances = []
        verification, verification_error = await _read_proof_verification(
            acorn,
            settings.wallet_load_timeout_seconds,
        )
        return HTMLResponse(
            _payment_form(
                acorn.get_balance(),
                form_token.issue(),
                balance_status=_balance_status_html(
                    acorn.get_balance(),
                    len(acorn.proofs),
                    verification,
                    verification_error,
                ),
                lightning_address=recipient,
                clear_balances=clear_balances,
            )
        )

    @app.post("/pay/invoice", response_class=HTMLResponse)
    async def pay_lightning_invoice(
        request: Request,
        acorn: PaymentAcornDependency,
        csrf_token: str = Form(...),
        invoice_state: str = Form(...),
        comment: str = Form("Paid from Safebox Web"),
        confirmed: str | None = Form(None),
    ) -> HTMLResponse:
        settings = request.app.state.settings
        form_token = CsrfProtector(settings)
        if not form_token.verify(csrf_token):
            return HTMLResponse(
                _page(
                    "Invoice payment not authorized",
                    '<p class="error">The form token is invalid or expired. Scan the invoice again.</p>'
                    '<p><a href="/scan/lightning">Return to scanner</a></p>',
                ),
                status_code=403,
            )
        if confirmed != "yes":
            return HTMLResponse(
                _page(
                    "Invoice payment not authorized",
                    '<p class="error">Explicit invoice payment confirmation is required.</p>'
                    '<p><a href="/scan/lightning">Return to scanner</a></p>',
                ),
                status_code=400,
            )
        try:
            state = InvoicePaymentCipher(settings).decode(invoice_state)
        except ValueError:
            return HTMLResponse(
                _page(
                    "Invoice review expired",
                    '<p class="error">The reviewed invoice is invalid or expired. Scan it again.</p>'
                    '<p><a href="/scan/lightning">Return to scanner</a></p>',
                ),
                status_code=400,
            )
        invoice = _decode_lightning_invoice(state.invoice)
        if invoice is None or int(invoice["amount"]) != state.amount:
            return HTMLResponse(
                _page(
                    "Invoice is no longer payable",
                    '<p class="error">The invoice is invalid, expired, or no longer matches the reviewed amount.</p>'
                    '<p><a href="/scan/lightning">Return to scanner</a></p>',
                ),
                status_code=400,
            )
        payment_comment = str(comment).strip() or "Paid from Safebox Web"
        if len(payment_comment) > 200:
            return HTMLResponse(
                _page(
                    "Invoice payment not authorized",
                    '<p class="error">Payment comment must be 200 characters or fewer.</p>'
                    '<p><a href="/scan/lightning">Return to scanner</a></p>',
                ),
                status_code=400,
            )
        verification, verification_error = await _read_proof_verification(
            acorn,
            settings.wallet_load_timeout_seconds,
        )
        if verification is None:
            return HTMLResponse(
                _page(
                    "Invoice payment blocked",
                    f'<p class="error">{escape(verification_error or "Mint verification was unavailable.")}</p>'
                    '<p><a href="/wallet">Return to wallet</a></p>',
                ),
                status_code=503,
            )
        if verification.get("status") != "clean":
            return HTMLResponse(
                _page(
                    "Invoice payment blocked",
                    '<p class="error">The wallet proof state is not clean. Review it before spending.</p>'
                    '<p><a href="/wallet">Return to wallet</a></p>',
                ),
                status_code=409,
            )
        confirmed_balance = int(
            verification.get("mint_confirmed_unspent", {}).get("amount", 0)
        )
        if state.amount > confirmed_balance:
            return HTMLResponse(
                _page(
                    "Invoice payment blocked",
                    '<p class="error">The invoice exceeds the confirmed cash balance.</p>'
                    '<p><a href="/wallet">Return to wallet</a></p>',
                ),
                status_code=400,
            )
        try:
            message, fees, _payment_hash, _preimage, _description_hash = (
                await asyncio.wait_for(
                    acorn.pay_multi_invoice(
                        lninvoice=state.invoice,
                        comment=payment_comment,
                    ),
                    timeout=settings.payment_timeout_seconds,
                )
            )
        except TimeoutError:
            logger.warning("lightning invoice payment timed out outcome=unknown")
            return HTMLResponse(
                _page(
                    "Invoice payment status unresolved",
                    "<p>The payment timed out before Safebox received a final result. "
                    "Do not retry it. Reconcile pending payments and review transaction "
                    "history first.</p><p><a href=\"/wallet\">Return to wallet</a></p>",
                ),
                status_code=504,
            )
        except Exception as exc:
            logger.warning(
                "lightning invoice payment did not return success error_type=%s",
                type(exc).__name__,
            )
            return HTMLResponse(
                _page(
                    "Invoice payment not confirmed",
                    "<p>Safebox did not receive a confirmed successful result. Do not "
                    "retry blindly. Reconcile pending payments and review transaction "
                    "history first.</p><p><a href=\"/wallet\">Return to wallet</a></p>",
                ),
                status_code=502,
            )
        return render_template(
            "payment_result.html",
            title="Invoice payment successful",
            amount=f"{state.amount:,}",
            fees=f"{int(fees):,}",
            recipient="Lightning invoice",
            message=str(message),
        )

    @app.get("/pay", response_class=HTMLResponse)
    async def payment_form(request: Request, acorn: PaymentAcornDependency) -> str:
        settings = request.app.state.settings
        try:
            clear_balances = await _payment_clear_balances(request, acorn)
        except Exception as exc:
            logger.warning(
                "Clear payment balance lookup failed error_type=%s",
                type(exc).__name__,
            )
            clear_balances = []
        verification, verification_error = await _read_proof_verification(
            acorn,
            settings.wallet_load_timeout_seconds,
        )
        return _payment_form(
            acorn.get_balance(),
            CsrfProtector(settings).issue(),
            balance_status=_balance_status_html(
                acorn.get_balance(),
                len(acorn.proofs),
                verification,
                verification_error,
            ),
            clear_balances=clear_balances,
        )

    @app.post("/pay", response_class=HTMLResponse)
    async def make_payment(
        request: Request,
        acorn: PaymentAcornDependency,
        csrf_token: str = Form(...),
        lightning_address: str = Form(...),
        amount: str = Form(...),
        comment: str = Form("Transferred from Safebox Web"),
        payment_mode: str = Form("confirmed"),
        payment_asset: str = Form("cash"),
        confirmed: str | None = Form(None),
    ):
        settings = request.app.state.settings
        form_token = CsrfProtector(settings)
        payment_mode = str(payment_mode).strip().lower()
        if payment_mode not in {"confirmed", "continuity"}:
            payment_mode = "confirmed"
        payment_asset = str(payment_asset or "cash").strip()
        try:
            clear_balances = await _payment_clear_balances(request, acorn)
        except Exception as exc:
            logger.warning(
                "Clear payment balance lookup failed error_type=%s",
                type(exc).__name__,
            )
            clear_balances = []
        selected_clear = None
        asset_error = None
        if payment_asset != "cash":
            identity = _decode_clear_payment_asset(payment_asset)
            if identity is None:
                asset_error = "Select a valid Cash or Clear Balance."
            else:
                selected_clear = next(
                    (
                        balance
                        for balance in clear_balances
                        if (
                            str(balance.get("mint") or "").rstrip("/"),
                            str(balance.get("unit") or ""),
                        )
                        == identity
                    ),
                    None,
                )
                if selected_clear is None:
                    asset_error = (
                        "The selected Clear Balance is no longer available. "
                        "Review the current balances before transferring."
                    )
        verification = None
        verification_error = None
        if selected_clear is not None:
            balance_status = (
                "<p>Selected Clear Balance: <strong>"
                f"{int(selected_clear['amount']):,} "
                f"{escape(str(selected_clear.get('display_unit') or selected_clear['unit']))}"
                "</strong></p>"
                f"<p>{escape(str(selected_clear.get('display_name') or selected_clear['unit']))}<br>"
                f"Canonical CMU: <code>{escape(str(selected_clear['unit']))}</code><br>"
                f"Issuing mint: <code>{escape(str(selected_clear['mint']))}</code></p>"
            )
        elif payment_mode == "confirmed":
            verification, verification_error = await _read_proof_verification(
                acorn,
                settings.wallet_load_timeout_seconds,
            )
            balance_status = _balance_status_html(
                acorn.get_balance(),
                len(acorn.proofs),
                verification,
                verification_error,
            )
        else:
            balance_status = (
                f"<p>Locally held proof total: "
                f"<strong>{int(acorn.get_balance()):,} sats</strong></p>"
                "<p>Continuity mode does not contact the mint. Received funds "
                "remain provisional until later reconciliation.</p>"
            )

        def payment_error(message: str, status_code: int = 400) -> HTMLResponse:
            return HTMLResponse(
                _payment_form(
                    acorn.get_balance(),
                    form_token.issue(),
                    message,
                    balance_status,
                    lightning_address=lightning_address,
                    amount=amount,
                    comment=comment,
                    payment_mode=payment_mode,
                    payment_asset=payment_asset,
                    clear_balances=clear_balances,
                ),
                status_code=status_code,
            )

        async def repair_stale_proofs_for_review(error_reason: str) -> HTMLResponse:
            try:
                repair_result = await asyncio.wait_for(
                    acorn.repair_proofs(),
                    timeout=settings.payment_timeout_seconds,
                )
            except TimeoutError:
                return payment_error(
                    "Safebox found stale proofs, but proof repair timed out. "
                    "Run proof maintenance before trying again.",
                    504,
                )
            except Exception as repair_exc:
                return payment_error(
                    "Safebox found stale proofs, but proof repair could not be "
                    f"completed: {repair_exc}",
                    502,
                )

            repaired_verification, repaired_error = await _read_proof_verification(
                acorn,
                settings.wallet_load_timeout_seconds,
            )
            repaired_balance_status = _balance_status_html(
                acorn.get_balance(),
                len(acorn.proofs),
                repaired_verification,
                repaired_error,
            )
            if repaired_verification is None or repaired_verification.get("status") != "clean":
                return HTMLResponse(
                    _payment_form(
                        acorn.get_balance(),
                        form_token.issue(),
                        "Safebox repaired stale proofs, but the wallet proof "
                        "state is still not clean. Review proof maintenance "
                        "before trying again.",
                        repaired_balance_status,
                        lightning_address=lightning_address,
                        amount=amount,
                        comment=comment,
                        payment_mode=payment_mode,
                        payment_asset=payment_asset,
                        clear_balances=clear_balances,
                    ),
                    status_code=409,
                )
            return HTMLResponse(
                _payment_form(
                    acorn.get_balance(),
                    form_token.issue(),
                    "Safebox repaired stale proofs. Review the recipient, "
                    "amount, and updated balance, then confirm the transfer again. "
                    f"Previous attempt stopped before confirmation: {error_reason}",
                    repaired_balance_status,
                    lightning_address=lightning_address,
                    amount=amount,
                    comment=comment,
                    payment_mode=payment_mode,
                    payment_asset=payment_asset,
                    clear_balances=clear_balances,
                ),
                status_code=409,
            )

        if not form_token.verify(csrf_token):
            return payment_error(
                "The form token is invalid or expired. Review the transfer again.",
                403,
            )
        if asset_error is not None:
            return payment_error(asset_error)
        if confirmed != "yes":
            return payment_error("Explicit transfer confirmation is required.")
        if selected_clear is not None and payment_mode == "continuity":
            return payment_error(
                "Continuity mode applies only to Cash transfers. Select Confirmed "
                "to send from a Clear Balance."
            )
        if (
            selected_clear is None
            and payment_mode == "confirmed"
            and verification is None
        ):
            return payment_error(
                "The transfer is blocked because a mint is unavailable. Continuity "
                "mode remains available for supported Safebox recipients.",
                503,
            )
        if (
            selected_clear is None
            and payment_mode == "confirmed"
            and verification.get("status") != "clean"
        ):
            return payment_error(
                "The transfer is blocked because the wallet proof state is not clean. "
                "Review it with 'acorn balance --verify' before spending.",
                409,
            )

        recipient = _normalize_lightning_address(lightning_address)
        if recipient is None:
            return payment_error("Enter a valid transfer address such as alice@example.com.")

        try:
            payment_amount = int(str(amount).strip())
        except ValueError:
            return payment_error("Transfer amount must be a whole number.")
        if payment_amount <= 0:
            return payment_error("Transfer amount must be greater than zero.")
        if selected_clear is not None:
            available_balance = int(selected_clear["amount"])
        elif payment_mode == "confirmed":
            available_balance = int(
                verification.get("mint_confirmed_unspent", {}).get("amount", 0)
            )
        else:
            available_balance = int(acorn.get_balance())
        if payment_amount > available_balance:
            return payment_error(
                "Transfer amount exceeds the available balance for this transfer mode."
            )

        payment_comment = str(comment).strip() or "Transferred from Safebox Web"
        if len(payment_comment) > 200:
            return payment_error("Transfer comment must be 200 characters or fewer.")

        if selected_clear is not None:
            clear_recipient = await _resolve_safebox_clear_recipient(
                recipient,
                mint=str(selected_clear["mint"]),
                unit=str(selected_clear["unit"]),
                timeout=settings.payment_timeout_seconds,
            )
            if clear_recipient is None:
                return payment_error(
                    "That address does not advertise support for this Clear "
                    "Balance. No value was sent.",
                    422,
                )
            sender = getattr(acorn, "send_clear_transfer", None)
            if sender is None:
                return payment_error(
                    "This Safebox installation does not yet support outgoing "
                    "Clear transfers.",
                    503,
                )
            try:
                delivery = await asyncio.wait_for(
                    sender(
                        amount=payment_amount,
                        recipient=clear_recipient["npub"],
                        relay=clear_recipient["relay"],
                        mint=str(selected_clear["mint"]),
                        unit=str(selected_clear["unit"]),
                        comment=payment_comment,
                    ),
                    timeout=settings.payment_timeout_seconds,
                )
            except TimeoutError:
                logger.warning(
                    "Clear payment timed out outcome=unknown recipient=%s mint=%s unit=%s",
                    clear_recipient["npub"],
                    selected_clear["mint"],
                    selected_clear["unit"],
                )
                return HTMLResponse(
                    _page(
                        "Clear transfer status unresolved",
                        "<p>The Clear transfer timed out before Safebox received a "
                        "final result. Do not retry it blindly. Review Clear "
                        "Transactions before attempting another payment.</p>"
                        '<p><a href="/clear">Review Clear Transactions</a></p>',
                    ),
                    status_code=504,
                )
            except Exception as exc:
                error_reason = str(exc).strip()
                logger.warning(
                    "Clear payment failed recipient=%s mint=%s unit=%s error_type=%s",
                    clear_recipient["npub"],
                    selected_clear["mint"],
                    selected_clear["unit"],
                    type(exc).__name__,
                )
                return HTMLResponse(
                    _page(
                        "Clear transfer not confirmed",
                        "<p>Safebox did not receive a confirmed successful Clear "
                        "transfer result. Do not retry blindly. Review Clear "
                        "Transactions first.</p>"
                        + (
                            f"<p><strong>Reason:</strong> {escape(error_reason)}</p>"
                            if error_reason
                            else ""
                        )
                        + '<p><a href="/clear">Review Clear Transactions</a></p>',
                    ),
                    status_code=502,
                )
            if not isinstance(delivery, dict) or delivery.get("status") != "OK":
                return HTMLResponse(
                    _page(
                        "Clear transfer not confirmed",
                        "<p>The transfer did not return a confirmed successful "
                        "result. Do not retry blindly. Review Clear Transactions "
                        "first.</p>"
                        '<p><a href="/clear">Review Clear Transactions</a></p>',
                    ),
                    status_code=502,
                )
            event_id = str(delivery.get("event_id") or "")
            message = "Private Clear transfer sent."
            if event_id:
                message += f" Event: {event_id}."
            message += (
                f" Canonical CMU: {selected_clear['unit']}. "
                "The recipient must accept it into the matching Clear Balance."
            )
            return render_template(
                "payment_result.html",
                title="Clear balance transferred",
                amount=f"{payment_amount:,}",
                fees=f"{int(delivery.get('fee') or 0):,}",
                unit=str(
                    selected_clear.get("display_unit") or selected_clear["unit"]
                ),
                recipient=recipient,
                message=message,
            )

        direct_recipient = await _resolve_safebox_lightning_recipient(
            recipient,
            timeout=settings.payment_timeout_seconds,
        )
        if payment_mode == "continuity" and direct_recipient is None:
            return payment_error(
                "Continuity Payments can only be sent to another Safebox address. "
                "No Lightning payment was attempted.",
                422,
            )
        if direct_recipient is not None:
            try:
                delivery = await asyncio.wait_for(
                    acorn.send_ecash_transfer(
                        amount=payment_amount,
                        recipient=direct_recipient["npub"],
                        relay=direct_recipient["relay"],
                        comment=payment_comment,
                        **(
                            {"payment_mode": "continuity"}
                            if payment_mode == "continuity"
                            else {}
                        ),
                    ),
                    timeout=settings.payment_timeout_seconds,
                )
            except TimeoutError:
                logger.warning(
                    "direct safebox ecash payment timed out outcome=unknown recipient=%s relay=%s",
                    direct_recipient["npub"],
                    direct_recipient["relay"],
                )
                return HTMLResponse(
                    _page(
                        "Transfer status unresolved",
                        "<p>The direct Safebox transfer timed out before Safebox "
                        "received a final result. Do not retry it blindly. Review "
                        "transaction history before attempting another transfer.</p>"
                        '<p><a href="/wallet">Return to wallet</a></p>',
                    ),
                    status_code=504,
                )
            except Exception as exc:
                error_reason = str(exc).strip()
                if payment_mode == "confirmed" and _is_stale_proof_error(error_reason):
                    return await repair_stale_proofs_for_review(error_reason)
                logger.warning(
                    "direct safebox ecash payment failed recipient=%s relay=%s error_type=%s error=%s",
                    direct_recipient["npub"],
                    direct_recipient["relay"],
                    type(exc).__name__,
                    str(exc),
                )
                return HTMLResponse(
                    _page(
                        "Transfer not completed",
                        "<p>Safebox found a recipient Safebox address, but direct "
                        "funds delivery could not be completed. Review "
                        "transaction history before deciding whether another "
                        "transfer is safe.</p>"
                        + (
                            f"<p><strong>Reason:</strong> {escape(error_reason)}</p>"
                            if error_reason
                            else ""
                        )
                        + '<p><a href="/wallet">Return to wallet</a></p>',
                    ),
                    status_code=502,
                )
            if not isinstance(delivery, dict) or delivery.get("status") != "OK":
                logger.warning(
                    "direct safebox ecash payment returned unconfirmed result recipient=%s relay=%s result=%r",
                    direct_recipient["npub"],
                    direct_recipient["relay"],
                    delivery,
                )
                return HTMLResponse(
                    _page(
                        "Transfer not confirmed",
                        "<p>Safebox found a recipient Safebox address, but direct "
                        "funds delivery did not return a confirmed successful "
                        "result. Review transaction history before deciding "
                        "whether another transfer is safe.</p>"
                        '<p><a href="/wallet">Return to wallet</a></p>',
                    ),
                    status_code=502,
                )
            event_id = str(delivery.get("event_id") or delivery.get("event") or "")
            if payment_mode == "continuity":
                message = (
                    "Provisional Continuity Payment sent. The mint was not "
                    "contacted; the recipient must reconcile the proofs later."
                )
            else:
                message = "Direct Safebox funds transfer sent."
            if event_id:
                message += f" Event: {event_id}."
            return render_template(
                "payment_result.html",
                title=(
                    "Continuity Payment sent"
                    if payment_mode == "continuity"
                    else "Balance transferred"
                ),
                amount=f"{payment_amount:,}",
                fees="0",
                recipient=recipient,
                message=message,
            )

        try:
            message, fees = await asyncio.wait_for(
                acorn.pay_multi(
                    amount=payment_amount,
                    lnaddress=recipient,
                    comment=payment_comment,
                ),
                timeout=settings.payment_timeout_seconds,
            )
        except TimeoutError:
            logger.warning("lightning payment timed out outcome=unknown")
            return HTMLResponse(
                _page(
                    "Transfer status unresolved",
                    "<p>The Lightning payment timed out before Safebox received a final result. "
                    "Do not retry it. Use <code>acorn reconcile-payments</code> and "
                    "review transaction history before attempting another transfer.</p>"
                    '<p><a href="/wallet">Return to wallet</a></p>',
                ),
                status_code=504,
            )
        except Exception as exc:
            error_reason = str(exc).strip()
            stale_proofs = _is_stale_proof_error(error_reason)
            if stale_proofs:
                return await repair_stale_proofs_for_review(error_reason)
            recovery_guidance = (
                "<p>The wallet proof state needs maintenance before another "
                "transfer. Run <code>acorn check-proofs</code>, then "
                "<code>acorn repair-proofs</code> if repair is recommended, "
                "and confirm the balance with <code>acorn balance --verify</code>.</p>"
                if stale_proofs
                else "<p>Do not retry blindly. Review transaction history and run "
                "<code>acorn reconcile-payments</code> before deciding whether "
                "another transfer is safe.</p>"
            )
            logger.warning(
                "lightning payment did not return success error_type=%s error=%s",
                type(exc).__name__,
                str(exc),
            )
            return HTMLResponse(
                _page(
                    "Transfer not confirmed",
                    "<p>Safebox did not receive a confirmed successful result.</p>"
                    + recovery_guidance
                    + (
                        f"<p><strong>Reason:</strong> {escape(error_reason)}</p>"
                        if error_reason
                        else ""
                    )
                    + '<p><a href="/wallet">Return to wallet</a></p>',
                ),
                status_code=502,
            )

        return render_template(
            "payment_result.html",
            title="Balance transferred",
            amount=f"{payment_amount:,}",
            fees=f"{int(fees):,}",
            recipient=recipient,
            message=str(message),
        )

    @app.get("/transactions", response_class=HTMLResponse)
    async def transactions(
        request: Request,
        acorn: LoadedAcornDependency,
        session: DatabaseSessionDependency,
    ):
        settings = request.app.state.settings
        checks_performed = request.query_params.get("check") == "1"
        verification = None
        verification_error = None
        if checks_performed:
            verification, verification_error = await _read_proof_verification(
                acorn,
                settings.wallet_load_timeout_seconds,
            )
        wallet_balance, wallet_balance_verified = _wallet_balance_summary(
            acorn.get_balance(),
            verification,
        )
        fiat_estimate = None
        if settings.currency_rates_enabled:
            fiat_estimate = currency_balance_estimate(
                session,
                sats=wallet_balance,
                currency_code=settings.default_display_currency,
                stale_seconds=settings.currency_rate_stale_seconds,
            )
        try:
            history = await asyncio.wait_for(
                acorn.get_tx_history(),
                timeout=settings.wallet_load_timeout_seconds,
            )
        except TimeoutError:
            return HTMLResponse(
                _page(
                    "Cash Transactions",
                    '<p class="error">Timed out while loading cash transactions.</p>'
                    '<p><a href="/wallet">Return to wallet</a></p>',
                ),
                status_code=504,
            )
        except Exception as exc:
            logger.warning(
                "transaction history lookup failed error_type=%s",
                type(exc).__name__,
            )
            return HTMLResponse(
                _page(
                    "Cash Transactions",
                    '<p class="error">Unable to load cash transactions from the bootstrap relay.</p>'
                    '<p><a href="/wallet">Return to wallet</a></p>',
                ),
                status_code=502,
            )

        entries = history if isinstance(history, list) else []
        entries = sorted(
            entries,
            key=lambda entry: (
                str(entry.get("create_time") or "")
                if isinstance(entry, dict)
                else ""
            ),
            reverse=True,
        )
        continuity_receipts = []
        incoming_preview = {}
        if checks_performed:
            try:
                continuity_receipts = await _read_continuity_receipts(
                    acorn,
                    settings.wallet_load_timeout_seconds,
                )
            except Exception as exc:
                logger.warning(
                    "continuity receipt lookup failed error_type=%s",
                    type(exc).__name__,
                )
            try:
                incoming_preview = await _preview_incoming_payments(
                    acorn,
                    settings.wallet_load_timeout_seconds,
                )
            except Exception as exc:
                logger.warning(
                    "incoming payment preview failed error_type=%s",
                    type(exc).__name__,
                )
        pending_amount, pending_count = _pending_transaction_totals(
            continuity_receipts,
            incoming_preview,
        )
        pending_transactions = _pending_transaction_view(
            continuity_receipts,
            incoming_preview,
        )
        finalization_job = get_finalization_job(
            request.app.state.database_engine,
            acorn.pubkey_bech32,
        )
        return _transactions_page(
            entries,
            CsrfProtector(settings).issue(),
            retention_notice=_ecash_retention_notice(settings),
            wallet_balance=wallet_balance,
            wallet_balance_verified=wallet_balance_verified,
            balance_status=(
                _balance_status_html(
                    acorn.get_balance(),
                    len(acorn.proofs),
                    verification,
                    verification_error,
                )
                if checks_performed
                else _unchecked_balance_status_html(
                    acorn.get_balance(),
                    len(acorn.proofs),
                )
            ),
            pending_amount=pending_amount,
            pending_count=pending_count,
            fiat_estimate=fiat_estimate,
            finalization_job=finalization_job,
            pending_transactions=pending_transactions,
            checks_performed=checks_performed,
        )

    @app.get("/clear", response_class=HTMLResponse)
    async def clear_transactions(
        request: Request,
        acorn: LoadedAcornDependency,
    ) -> HTMLResponse:
        settings = request.app.state.settings
        try:
            receipts = await _read_clear_receipts(
                acorn,
                settings.wallet_load_timeout_seconds,
                status=None,
            )
        except Exception as exc:
            logger.warning(
                "clear transaction lookup failed error_type=%s",
                type(exc).__name__,
            )
            return HTMLResponse(
                _page(
                    "Clear Transactions",
                    '<p class="error">Unable to load Clear transactions from the bootstrap relay.</p>'
                    '<p><a href="/wallet">Return to wallet</a></p>',
                ),
                status_code=502,
            )

        balance_result, history_result = await asyncio.gather(
            _read_clear_balances(acorn, settings.wallet_load_timeout_seconds),
            _read_clear_history(acorn, settings.wallet_load_timeout_seconds),
            return_exceptions=True,
        )
        if isinstance(balance_result, BaseException):
            logger.warning(
                "clear balance lookup failed error_type=%s",
                type(balance_result).__name__,
            )
            spendable_balances = []
        else:
            spendable_balances = balance_result
        if isinstance(history_result, BaseException):
            logger.warning(
                "clear history lookup failed error_type=%s",
                type(history_result).__name__,
            )
            history = []
        else:
            history = history_result
        # New relay transfers are discovered only through the explicit
        # "Check for Clear Transfers" action below. A normal GET renders stored
        # receipts, balances, and history without performing another scan.
        pending_receipts = receipts

        try:
            clear_summary = await _resolve_clear_aliases(
                _clear_balance_summary(pending_receipts, spendable_balances),
                timeout=settings.wallet_load_timeout_seconds,
                configured_mints=settings.clear_mints,
                cache=request.app.state.clear_mint_metadata_cache,
            )
        except Exception as exc:
            logger.warning(
                "clear mint metadata lookup failed error_type=%s",
                type(exc).__name__,
            )
            clear_summary = _clear_balance_summary(
                pending_receipts,
                spendable_balances,
            )

        entries = _clear_transaction_view(
            pending_receipts,
            clear_summary,
            history,
        )
        pending_entries = [
            entry for entry in entries if entry.get("status") == "pending"
        ]
        history_entries = [
            entry for entry in entries if entry.get("status") != "pending"
        ]
        acceptance_job = get_clear_acceptance_job(
            request.app.state.database_engine,
            acorn.pubkey_bech32,
        )

        return HTMLResponse(
            render_template(
                "clear_transactions.html",
                title="Clear Transactions",
                headline_class="transaction-headline",
                clear_summary=clear_summary,
                pending_entries=pending_entries,
                history_entries=history_entries,
                acceptance_job=acceptance_job,
                csrf_token=CsrfProtector(settings).issue(),
                notice=_clear_page_notice(request.query_params),
            )
        )

    @app.post("/clear/receive", response_class=HTMLResponse)
    async def receive_clear_transfers(
        request: Request,
        acorn: LoadedAcornDependency,
        csrf_token: str = Form(...),
    ):
        settings = request.app.state.settings
        if not CsrfProtector(settings).verify(csrf_token):
            return HTMLResponse(
                _page(
                    "Clear transfers not checked",
                    '<p class="error">The form token is invalid or expired.</p>'
                    '<p><a href="/clear">Return to Clear Transactions</a></p>',
                ),
                status_code=403,
            )

        receiver = getattr(acorn, "sweep_clear_transfers", None)
        if receiver is None:
            return HTMLResponse(
                _page(
                    "Clear transfer receive unavailable",
                    "<p class=\"error\">This Safebox Acorn installation does not "
                    "support receiving Clear transfers. Update the component "
                    "before trying again.</p>"
                    '<p><a href="/clear">Return to Clear Transactions</a></p>',
                ),
                status_code=501,
            )
        try:
            result = await asyncio.wait_for(
                receiver(),
                timeout=settings.wallet_load_timeout_seconds,
            )
        except Exception as exc:
            logger.warning(
                "Clear transfer receive failed error_type=%s",
                type(exc).__name__,
            )
            return HTMLResponse(
                _page(
                    "Clear transfers not checked",
                    '<p class="error">Safebox could not complete the Clear '
                    "transfer relay check.</p>"
                    '<p><a href="/clear">Return to Clear Transactions</a></p>',
                ),
                status_code=502,
            )

        stored_count = max(0, int((result or {}).get("stored_count", 0)))
        return RedirectResponse(f"/clear?received={stored_count}", status_code=303)

    @app.post("/clear/receipts/accept", response_class=HTMLResponse)
    async def accept_pending_clear_receipt(
        request: Request,
        acorn: AcornDependency,
        event_id: str = Form(...),
        csrf_token: str = Form(...),
    ):
        settings = request.app.state.settings
        if not CsrfProtector(settings).verify(csrf_token):
            return HTMLResponse(
                _page(
                    "Clear transfer not accepted",
                    '<p class="error">The form token is invalid or expired.</p>'
                    '<p><a href="/clear">Return to Clear Transactions</a></p>',
                ),
                status_code=403,
            )

        accepter = getattr(acorn, "accept_pending_clear_receipt", None)
        if accepter is None:
            return HTMLResponse(
                _page(
                    "Clear transfer acceptance unavailable",
                    '<p class="error">This Safebox Acorn installation does not '
                    "support accepting Clear transfers. Update the component "
                    "before trying again.</p>"
                    '<p><a href="/clear">Return to Clear Transactions</a></p>',
                ),
                status_code=501,
            )
        event_id = str(event_id or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", event_id) is None:
            return HTMLResponse(
                _page(
                    "Clear transfer not accepted",
                    '<p class="error">The pending transfer identifier is invalid.</p>'
                    '<p><a href="/clear">Return to Clear Transactions</a></p>',
                ),
                status_code=400,
            )
        npub = acorn.pubkey_bech32
        cash_job = get_finalization_job(
            request.app.state.database_engine,
            npub,
        )
        if cash_job and cash_job.get("status") == "RUNNING":
            return RedirectResponse(
                "/transactions?finalization=running",
                status_code=303,
            )
        claimed, owner_token, _job = claim_clear_acceptance_job(
            request.app.state.database_engine,
            npub,
            event_id,
            worker_id=request.app.state.worker_id,
        )
        if claimed:
            task = asyncio.create_task(
                run_clear_acceptance_job(
                    engine=request.app.state.database_engine,
                    acorn=acorn,
                    npub=npub,
                    event_id=event_id,
                    owner_token=owner_token,
                    load_timeout_seconds=settings.wallet_load_timeout_seconds,
                ),
                name=f"clear-acceptance:{npub}",
            )
            request.app.state.clear_acceptance_tasks[npub] = task

            def remove_completed_task(completed: asyncio.Task) -> None:
                current = request.app.state.clear_acceptance_tasks.get(npub)
                if current is completed:
                    request.app.state.clear_acceptance_tasks.pop(npub, None)

            task.add_done_callback(remove_completed_task)
            state = "started"
        else:
            state = "running"
        return RedirectResponse(
            f"/clear?acceptance={state}",
            status_code=303,
        )

    @app.post("/clear/receipts/delete", response_class=HTMLResponse)
    async def delete_pending_clear_receipt(
        request: Request,
        acorn: LoadedAcornDependency,
        event_id: str = Form(...),
        csrf_token: str = Form(...),
        confirmed: str | None = Form(None),
    ):
        settings = request.app.state.settings
        if not CsrfProtector(settings).verify(csrf_token):
            return HTMLResponse(
                _page(
                    "Clear transfer not deleted",
                    '<p class="error">The form token is invalid or expired.</p>'
                    '<p><a href="/clear">Return to Clear Transactions</a></p>',
                ),
                status_code=403,
            )
        if confirmed != "yes":
            return HTMLResponse(
                _page(
                    "Clear transfer not deleted",
                    '<p class="error">Explicit deletion confirmation is required.</p>'
                    '<p><a href="/clear">Return to Clear Transactions</a></p>',
                ),
                status_code=400,
            )

        deleter = getattr(acorn, "delete_pending_clear_receipt", None)
        if deleter is None:
            return HTMLResponse(
                _page(
                    "Clear transfer deletion unavailable",
                    "<p class=\"error\">This Safebox Acorn installation does not "
                    "support pending Clear transfer deletion. Update the component "
                    "before trying again.</p>"
                    '<p><a href="/clear">Return to Clear Transactions</a></p>',
                ),
                status_code=501,
            )
        try:
            await asyncio.wait_for(
                deleter(event_id),
                timeout=settings.wallet_load_timeout_seconds,
            )
        except ValueError as exc:
            error_text = str(exc).lower()
            if "not found" in error_text:
                message = "Pending Clear transfer was not found."
            elif "only pending" in error_text:
                message = "Only pending Clear transfers can be deleted."
            else:
                message = "The pending Clear transfer could not be deleted."
            return HTMLResponse(
                _page(
                    "Clear transfer not deleted",
                    f'<p class="error">{escape(message)}</p>'
                    '<p><a href="/clear">Return to Clear Transactions</a></p>',
                ),
                status_code=400,
            )
        except Exception as exc:
            logger.warning(
                "pending Clear transfer deletion failed error_type=%s",
                type(exc).__name__,
            )
            return HTMLResponse(
                _page(
                    "Clear transfer not deleted",
                    '<p class="error">Safebox could not confirm deletion of the '
                    "pending Clear transfer.</p>"
                    '<p><a href="/clear">Return to Clear Transactions</a></p>',
                ),
                status_code=502,
            )

        return RedirectResponse("/clear?receipt_deleted=1", status_code=303)

    @app.post("/transactions/finalize-background")
    async def start_background_funds_finalization(
        request: Request,
        acorn: ReceiveAcornDependency,
        csrf_token: str = Form(...),
    ) -> RedirectResponse:
        settings = request.app.state.settings
        if not CsrfProtector(settings).verify(csrf_token):
            raise HTTPException(status_code=403, detail="Form token is invalid or expired")

        npub = acorn.pubkey_bech32
        clear_job = get_clear_acceptance_job(
            request.app.state.database_engine,
            npub,
        )
        if clear_job and clear_job.get("status") == "RUNNING":
            return RedirectResponse(
                "/clear?acceptance=running",
                status_code=303,
            )
        claimed, owner_token, _job = claim_finalization_job(
            request.app.state.database_engine,
            npub,
            worker_id=request.app.state.worker_id,
        )
        if claimed:
            task = asyncio.create_task(
                run_finalization_job(
                    engine=request.app.state.database_engine,
                    acorn=acorn,
                    npub=npub,
                    owner_token=owner_token,
                ),
                name=f"funds-finalization:{npub}",
            )
            request.app.state.finalization_tasks[npub] = task

            def remove_completed_task(completed: asyncio.Task) -> None:
                current = request.app.state.finalization_tasks.get(npub)
                if current is completed:
                    request.app.state.finalization_tasks.pop(npub, None)

            task.add_done_callback(remove_completed_task)
            state = "started"
        else:
            state = "running"
        return RedirectResponse(
            f"/transactions?finalization={state}",
            status_code=303,
        )

    @app.post("/transactions/receive", response_class=HTMLResponse)
    async def receive_ecash_from_transactions(
        request: Request,
        acorn: ReceiveAcornDependency,
        csrf_token: str = Form(...),
        force: bool = Form(False),
        force_confirm: str | None = Form(None),
    ):
        settings = request.app.state.settings
        if not CsrfProtector(settings).verify(csrf_token):
            return HTMLResponse(
                _page(
                    "Receive funds",
                    '<p class="error">The form expired or could not be verified.</p>'
                    '<p><a href="/transactions">Return to transaction history</a></p>',
                ),
                status_code=403,
            )

        if force and force_confirm != "acknowledged":
            return HTMLResponse(
                _page(
                    "Force finalization not confirmed",
                    '<p class="error">Force finalization can permanently remove '
                    "proofs that the mint reports as spent or stale. Confirm the "
                    "warning before continuing.</p>"
                    '<p><a href="/transactions">Return to transaction history</a></p>',
                ),
                status_code=400,
            )

        stale_reconciliation: dict | None = None
        if force:
            reconciler = getattr(acorn, "reconcile_stale_proofs", None)
            if reconciler is None:
                return HTMLResponse(
                    _page(
                        "Force finalization unavailable",
                        '<p class="error">This Safebox Acorn installation does not '
                        "provide targeted stale-proof reconciliation. Update the "
                        "component before using force finalization.</p>"
                        '<p><a href="/transactions">Return to transaction history</a></p>',
                    ),
                    status_code=409,
                )
            try:
                stale_reconciliation = await asyncio.wait_for(
                    reconciler(),
                    timeout=settings.payment_timeout_seconds,
                )
            except TimeoutError:
                return HTMLResponse(
                    _page(
                        "Force finalization timed out",
                        '<p class="error">Proof reconciliation timed out. Wallet state '
                        "may have changed before the timeout. Review the wallet and "
                        "transaction history before trying again.</p>"
                        '<p><a href="/transactions">Return to transaction history</a></p>',
                    ),
                    status_code=504,
                )
            except Exception as exc:
                logger.warning(
                    "targeted stale-proof reconciliation failed error_type=%s error=%s",
                    type(exc).__name__,
                    str(exc),
                )
                return HTMLResponse(
                    _page(
                        "Force finalization stopped safely",
                        '<p class="error">Safebox could not conclusively identify stale '
                        "spent proofs, so pending transactions were not finalized.</p>"
                        f"<p><strong>Reason:</strong> {escape(str(exc))}</p>"
                        '<p><a href="/transactions">Return to transaction history</a></p>',
                    ),
                    status_code=409,
                )

        try:
            result = await asyncio.wait_for(
                acorn.sweep_ecash_transfers(finalize=False),
                timeout=settings.payment_timeout_seconds,
            )
        except TimeoutError:
            return HTMLResponse(
                _page(
                    "Receive funds outcome uncertain",
                    '<p class="error">The receive operation timed out. It may have '
                    "accepted proofs before the timeout. Review the wallet balance and "
                    "transaction history before trying again.</p>"
                    '<p><a href="/transactions">Reload transaction history</a></p>',
                ),
                status_code=504,
            )
        except Exception as exc:
            error_reason = str(exc).strip()
            if _is_stale_proof_error(error_reason):
                try:
                    await asyncio.wait_for(
                        acorn.repair_proofs(),
                        timeout=settings.payment_timeout_seconds,
                    )
                except TimeoutError:
                    return HTMLResponse(
                        _page(
                            "Unable to receive funds",
                            '<p class="error">Safebox found stale proofs, but '
                            "proof repair timed out. Review wallet balance and "
                            "transaction history before trying again.</p>"
                            '<p><a href="/transactions">Return to transaction history</a></p>',
                        ),
                        status_code=504,
                    )
                except Exception as repair_exc:
                    return HTMLResponse(
                        _page(
                            "Unable to receive funds",
                            '<p class="error">Safebox found stale proofs, but '
                            "proof repair could not be completed.</p>"
                            f"<p><strong>Reason:</strong> {escape(str(repair_exc))}</p>"
                            '<p><a href="/transactions">Return to transaction history</a></p>',
                        ),
                        status_code=502,
                    )
                return HTMLResponse(
                    _page(
                        "Proofs repaired",
                        "<p>Safebox repaired stale proofs before accepting "
                        "incoming payments. Finalize pending transactions again "
                        "to complete the operation.</p>"
                        '<p><a href="/transactions">Return to transaction history</a></p>',
                    ),
                    status_code=409,
                )
            logger.warning(
                "incoming ecash receive failed error_type=%s error=%s",
                type(exc).__name__,
                str(exc),
            )
            return HTMLResponse(
                _page(
                    "Unable to receive funds",
                    '<p class="error">Safebox could not complete the incoming funds '
                    "check. No unverified balance has been displayed.</p>"
                    + (
                        f"<p><strong>Reason:</strong> {escape(error_reason)}</p>"
                        if error_reason
                        else ""
                    )
                    + '<p><a href="/transactions">Return to transaction history</a></p>',
                ),
                    status_code=502,
                )

        try:
            reconciliation = await _reconcile_continuity_receipts(
                acorn,
                settings.payment_timeout_seconds,
            )
        except Exception as exc:
            logger.warning(
                "continuity reconciliation failed error_type=%s error=%s",
                type(exc).__name__,
                str(exc),
            )
            reconciliation = {
                "supported": True,
                "confirmed_count": 0,
                "confirmed_amount": 0,
                "pending_count": int(result.get("provisional_count", 0)),
                "pending_amount": int(result.get("provisional_amount", 0)),
            }

        accepted_count = int(result.get("accepted_count", 0))
        confirmed_count = (
            int(result.get("confirmed_count", accepted_count))
            + int(reconciliation.get("confirmed_count", 0))
        )
        provisional_count = int(
            reconciliation.get("pending_count", 0)
            if reconciliation.get("supported")
            else result.get("provisional_count", 0)
        )
        accepted_amount = (
            int(result.get("accepted_amount", 0))
            + int(reconciliation.get("confirmed_amount", 0))
        )
        provisional_amount = int(
            reconciliation.get("pending_amount", 0)
            if reconciliation.get("supported")
            else result.get("provisional_amount", 0)
        )
        terminal_error_count = int(reconciliation.get("terminal_error_count", 0))
        terminal_error_amount = int(reconciliation.get("terminal_error_amount", 0))
        if confirmed_count and provisional_count:
            notice = (
                f"Finalized {accepted_amount:,} sats. "
                f"{provisional_amount:,} sats remain pending."
            )
        elif confirmed_count:
            notice = f"Finalized {accepted_amount:,} sats."
        elif provisional_count:
            notice = f"{provisional_amount:,} sats remain pending."
        else:
            notice = "No pending transactions were found."
        if terminal_error_count:
            terminal_notice = (
                f"Recorded {terminal_error_count:,} failed transaction"
                f"{'s' if terminal_error_count != 1 else ''} totaling "
                f"{terminal_error_amount:,} sats; no balance was credited."
            )
            notice = (
                terminal_notice
                if not confirmed_count and not provisional_count
                else f"{notice} {terminal_notice}"
            )
        if force and stale_reconciliation is not None:
            removed = int(stale_reconciliation.get("removed", 0))
            removed_amount = int(stale_reconciliation.get("amount", 0))
            if removed:
                notice = (
                    f"Removed {removed_amount:,} sats in {removed:,} stale proof"
                    f"{'s' if removed != 1 else ''}. {notice}"
                )
            else:
                notice = f"No mint-confirmed stale proofs were found. {notice}"

        try:
            history = await _read_receive_history_with_retry(
                acorn,
                accepted_amount=accepted_amount,
                accepted_count=confirmed_count,
                timeout=settings.wallet_load_timeout_seconds,
            )
        except Exception as exc:
            logger.warning(
                "post-receive transaction history lookup failed error_type=%s",
                type(exc).__name__,
            )
            return HTMLResponse(
                _page(
                    "Funds receive completed",
                    f"<p><strong>{escape(notice)}</strong></p>"
                    "<p>The updated transaction history could not be loaded. "
                    "Reload it to verify the resulting credit.</p>"
                    '<p><a href="/transactions">Reload transaction history</a></p>',
                ),
                status_code=200,
            )

        verification = None
        if confirmed_count or not provisional_count:
            verification, _verification_error = await _read_proof_verification(
                acorn,
                settings.wallet_load_timeout_seconds,
            )
        wallet_balance, wallet_balance_verified = _wallet_balance_summary(
            acorn.get_balance(),
            verification,
        )
        entries = history if isinstance(history, list) else []
        entries = sorted(
            entries,
            key=lambda entry: (
                str(entry.get("create_time") or "")
                if isinstance(entry, dict)
                else ""
            ),
            reverse=True,
        )
        try:
            continuity_receipts = await _read_continuity_receipts(
                acorn,
                settings.wallet_load_timeout_seconds,
            )
        except Exception as exc:
            logger.warning(
                "continuity receipt lookup failed error_type=%s",
                type(exc).__name__,
            )
            continuity_receipts = []
        pending_amount = sum(
            int(receipt.get("amount") or 0) for receipt in continuity_receipts
        )
        pending_count = len(continuity_receipts)
        if not _history_has_receive_credit(
            entries,
            accepted_amount=accepted_amount,
            accepted_count=confirmed_count,
        ):
            notice += (
                " The wallet balance may already reflect the accepted funds, "
                "but the matching transaction-history entry was not readable "
                "yet. Reload transaction history before relying on the journal."
            )
        return _transactions_page(
            entries,
            CsrfProtector(settings).issue(),
            notice=notice,
            retention_notice=_ecash_retention_notice(settings),
            wallet_balance=wallet_balance,
            wallet_balance_verified=wallet_balance_verified,
            pending_amount=pending_amount,
            pending_count=pending_count,
        )

    @app.get("/record/present", response_class=HTMLResponse)
    async def present_record_form(
        request: Request,
        acorn: RecordAcornDependency,
        label: str,
    ) -> HTMLResponse:
        settings = request.app.state.settings
        try:
            record_label = _validate_record_label(label)
            await asyncio.wait_for(
                acorn.get_record_safebox(record_name=record_label),
                timeout=settings.wallet_load_timeout_seconds,
            )
        except (ValueError, TimeoutError):
            return HTMLResponse(
                _page(
                    "Present Record",
                    '<p class="error">The requested record could not be loaded.</p>'
                    '<p><a class="nav-button" href="/records">Return to records</a></p>',
                ),
                status_code=404,
            )
        return HTMLResponse(
            render_template(
                "record_present.html",
                title="Present Record",
                label=record_label,
                record_url=f'/record?{urlencode({"label": record_label})}',
                csrf_token=CsrfProtector(settings).issue(),
                error=None,
            ),
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/record/present", response_class=HTMLResponse)
    async def create_record_presentation(
        request: Request,
        acorn: RecordAcornDependency,
        csrf_token: str = Form(...),
        label: str = Form(...),
    ) -> HTMLResponse:
        settings = request.app.state.settings
        form_token = CsrfProtector(settings)
        try:
            record_label = _validate_record_label(label)
        except ValueError as exc:
            return HTMLResponse(_page("Present Record", f'<p class="error">{escape(str(exc))}</p>'), status_code=400)
        record_url = f'/record?{urlencode({"label": record_label})}'
        if not form_token.verify(csrf_token):
            return HTMLResponse(
                render_template(
                    "record_present.html",
                    title="Present Record",
                    label=record_label,
                    record_url=record_url,
                    csrf_token=form_token.issue(),
                    error="The form token is invalid or expired. Try again.",
                ),
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        try:
            presentation = await asyncio.wait_for(
                acorn.create_record_presentation(
                    record_label,
                    expires_in=3600,
                    blossom_transfer_server=settings.blossom_home_server,
                ),
                timeout=settings.payment_timeout_seconds,
            )
        except TimeoutError:
            error = "Creating the temporary presentation timed out. Its outcome may be uncertain."
        except Exception as exc:
            logger.warning("record presentation creation failed error_type=%s", type(exc).__name__)
            error = "Safebox could not create the temporary presentation."
        else:
            descriptor = str(presentation["descriptor"])
            return HTMLResponse(
                render_template(
                    "record_present_qr.html",
                    title="Present Record",
                    label=record_label,
                    record_url=record_url,
                    descriptor=descriptor,
                    presentation_qr=_qr_svg(descriptor),
                    csrf_token=form_token.issue(),
                    expires_at=datetime.fromtimestamp(
                        int(presentation["expires_at"]), tz=timezone.utc
                    ).strftime("%Y-%m-%d %H:%M UTC"),
                ),
                headers={"Cache-Control": "no-store"},
            )
        return HTMLResponse(
            render_template(
                "record_present.html",
                title="Present Record",
                label=record_label,
                record_url=record_url,
                csrf_token=form_token.issue(),
                error=error,
            ),
            status_code=502,
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/record/present/stop", response_class=HTMLResponse)
    async def stop_record_presentation(
        request: Request,
        acorn: RecordAcornDependency,
        csrf_token: str = Form(...),
        descriptor: str = Form(...),
        label: str = Form(...),
    ) -> HTMLResponse:
        settings = request.app.state.settings
        if not CsrfProtector(settings).verify(csrf_token):
            return HTMLResponse(_page("Stop Presenting", '<p class="error">The form token is invalid or expired.</p>'), status_code=403)
        try:
            record_label = _validate_record_label(label)
            await asyncio.wait_for(
                acorn.delete_record_transfer(
                    str(descriptor).strip(),
                    allowed_servers=[settings.blossom_home_server],
                ),
                timeout=settings.payment_timeout_seconds,
            )
            message = f"Presentation of {escape(record_label)} has stopped."
        except Exception as exc:
            logger.warning("record presentation stop uncertain error_type=%s", type(exc).__name__)
            message = "The presentation is closed. Deletion of its temporary copy could not be confirmed."
        return HTMLResponse(
            render_template(
                "record_presentation_closed.html",
                title="Presentation Closed",
                message=message,
                record_url=f'/record?{urlencode({"label": str(label)})}',
            ),
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/record/presentation/done", response_class=HTMLResponse)
    async def finish_record_presentation(
        request: Request,
        acorn: RecordAcornDependency,
        csrf_token: str = Form(...),
        descriptor: str = Form(...),
    ) -> HTMLResponse:
        settings = request.app.state.settings
        if not CsrfProtector(settings).verify(csrf_token):
            return HTMLResponse(_page("Close Presentation", '<p class="error">The form token is invalid or expired.</p>'), status_code=403)
        deleted = False
        try:
            result = await asyncio.wait_for(
                acorn.delete_record_transfer(
                    str(descriptor).strip(),
                    allowed_servers=[settings.blossom_home_server],
                ),
                timeout=settings.payment_timeout_seconds,
            )
            deleted = bool(result.get("transfer_deleted"))
        except Exception as exc:
            logger.info("presentation recipient cleanup unavailable error_type=%s", type(exc).__name__)
        return HTMLResponse(
            render_template(
                "record_presentation_closed.html",
                title="Presentation Complete",
                message=(
                    "The temporary presentation was deleted."
                    if deleted
                    else "The presentation is closed; its temporary copy was already unavailable or deletion could not be confirmed."
                ),
                record_url=None,
            ),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/record/share", response_class=HTMLResponse)
    async def share_record_form(
        request: Request,
        acorn: RecordAcornDependency,
        label: str,
    ) -> HTMLResponse:
        settings = request.app.state.settings
        try:
            record_label = _validate_record_label(label)
            await asyncio.wait_for(
                acorn.get_record_safebox(record_name=record_label),
                timeout=settings.wallet_load_timeout_seconds,
            )
        except (ValueError, TimeoutError):
            return HTMLResponse(
                _page(
                    "Share Record",
                    '<p class="error">The requested record could not be loaded.</p>'
                    '<p><a class="nav-button" href="/records">Return to records</a></p>',
                ),
                status_code=404,
            )
        return HTMLResponse(
            render_template(
                "record_share.html",
                title="Share Record",
                label=record_label,
                record_url=f'/record?{urlencode({"label": record_label})}',
                csrf_token=CsrfProtector(settings).issue(),
                error=None,
            ),
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/record/share", response_class=HTMLResponse)
    async def create_record_share(
        request: Request,
        acorn: RecordAcornDependency,
        csrf_token: str = Form(...),
        label: str = Form(...),
    ) -> HTMLResponse:
        settings = request.app.state.settings
        form_token = CsrfProtector(settings)
        try:
            record_label = _validate_record_label(label)
        except ValueError as exc:
            return HTMLResponse(_page("Share Record", f'<p class="error">{escape(str(exc))}</p>'), status_code=400)
        record_url = f'/record?{urlencode({"label": record_label})}'
        if not form_token.verify(csrf_token):
            return HTMLResponse(
                render_template(
                    "record_share.html",
                    title="Share Record",
                    label=record_label,
                    record_url=record_url,
                    csrf_token=form_token.issue(),
                    error="The form token is invalid or expired. Try again.",
                ),
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        try:
            transfer = await asyncio.wait_for(
                acorn.create_record_transfer(
                    record_label,
                    expires_in=3600,
                    blossom_transfer_server=settings.blossom_home_server,
                ),
                timeout=settings.payment_timeout_seconds,
            )
        except TimeoutError:
            error = "Creating the temporary record transfer timed out. Its outcome may be uncertain."
        except Exception as exc:
            logger.warning(
                "record transfer creation failed error_type=%s",
                type(exc).__name__,
            )
            error = "Safebox could not create the temporary record transfer."
        else:
            descriptor = str(transfer["descriptor"])
            return HTMLResponse(
                render_template(
                    "record_share_qr.html",
                    title="Share Record",
                    label=record_label,
                    record_url=record_url,
                    descriptor=descriptor,
                    transfer_qr=_qr_svg(descriptor),
                    csrf_token=form_token.issue(),
                    expires_at=datetime.fromtimestamp(
                        int(transfer["expires_at"]),
                        tz=timezone.utc,
                    ).strftime("%Y-%m-%d %H:%M UTC"),
                ),
                headers={"Cache-Control": "no-store"},
            )
        return HTMLResponse(
            render_template(
                "record_share.html",
                title="Share Record",
                label=record_label,
                record_url=record_url,
                csrf_token=form_token.issue(),
                error=error,
            ),
            status_code=502,
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/record/share/stop", response_class=HTMLResponse)
    async def stop_record_share(
        request: Request,
        acorn: RecordAcornDependency,
        csrf_token: str = Form(...),
        descriptor: str = Form(...),
        label: str = Form(...),
    ) -> HTMLResponse:
        settings = request.app.state.settings
        form_token = CsrfProtector(settings)
        try:
            record_label = _validate_record_label(label)
        except ValueError as exc:
            return HTMLResponse(
                _page("Stop Sharing", f'<p class="error">{escape(str(exc))}</p>'),
                status_code=400,
            )
        record_url = f'/record?{urlencode({"label": record_label})}'

        def stop_result(error: str | None, status_code: int = 200) -> HTMLResponse:
            return HTMLResponse(
                render_template(
                    "record_share_stopped.html",
                    title="Sharing Stopped" if error is None else "Stop Sharing",
                    label=record_label,
                    record_url=record_url,
                    error=error,
                ),
                status_code=status_code,
                headers={"Cache-Control": "no-store"},
            )

        if not form_token.verify(csrf_token):
            return stop_result(
                "The form token is invalid or expired. Try again.",
                403,
            )
        try:
            result = await asyncio.wait_for(
                acorn.delete_record_transfer(
                    str(descriptor).strip(),
                    allowed_servers=[settings.blossom_home_server],
                ),
                timeout=settings.payment_timeout_seconds,
            )
            if not result.get("transfer_deleted"):
                return stop_result(
                    "Deletion of the temporary sharing copy could not be confirmed.",
                    502,
                )
        except TimeoutError:
            return stop_result(
                "The deletion request timed out and its outcome is uncertain.",
                504,
            )
        except RecordTransferError as exc:
            message = str(exc)
            status_code = 502 if "could not be confirmed" in message else 400
            return stop_result(message, status_code)
        except Exception as exc:
            logger.warning(
                "record transfer sender cleanup failed error_type=%s",
                type(exc).__name__,
            )
            return stop_result(
                "Safebox could not confirm deletion of the temporary sharing copy.",
                502,
            )
        return stop_result(None)

    @app.post("/record/import", response_class=HTMLResponse)
    async def import_record_transfer(
        request: Request,
        acorn: RecordAcornDependency,
        csrf_token: str = Form(...),
        descriptor: str = Form(...),
        label: str = Form(...),
    ) -> HTMLResponse:
        settings = request.app.state.settings
        form_token = CsrfProtector(settings)

        def import_error(message: str, status_code: int) -> HTMLResponse:
            return HTMLResponse(
                render_template(
                    "record_transfer_result.html",
                    title="Import Record",
                    error=message,
                    label=None,
                    transfer_deleted=False,
                    record_url=None,
                ),
                status_code=status_code,
                headers={"Cache-Control": "no-store"},
            )

        if not form_token.verify(csrf_token):
            return import_error("The form token is invalid or expired. Scan again.", 403)
        try:
            record_label = _validate_record_label(label)
            existing_labels = await asyncio.wait_for(
                acorn.get_user_record_labels(),
                timeout=settings.wallet_load_timeout_seconds,
            )
            if record_label in {str(item) for item in existing_labels}:
                return import_error(
                    "A record with that label already exists. Scan again and choose another label.",
                    409,
                )
            result = await asyncio.wait_for(
                acorn.accept_record_transfer(
                    str(descriptor).strip(),
                    record_name=record_label,
                    allowed_servers=[settings.blossom_home_server],
                    delete_transfer=True,
                ),
                timeout=settings.payment_timeout_seconds,
            )
        except RecordTransferError as exc:
            return import_error(str(exc), 400)
        except TimeoutError:
            return import_error(
                "The import timed out and its outcome may be uncertain. Check the records list before retrying.",
                504,
            )
        except Exception as exc:
            logger.warning(
                "record transfer import failed error_type=%s",
                type(exc).__name__,
            )
            return import_error("Safebox could not import the record transfer.", 502)
        return HTMLResponse(
            render_template(
                "record_transfer_result.html",
                title="Record Imported",
                error=None,
                label=record_label,
                transfer_deleted=bool(result.get("transfer_deleted")),
                record_url=f'/record?{urlencode({"label": record_label})}',
            ),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/records", response_class=HTMLResponse)
    async def records(
        request: Request,
        acorn: LoadedAcornDependency,
        page: str | None = None,
        view: str | None = None,
        folder: str | None = None,
    ):
        settings = request.app.state.settings
        record_view = (view or "list").strip().lower()
        if record_view not in {"list", "folders"}:
            return HTMLResponse(
                _page(
                    "Manage Records",
                    '<p class="error">The requested records view is invalid.</p>'
                    '<p><a class="nav-button" href="/records">Return to records</a></p>',
                ),
                status_code=400,
            )
        current_folder = (folder or "").strip("/")
        try:
            requested_page = int(page or "1")
            if requested_page <= 0:
                raise ValueError
        except (TypeError, ValueError):
            return HTMLResponse(
                _page(
                    "Manage Records",
                    '<p class="error">The requested records page is invalid.</p>'
                    '<p><a class="nav-button" href="/records">Return to records</a></p>',
                ),
                status_code=400,
            )
        try:
            record_entries = await _record_index_entries(
                acorn,
                settings.wallet_load_timeout_seconds,
            )
        except TimeoutError:
            return HTMLResponse(
                _page(
                    "Manage Records",
                    '<p class="error">Timed out while loading record labels.</p>'
                    '<p><a class="nav-button" href="/wallet">Return to wallet</a></p>',
                ),
                status_code=504,
            )
        except Exception as exc:
            logger.warning(
                "record label lookup failed error_type=%s",
                type(exc).__name__,
            )
            return HTMLResponse(
                _page(
                    "Manage Records",
                    '<p class="error">Unable to load record labels from the bootstrap relay.</p>'
                    '<p><a class="nav-button" href="/wallet">Return to wallet</a></p>',
                ),
                status_code=502,
            )

        if record_view == "folders":
            folder_prefix = f"{current_folder}/" if current_folder else ""
            folders: dict[str, int] = {}
            folder_records: list[dict[str, object]] = []
            for entry in record_entries:
                label = str(entry["label"])
                if current_folder:
                    if not label.startswith(folder_prefix):
                        continue
                    relative_label = label[len(folder_prefix) :]
                else:
                    relative_label = label

                child_name, separator, remainder = relative_label.partition("/")
                if separator and child_name and remainder:
                    child_path = (
                        f"{current_folder}/{child_name}"
                        if current_folder
                        else child_name
                    )
                    folders[child_path] = max(
                        folders.get(child_path, 0),
                        int(entry["modified_at"]),
                    )
                    continue

                folder_records.append(entry)

            breadcrumbs = [{"label": "Records", "url": "/records?view=folders"}]
            accumulated: list[str] = []
            for segment in current_folder.split("/") if current_folder else []:
                accumulated.append(segment)
                breadcrumbs.append(
                    {
                        "label": segment,
                        "url": f'/records?{urlencode({"view": "folders", "folder": "/".join(accumulated)})}',
                    }
                )

            return render_template(
                "records.html",
                title="Manage Records",
                record_view=record_view,
                list_view_url="/records",
                folder_view_url="/records?view=folders",
                current_folder=current_folder,
                breadcrumbs=breadcrumbs,
                folders=[
                    {
                        "label": path.rsplit("/", 1)[-1],
                        "url": f'/records?{urlencode({"view": "folders", "folder": path})}',
                    }
                    for path in sorted(
                        folders,
                        key=lambda item: item.rsplit("/", 1)[-1].casefold(),
                    )
                ],
                labels=[
                    {
                        "label": str(entry["label"])[len(folder_prefix) :],
                        "modified_at": entry["modified_at"],
                        "url": f'/record?{urlencode({"label": entry["label"]})}',
                    }
                    for entry in folder_records
                ],
                total_records=len(record_entries),
                current_page=None,
                total_pages=None,
                previous_url=None,
                next_url=None,
            )

        total_records = len(record_entries)
        total_pages = max(
            1,
            (total_records + RECORDS_PAGE_SIZE - 1) // RECORDS_PAGE_SIZE,
        )
        current_page = min(requested_page, total_pages)
        page_start = (current_page - 1) * RECORDS_PAGE_SIZE
        page_entries = record_entries[page_start : page_start + RECORDS_PAGE_SIZE]
        return render_template(
            "records.html",
            title="Manage Records",
            record_view=record_view,
            list_view_url="/records",
            folder_view_url="/records?view=folders",
            current_folder="",
            breadcrumbs=[],
            folders=[],
            labels=[
                {
                    "label": entry["label"],
                    "modified_at": entry["modified_at"],
                    "url": f'/record?{urlencode({"label": entry["label"]})}',
                }
                for entry in page_entries
            ],
            current_page=current_page,
            total_pages=total_pages,
            total_records=total_records,
            previous_url=(
                f'/records?{urlencode({"page": current_page - 1})}'
                if current_page > 1
                else None
            ),
            next_url=(
                f'/records?{urlencode({"page": current_page + 1})}'
                if current_page < total_pages
                else None
            ),
        )

    @app.get("/blob/upload", response_class=HTMLResponse)
    async def blob_upload_form(request: Request, acorn: LoadedAcornDependency):
        """Keep old bookmarks working while presenting one record workflow."""
        return RedirectResponse("/record/edit", status_code=303)

    @app.post("/blob/upload", response_class=HTMLResponse)
    async def upload_blob_record(
        request: Request,
        acorn: RecordAcornDependency,
        csrf_token: str = Form(...),
        label: str = Form(...),
        description: str = Form(""),
        confirmed: str | None = Form(None),
        blob: UploadFile = File(...),
    ):
        settings = request.app.state.settings
        form_token = CsrfProtector(settings)
        record_label = str(label).strip()
        record_description = str(description).strip()

        def upload_error(message: str, status_code: int = 400) -> HTMLResponse:
            return HTMLResponse(
                _blob_upload_form(
                    form_token.issue(),
                    settings.max_blob_bytes,
                    label=record_label,
                    description=record_description,
                    error=message,
                ),
                status_code=status_code,
            )

        if not form_token.verify(csrf_token):
            return upload_error(
                "The form token is invalid or expired. Select the file again.",
                403,
            )
        if confirmed != "yes":
            return upload_error("Explicit confirmation is required.")
        try:
            record_label = _validate_record_label(record_label)
        except ValueError as exc:
            return upload_error(str(exc))
        if len(record_description) > 4000:
            return upload_error("Description must be 4000 characters or fewer.")

        try:
            await asyncio.wait_for(
                acorn.get_record_safebox(record_name=record_label),
                timeout=settings.wallet_load_timeout_seconds,
            )
        except ValueError as exc:
            if "No event found" not in str(exc) and "record not found" not in str(exc):
                logger.warning(
                    "blob label availability check failed error_type=%s",
                    type(exc).__name__,
                )
                return upload_error(
                    "Safebox could not confirm whether that record label already exists.",
                    502,
                )
        except TimeoutError:
            return upload_error(
                "Safebox could not confirm whether that record label already exists.",
                504,
            )
        except Exception as exc:
            logger.warning(
                "blob label availability check failed error_type=%s",
                type(exc).__name__,
            )
            return upload_error(
                "Safebox could not confirm whether that record label already exists.",
                502,
            )
        else:
            return upload_error(
                "That record label already exists. Use the record editor to preserve or replace its Original Record.",
                409,
            )

        try:
            blob_data = await blob.read(settings.max_blob_bytes + 1)
        finally:
            await blob.close()
        if not blob_data:
            return upload_error("Select a non-empty file.")
        if len(blob_data) > settings.max_blob_bytes:
            return upload_error(
                f"The file exceeds the {settings.max_blob_bytes:,}-byte upload limit.",
                413,
            )

        original_filename = str(blob.filename or "blob").replace("\\", "/").split("/")[-1]
        original_filename = "".join(
            character
            for character in original_filename
            if character not in ("\x00", "\r", "\n")
        )[:255] or "blob"
        metadata = {
            "description": record_description,
            "filename": original_filename,
            "size": len(blob_data),
        }
        mime_resolution = _resolve_upload_effective_mime(blob, blob_data)
        metadata["content_type"] = mime_resolution.effective_mime
        blob_type = metadata["content_type"]
        try:
            await asyncio.wait_for(
                _put_acorn_record(
                    acorn,
                    record_name=record_label,
                    record_value=metadata,
                    record_type="blob",
                    record_kind=37375,
                    return_result=True,
                    blob_data=blob_data,
                    blob_type=blob_type,
                ),
                timeout=settings.payment_timeout_seconds,
            )
        except TimeoutError:
            return upload_error(
                "Saving the Original Record timed out and its outcome is uncertain. Check the record list before retrying.",
                504,
            )
        except ValueError as exc:
            return upload_error(str(exc))
        except Exception as exc:
            logger.warning(
                "encrypted blob save failed error_type=%s error=%s",
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            return upload_error(
                "Safebox could not store and verify the Original Record.",
                502,
            )

        return RedirectResponse(
            f'/record?{urlencode({"label": record_label, "saved": "1"})}',
            status_code=303,
        )

    @app.get("/record/blob")
    async def download_record_blob(
        request: Request,
        label: str,
        acorn: LoadedAcornDependency,
        inline: bool = False,
    ):
        settings = request.app.state.settings
        try:
            record_label = _validate_record_label(label)
        except ValueError as exc:
            return HTMLResponse(
                _page(
                    "Original Record",
                    f'<p class="error">{escape(str(exc))}</p>'
                    '<p><a href="/records">Return to records</a></p>',
                ),
                status_code=400,
            )
        try:
            record_value = await asyncio.wait_for(
                acorn.get_record_safebox(record_name=record_label),
                timeout=settings.wallet_load_timeout_seconds,
            )
            media_type, blob_data = await asyncio.wait_for(
                acorn.get_record_blobdata(record_label),
                timeout=settings.payment_timeout_seconds,
            )
        except TimeoutError:
            return HTMLResponse(
                _page(
                    "Original Record",
                    '<p class="error">Timed out while retrieving the Original Record.</p>'
                    '<p><a href="/records">Return to records</a></p>',
                ),
                status_code=504,
            )
        except Exception as exc:
            logger.warning(
                "encrypted blob retrieval failed error_type=%s",
                type(exc).__name__,
            )
            return HTMLResponse(
                _page(
                    "Original Record",
                    '<p class="error">Unable to retrieve the Original Record.</p>'
                    '<p><a href="/records">Return to records</a></p>',
                ),
                status_code=502,
            )
        if not blob_data:
            return HTMLResponse(
                _page(
                    "Original Record",
                    '<p class="error">This record has no retrievable Original Record.</p>'
                    '<p><a href="/records">Return to records</a></p>',
                ),
                status_code=404,
            )

        resolved_type = _effective_blob_media_type(media_type, record_value)
        allow_inline = inline and _blob_preview_kind(resolved_type) is not None
        return Response(
            content=blob_data,
            media_type=resolved_type,
            headers=_blob_download_headers(
                record_label,
                resolved_type,
                inline=allow_inline,
            ),
        )

    @app.get("/record/edit", response_class=HTMLResponse)
    async def edit_record_form(
        request: Request,
        acorn: LoadedAcornDependency,
        label: str | None = None,
    ) -> str:
        settings = request.app.state.settings
        record_label = str(label or "").strip()
        if not record_label:
            return _record_form(
                CsrfProtector(settings).issue(),
                settings.max_blob_bytes,
            )

        try:
            record_value = await asyncio.wait_for(
                acorn.get_record_safebox(record_name=record_label),
                timeout=settings.wallet_load_timeout_seconds,
            )
        except TimeoutError:
            return HTMLResponse(
                _record_form(
                    CsrfProtector(settings).issue(),
                    settings.max_blob_bytes,
                    label=record_label,
                    updating=True,
                    error="Timed out while loading the record for editing.",
                ),
                status_code=504,
            )
        except ValueError:
            return HTMLResponse(
                _record_form(
                    CsrfProtector(settings).issue(),
                    settings.max_blob_bytes,
                    label=record_label,
                    error="The requested record was not found.",
                ),
                status_code=404,
            )
        except Exception as exc:
            logger.warning(
                "record edit lookup failed error_type=%s",
                type(exc).__name__,
            )
            return HTMLResponse(
                _record_form(
                    CsrfProtector(settings).issue(),
                    settings.max_blob_bytes,
                    label=record_label,
                    updating=True,
                    error="Unable to load the record from the bootstrap relay.",
                ),
                status_code=502,
            )

        value = record_value.payload
        if isinstance(value, str):
            rendered_payload = value
            payload_format = "text"
        else:
            rendered_payload = json.dumps(
                value,
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            payload_format = "json"
        return _record_form(
            CsrfProtector(settings).issue(),
            settings.max_blob_bytes,
            label=record_label,
            payload=rendered_payload,
            payload_format=payload_format,
            updating=True,
            has_blob=bool(getattr(record_value, "blobref", None)),
        )

    @app.post("/record/save", response_class=HTMLResponse)
    async def save_record(
        request: Request,
        acorn: RecordAcornDependency,
        csrf_token: str = Form(...),
        label: str = Form(...),
        payload: str = Form(...),
        payload_format: str = Form("text"),
        confirmed: str | None = Form(None),
        attachment: UploadFile | None = File(None),
    ):
        settings = request.app.state.settings
        form_token = CsrfProtector(settings)
        record_label = str(label).strip()
        record_payload = str(payload)
        selected_format = str(payload_format).strip().lower()
        attachment_data: bytes | None = None
        attachment_selected = bool(attachment and attachment.filename)

        def save_error(message: str, status_code: int = 400) -> HTMLResponse:
            return HTMLResponse(
                _record_form(
                    form_token.issue(),
                    settings.max_blob_bytes,
                    label=record_label,
                    payload=record_payload,
                    payload_format=selected_format,
                    error=message,
                ),
                status_code=status_code,
            )

        if not form_token.verify(csrf_token):
            return save_error(
                "The form token is invalid or expired. Review the record again.",
                403,
            )
        if confirmed != "yes":
            return save_error("Explicit confirmation is required.")
        try:
            record_label = _validate_record_label(record_label)
        except ValueError as exc:
            return save_error(str(exc))
        if not record_payload.strip() and not attachment_selected:
            return save_error("Enter record contents or select a file attachment.")
        if len(record_payload) > 262_144:
            return save_error("Record contents must be 262144 characters or fewer.")
        if selected_format not in {"text", "json"}:
            return save_error("Choose text or JSON as the record content format.")
        if selected_format == "json" and record_payload.strip():
            try:
                stored_payload = json.loads(record_payload)
            except json.JSONDecodeError:
                return save_error("JSON record contents must contain valid JSON.")
        elif selected_format == "json":
            stored_payload = {}
        else:
            stored_payload = record_payload

        if attachment_selected and attachment is not None:
            try:
                attachment_data = await attachment.read(settings.max_blob_bytes + 1)
            finally:
                await attachment.close()
            if not attachment_data:
                return save_error("Select a non-empty attachment.")
            if len(attachment_data) > settings.max_blob_bytes:
                return save_error(
                    f"The attachment exceeds the {settings.max_blob_bytes:,}-byte upload limit.",
                    413,
                )

        attachment_mime = (
            _resolve_upload_effective_mime(attachment, attachment_data).effective_mime
            if attachment_data is not None
            else None
        )
        try:
            await asyncio.wait_for(
                _put_acorn_record(
                    acorn,
                    record_name=record_label,
                    record_value=stored_payload,
                    record_type="generic",
                    record_kind=37375,
                    blob_data=attachment_data,
                    blob_type=attachment_mime,
                    preserve_existing_blob=True,
                    return_result=True,
                ),
                timeout=(
                    settings.payment_timeout_seconds
                    if attachment_data is not None
                    else settings.wallet_load_timeout_seconds
                ),
            )
        except TimeoutError:
            return save_error(
                "The save timed out and its outcome is uncertain. Reload the record "
                "before trying again.",
                504,
            )
        except ValueError as exc:
            return save_error(str(exc))
        except Exception as exc:
            logger.warning(
                "private record save failed error_type=%s error=%s",
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            return save_error(
                "Safebox could not publish and verify the record. Reload "
                "the record before trying again.",
                502,
            )

        return RedirectResponse(
            f'/record?{urlencode({"label": record_label, "saved": "1"})}',
            status_code=303,
        )

    @app.post("/record/delete", response_class=HTMLResponse)
    async def delete_record(
        request: Request,
        acorn: RecordAcornDependency,
        csrf_token: str = Form(...),
        label: str = Form(...),
        confirmed: str | None = Form(None),
    ):
        settings = request.app.state.settings
        record_label = str(label).strip()
        return_url = f'/record?{urlencode({"label": record_label})}'

        def delete_error(message: str, status_code: int) -> HTMLResponse:
            return HTMLResponse(
                render_template(
                    "record_deleted.html",
                    title="Delete Record",
                    label=record_label,
                    error=message,
                    return_url=return_url,
                ),
                status_code=status_code,
            )

        if not CsrfProtector(settings).verify(csrf_token):
            return delete_error(
                "The deletion form token is invalid or expired. Reload the record and try again.",
                403,
            )
        if confirmed != "yes":
            return delete_error("Explicit deletion confirmation is required.", 400)
        try:
            record_label = _validate_record_label(record_label)
        except ValueError as exc:
            return delete_error(str(exc), 400)

        try:
            user_labels = await asyncio.wait_for(
                acorn.get_user_record_labels(),
                timeout=settings.wallet_load_timeout_seconds,
            )
        except TimeoutError:
            return delete_error(
                "Timed out while confirming that this is a user record. Nothing was deleted.",
                504,
            )
        except Exception as exc:
            logger.warning(
                "record deletion label check failed error_type=%s",
                type(exc).__name__,
            )
            return delete_error(
                "Safebox could not confirm that this is a user record. Nothing was deleted.",
                502,
            )
        if record_label not in {str(each) for each in user_labels}:
            return delete_error("The requested user record was not found.", 404)

        try:
            result = await asyncio.wait_for(
                acorn.delete_record(
                    record_label,
                    record_kind=37375,
                    delete_blob=True,
                ),
                timeout=settings.payment_timeout_seconds,
            )
        except TimeoutError:
            return delete_error(
                "The deletion timed out and its outcome is uncertain. Reload the records list before retrying.",
                504,
            )
        except Exception as exc:
            logger.warning(
                "private record deletion failed error_type=%s",
                type(exc).__name__,
            )
            return delete_error(
                "Safebox could not complete the deletion request. Its outcome may be uncertain.",
                502,
            )

        if result.get("status") == "NOT_FOUND":
            return delete_error("The requested user record was not found.", 404)

        blob_cleanup = result.get("blob_cleanup")
        return render_template(
            "record_deleted.html",
            title="Record Deleted",
            label=record_label,
            error=None,
            return_url=None,
            blob_requested=blob_cleanup is not None,
            blob_deleted=bool(blob_cleanup and blob_cleanup.get("deleted")),
            hidden_count=len(result.get("hidden_on") or []),
            index_error=result.get("index_error"),
        )

    @app.get("/record", response_class=HTMLResponse)
    async def record(
        request: Request,
        label: str,
        acorn: LoadedAcornDependency,
        saved: bool = False,
        openetr: bool = False,
    ):
        settings = request.app.state.settings
        try:
            record_value = await asyncio.wait_for(
                acorn.get_record_safebox(record_name=label),
                timeout=settings.wallet_load_timeout_seconds,
            )
        except TimeoutError:
            return HTMLResponse(
                _page(
                    "Record",
                    '<p class="error">Timed out while loading the record.</p>'
                    '<p><a class="nav-button" href="/records">Return to records</a></p>',
                ),
                status_code=504,
            )
        except ValueError:
            return HTMLResponse(
                _page(
                    "Record",
                    '<p class="error">The requested record was not found.</p>'
                    '<p><a class="nav-button" href="/records">Return to records</a></p>',
                ),
                status_code=404,
            )
        except Exception as exc:
            logger.warning(
                "record retrieval failed error_type=%s",
                type(exc).__name__,
            )
            return HTMLResponse(
                _page(
                    "Record",
                    '<p class="error">Unable to load the record from the bootstrap relay.</p>'
                    '<p><a class="nav-button" href="/records">Return to records</a></p>',
                ),
                status_code=502,
            )

        payload = record_value.payload
        if isinstance(payload, str):
            rendered_payload = payload
        else:
            rendered_payload = json.dumps(
                payload,
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        blob_type = _effective_blob_media_type(getattr(record_value, "blobtype", None), record_value)
        blob_preview = _blob_preview_kind(blob_type)
        pkpass_preview = None
        json_credential_preview = None
        mdoc_preview = None
        if blob_type == PKPASS_MIME_TYPE and getattr(record_value, "blobref", None):
            try:
                _pkpass_blob_type, pkpass_blob_data = await asyncio.wait_for(
                    acorn.get_record_blobdata(label),
                    timeout=settings.payment_timeout_seconds,
                )
                pkpass_preview = _pkpass_preview(pkpass_blob_data)
            except Exception as exc:
                logger.info(
                    "pkpass preview unavailable label=%s error_type=%s",
                    label,
                    type(exc).__name__,
                )
        elif blob_type in JSON_CREDENTIAL_PREVIEW_TYPES and getattr(
            record_value, "blobref", None
        ):
            try:
                _credential_blob_type, credential_blob_data = await asyncio.wait_for(
                    acorn.get_record_blobdata(label),
                    timeout=settings.payment_timeout_seconds,
                )
                json_credential_preview = _json_credential_preview(
                    credential_blob_data
                )
            except Exception as exc:
                logger.info(
                    "json credential preview unavailable label=%s error_type=%s",
                    label,
                    type(exc).__name__,
                )
        elif blob_type == MDOC_CBOR_MIME_TYPE and getattr(record_value, "blobref", None):
            try:
                _mdoc_blob_type, mdoc_blob_data = await asyncio.wait_for(
                    acorn.get_record_blobdata(label),
                    timeout=settings.payment_timeout_seconds,
                )
                mdoc_preview = _mdoc_preview(mdoc_blob_data)
            except Exception as exc:
                logger.info(
                    "mdoc preview unavailable label=%s error_type=%s",
                    label,
                    type(exc).__name__,
                )
        blob_fingerprint = _blob_recognition_fingerprint(
            getattr(record_value, "origsha256", None)
        )
        openetr_history = None
        if openetr and blob_fingerprint:
            try:
                openetr_history = await asyncio.wait_for(
                    query_openetr_history(
                        str(record_value.origsha256).strip().lower(),
                        settings.openetr_relays,
                        timeout=settings.openetr_query_timeout_seconds,
                        limit=settings.openetr_query_limit,
                    ),
                    timeout=(settings.openetr_query_timeout_seconds * 2) + 1,
                )
            except Exception as exc:
                logger.warning(
                    "OpenETR history query failed error_type=%s",
                    type(exc).__name__,
                )
                openetr_history = {
                    "digest": str(record_value.origsha256).strip().lower(),
                    "relays": settings.openetr_relays,
                    "origin": None,
                    "controls": [],
                    "warnings": [],
                    "error": "OpenETR history is temporarily unavailable.",
                }
        blob_query = urlencode({"label": label})
        record_url = f"/record?{blob_query}"
        if openetr:
            return render_template(
                "control_history.html",
                title="Control History",
                label=label,
                record_url=record_url,
                has_blob=bool(getattr(record_value, "blobref", None)),
                blob_fingerprint=blob_fingerprint,
                openetr_history=openetr_history,
            )
        return render_template(
            "record.html",
            title=label,
            label=label,
            edit_url=f'/record/edit?{urlencode({"label": label})}',
            share_url=f'/record/share?{urlencode({"label": label})}',
            present_url=f'/record/present?{urlencode({"label": label})}',
            control_history_url=(
                f'/record?{urlencode({"label": label, "openetr": "1"})}'
            ),
            saved=saved,
            record_type=str(record_value.type),
            payload=rendered_payload,
            has_blob=bool(getattr(record_value, "blobref", None)),
            blob_type=blob_type,
            blob_type_notice=_original_record_type_notice(record_value, blob_type),
            blob_preview=blob_preview,
            pkpass_preview=pkpass_preview,
            json_credential_preview=json_credential_preview,
            mdoc_preview=mdoc_preview,
            blob_fingerprint=blob_fingerprint,
            blob_url=f"/record/blob?{blob_query}",
            blob_inline_url=f"/record/blob?{blob_query}&inline=1",
            delete_csrf_token=CsrfProtector(settings).issue(),
        )

    @app.get("/api/session", response_class=JSONResponse)
    async def session_info(credentials: CredentialsDependency, acorn: AcornDependency):
        return {
            "authenticated": True,
            "npub": acorn.pubkey_bech32,
            "bootstrap_relay": credentials.bootstrap_relay,
        }

    return app


app = create_app()
