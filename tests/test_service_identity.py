from __future__ import annotations

import json

from cryptography.fernet import Fernet
import pytest
from starlette.testclient import TestClient

from app.config import Settings
from app.identity import fips_ipv6_address, service_npub
from app.main import create_app


SERVICE_NSEC = "11" * 32
SERVICE_NSEC_BECH32 = (
    "nsec1zyg3zyg3zyg3zyg3zyg3zyg3zyg3zyg3zyg3zyg3zyg3zyg3zygs4rm7hz"
)
SERVICE_NPUB = (
    "npub1fu64hh9hes90w2808n8tjc2ajp5yhddjef0ctx4s7zmsgp6cwx4qgy4eg9"
)
SERVICE_FIPS_IPV6_ADDRESS = "fd34:da5e:3969:3577:9c48:835a:7f60:8b56"


def settings(tmp_path, **changes) -> Settings:
    values = {
        "cookie_key": Fernet.generate_key().decode("ascii"),
        "database_url": f"sqlite:///{tmp_path / 'database.db'}",
        "service_identity_file": tmp_path / "safebox-web-service-identity.json",
    }
    values.update(changes)
    return Settings(**values)


def test_service_identity_accepts_hex_and_nsec_encoding() -> None:
    assert service_npub(SERVICE_NSEC) == SERVICE_NPUB
    assert service_npub(SERVICE_NSEC_BECH32) == SERVICE_NPUB
    assert fips_ipv6_address(SERVICE_NPUB) == SERVICE_FIPS_IPV6_ADDRESS


def test_service_identity_is_reported_and_bound_to_persistent_data(tmp_path) -> None:
    configured = settings(tmp_path, service_nsec=SERVICE_NSEC)
    with TestClient(create_app(configured), base_url="https://safebox.example") as client:
        response = client.get("/info")

    assert response.status_code == 200
    assert response.json()["service_identity"] == {
        "npub": SERVICE_NPUB,
        "fips_ipv6_address": SERVICE_FIPS_IPV6_ADDRESS,
        "type": "safebox-web",
        "management": "independent",
        "state": "uncommissioned",
        "descriptor_event_id": None,
        "operator": None,
    }
    sentinel = json.loads(
        configured.service_identity_file.read_text(encoding="utf-8")
    )
    assert sentinel["npub"] == SERVICE_NPUB
    assert SERVICE_NSEC not in response.text


def test_standalone_service_identity_is_optional(tmp_path) -> None:
    with TestClient(
        create_app(settings(tmp_path)), base_url="https://safebox.example"
    ) as client:
        response = client.get("/info")

    identity = response.json()["service_identity"]
    assert identity["npub"] is None
    assert identity["fips_ipv6_address"] is None
    assert identity["management"] == "independent"
    assert identity["state"] == "unconfigured"
    assert not (tmp_path / "safebox-web-service-identity.json").exists()


def test_service_identity_cannot_change_for_existing_data(tmp_path) -> None:
    with TestClient(
        create_app(settings(tmp_path, service_nsec=SERVICE_NSEC)),
        base_url="https://safebox.example",
    ):
        pass

    with pytest.raises(RuntimeError, match="does not match the recorded"):
        with TestClient(
            create_app(settings(tmp_path, service_nsec="22" * 32)),
            base_url="https://safebox.example",
        ):
            pass


def test_recorded_service_identity_requires_private_key(tmp_path) -> None:
    with TestClient(
        create_app(settings(tmp_path, service_nsec=SERVICE_NSEC)),
        base_url="https://safebox.example",
    ):
        pass

    with pytest.raises(RuntimeError, match="SAFEBOX_WEB_SERVICE_NSEC is required"):
        with TestClient(
            create_app(settings(tmp_path)),
            base_url="https://safebox.example",
        ):
            pass


def test_mainstay_management_requires_service_key(tmp_path) -> None:
    with pytest.raises(ValueError, match="mainstay-managed Safebox Web requires"):
        settings(tmp_path, service_management="mainstay-managed")
