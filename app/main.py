"""Minimal stateless Safebox web shell."""

from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from html import escape
import json
import logging
import mimetypes
from pathlib import Path
import re
from urllib.parse import quote, urlencode

import bolt11
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
from app.models import ClaimedHandle
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
    credentials_from_login,
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


def _login_form(
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
        "login.html",
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
    comment: str = "Paid from Safebox Web",
    payment_mode: str = "confirmed",
) -> str:
    if balance_status is None:
        balance_status = (
            f"<p>Relay-visible proof total: <strong>{int(balance):,} sats</strong></p>"
        )
    return render_template(
        "pay.html",
        title="Pay to an Address",
        balance_status=balance_status,
        csrf_token=csrf_token,
        error=error,
        lightning_address=lightning_address,
        amount=amount,
        comment=comment,
        payment_mode=payment_mode,
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
            + '<p class="error"><strong>Spendable balance not verified.</strong> '
            + escape(verification_error or "Mint verification was unavailable.")
            + " Do not rely on the relay-visible total for a payment.</p>"
        )

    confirmed = verification.get("mint_confirmed_unspent", {})
    confirmed_amount = int(confirmed.get("amount", 0))
    confirmed_count = int(confirmed.get("proof_count", 0))
    status = str(verification.get("status", "inconclusive"))
    confirmed_html = (
        "<p>Mint-confirmed spendable balance: "
        f"<strong>{confirmed_amount:,} sats</strong> in {confirmed_count:,} proofs</p>"
    )
    if status != "clean" or confirmed_amount != int(relay_balance):
        difference = max(0, int(relay_balance) - confirmed_amount)
        warning = (
            '<p class="error"><strong>Proof state requires attention.</strong> '
            f"Verification status: {escape(status)}. "
        )
        if difference:
            warning += f"The relay total includes {difference:,} sats not confirmed as spendable. "
        warning += "Do not make a payment until the proof state has been reviewed.</p>"
        return relay_html + confirmed_html + warning
    return relay_html + confirmed_html


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
    pending_amount: int = 0,
) -> str:
    """Render transaction history with an explicit incoming funds check."""

    return render_template(
        "transactions.html",
        title="Transaction History",
        headline_class="transaction-headline",
        entries=_transaction_history_view(entries),
        csrf_token=csrf_token,
        notice=notice,
        retention_notice=retention_notice,
        wallet_balance=wallet_balance,
        wallet_balance_verified=wallet_balance_verified,
        pending_amount=int(pending_amount),
    )


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

    extension = mimetypes.guess_extension(media_type or "") or ".bin"
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


def _deposit_form(
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
        "deposit.html",
        title="Deposit funds",
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


def _deposit_invoice_page(
    state: DepositQuoteState,
    state_token: str,
    csrf_token: str,
    message: str | None = None,
) -> str:
    return render_template(
        "deposit_invoice.html",
        title="Pay deposit invoice",
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
        try:
            yield
        finally:
            app.state.database_engine.dispose()

    app = FastAPI(title="Safebox Web", version="0.1.0", lifespan=lifespan)
    app.state.settings = runtime_settings
    app.include_router(lnurl_pay_router)
    app.mount(
        "/static",
        StaticFiles(directory=static_directory),
        name="static",
    )

    @app.exception_handler(HTTPException)
    async def browser_session_error(request: Request, exc: HTTPException):
        session_errors = {
            "Acorn login required",
            "Acorn session is invalid or expired",
        }
        accepts_html = "text/html" in request.headers.get("accept", "").lower()
        if (
            exc.status_code == 401
            and str(exc.detail) in session_errors
            and request.method in {"GET", "HEAD"}
            and accepts_html
        ):
            return RedirectResponse("/", status_code=303)
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
            _login_form(
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

    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request) -> str:
        settings = request.app.state.settings
        return _login_form(
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

        credentials = credentials_from_login(
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

    @app.post("/login")
    async def login(
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
                _login_form(
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
            credentials = credentials_from_login(
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
                _login_form(
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

    @app.post("/logout")
    async def logout(
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
        recovery_backup_pending = False
        try:
            recovery_state = await asyncio.wait_for(
                acorn.get_deferred_recovery_status(),
                timeout=settings.wallet_load_timeout_seconds,
            )
            recovery_backup_pending = bool(recovery_state.get("pending"))
        except Exception as exc:
            logger.warning(
                "deferred recovery status unavailable error_type=%s",
                type(exc).__name__,
            )
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
        wallet_balance, wallet_balance_verified = _wallet_balance_summary(
            acorn.get_balance(),
            verification,
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
        pending_continuity_amount = sum(
            int(receipt.get("amount") or 0)
            for receipt in continuity_receipts
            if str(receipt.get("status") or "provisional") == "provisional"
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
            incoming_preview = {}
        pending_payment_amount = (
            pending_continuity_amount
            + int(incoming_preview.get("previewed_amount", 0))
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
            balance_status=balance_status,
            wallet_balance=wallet_balance,
            wallet_balance_verified=wallet_balance_verified,
            pending_payment_amount=pending_payment_amount,
            onboard_invite_path="/invite",
            csrf_token=csrf_token,
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

        return JSONResponse(
            content={
                "names": {normalized_name: pubkey_hex},
                "relays": {pubkey_hex: [registration.home_relay]},
            },
            headers={"Access-Control-Allow-Origin": "*"},
        )

    @app.get("/deposit", response_class=HTMLResponse)
    async def deposit_form(request: Request, acorn: DepositAcornDependency) -> str:
        settings = request.app.state.settings
        verification, verification_error = await _read_proof_verification(
            acorn,
            settings.wallet_load_timeout_seconds,
        )
        return _deposit_form(
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

    @app.post("/deposit", response_class=HTMLResponse)
    async def create_deposit_invoice(
        request: Request,
        acorn: DepositAcornDependency,
        csrf_token: str = Form(...),
        amount: str = Form(...),
    ):
        settings = request.app.state.settings
        form_token = CsrfProtector(settings)

        def deposit_error(message: str, status_code: int = 400) -> HTMLResponse:
            return HTMLResponse(
                _deposit_form(
                    acorn.get_balance(),
                    acorn.home_mint,
                    form_token.issue(),
                    message,
                ),
                status_code=status_code,
            )

        if not form_token.verify(csrf_token):
            return deposit_error(
                "The form token is invalid or expired. Enter the amount again.",
                403,
            )
        try:
            amount_sats = int(str(amount).strip())
        except ValueError:
            return deposit_error("Deposit amount must be a whole number of sats.")
        if amount_sats <= 0:
            return deposit_error("Deposit amount must be greater than zero.")

        try:
            quote = await asyncio.wait_for(
                asyncio.to_thread(acorn.deposit, amount_sats),
                timeout=settings.payment_timeout_seconds,
            )
        except TimeoutError:
            logger.warning("deposit quote request timed out mint=%s", acorn.home_mint)
            return deposit_error(
                "The mint did not return an invoice before the request timed out.",
                504,
            )
        except Exception as exc:
            logger.warning(
                "deposit quote request failed mint=%s error_type=%s",
                acorn.home_mint,
                type(exc).__name__,
            )
            return deposit_error(
                "Safebox could not obtain a Lightning invoice from the home mint.",
                502,
            )

        quote_id = str(quote.quote).strip()
        invoice = str(quote.invoice).strip()
        if not quote_id or not invoice or len(quote_id) > 512 or len(invoice) > 2048:
            return deposit_error(
                "The home mint returned an invalid or oversized deposit invoice.",
                502,
            )

        state = DepositQuoteState(
            quote=quote_id,
            amount=amount_sats,
            mint=str(acorn.home_mint).rstrip("/"),
            invoice=invoice,
        )
        state_token = DepositQuoteCipher(settings).encode(state)
        return _deposit_invoice_page(
            state,
            state_token,
            form_token.issue(),
        )

    @app.post("/deposit/check")
    async def check_deposit_invoice(
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
                    "Deposit quote expired",
                    f'<p class="error">{escape(str(exc))}.</p>'
                    '<p><a href="/deposit">Create a new deposit invoice</a></p>',
                ),
                status_code=400,
            )

        if str(acorn.home_mint).rstrip("/") != state.mint:
            return HTMLResponse(
                _deposit_invoice_page(
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
                _deposit_invoice_page(
                    state,
                    deposit_token,
                    form_token.issue(),
                    "Payment confirmation timed out. Do not create or pay another "
                    "invoice; wait and check this invoice again.",
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
                _deposit_invoice_page(
                    state,
                    deposit_token,
                    form_token.issue(),
                    "Safebox could not confirm the payment. Do not create or pay "
                    "another invoice; wait and check this invoice again.",
                ),
                status_code=502,
            )

        if not success:
            return HTMLResponse(
                _deposit_invoice_page(
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
                comment="safebox web deposit",
            )
        except Exception as exc:
            logger.warning(
                "deposit confirmed but transaction history write failed "
                "mint=%s error_type=%s",
                state.mint,
                type(exc).__name__,
            )
        return RedirectResponse("/wallet", status_code=303)

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
            blob_type = presentation.get("blob_type")
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
                    '<p class="error">The invoice exceeds the mint-confirmed spendable balance.</p>'
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
        )

    @app.post("/pay", response_class=HTMLResponse)
    async def make_payment(
        request: Request,
        acorn: PaymentAcornDependency,
        csrf_token: str = Form(...),
        lightning_address: str = Form(...),
        amount: str = Form(...),
        comment: str = Form("Paid from Safebox Web"),
        payment_mode: str = Form("confirmed"),
        confirmed: str | None = Form(None),
    ):
        settings = request.app.state.settings
        form_token = CsrfProtector(settings)
        payment_mode = str(payment_mode).strip().lower()
        if payment_mode not in {"confirmed", "continuity"}:
            payment_mode = "confirmed"
        verification = None
        verification_error = None
        if payment_mode == "confirmed":
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
                    ),
                    status_code=409,
                )
            return HTMLResponse(
                _payment_form(
                    acorn.get_balance(),
                    form_token.issue(),
                    "Safebox repaired stale proofs. Review the recipient, "
                    "amount, and updated balance, then confirm the payment again. "
                    f"Previous attempt stopped before confirmation: {error_reason}",
                    repaired_balance_status,
                    lightning_address=lightning_address,
                    amount=amount,
                    comment=comment,
                    payment_mode=payment_mode,
                ),
                status_code=409,
            )

        if not form_token.verify(csrf_token):
            return payment_error(
                "The form token is invalid or expired. Review the payment again.",
                403,
            )
        if confirmed != "yes":
            return payment_error("Explicit payment confirmation is required.")
        if payment_mode == "confirmed" and verification is None:
            return payment_error(
                "Payment is blocked because a mint is unavailable. Continuity "
                "Payments remain available for supported Safebox recipients.",
                503,
            )
        if payment_mode == "confirmed" and verification.get("status") != "clean":
            return payment_error(
                "Payment is blocked because the wallet proof state is not clean. "
                "Review it with 'acorn balance --verify' before spending.",
                409,
            )

        recipient = _normalize_lightning_address(lightning_address)
        if recipient is None:
            return payment_error("Enter a valid Lightning address such as alice@example.com.")

        try:
            amount_sats = int(str(amount).strip())
        except ValueError:
            return payment_error("Payment amount must be a whole number of sats.")
        if amount_sats <= 0:
            return payment_error("Payment amount must be greater than zero.")
        available_balance = (
            int(verification.get("mint_confirmed_unspent", {}).get("amount", 0))
            if payment_mode == "confirmed"
            else int(acorn.get_balance())
        )
        if amount_sats > available_balance:
            return payment_error(
                "Payment amount exceeds the available spendable balance."
            )

        payment_comment = str(comment).strip() or "Paid from Safebox Web"
        if len(payment_comment) > 200:
            return payment_error("Payment comment must be 200 characters or fewer.")

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
                        amount=amount_sats,
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
                        "Payment status unresolved",
                        "<p>The direct Safebox transfer timed out before Safebox "
                        "received a final result. Do not retry it blindly. Review "
                        "transaction history before attempting another payment.</p>"
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
                        "Payment not completed",
                        "<p>Safebox found a recipient Safebox address, but direct "
                        "funds delivery could not be completed. Review "
                        "transaction history before deciding whether another "
                        "payment is safe.</p>"
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
                        "Payment not confirmed",
                        "<p>Safebox found a recipient Safebox address, but direct "
                        "funds delivery did not return a confirmed successful "
                        "result. Review transaction history before deciding "
                        "whether another payment is safe.</p>"
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
                    else "Payment successful"
                ),
                amount=f"{amount_sats:,}",
                fees="0",
                recipient=recipient,
                message=message,
            )

        try:
            message, fees = await asyncio.wait_for(
                acorn.pay_multi(
                    amount=amount_sats,
                    lnaddress=recipient,
                    comment=payment_comment,
                ),
                timeout=settings.payment_timeout_seconds,
            )
        except TimeoutError:
            logger.warning("lightning payment timed out outcome=unknown")
            return HTMLResponse(
                _page(
                    "Payment status unresolved",
                    "<p>The payment timed out before Safebox received a final result. "
                    "Do not retry it. Use <code>acorn reconcile-payments</code> and "
                    "review transaction history before attempting another payment.</p>"
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
                "payment. Run <code>acorn check-proofs</code>, then "
                "<code>acorn repair-proofs</code> if repair is recommended, "
                "and confirm the balance with <code>acorn balance --verify</code>.</p>"
                if stale_proofs
                else "<p>Do not retry blindly. Review transaction history and run "
                "<code>acorn reconcile-payments</code> before deciding whether "
                "another payment is safe.</p>"
            )
            logger.warning(
                "lightning payment did not return success error_type=%s error=%s",
                type(exc).__name__,
                str(exc),
            )
            return HTMLResponse(
                _page(
                    "Payment not confirmed",
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
            title="Payment successful",
            amount=f"{amount_sats:,}",
            fees=f"{int(fees):,}",
            recipient=recipient,
            message=str(message),
        )

    @app.get("/transactions", response_class=HTMLResponse)
    async def transactions(request: Request, acorn: LoadedAcornDependency):
        settings = request.app.state.settings
        verification, _verification_error = await _read_proof_verification(
            acorn,
            settings.wallet_load_timeout_seconds,
        )
        wallet_balance, wallet_balance_verified = _wallet_balance_summary(
            acorn.get_balance(),
            verification,
        )
        try:
            history = await asyncio.wait_for(
                acorn.get_tx_history(),
                timeout=settings.wallet_load_timeout_seconds,
            )
        except TimeoutError:
            return HTMLResponse(
                _page(
                    "Transaction history",
                    '<p class="error">Timed out while loading transaction history.</p>'
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
                    "Transaction history",
                    '<p class="error">Unable to load transaction history from the bootstrap relay.</p>'
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
            incoming_preview = {}
        pending_amount = sum(
            int(receipt.get("amount") or 0) for receipt in continuity_receipts
        ) + int(incoming_preview.get("previewed_amount", 0))
        return _transactions_page(
            entries,
            CsrfProtector(settings).issue(),
            retention_notice=_ecash_retention_notice(settings),
            wallet_balance=wallet_balance,
            wallet_balance_verified=wallet_balance_verified,
            pending_amount=pending_amount,
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
    async def records(request: Request, acorn: LoadedAcornDependency):
        settings = request.app.state.settings
        try:
            labels = await asyncio.wait_for(
                acorn.get_user_record_labels(),
                timeout=settings.wallet_load_timeout_seconds,
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

        unique_labels = list(dict.fromkeys(str(label) for label in labels))
        return render_template(
            "records.html",
            title="Manage Records",
            labels=[
                {
                    "label": record_label,
                    "url": f'/record?{urlencode({"label": record_label})}',
                }
                for record_label in unique_labels
            ],
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
        try:
            await asyncio.wait_for(
                acorn.put_record(
                    record_name=record_label,
                    record_value=metadata,
                    record_type="blob",
                    record_kind=37375,
                    blob_data=blob_data,
                    return_result=True,
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
                "encrypted blob save failed error_type=%s",
                type(exc).__name__,
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

        resolved_type = (
            str(media_type or "application/octet-stream")
            .split(";", 1)[0]
            .strip()
            .lower()
        )
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

        try:
            await asyncio.wait_for(
                acorn.put_record(
                    record_name=record_label,
                    record_value=stored_payload,
                    record_type="generic",
                    record_kind=37375,
                    blob_data=attachment_data,
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
                "private record save failed error_type=%s",
                type(exc).__name__,
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
        blob_type = getattr(record_value, "blobtype", None)
        blob_preview = _blob_preview_kind(blob_type)
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
            blob_preview=blob_preview,
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
