from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import stat

from cryptography.fernet import Fernet
import pytest

from app.config import Settings
from app.service_acorn import start_service_acorn, stop_service_acorn


TEST_SERVICE_NSEC = "nsec1service"
TEST_SETTINGS = Settings(
    cookie_key=Fernet.generate_key().decode("ascii"),
    session_ttl_seconds=3600,
)


class FakeServiceAcorn:
    instances: list["FakeServiceAcorn"] = []
    next_balance = 0
    fail_burn = False

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.pubkey_bech32 = "npub1service"
        self.home_relay = kwargs["home_relay"]
        self.balance = self.__class__.next_balance
        self.create_calls: list[dict] = []
        self.load_calls = 0
        self.burn_calls: list[dict] = []
        self.__class__.instances.append(self)

    async def create_instance(self, **kwargs):
        self.create_calls.append(kwargs)
        return TEST_SERVICE_NSEC

    async def load_data(self) -> None:
        self.load_calls += 1

    def get_balance(self) -> int:
        return self.balance

    async def burn_wallet(self, **kwargs) -> dict:
        self.burn_calls.append(kwargs)
        if self.__class__.fail_burn:
            raise RuntimeError("burn failed")
        return {"status": "OK", "balance_before": self.balance}


@pytest.fixture(autouse=True)
def reset_fake_service_acorn():
    FakeServiceAcorn.instances.clear()
    FakeServiceAcorn.next_balance = 0
    FakeServiceAcorn.fail_burn = False


def service_settings(tmp_path, **changes) -> Settings:
    settings = replace(
        TEST_SETTINGS,
        service_acorn_enabled=True,
        service_acorn_home_relay="wss://relay.example.com",
        service_acorn_home_mint="https://mint.example.com",
        service_acorn_state_file=str(tmp_path / "service-acorn.json"),
    )
    return replace(settings, **changes)


def test_service_acorn_is_created_and_cleanly_burned(tmp_path) -> None:
    settings = service_settings(tmp_path)

    runtime = asyncio.run(
        start_service_acorn(
            settings,
            acorn_factory=FakeServiceAcorn,
            key_generator=lambda: ("service seed phrase", TEST_SERVICE_NSEC),
        )
    )

    state = json.loads(runtime.state_path.read_text(encoding="utf-8"))
    assert runtime.recovered is False
    assert state == {
        "home_mint": "https://mint.example.com",
        "home_relay": "wss://relay.example.com",
        "initialized": True,
        "nsec": TEST_SERVICE_NSEC,
    }
    assert stat.S_IMODE(runtime.state_path.stat().st_mode) == 0o600
    assert runtime.acorn.create_calls == [
        {"keepkey": False, "seed_phrase": "service seed phrase"}
    ]

    result = asyncio.run(stop_service_acorn(runtime, settings))

    assert result["status"] == "OK"
    assert runtime.acorn.burn_calls == [
        {
            "send_to": None,
            "send_relay": None,
            "relays": ["wss://relay.example.com"],
            "allow_funded": False,
        }
    ]
    assert not runtime.state_path.exists()


def test_service_acorn_recovers_after_unclean_stop(tmp_path) -> None:
    settings = service_settings(tmp_path)
    first = asyncio.run(
        start_service_acorn(
            settings,
            acorn_factory=FakeServiceAcorn,
            key_generator=lambda: ("service seed phrase", TEST_SERVICE_NSEC),
        )
    )

    recovered = asyncio.run(
        start_service_acorn(
            settings,
            acorn_factory=FakeServiceAcorn,
            key_generator=lambda: (_ for _ in ()).throw(
                AssertionError("must not generate")
            ),
        )
    )

    assert first.state_path == recovered.state_path
    assert recovered.recovered is True
    assert recovered.acorn.kwargs["nsec"] == TEST_SERVICE_NSEC
    assert recovered.acorn.create_calls == []
    assert recovered.acorn.load_calls == 1


def test_service_acorn_migrates_when_configured_endpoints_change(tmp_path) -> None:
    original_settings = service_settings(tmp_path)
    first = asyncio.run(
        start_service_acorn(
            original_settings,
            acorn_factory=FakeServiceAcorn,
            key_generator=lambda: ("old seed phrase", "nsec1oldservice"),
        )
    )
    migration_settings = replace(
        original_settings,
        service_acorn_migrate=True,
        service_acorn_home_relay="wss://spurline.example.com/",
        service_acorn_home_mint="https://mint-new.example.com/",
    )

    migrated = asyncio.run(
        start_service_acorn(
            migration_settings,
            acorn_factory=FakeServiceAcorn,
            key_generator=lambda: ("new seed phrase", "nsec1newservice"),
        )
    )

    state = json.loads(migrated.state_path.read_text(encoding="utf-8"))
    previous_acorn = FakeServiceAcorn.instances[1]
    assert first.state_path == migrated.state_path
    assert migrated.recovered is True
    assert migrated.migrated is True
    assert migrated.acorn.kwargs == {
        "nsec": "nsec1newservice",
        "home_relay": "wss://spurline.example.com/",
        "relays": ["wss://spurline.example.com/"],
        "mints": ["https://mint-new.example.com"],
    }
    assert migrated.acorn.create_calls == [
        {"keepkey": False, "seed_phrase": "new seed phrase"}
    ]
    assert previous_acorn.burn_calls == [
        {
            "send_to": None,
            "send_relay": None,
            "relays": ["wss://relay.example.com"],
            "allow_funded": False,
        }
    ]
    assert state == {
        "home_mint": "https://mint-new.example.com",
        "home_relay": "wss://spurline.example.com/",
        "initialized": True,
        "nsec": "nsec1newservice",
    }


def test_service_acorn_migration_is_noop_when_endpoints_match(tmp_path) -> None:
    original_settings = service_settings(tmp_path)
    asyncio.run(
        start_service_acorn(
            original_settings,
            acorn_factory=FakeServiceAcorn,
            key_generator=lambda: ("service seed phrase", TEST_SERVICE_NSEC),
        )
    )

    recovered = asyncio.run(
        start_service_acorn(
            replace(original_settings, service_acorn_migrate=True),
            acorn_factory=FakeServiceAcorn,
            key_generator=lambda: (_ for _ in ()).throw(
                AssertionError("must not generate")
            ),
        )
    )

    assert recovered.recovered is True
    assert recovered.migrated is False
    assert recovered.acorn.create_calls == []
    assert len(FakeServiceAcorn.instances) == 2


def test_service_acorn_migration_falls_back_for_funded_wallet(tmp_path) -> None:
    original_settings = service_settings(tmp_path)
    asyncio.run(
        start_service_acorn(
            original_settings,
            acorn_factory=FakeServiceAcorn,
            key_generator=lambda: ("old seed phrase", "nsec1oldservice"),
        )
    )
    original_state = (tmp_path / "service-acorn.json").read_text(encoding="utf-8")
    FakeServiceAcorn.next_balance = 21

    recovered = asyncio.run(
        start_service_acorn(
            replace(
                original_settings,
                service_acorn_migrate=True,
                service_acorn_home_relay="wss://spurline.example.com",
            ),
            acorn_factory=FakeServiceAcorn,
            key_generator=lambda: (_ for _ in ()).throw(
                AssertionError("must not generate")
            ),
        )
    )

    assert recovered.migrated is False
    assert recovered.acorn.kwargs["nsec"] == "nsec1oldservice"
    assert recovered.acorn.get_balance() == 21
    assert (tmp_path / "service-acorn.json").read_text(encoding="utf-8") == original_state
    assert FakeServiceAcorn.instances[-1].burn_calls == []


def test_service_acorn_migration_falls_back_when_replacement_fails(tmp_path) -> None:
    original_settings = service_settings(tmp_path)
    asyncio.run(
        start_service_acorn(
            original_settings,
            acorn_factory=FakeServiceAcorn,
            key_generator=lambda: ("old seed phrase", "nsec1oldservice"),
        )
    )
    original_state = (tmp_path / "service-acorn.json").read_text(encoding="utf-8")

    recovered = asyncio.run(
        start_service_acorn(
            replace(
                original_settings,
                service_acorn_migrate=True,
                service_acorn_home_mint="https://mint-new.example.com",
            ),
            acorn_factory=FakeServiceAcorn,
            key_generator=lambda: (_ for _ in ()).throw(
                RuntimeError("replacement key generation failed")
            ),
        )
    )

    assert recovered.migrated is False
    assert recovered.acorn.kwargs["nsec"] == "nsec1oldservice"
    assert (tmp_path / "service-acorn.json").read_text(encoding="utf-8") == original_state


def test_service_acorn_migration_falls_back_for_invalid_target(tmp_path) -> None:
    original_settings = service_settings(tmp_path)
    asyncio.run(
        start_service_acorn(
            original_settings,
            acorn_factory=FakeServiceAcorn,
            key_generator=lambda: ("old seed phrase", "nsec1oldservice"),
        )
    )

    recovered = asyncio.run(
        start_service_acorn(
            replace(
                original_settings,
                service_acorn_migrate=True,
                service_acorn_home_relay="https://not-a-relay.example.com",
            ),
            acorn_factory=FakeServiceAcorn,
            key_generator=lambda: (_ for _ in ()).throw(
                AssertionError("must not generate")
            ),
        )
    )

    assert recovered.migrated is False
    assert recovered.acorn.kwargs["nsec"] == "nsec1oldservice"
    assert recovered.acorn.kwargs["home_relay"] == "wss://relay.example.com"


def test_service_acorn_migration_falls_back_when_old_burn_fails(tmp_path) -> None:
    original_settings = service_settings(tmp_path)
    asyncio.run(
        start_service_acorn(
            original_settings,
            acorn_factory=FakeServiceAcorn,
            key_generator=lambda: ("old seed phrase", "nsec1oldservice"),
        )
    )
    original_state = (tmp_path / "service-acorn.json").read_text(encoding="utf-8")
    FakeServiceAcorn.fail_burn = True

    recovered = asyncio.run(
        start_service_acorn(
            replace(
                original_settings,
                service_acorn_migrate=True,
                service_acorn_home_relay="wss://spurline.example.com",
            ),
            acorn_factory=FakeServiceAcorn,
            key_generator=lambda: ("new seed phrase", "nsec1newservice"),
        )
    )

    assert recovered.migrated is False
    assert recovered.acorn.kwargs["nsec"] == "nsec1oldservice"
    assert (tmp_path / "service-acorn.json").read_text(encoding="utf-8") == original_state


def test_funded_service_acorn_sweeps_before_burn(tmp_path) -> None:
    settings = service_settings(
        tmp_path,
        service_acorn_shutdown_recipient="npub1recovery",
        service_acorn_shutdown_relay="wss://delivery.example.com",
    )
    FakeServiceAcorn.next_balance = 21
    runtime = asyncio.run(
        start_service_acorn(
            settings,
            acorn_factory=FakeServiceAcorn,
            key_generator=lambda: ("service seed phrase", TEST_SERVICE_NSEC),
        )
    )

    asyncio.run(stop_service_acorn(runtime, settings))

    assert runtime.acorn.burn_calls[0]["send_to"] == "npub1recovery"
    assert runtime.acorn.burn_calls[0]["send_relay"] == "wss://delivery.example.com"
    assert not runtime.state_path.exists()


def test_funded_service_acorn_without_sweep_recipient_keeps_recovery_file(
    tmp_path,
) -> None:
    settings = service_settings(tmp_path)
    FakeServiceAcorn.next_balance = 21
    runtime = asyncio.run(
        start_service_acorn(
            settings,
            acorn_factory=FakeServiceAcorn,
            key_generator=lambda: ("service seed phrase", TEST_SERVICE_NSEC),
        )
    )

    with pytest.raises(RuntimeError, match="still holds funds"):
        asyncio.run(stop_service_acorn(runtime, settings))

    assert runtime.state_path.exists()
    assert runtime.acorn.burn_calls == []


def test_failed_burn_keeps_recovery_file(tmp_path) -> None:
    settings = service_settings(tmp_path)
    FakeServiceAcorn.fail_burn = True
    runtime = asyncio.run(
        start_service_acorn(
            settings,
            acorn_factory=FakeServiceAcorn,
            key_generator=lambda: ("service seed phrase", TEST_SERVICE_NSEC),
        )
    )

    with pytest.raises(RuntimeError, match="burn failed"):
        asyncio.run(stop_service_acorn(runtime, settings))

    assert runtime.state_path.exists()
