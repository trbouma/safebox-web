"""Minimal stateless Safebox web shell."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from html import escape
import json
import logging
from pathlib import Path
import re
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import qrcode
import qrcode.image.svg
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from acorn import Acorn
from acorn.func_utils import generate_seed_phrase_and_nsec, npub_to_hex

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
)
from app.models import ClaimedHandle
from app.lnurl_pay import router as lnurl_pay_router
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


logger = logging.getLogger("safebox_web.security")


def _relationship_visual() -> str:
    """Render the Acorn-to-Safebox relationship without external assets."""

    return """
<section class="relationship" aria-label="Acorn connected with Safebox">
  <div class="relationship-card">
    <svg class="relationship-mark" viewBox="0 0 88 88" aria-hidden="true">
      <path fill="#65774a" d="M18 34c0-14 12-25 26-25s26 11 26 25H18Z"/>
      <path fill="#465533" d="M42 12 48 0l10 5-7 12Z"/>
      <path fill="#955522" d="M20 38h48c0 25-9 39-24 50C29 77 20 63 20 38Z"/>
      <path d="M25 42h15v11h13v12H42v17" fill="none" stroke="#fffaf1"
            stroke-width="5" stroke-linejoin="round"/>
    </svg>
    <span><strong>Acorn</strong><small>User-controlled component</small></span>
  </div>
  <div class="relationship-connection">
    <span class="relationship-arrow" aria-hidden="true">↔</span>
        <small>User-controlled session</small>
  </div>
  <div class="relationship-card">
    <svg class="relationship-mark" viewBox="0 0 88 88" aria-hidden="true">
      <rect x="4" y="4" width="80" height="80" rx="13" fill="#3d60e8"/>
      <path d="M4 47h20V20h29V4M31 84V59h27V35h26M24 47h15V31h20v16H47v17H4"
            fill="none" stroke="#f8f9ff" stroke-width="6"
            stroke-linejoin="miter"/>
    </svg>
    <span><strong>Safebox</strong><small>Web service surface</small></span>
  </div>
</section>"""


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} · Safebox</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 42rem; margin: 4rem auto; padding: 0 1rem; line-height: 1.5; }}
    label {{ display: block; margin-top: 1rem; }}
    input, select, textarea, button {{ box-sizing: border-box; font: inherit; padding: .6rem; width: 100%; }}
    textarea {{ min-height: 7rem; }}
    button {{ cursor: pointer; margin-top: 1.25rem; }}
    button:disabled {{ cursor: wait; opacity: .65; }}
    .error {{ color: #9b1c1c; }}
    .progress {{ margin-top: 1rem; color: #465533; font-weight: 650; }}
    code {{ overflow-wrap: anywhere; }}
    pre {{ background: #f4f3ef; overflow-x: auto; padding: 1rem; white-space: pre-wrap; word-break: break-word; }}
    .invoice-qr {{ display: flex; justify-content: center; margin: 1.5rem 0; }}
    .invoice-qr svg {{ width: min(100%, 22rem); height: auto; }}
    .relationship {{ display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); gap: .8rem; align-items: center; margin: 0 0 2.5rem; }}
    .relationship-card {{ display: flex; align-items: center; gap: .75rem; min-width: 0; padding: .8rem; border: 1px solid #d8d5cc; border-radius: 1rem; background: #faf9f5; }}
    .relationship-card span {{ display: flex; min-width: 0; flex-direction: column; }}
    .relationship-card strong {{ font-size: 1.05rem; }}
    .relationship-card small, .relationship-connection small {{ color: #625f57; line-height: 1.25; }}
    .relationship-mark {{ width: 3.5rem; height: 3.5rem; flex: 0 0 auto; }}
    .relationship-connection {{ display: flex; flex-direction: column; align-items: center; text-align: center; max-width: 7rem; }}
    .relationship-arrow {{ color: #56653f; font-size: 2rem; line-height: 1; }}
    .transaction-list {{ display: grid; gap: 1rem; margin: 1.5rem 0; }}
    .transaction-card {{ border: 1px solid #d8d5cc; border-left: .35rem solid #777; border-radius: .8rem; padding: 1rem; background: #faf9f5; min-width: 0; }}
    .transaction-card.credit {{ border-left-color: #65774a; }}
    .transaction-card.debit {{ border-left-color: #955522; }}
    .transaction-card.advisory {{ border-left-color: #68769a; }}
    .transaction-header {{ display: flex; justify-content: space-between; gap: 1rem; align-items: baseline; }}
    .transaction-kind {{ font-weight: 750; }}
    .transaction-date {{ color: #625f57; font-size: .92rem; }}
    .transaction-amount {{ font-size: 1.45rem; font-weight: 750; margin: .45rem 0 .75rem; }}
    .transaction-card.credit .transaction-amount {{ color: #465533; }}
    .transaction-card.debit .transaction-amount {{ color: #7d431b; }}
    .transaction-details {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .6rem 1rem; margin: 0; }}
    .transaction-details div {{ min-width: 0; }}
    .transaction-details dt {{ color: #625f57; font-size: .8rem; text-transform: uppercase; letter-spacing: .04em; }}
    .transaction-details dd {{ margin: .1rem 0 0; overflow-wrap: anywhere; }}
    .transaction-note {{ margin: .85rem 0 0; padding-top: .75rem; border-top: 1px solid #e2dfd6; overflow-wrap: anywhere; }}
    @media (max-width: 36rem) {{
      body {{ margin-top: 2rem; }}
      .relationship {{ grid-template-columns: 1fr; justify-items: stretch; }}
      .relationship-connection {{ justify-self: center; }}
      .relationship-arrow {{ transform: rotate(90deg); }}
      .transaction-header {{ align-items: flex-start; flex-direction: column; gap: .2rem; }}
      .transaction-details {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
</style>
  <script src="/static/forms.js" defer></script>
</head>
<body>{_relationship_visual()}<h1>{escape(title)}</h1>{body}</body>
</html>"""


def _login_form(
    default_relay: str, csrf_token: str, error: str | None = None
) -> str:
    error_html = f'<p class="error">{escape(error)}</p>' if error else ""
    return _page(
        "Connect an Acorn",
        f"""
{error_html}
<p>Safebox does not retain wallet state on the server. Your secret is encrypted
into an authenticated browser cookie for this session.</p>
<form method="post" action="/login" autocomplete="off">
  <input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">
  <label for="secret_type">Recovery material</label>
  <select id="secret_type" name="secret_type">
    <option value="nsec">nsec private key</option>
    <option value="mnemonic">offline mnemonic</option>
  </select>
  <label for="secret">Secret</label>
  <textarea id="secret" name="secret" required spellcheck="false" autocapitalize="none"></textarea>
  <label for="bootstrap_relay">Bootstrap relay</label>
  <input id="bootstrap_relay" name="bootstrap_relay" type="text"
         value="{escape(default_relay, quote=True)}" required spellcheck="false">
  <button type="submit">Connect</button>
</form>
<hr>
<p>Don't have an Acorn yet? <a href="/create">Create a new Acorn</a>.</p>""",
    )


def _create_form(
    default_relay: str,
    default_mint: str,
    csrf_token: str,
    error: str | None = None,
) -> str:
    error_html = f'<p class="error">{escape(error)}</p>' if error else ""
    return _page(
        "Create a new Acorn",
        f"""
{error_html}
<p>Safebox will generate a new component keypair, initialize its wallet state
on the home relay, and start a user-controlled session.</p>
<form method="post" action="/create" autocomplete="off">
  <input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">
  <label for="home_relay">Home relay</label>
  <input id="home_relay" name="home_relay" type="text"
         value="{escape(default_relay, quote=True)}" required spellcheck="false">
  <label for="home_mint">Home mint</label>
  <input id="home_mint" name="home_mint" type="text"
         value="{escape(default_mint, quote=True)}" required spellcheck="false">
  <label>
    <input name="confirmed" type="checkbox" value="yes" required>
    I understand that Safebox will display sensitive recovery material once the
    new Acorn has been verified, and I must save it securely.
  </label>
  <button type="submit">Create Acorn</button>
</form>
<p><a href="/login">Connect an existing Acorn instead</a></p>""",
    )


def _payment_form(
    balance: int,
    csrf_token: str,
    error: str | None = None,
    balance_status: str | None = None,
) -> str:
    error_html = f'<p class="error">{escape(error)}</p>' if error else ""
    return _page(
        "Pay a Lightning address",
        f"""
{error_html}
{balance_status or f'<p>Relay-visible proof total: <strong>{int(balance):,} sats</strong></p>'}
<p>The payment amount and the mint's fee reserve must fit within one spendable
keyset. The displayed total balance may therefore be greater than the amount
available for one payment.</p>
<form method="post" action="/pay" autocomplete="off"
      data-progress-message="Payment in progress. Please wait and do not refresh this page."
      data-progress-button="Sending payment…">
  <input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">
  <label for="lightning_address">Lightning address</label>
  <input id="lightning_address" name="lightning_address" type="text"
         placeholder="alice@example.com" required spellcheck="false" autocapitalize="none">
  <label for="amount">Amount in sats</label>
  <input id="amount" name="amount" type="number" min="1" step="1" required>
  <label for="comment">Comment</label>
  <input id="comment" name="comment" type="text" maxlength="200"
         value="Paid from Safebox Web">
  <label>
    <input name="confirmed" type="checkbox" value="yes" required>
    I confirm the recipient and amount and understand that this spends funds.
  </label>
  <button type="submit">Send payment</button>
</form>
<p><a href="/wallet">Cancel and return to wallet</a></p>""",
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


def _transaction_history_html(entries: list[dict]) -> str:
    """Render Acorn journal entries as compact, mobile-friendly cards."""

    cards: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue

        tx_type = str(entry.get("tx_type") or "").upper()
        direction, sign, style = {
            "C": ("Credit", "+", "credit"),
            "D": ("Debit", "−", "debit"),
            "X": ("Advisory", "", "advisory"),
        }.get(tx_type, (tx_type or "Transaction", "", "advisory"))
        amount = escape(str(entry.get("amount", 0)))
        created = escape(str(entry.get("create_time") or "Unknown time"))
        fees = escape(str(entry.get("fees") or 0))
        current_balance = entry.get("current_balance")
        balance = "—" if current_balance is None else escape(str(current_balance))
        tendered_amount = entry.get("tendered_amount")
        tendered_currency = str(entry.get("tendered_currency") or "SAT")
        tender = (
            "—"
            if tendered_amount is None
            else f"{escape(str(tendered_amount))} {escape(tendered_currency)}"
        )
        comment = str(entry.get("comment") or "").strip()
        note = (
            f'<p class="transaction-note"><strong>Note:</strong> {escape(comment)}</p>'
            if comment
            else ""
        )

        cards.append(
            f"""
<article class="transaction-card {style}">
  <header class="transaction-header">
    <span class="transaction-kind">{escape(direction)}</span>
    <time class="transaction-date">{created}</time>
  </header>
  <div class="transaction-amount">{sign}{amount} sats</div>
  <dl class="transaction-details">
    <div><dt>Tender</dt><dd>{tender}</dd></div>
    <div><dt>Fees</dt><dd>{fees} sats</dd></div>
    <div><dt>Balance</dt><dd>{balance} sats</dd></div>
  </dl>
  {note}
</article>"""
        )

    if not cards:
        return "<p>No transaction history was found.</p>"
    return '<section class="transaction-list" aria-label="Transaction history">' + "".join(cards) + "</section>"


def _transactions_page(
    entries: list[dict],
    csrf_token: str,
    notice: str | None = None,
) -> str:
    """Render transaction history together with explicit incoming-ecash receipt."""

    notice_html = (
        f'<p role="status"><strong>{escape(notice)}</strong></p>' if notice else ""
    )
    return _page(
        "Transaction history",
        f"""
<p><a href="/wallet">← Back to wallet</a></p>
{notice_html}
<section aria-labelledby="receive-ecash-heading">
  <h2 id="receive-ecash-heading">Incoming ecash</h2>
  <p>Check this Acorn's home relay for incoming ecash transfers and accept any
  valid proofs into the wallet balance.</p>
  <form method="post" action="/transactions/receive"
        data-progress-message="Checking for incoming ecash and refreshing accepted proofs. Please wait."
        data-progress-button="Receiving ecash…">
    <input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">
    <button type="submit">Check and receive ecash</button>
  </form>
</section>
<hr>
{_transaction_history_html(entries)}
<p><a href="/wallet">Return to wallet</a></p>""",
    )


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
    error_html = f'<p class="error">{escape(error)}</p>' if error else ""
    host = hostname or "this service"
    if existing is not None:
        address = f"{existing.claimed_handle}@{host}"
        return _page(
            "NIP-05 handle",
            f"""
<p><a href="/wallet">← Back to wallet</a></p>
{error_html}
<p>This Acorn controls <strong>{escape(address)}</strong>.</p>
<p>Component public key: <code>{escape(existing.npub)}</code></p>
<p>Resolution relay: <code>{escape(existing.home_relay)}</code></p>
<p>Submit the current handle to refresh its home relay, or choose another
unclaimed handle to change the address. Changing it immediately releases the
current address, which another Acorn may then claim.</p>
<form method="post" action="/handle" autocomplete="off">
  <input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">
  <label for="claimed_handle">Handle</label>
  <input id="claimed_handle" name="claimed_handle" type="text" minlength="1"
         maxlength="64" pattern="[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]|[A-Za-z0-9]"
         value="{escape(existing.claimed_handle, quote=True)}" required
         spellcheck="false" autocapitalize="none">
  <button type="submit">Update handle</button>
</form>
<p><a href="/.well-known/nostr.json?{urlencode({'name': existing.claimed_handle})}">View public NIP-05 response</a></p>
<hr>
<form method="post" action="/handle/remove">
  <input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">
  <label>
    <input name="confirmed" type="checkbox" value="yes" required>
    Remove this public NIP-05 address. The released handle may subsequently be
    claimed by another Acorn.
  </label>
  <button type="submit">Remove handle</button>
</form>""",
        )

    return _page(
        "Claim a NIP-05 handle",
        f"""
<p><a href="/wallet">← Back to wallet</a></p>
{error_html}
<p>Choose a public handle for this Acorn component. Safebox stores only the
handle, component public key, and home relay. Your private key is never written
to this directory.</p>
<form method="post" action="/handle" autocomplete="off">
  <input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">
  <label for="claimed_handle">Handle</label>
  <input id="claimed_handle" name="claimed_handle" type="text" minlength="1"
         maxlength="64" pattern="[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]|[A-Za-z0-9]"
         placeholder="alice" required spellcheck="false" autocapitalize="none">
  <p>Your address will be <strong>handle@{escape(host)}</strong>.</p>
  <button type="submit">Claim handle</button>
</form>""",
    )


def _deposit_form(
    balance: int,
    home_mint: str,
    csrf_token: str,
    error: str | None = None,
    balance_status: str | None = None,
) -> str:
    error_html = f'<p class="error">{escape(error)}</p>' if error else ""
    return _page(
        "Deposit funds",
        f"""
{error_html}
{balance_status or f'<p>Relay-visible proof total: <strong>{int(balance):,} sats</strong></p>'}
<p>Home mint: <code>{escape(home_mint)}</code></p>
<form method="post" action="/deposit" autocomplete="off"
      data-progress-message="Creating a deposit invoice. Please wait."
      data-progress-button="Creating invoice…">
  <input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">
  <label for="amount">Amount in sats</label>
  <input id="amount" name="amount" type="number" min="1" step="1" required>
  <button type="submit">Create Lightning invoice</button>
</form>
<p><a href="/wallet">Cancel and return to wallet</a></p>""",
    )


def _invoice_svg(invoice: str) -> str:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(invoice)
    qr.make(fit=True)
    image = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    return image.to_string(encoding="unicode")


def _deposit_invoice_page(
    state: DepositQuoteState,
    state_token: str,
    csrf_token: str,
    message: str | None = None,
) -> str:
    message_html = f'<p class="error">{escape(message)}</p>' if message else ""
    return _page(
        "Pay deposit invoice",
        f"""
{message_html}
<p>Pay <strong>{state.amount:,} sats</strong> to the Acorn home mint:</p>
<p><code>{escape(state.mint)}</code></p>
<div class="invoice-qr">{_invoice_svg(state.invoice)}</div>
<label for="invoice">Lightning invoice</label>
<textarea id="invoice" readonly spellcheck="false">{escape(state.invoice)}</textarea>
<p>Safebox does not poll for payment. After paying the invoice, use the button
below to ask the mint to confirm payment and add the funds to this Acorn.</p>
<form method="post" action="/deposit/check"
      data-progress-message="Checking and finalizing the deposit. Please wait and do not refresh this page."
      data-progress-button="Checking deposit…">
  <input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">
  <input type="hidden" name="deposit_token" value="{escape(state_token, quote=True)}">
  <button type="submit">I have paid this invoice</button>
</form>
<p><a href="/wallet">Return to wallet without checking</a></p>""",
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
            "default-src 'self'; script-src 'self'; style-src 'unsafe-inline'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.get("/health", response_class=JSONResponse)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def home() -> str:
        return _page(
            "Safebox",
            '<p>A minimal stateless web interface for Acorn.</p><p><a href="/login">Connect an Acorn</a></p>',
        )

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

        try:
            normalized_relay = normalize_bootstrap_relay(home_relay)
            normalized_mint = normalize_home_mint(home_mint)
        except ValueError as exc:
            return creation_error(str(exc))

        seed_phrase, generated_nsec = generate_seed_phrase_and_nsec()
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
        )
        response = HTMLResponse(
            _page(
                "New Acorn created",
                f"""
<p class="error"><strong>Save this recovery material now.</strong> Anyone who
obtains it can control this Acorn and its funds and records.</p>
<p>Offline mnemonic:</p>
<pre>{escape(seed_phrase)}</pre>
<p>nsec private key:</p>
<pre>{escape(generated_nsec)}</pre>
<p>Component public key: <code>{escape(acorn.pubkey_bech32)}</code></p>
<p>Home relay: <code>{escape(normalized_relay)}</code></p>
<p>Home mint: <code>{escape(normalized_mint)}</code></p>
<p>This page is not server-side recovery storage. Save the material offline
before leaving it. Do not refresh or resubmit this page; that would create a
different Acorn.</p>
<p><a href="/wallet">Continue to wallet</a></p>""",
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
            return JSONResponse(
                status_code=403,
                content={"detail": "Form token is invalid or expired; reload the login page"},
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
            return JSONResponse(
                status_code=403,
                content={"detail": "Form token is invalid or expired; reload the page"},
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
        nip05_html = ""
        if claimed_handle is not None:
            nip05_address = (
                f"{claimed_handle.claimed_handle}@{request.url.hostname}"
            )
            nip05_html = (
                '<p>NIP-05 address: <strong>'
                f'<a href="/handle">{escape(nip05_address)}</a>'
                "</strong></p>"
            )
            if settings.service_acorn_enabled:
                nip05_html += (
                    '<p>Lightning address: <strong>'
                    f"{escape(nip05_address)}"
                    "</strong></p>"
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
        return _page(
            "Connected Acorn",
            f"""
<p>Component public key: <code>{escape(acorn.pubkey_bech32)}</code></p>
<p>Bootstrap relay: <code>{escape(acorn.home_relay)}</code></p>
{nip05_html}
{balance_status}
<p>Wallet state was loaded from the relay for this request. It was not stored
by the web application.</p>
<p><a href="/deposit">Deposit funds</a></p>
<p><a href="/pay">Pay a Lightning address</a></p>
<p><a href="/transactions">View transaction history</a></p>
<p><a href="/records">View private record labels</a></p>
<p><a href="/handle">Claim or view a NIP-05 handle</a></p>
<form method="post" action="/logout">
  <input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">
  <button type="submit">Disconnect</button>
</form>""",
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

        return _page(
            "Payment successful",
            f"""
<p>Sent: <strong>{amount_sats:,} sats</strong></p>
<p>Fee: <strong>{int(fees):,} sats</strong></p>
<p>Recipient: <code>{escape(recipient)}</code></p>
<p>{escape(str(message))}</p>
<p>Do not refresh this result page. <a href="/wallet">Return to wallet</a>.</p>""",
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
        if unique_labels:
            items = "".join(
                f'<li><a href="/record?{urlencode({"label": label})}">'
                f"{escape(label)}</a></li>"
                for label in unique_labels
            )
            content = f"<ul>{items}</ul>"
        else:
            content = "<p>No private user records were found.</p>"
        return _page(
            "Private records",
            content + '<p><a href="/wallet">Return to wallet</a></p>',
        )

    @app.get("/record", response_class=HTMLResponse)
    async def record(request: Request, label: str, acorn: LoadedAcornDependency):
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
        return _page(
            label,
            f"""
<p>Type: <code>{escape(str(record_value.type))}</code></p>
<pre>{escape(rendered_payload)}</pre>
<p><a href="/records">Return to records</a></p>""",
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
