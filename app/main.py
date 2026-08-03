"""Minimal stateless Safebox web shell."""

from __future__ import annotations

import asyncio
from html import escape
import json
import logging
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.config import Settings
from app.dependencies import (
    AcornDependency,
    CredentialsDependency,
    LoadedAcornDependency,
)
from app.security import (
    LOOPBACK_COOKIE_NAME,
    SECURE_COOKIE_NAME,
    CsrfProtector,
    SessionCipher,
    cookie_name_for_request,
    credentials_from_login,
    is_allowed_transport,
    is_loopback_http_request,
    is_same_origin,
)


logger = logging.getLogger("safebox_web.security")


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
    .error {{ color: #9b1c1c; }}
    code {{ overflow-wrap: anywhere; }}
    pre {{ background: #f4f3ef; overflow-x: auto; padding: 1rem; white-space: pre-wrap; word-break: break-word; }}
  </style>
</head>
<body><h1>{escape(title)}</h1>{body}</body>
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
</form>""",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Safebox Web", version="0.1.0")
    app.state.settings = settings or Settings.from_env()

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
            "default-src 'self'; style-src 'unsafe-inline'; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'"
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
    async def wallet(request: Request, acorn: LoadedAcornDependency) -> str:
        csrf_token = CsrfProtector(request.app.state.settings).issue()
        return _page(
            "Connected Acorn",
            f"""
<p>Component identity: <code>{escape(acorn.pubkey_bech32)}</code></p>
<p>Bootstrap relay: <code>{escape(acorn.home_relay)}</code></p>
<p>Balance: <strong>{int(acorn.get_balance()):,} sats</strong></p>
<p>Wallet state was loaded from the relay for this request. It was not stored
by the web application.</p>
<p><a href="/records">View private record labels</a></p>
<form method="post" action="/logout">
  <input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">
  <button type="submit">Disconnect</button>
</form>""",
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
