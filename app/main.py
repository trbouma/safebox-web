"""Minimal stateless Safebox web shell."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from html import escape
import json
import logging
import mimetypes
from pathlib import Path
import re
from urllib.parse import quote, urlencode

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
import qrcode
import qrcode.image.svg
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from acorn import (
    Acorn,
    generate_record_protection_key,
    record_protection_key_from_entropy,
)
from acorn.func_utils import (
    generate_seed_phrase_and_nsec,
    npub_to_hex,
    seed_phrase_and_nsec_from_entropy,
)

from app.config import Settings
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
from app.lnurl_pay import encode_lnurl, router as lnurl_pay_router
from app.security import (
    LOOPBACK_COOKIE_NAME,
    SECURE_COOKIE_NAME,
    CsrfProtector,
    DepositQuoteCipher,
    DepositQuoteState,
    SessionCipher,
    cookie_name_for_request,
    credentials_from_login,
    is_allowed_transport,
    is_loopback_http_request,
    is_same_origin,
    normalize_bootstrap_relay,
    normalize_home_mint,
)
from app.templating import render_template


logger = logging.getLogger("safebox_web.security")


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
            "Safebox does not request automatic expiration for encrypted ecash "
            "delivery messages. Relays may retain them according to their own policy."
        )
    else:
        duration = _humanize_retention(retention)
        message = (
            "Safebox asks compatible relays to retain encrypted ecash delivery "
            f"messages for {duration} after publication, then expire and delete "
            "them. Receive incoming ecash before this period ends. Relay "
            "enforcement and physical deletion can vary."
        )
    return (
        '<aside class="retention-notice" aria-labelledby="retention-heading">'
        '<h2 id="retention-heading">Ecash message retention</h2>'
        f"<p>{escape(message)}</p>"
        "</aside>"
    )


def _page(title: str, body: str) -> str:
    """Render a generic result or error page through the shared Jinja layout."""

    return render_template("page.html", title=title, body=body)


def _login_form(
    default_relay: str, csrf_token: str, error: str | None = None
) -> str:
    return render_template(
        "login.html",
        title="Connect an Acorn",
        default_relay=default_relay,
        csrf_token=csrf_token,
        error=error,
    )


def _create_form(
    default_relay: str,
    default_mint: str,
    csrf_token: str,
    error: str | None = None,
    mnemonic_words: str = "12",
) -> str:
    return render_template(
        "create.html",
        title="Create a new Acorn",
        default_relay=default_relay,
        default_mint=default_mint,
        csrf_token=csrf_token,
        error=error,
        mnemonic_words=mnemonic_words,
    )


def _payment_form(
    balance: int,
    csrf_token: str,
    error: str | None = None,
    balance_status: str | None = None,
) -> str:
    if balance_status is None:
        balance_status = (
            f"<p>Relay-visible proof total: <strong>{int(balance):,} sats</strong></p>"
        )
    return render_template(
        "pay.html",
        title="Pay a Lightning address",
        balance_status=balance_status,
        csrf_token=csrf_token,
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
            "X": ("Advisory", "", "advisory"),
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


def _transactions_page(
    entries: list[dict],
    csrf_token: str,
    notice: str | None = None,
    retention_notice: str = "",
) -> str:
    """Render transaction history together with explicit incoming-ecash receipt."""

    return render_template(
        "transactions.html",
        title="Transaction history",
        entries=_transaction_history_view(entries),
        csrf_token=csrf_token,
        notice=notice,
        retention_notice=retention_notice,
    )


def _record_form(
    csrf_token: str,
    *,
    label: str = "",
    payload: str = "",
    payload_format: str = "text",
    updating: bool = False,
    error: str | None = None,
) -> str:
    """Render the add/update form without retaining record data server-side."""

    title = "Update private record" if updating else "Add private record"
    return render_template(
        "record_form.html",
        title=title,
        csrf_token=csrf_token,
        label=label,
        payload=payload,
        payload_format=payload_format,
        updating=updating,
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
        title="Store an encrypted blob",
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
        border=4 if include_acorn else 2,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    image = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    svg = image.to_string(encoding="unicode")
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
        StaticFiles(directory=Path(__file__).resolve().parent / "static"),
        name="static",
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
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
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

    @app.get("/", response_class=HTMLResponse)
    async def home() -> str:
        return render_template("home.html", title="Safebox")

    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request) -> str:
        settings = request.app.state.settings
        return _login_form(
            settings.default_bootstrap_relay,
            CsrfProtector(settings).issue(),
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
        mnemonic_words: str = Form("12"),
        entropy_hex: str = Form(""),
        entropy_confirmation: str = Form(""),
        record_protection_entropy_hex: str = Form(""),
        record_protection_entropy_confirmation: str = Form(""),
        confirmed: str | None = Form(None),
    ):
        settings = request.app.state.settings
        form_token = CsrfProtector(settings)

        def creation_error(message: str, status_code: int = 400) -> HTMLResponse:
            return HTMLResponse(
                _create_form(
                    settings.default_bootstrap_relay,
                    settings.default_home_mint,
                    form_token.issue(),
                    message,
                    mnemonic_words=mnemonic_words,
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

        supplied_entropy = str(entropy_hex).strip()
        repeated_entropy = str(entropy_confirmation).strip()
        uses_external_entropy = bool(supplied_entropy or repeated_entropy)
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
                    "Choose a 12- or 24-word offline mnemonic."
                )
            mnemonic_strength = 128 if mnemonic_words == "12" else 256
            seed_phrase, generated_nsec = generate_seed_phrase_and_nsec(
                strength=mnemonic_strength
            )

        supplied_rpk_entropy = str(record_protection_entropy_hex).strip()
        repeated_rpk_entropy = str(
            record_protection_entropy_confirmation
        ).strip()
        uses_external_rpk_entropy = bool(
            supplied_rpk_entropy or repeated_rpk_entropy
        )
        if uses_external_rpk_entropy:
            if not supplied_rpk_entropy or not repeated_rpk_entropy:
                return creation_error(
                    "Enter the record-protection entropy in both fields."
                )
            if supplied_rpk_entropy != repeated_rpk_entropy:
                return creation_error(
                    "The record-protection entropy values do not match."
                )
            if supplied_entropy and supplied_rpk_entropy.lower() == supplied_entropy.lower():
                return creation_error(
                    "Wallet entropy and record-protection entropy must be independent."
                )
            try:
                record_protection_key = record_protection_key_from_entropy(
                    supplied_rpk_entropy
                )
            except ValueError as exc:
                return creation_error(
                    f"Invalid record-protection entropy: {exc}"
                )
        else:
            record_protection_key = generate_record_protection_key()

        try:
            normalized_relay = normalize_bootstrap_relay(home_relay)
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
                acorn.create_instance(seed_phrase=seed_phrase),
                timeout=settings.wallet_load_timeout_seconds,
            )
            await asyncio.wait_for(
                acorn.load_data(),
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

        credentials = credentials_from_login(
            secret_type="nsec",
            secret=generated_nsec,
            bootstrap_relay=normalized_relay,
            record_protection_key=record_protection_key,
        )
        response = HTMLResponse(
            render_template(
                "new_acorn.html",
                title="New Acorn created",
                seed_phrase=seed_phrase,
                nsec=generated_nsec,
                npub=acorn.pubkey_bech32,
                home_relay=normalized_relay,
                home_mint=normalized_mint,
            ),
            status_code=201,
        )
        response.set_cookie(
            key=cookie_name_for_request(request),
            value=SessionCipher(settings).encode(credentials),
            max_age=settings.session_ttl_seconds,
            httponly=True,
            secure=not is_loopback_http_request(request),
            samesite="strict",
            path="/",
        )
        return response

    @app.post("/login")
    async def login(
        request: Request,
        csrf_token: str = Form(...),
        secret_type: str = Form(...),
        secret: str = Form(...),
        bootstrap_relay: str = Form(...),
    ):
        settings = request.app.state.settings
        if not CsrfProtector(settings).verify(csrf_token):
            return HTMLResponse(
                _login_form(
                    settings.default_bootstrap_relay,
                    CsrfProtector(settings).issue(),
                    "The form token is invalid or expired. Reload and try again.",
                ),
                status_code=403,
            )
        try:
            credentials = credentials_from_login(
                secret_type=secret_type,
                secret=secret,
                bootstrap_relay=bootstrap_relay,
            )
        except ValueError as exc:
            return HTMLResponse(
                _login_form(
                    settings.default_bootstrap_relay,
                    CsrfProtector(settings).issue(),
                    str(exc),
                ),
                status_code=400,
            )

        response = RedirectResponse("/wallet", status_code=303)
        response.set_cookie(
            key=cookie_name_for_request(request),
            value=SessionCipher(settings).encode(credentials),
            max_age=settings.session_ttl_seconds,
            httponly=True,
            secure=not is_loopback_http_request(request),
            samesite="strict",
            path="/",
        )
        return response

    @app.post("/logout")
    async def logout(request: Request, csrf_token: str = Form(...)):
        if not CsrfProtector(request.app.state.settings).verify(csrf_token):
            return HTMLResponse(
                _page(
                    "Unable to disconnect",
                    '<p class="error">The form token is invalid or expired.</p>'
                    '<p><a href="/wallet">Return to wallet</a></p>',
                ),
                status_code=403,
            )
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
        claimed_handle = session.exec(
            select(ClaimedHandle).where(
                ClaimedHandle.npub == acorn.pubkey_bech32
            )
        ).first()
        nip05_address = None
        lightning_lnurl = None
        lightning_qr = None
        if claimed_handle is not None:
            nip05_address = (
                f"{claimed_handle.claimed_handle}@{request.url.hostname}"
            )
            if settings.service_acorn_enabled:
                pay_endpoint = str(
                    request.url_for(
                        "lnurl_pay_resolve",
                        handle=claimed_handle.claimed_handle,
                    )
                )
                lightning_lnurl = encode_lnurl(pay_endpoint)
                lightning_qr = _qr_svg(lightning_lnurl, include_acorn=True)
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
        return render_template(
            "wallet.html",
            title="Connected Acorn",
            npub=acorn.pubkey_bech32,
            home_relay=acorn.home_relay,
            nip05_address=nip05_address,
            lightning_lnurl=lightning_lnurl,
            lightning_qr=lightning_qr,
            retention_notice=_ecash_retention_notice(settings),
            balance_status=balance_status,
            csrf_token=csrf_token,
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
        confirmed: str | None = Form(None),
    ):
        settings = request.app.state.settings
        form_token = CsrfProtector(settings)
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

        def payment_error(message: str, status_code: int = 400) -> HTMLResponse:
            return HTMLResponse(
                _payment_form(
                    acorn.get_balance(),
                    form_token.issue(),
                    message,
                    balance_status,
                ),
                status_code=status_code,
            )

        if not form_token.verify(csrf_token):
            return payment_error(
                "The form token is invalid or expired. Review the payment again.",
                403,
            )
        if confirmed != "yes":
            return payment_error("Explicit payment confirmation is required.")
        if verification is None:
            return payment_error(
                "Payment is blocked because Safebox could not verify the proofs "
                "with their mints.",
                503,
            )
        if verification.get("status") != "clean":
            return payment_error(
                "Payment is blocked because the wallet proof state is not clean. "
                "Review it with 'acorn balance --verify' before spending.",
                409,
            )

        recipient = str(lightning_address).strip()
        if (
            recipient.count("@") != 1
            or any(character.isspace() for character in recipient)
            or recipient.startswith("@")
            or recipient.endswith("@")
        ):
            return payment_error("Enter a valid Lightning address such as alice@example.com.")

        try:
            amount_sats = int(str(amount).strip())
        except ValueError:
            return payment_error("Payment amount must be a whole number of sats.")
        if amount_sats <= 0:
            return payment_error("Payment amount must be greater than zero.")
        confirmed_balance = int(
            verification.get("mint_confirmed_unspent", {}).get("amount", 0)
        )
        if amount_sats > confirmed_balance:
            return payment_error(
                "Payment amount exceeds the mint-confirmed spendable balance."
            )

        payment_comment = str(comment).strip() or "Paid from Safebox Web"
        if len(payment_comment) > 200:
            return payment_error("Payment comment must be 200 characters or fewer.")

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
            logger.warning(
                "lightning payment did not return success error_type=%s",
                type(exc).__name__,
            )
            return HTMLResponse(
                _page(
                    "Payment not confirmed",
                    "<p>Safebox did not receive a confirmed successful result. "
                    "Do not retry blindly. Review transaction history and run "
                    "<code>acorn reconcile-payments</code> before deciding whether "
                    "another payment is safe.</p>"
                    '<p><a href="/wallet">Return to wallet</a></p>',
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
        return _transactions_page(
            entries,
            CsrfProtector(settings).issue(),
            retention_notice=_ecash_retention_notice(settings),
        )

    @app.post("/transactions/receive", response_class=HTMLResponse)
    async def receive_ecash_from_transactions(
        request: Request,
        acorn: ReceiveAcornDependency,
        csrf_token: str = Form(...),
    ):
        settings = request.app.state.settings
        if not CsrfProtector(settings).verify(csrf_token):
            return HTMLResponse(
                _page(
                    "Receive ecash",
                    '<p class="error">The form expired or could not be verified.</p>'
                    '<p><a href="/transactions">Return to transaction history</a></p>',
                ),
                status_code=403,
            )

        try:
            result = await asyncio.wait_for(
                acorn.sweep_ecash_transfers(),
                timeout=settings.payment_timeout_seconds,
            )
        except TimeoutError:
            return HTMLResponse(
                _page(
                    "Receive ecash outcome uncertain",
                    '<p class="error">The receive operation timed out. It may have '
                    "accepted proofs before the timeout. Review the wallet balance and "
                    "transaction history before trying again.</p>"
                    '<p><a href="/transactions">Reload transaction history</a></p>',
                ),
                status_code=504,
            )
        except Exception as exc:
            logger.warning(
                "incoming ecash receive failed error_type=%s",
                type(exc).__name__,
            )
            return HTMLResponse(
                _page(
                    "Unable to receive ecash",
                    '<p class="error">Safebox could not complete the incoming ecash '
                    "check. No unverified balance has been displayed.</p>"
                    '<p><a href="/transactions">Return to transaction history</a></p>',
                ),
                status_code=502,
            )

        accepted_count = int(result.get("accepted_count", 0))
        accepted_amount = int(result.get("accepted_amount", 0))
        queried = int(result.get("queried", 0))
        if accepted_count:
            notice = (
                f"Received {accepted_amount:,} sats from "
                f"{accepted_count:,} incoming ecash transfer(s)."
            )
        else:
            notice = f"No incoming ecash was accepted ({queried:,} transfer event(s) checked)."

        try:
            history = await asyncio.wait_for(
                acorn.get_tx_history(),
                timeout=settings.wallet_load_timeout_seconds,
            )
        except Exception as exc:
            logger.warning(
                "post-receive transaction history lookup failed error_type=%s",
                type(exc).__name__,
            )
            return HTMLResponse(
                _page(
                    "Ecash receive completed",
                    f"<p><strong>{escape(notice)}</strong></p>"
                    "<p>The updated transaction history could not be loaded. "
                    "Reload it to verify the resulting credit.</p>"
                    '<p><a href="/transactions">Reload transaction history</a></p>',
                ),
                status_code=200,
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
        return _transactions_page(
            entries,
            CsrfProtector(settings).issue(),
            notice=notice,
            retention_notice=_ecash_retention_notice(settings),
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
                    "Private records",
                    '<p class="error">Timed out while loading record labels.</p>'
                    '<p><a href="/wallet">Return to wallet</a></p>',
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
                    "Private records",
                    '<p class="error">Unable to load record labels from the bootstrap relay.</p>'
                    '<p><a href="/wallet">Return to wallet</a></p>',
                ),
                status_code=502,
            )

        unique_labels = list(dict.fromkeys(str(label) for label in labels))
        return render_template(
            "records.html",
            title="Private records",
            labels=[
                {
                    "label": record_label,
                    "url": f'/record?{urlencode({"label": record_label})}',
                }
                for record_label in unique_labels
            ],
        )

    @app.get("/blob/upload", response_class=HTMLResponse)
    async def blob_upload_form(request: Request, acorn: LoadedAcornDependency) -> str:
        settings = request.app.state.settings
        return _blob_upload_form(
            CsrfProtector(settings).issue(),
            settings.max_blob_bytes,
        )

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
                "That record label already exists. Choose a new label to avoid replacing or orphaning its blob.",
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
                "The encrypted blob save timed out and its outcome is uncertain. Check the record list before retrying.",
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
                "Safebox could not encrypt, upload, publish, and verify the blob record.",
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
                    "Encrypted blob",
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
                    "Encrypted blob",
                    '<p class="error">Timed out while retrieving and decrypting the blob.</p>'
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
                    "Encrypted blob",
                    '<p class="error">Unable to retrieve and decrypt the blob.</p>'
                    '<p><a href="/records">Return to records</a></p>',
                ),
                status_code=502,
            )
        if not blob_data:
            return HTMLResponse(
                _page(
                    "Encrypted blob",
                    '<p class="error">This record has no retrievable blob.</p>'
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
            return _record_form(CsrfProtector(settings).issue())

        try:
            record_value = await asyncio.wait_for(
                acorn.get_record_safebox(record_name=record_label),
                timeout=settings.wallet_load_timeout_seconds,
            )
        except TimeoutError:
            return HTMLResponse(
                _record_form(
                    CsrfProtector(settings).issue(),
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
            label=record_label,
            payload=rendered_payload,
            payload_format=payload_format,
            updating=True,
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
    ):
        settings = request.app.state.settings
        form_token = CsrfProtector(settings)
        record_label = str(label).strip()
        record_payload = str(payload)
        selected_format = str(payload_format).strip().lower()

        def save_error(message: str, status_code: int = 400) -> HTMLResponse:
            return HTMLResponse(
                _record_form(
                    form_token.issue(),
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
        if not record_payload.strip():
            return save_error("Record contents are required.")
        if len(record_payload) > 262_144:
            return save_error("Record contents must be 262144 characters or fewer.")
        if selected_format not in {"text", "json"}:
            return save_error("Choose text or JSON as the record content format.")
        if selected_format == "json":
            try:
                stored_payload = json.loads(record_payload)
            except json.JSONDecodeError:
                return save_error("JSON record contents must contain valid JSON.")
        else:
            stored_payload = record_payload

        try:
            await asyncio.wait_for(
                acorn.put_record(
                    record_name=record_label,
                    record_value=stored_payload,
                    record_type="generic",
                    record_kind=37375,
                    return_result=True,
                ),
                timeout=settings.wallet_load_timeout_seconds,
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
                "Safebox could not publish and verify the private record. Reload "
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
                    title="Delete private record",
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
            title="Private record deleted",
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
                    "Private record",
                    '<p class="error">Timed out while loading the record.</p>'
                    '<p><a href="/records">Return to records</a></p>',
                ),
                status_code=504,
            )
        except ValueError:
            return HTMLResponse(
                _page(
                    "Private record",
                    '<p class="error">The requested record was not found.</p>'
                    '<p><a href="/records">Return to records</a></p>',
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
                    "Private record",
                    '<p class="error">Unable to load the record from the bootstrap relay.</p>'
                    '<p><a href="/records">Return to records</a></p>',
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
        blob_query = urlencode({"label": label})
        return render_template(
            "record.html",
            title=label,
            label=label,
            edit_url=f'/record/edit?{urlencode({"label": label})}',
            saved=saved,
            record_type=str(record_value.type),
            payload=rendered_payload,
            has_blob=bool(getattr(record_value, "blobref", None)),
            blob_type=blob_type,
            blob_preview=blob_preview,
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
