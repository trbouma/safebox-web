"""FastAPI dependency-injection boundary for Acorn sessions."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from acorn import Acorn

from app.config import Settings
from app.security import SessionCipher, SessionCredentials, cookie_name_for_request


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


SettingsDependency = Annotated[Settings, Depends(get_settings)]


def get_session_credentials(
    request: Request, settings: SettingsDependency
) -> SessionCredentials:
    token = request.cookies.get(cookie_name_for_request(request))
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Acorn login required",
        )
    try:
        return SessionCipher(settings).decode(token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Acorn session is invalid or expired",
        ) from exc


CredentialsDependency = Annotated[
    SessionCredentials, Depends(get_session_credentials)
]


def get_acorn(credentials: CredentialsDependency) -> Acorn:
    """Build a request-scoped Acorn component without loading or storing state."""

    return Acorn(
        nsec=credentials.nsec,
        home_relay=credentials.bootstrap_relay,
        relays=[credentials.bootstrap_relay],
    )


AcornDependency = Annotated[Acorn, Depends(get_acorn)]


async def get_loaded_acorn(
    acorn: AcornDependency, settings: SettingsDependency
) -> Acorn:
    """Load relay-backed state into a request-scoped Acorn instance."""

    try:
        await asyncio.wait_for(
            acorn.load_data(), timeout=settings.wallet_load_timeout_seconds
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Timed out while loading the Acorn wallet from its bootstrap relay",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to load the Acorn wallet from its bootstrap relay",
        ) from exc
    return acorn


LoadedAcornDependency = Annotated[Acorn, Depends(get_loaded_acorn)]


def get_payment_acorn(acorn: LoadedAcornDependency) -> Acorn:
    """Make the mutation boundary explicit for payment routes."""

    return acorn


PaymentAcornDependency = Annotated[Acorn, Depends(get_payment_acorn)]
