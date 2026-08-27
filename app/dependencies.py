"""FastAPI dependency-injection boundary for Acorn sessions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from time import monotonic
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlmodel import Session

from acorn import Acorn

from app.config import Settings
from app.database import get_database_session
from app.security import SessionCipher, SessionCredentials, cookie_name_for_request


logger = logging.getLogger("safebox_web.security")


def _session_rejection_reason(exc: ValueError) -> str:
    cause = exc.__cause__
    if isinstance(cause, ValueError) and "expired" in str(cause).lower():
        return "expired"
    if cause is not None and cause.__class__.__name__ in {
        "InvalidTag",
        "InvalidToken",
    }:
        return "authentication_failed_or_expired"
    return "malformed_or_invalid"


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


SettingsDependency = Annotated[Settings, Depends(get_settings)]
DatabaseSessionDependency = Annotated[Session, Depends(get_database_session)]


def get_session_credentials(
    request: Request, settings: SettingsDependency
) -> SessionCredentials:
    cookie_name = cookie_name_for_request(request)
    token = request.cookies.get(cookie_name)
    if not token:
        logger.info(
            "session rejected reason=missing_cookie path=%s cookie=%s",
            request.url.path,
            cookie_name,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Acorn connection required",
        )
    try:
        return SessionCipher(settings).decode(token)
    except ValueError as exc:
        logger.warning(
            "session rejected reason=%s path=%s cookie=%s",
            _session_rejection_reason(exc),
            request.url.path,
            cookie_name,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Acorn connection is invalid or expired",
        ) from exc


CredentialsDependency = Annotated[
    SessionCredentials, Depends(get_session_credentials)
]


def build_acorn(credentials: SessionCredentials, settings: Settings) -> Acorn:
    """Build an Acorn from client-held credentials without loading state."""
    return Acorn(
        nsec=credentials.nsec,
        home_relay=credentials.bootstrap_relay,
        relays=[credentials.bootstrap_relay],
        blossom_home_server=settings.blossom_home_server,
        blossom_servers=[settings.blossom_home_server],
    )


def get_acorn(credentials: CredentialsDependency, settings: SettingsDependency) -> Acorn:
    """Build a request-scoped Acorn component without loading or storing state."""

    return build_acorn(credentials, settings)


AcornDependency = Annotated[Acorn, Depends(get_acorn)]


AcornFactory = Callable[[], Acorn]


def get_background_acorn_factory(
    credentials: CredentialsDependency,
    settings: SettingsDependency,
) -> AcornFactory:
    """Capture session credentials for one in-memory background job."""

    nsec = credentials.nsec
    bootstrap_relay = credentials.bootstrap_relay
    blossom_home_server = settings.blossom_home_server

    def create() -> Acorn:
        return Acorn(
            nsec=nsec,
            home_relay=bootstrap_relay,
            relays=[bootstrap_relay],
            blossom_home_server=blossom_home_server,
            blossom_servers=[blossom_home_server],
        )

    return create


BackgroundAcornFactoryDependency = Annotated[
    AcornFactory,
    Depends(get_background_acorn_factory),
]


async def get_loaded_acorn(
    acorn: AcornDependency, settings: SettingsDependency
) -> Acorn:
    """Load relay-backed state into a request-scoped Acorn instance."""

    started = monotonic()
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
    finally:
        logger.info(
            "acorn state load scope=funds duration_ms=%s",
            int((monotonic() - started) * 1000),
        )
    return acorn


LoadedAcornDependency = Annotated[Acorn, Depends(get_loaded_acorn)]


def get_payment_acorn(acorn: LoadedAcornDependency) -> Acorn:
    """Make the mutation boundary explicit for payment routes."""

    return acorn


PaymentAcornDependency = Annotated[Acorn, Depends(get_payment_acorn)]


def get_payment_acorn_factory(
    acorn: PaymentAcornDependency,
    settings: SettingsDependency,
) -> AcornFactory:
    """Clone the authenticated payment component for an in-memory job."""

    nsec = getattr(acorn, "privkey_bech32", None)
    bootstrap_relay = getattr(acorn, "home_relay", None)
    blossom_home_server = settings.blossom_home_server

    def create() -> Acorn:
        # Test and adapter implementations may not expose Acorn's key fields.
        # Production Acorn instances always take the fresh-instance branch.
        if not nsec or not bootstrap_relay:
            return acorn
        return Acorn(
            nsec=nsec,
            home_relay=bootstrap_relay,
            relays=[bootstrap_relay],
            blossom_home_server=blossom_home_server,
            blossom_servers=[blossom_home_server],
        )

    return create


PaymentAcornFactoryDependency = Annotated[
    AcornFactory,
    Depends(get_payment_acorn_factory),
]


def get_deposit_acorn(acorn: LoadedAcornDependency) -> Acorn:
    """Make the Lightning deposit mutation boundary explicit."""

    return acorn


DepositAcornDependency = Annotated[Acorn, Depends(get_deposit_acorn)]


def get_deposit_acorn_factory(
    acorn: DepositAcornDependency,
    settings: SettingsDependency,
) -> AcornFactory:
    """Clone the authenticated deposit component for an in-memory job."""

    nsec = getattr(acorn, "privkey_bech32", None)
    bootstrap_relay = getattr(acorn, "home_relay", None)
    blossom_home_server = settings.blossom_home_server

    def create() -> Acorn:
        if not nsec or not bootstrap_relay:
            return acorn
        return Acorn(
            nsec=nsec,
            home_relay=bootstrap_relay,
            relays=[bootstrap_relay],
            blossom_home_server=blossom_home_server,
            blossom_servers=[blossom_home_server],
        )

    return create


DepositAcornFactoryDependency = Annotated[
    AcornFactory,
    Depends(get_deposit_acorn_factory),
]


def get_receive_acorn(acorn: LoadedAcornDependency) -> Acorn:
    """Make the incoming-ecash mutation boundary explicit."""

    return acorn


ReceiveAcornDependency = Annotated[Acorn, Depends(get_receive_acorn)]


def get_record_acorn(acorn: AcornDependency) -> Acorn:
    """Provide record operations without loading funds or proof state."""

    return acorn


RecordAcornDependency = Annotated[Acorn, Depends(get_record_acorn)]
