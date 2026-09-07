"""FastAPI dependency-injection boundary for Acorn sessions."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from time import monotonic, time
from typing import Annotated

import httpx
from acorn import Acorn
from acorn.service_resolution import (
    ContextEndpointHint,
    ContextEndpointsRecord,
    ServiceEndpoint,
)
from fastapi import Depends, HTTPException, Request, status
from monstr.encrypt import Keys
from sqlmodel import Session

from app.config import Settings
from app.database import get_database_session
from app.security import SessionCipher, SessionCredentials, cookie_name_for_request


logger = logging.getLogger("safebox_web.security")
_INBOX_RELAY_CHECK_RETRY_SECONDS = 300.0
_inbox_relay_checks: dict[tuple[str, tuple[str, ...]], float | None] = {}
_MAINSTAY_CONTEXT_CACHE_SECONDS = 60.0
_MAINSTAY_CONTEXT_RECORD_CHECK_SECONDS = 300.0
_mainstay_context_cache: dict[str, tuple[float, dict]] = {}
_mainstay_context_checks: dict[tuple[str, str, str, str, str], float] = {}


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
        public_relays=list(settings.nip05_external_relays),
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
            public_relays=list(settings.nip05_external_relays),
            blossom_home_server=blossom_home_server,
            blossom_servers=[blossom_home_server],
        )

    return create


BackgroundAcornFactoryDependency = Annotated[
    AcornFactory,
    Depends(get_background_acorn_factory),
]


async def ensure_acorn_inbox_relays(acorn: Acorn, settings: Settings) -> None:
    """Initialize a missing signed inbox record from app-level relay defaults."""

    configured_relays = tuple(settings.nip05_external_relays)
    if not configured_relays:
        return
    resolver = getattr(acorn, "resolve_inbox_relays", None)
    publisher = getattr(acorn, "publish_inbox_relays", None)
    pubkey = str(getattr(acorn, "pubkey_hex", "") or "").lower()
    if not callable(resolver) or not callable(publisher) or not pubkey:
        logger.warning(
            "acorn inbox relay initialization skipped reason=unsupported_component"
        )
        return

    cache_key = (pubkey, configured_relays)
    retry_at = _inbox_relay_checks.get(cache_key)
    if retry_at is None and cache_key in _inbox_relay_checks:
        return
    if retry_at is not None and monotonic() < retry_at:
        return

    timeout = min(5.0, settings.wallet_load_timeout_seconds)
    try:
        resolution = await asyncio.wait_for(
            resolver(pubkey, lookup_relays=list(configured_relays)),
            timeout=timeout,
        )
        if not resolution.get("found"):
            await asyncio.wait_for(
                publisher(
                    list(configured_relays),
                    publish_relays=list(configured_relays),
                ),
                timeout=timeout,
            )
            logger.info(
                "acorn inbox relays initialized npub=%s relays=%s",
                getattr(acorn, "pubkey_bech32", pubkey),
                configured_relays,
            )
        _inbox_relay_checks[cache_key] = None
    except Exception as exc:
        _inbox_relay_checks[cache_key] = (
            monotonic() + _INBOX_RELAY_CHECK_RETRY_SECONDS
        )
        logger.warning(
            "acorn inbox relay initialization deferred npub=%s error_type=%s",
            getattr(acorn, "pubkey_bech32", pubkey),
            type(exc).__name__,
        )


async def ensure_acorn_mainstay_context(acorn: Acorn, settings: Settings) -> None:
    """Apply Mainstay's public Grove route to an Acorn's private context record."""

    context_url = settings.mainstay_context_url
    pubkey = str(getattr(acorn, "pubkey_hex", "") or "").lower()
    if not context_url:
        return
    if not pubkey:
        logger.warning(
            "acorn Mainstay context initialization skipped "
            "reason=unsupported_component"
        )
        return

    try:
        now = monotonic()
        cached = _mainstay_context_cache.get(context_url)
        if cached is not None and cached[0] > now:
            manifest = cached[1]
        else:
            timeout = min(5.0, settings.wallet_load_timeout_seconds)
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.get(
                    context_url,
                    headers={"Accept": "application/json"},
                )
                response.raise_for_status()
            manifest = response.json()
            if (
                not isinstance(manifest, dict)
                or manifest.get("type") != "mainstay-service-context"
                or manifest.get("version") != 1
            ):
                raise ValueError("unsupported Mainstay context manifest")
            _mainstay_context_cache[context_url] = (
                now + _MAINSTAY_CONTEXT_CACHE_SECONDS,
                manifest,
            )

        context_npub = Keys(
            pub_k=str(manifest.get("context_npub") or "")
        ).public_key_bech32()
        acorn.service_context_npub = context_npub
        for service in manifest.get("services") or []:
            if not isinstance(service, dict) or service.get("service_type") not in {
                "blossom",
                "grove",
            }:
                continue
            service_npub = Keys(
                pub_k=str(service.get("service_npub") or "")
            ).public_key_bech32()
            for endpoint in service.get("endpoints") or []:
                if not isinstance(endpoint, dict):
                    continue
                endpoint_id = str(endpoint.get("endpoint_id") or "")
                cache_key = (
                    pubkey,
                    context_npub,
                    service_npub,
                    endpoint_id,
                    repr(endpoint),
                )
                if _mainstay_context_checks.get(cache_key, 0.0) > monotonic():
                    continue
                await _install_context_service_endpoint(
                    acorn,
                    context_npub=context_npub,
                    service_npub=service_npub,
                    endpoint=endpoint,
                )
                _mainstay_context_checks[cache_key] = (
                    monotonic() + _MAINSTAY_CONTEXT_RECORD_CHECK_SECONDS
                )
    except Exception as exc:
        logger.warning(
            "acorn Mainstay context initialization deferred npub=%s "
            "error_type=%s",
            getattr(acorn, "pubkey_bech32", pubkey),
            type(exc).__name__,
        )


async def _install_context_service_endpoint(
    acorn: Acorn,
    *,
    context_npub: str,
    service_npub: str,
    endpoint: dict,
) -> bool:
    installer = getattr(acorn, "ensure_context_service_endpoint", None)
    if callable(installer):
        return await installer(
            context_npub=context_npub,
            service_npub=service_npub,
            endpoint=endpoint,
            source="mainstay",
            source_npub=context_npub,
        )

    getter = getattr(acorn, "get_context_endpoints", None)
    publisher = getattr(acorn, "publish_context_endpoints", None)
    if not callable(getter) or not callable(publisher):
        raise RuntimeError("Acorn does not support context endpoint records")

    normalized_endpoint = ServiceEndpoint.model_validate(endpoint)
    current = await getter()
    key = (context_npub, service_npub, normalized_endpoint.endpoint_id)
    existing = next(
        (
            hint
            for hint in current.hints
            if (
                hint.context_npub,
                hint.service_npub,
                hint.endpoint.endpoint_id,
            )
            == key
        ),
        None,
    )
    if (
        existing is not None
        and existing.endpoint == normalized_endpoint
        and existing.source == "mainstay"
        and existing.source_npub == context_npub
        and existing.state != "rejected"
    ):
        return False

    replacement = ContextEndpointHint(
        context_npub=context_npub,
        service_npub=service_npub,
        endpoint=normalized_endpoint,
        source="mainstay",
        source_npub=context_npub,
        state="candidate",
        sequence=(existing.sequence + 1 if existing is not None else 0),
        updated_at=int(time()),
    )
    hints = [
        hint
        for hint in current.hints
        if (
            hint.context_npub,
            hint.service_npub,
            hint.endpoint.endpoint_id,
        )
        != key
    ]
    hints.append(replacement)
    await publisher(ContextEndpointsRecord(hints=hints))
    return True


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
    await ensure_acorn_inbox_relays(acorn, settings)
    await ensure_acorn_mainstay_context(acorn, settings)
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
    service_context_npub = getattr(acorn, "service_context_npub", None)

    def create() -> Acorn:
        # Test and adapter implementations may not expose Acorn's key fields.
        # Production Acorn instances always take the fresh-instance branch.
        if not nsec or not bootstrap_relay:
            return acorn
        return Acorn(
            nsec=nsec,
            home_relay=bootstrap_relay,
            relays=[bootstrap_relay],
            public_relays=list(settings.nip05_external_relays),
            blossom_home_server=blossom_home_server,
            blossom_servers=[blossom_home_server],
            service_context_npub=service_context_npub,
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
    service_context_npub = getattr(acorn, "service_context_npub", None)

    def create() -> Acorn:
        if not nsec or not bootstrap_relay:
            return acorn
        return Acorn(
            nsec=nsec,
            home_relay=bootstrap_relay,
            relays=[bootstrap_relay],
            public_relays=list(settings.nip05_external_relays),
            blossom_home_server=blossom_home_server,
            blossom_servers=[blossom_home_server],
            service_context_npub=service_context_npub,
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


async def get_record_acorn(
    acorn: AcornDependency, settings: SettingsDependency
) -> Acorn:
    """Provide record operations without loading funds or proof state."""

    await ensure_acorn_mainstay_context(acorn, settings)
    return acorn


RecordAcornDependency = Annotated[Acorn, Depends(get_record_acorn)]
