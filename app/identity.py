"""Persistent Nostr identity binding for a Safebox Web instance."""

from __future__ import annotations

import json
import os
from pathlib import Path

from stroma import KeyError as StromaKeyError
from stroma import Keys
from stroma import fips_ipv6_address as stroma_fips_ipv6_address


def service_npub(secret: str) -> str:
    """Derive the NIP-19 npub for a hex or nsec-encoded private key."""

    try:
        return Keys(priv_k=secret).public_key_bech32()
    except StromaKeyError as exc:
        raise ValueError("SAFEBOX_WEB_SERVICE_NSEC is invalid") from exc


def fips_ipv6_address(npub: str) -> str:
    """Derive the FIPS fd00::/8 address for a service npub."""

    try:
        return stroma_fips_ipv6_address(npub)
    except StromaKeyError as exc:
        raise ValueError("Safebox Web service npub is invalid") from exc


def bind_service_identity(path: Path, *, npub: str | None) -> None:
    """Bind persistent Safebox Web state to one application identity."""

    if path.is_file():
        try:
            recorded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "Safebox Web service identity sentinel is invalid"
            ) from exc
        recorded_npub = recorded.get("npub") if isinstance(recorded, dict) else None
        if not isinstance(recorded_npub, str) or not recorded_npub:
            raise RuntimeError("Safebox Web service identity sentinel is invalid")
        if npub is None:
            raise RuntimeError(
                "SAFEBOX_WEB_SERVICE_NSEC is required for the recorded "
                "Safebox Web identity"
            )
        if recorded_npub != npub:
            raise RuntimeError(
                "SAFEBOX_WEB_SERVICE_NSEC does not match the recorded "
                "Safebox Web identity"
            )
        return

    if npub is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps({"schema": "org.mainstay.service-identity", "npub": npub})
        + "\n"
    )
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another web worker may have won the first-start race.
        bind_service_identity(path, npub=npub)
        return
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(payload)
