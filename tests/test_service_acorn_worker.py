from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.config import ServiceAcornSettings
import app.service_acorn_worker as worker_module


def worker_settings(tmp_path, **changes) -> ServiceAcornSettings:
    settings = ServiceAcornSettings(
        service_acorn_enabled=True,
        service_acorn_state_file=str(tmp_path / "service-acorn.json"),
        database_url=f"sqlite:///{tmp_path / 'worker.db'}",
    )
    return replace(settings, **changes)


def test_worker_owns_runtime_and_retains_recovery_on_routine_stop(
    tmp_path,
    monkeypatch,
) -> None:
    state_path = tmp_path / "service-acorn.json"
    state_path.write_text("recovery", encoding="utf-8")
    acorn = SimpleNamespace(pubkey_bech32="npub1service")
    runtime = SimpleNamespace(acorn=acorn, recovered=False, state_path=state_path)
    observed: list[tuple[object, object]] = []

    async def fake_start(settings):
        observed.append((worker_module.service_acorn_runtime, worker_module.service_acorn))
        return runtime

    async def scenario() -> None:
        stop_event = asyncio.Event()
        task = asyncio.create_task(
            worker_module.run_worker(
                worker_settings(tmp_path),
                stop_event=stop_event,
            )
        )
        for _ in range(100):
            if worker_module.service_acorn_runtime is runtime:
                break
            await asyncio.sleep(0.01)
        assert worker_module.service_acorn_runtime is runtime
        assert worker_module.service_acorn is acorn
        stop_event.set()
        await task

    monkeypatch.setattr(worker_module, "start_service_acorn", fake_start)
    asyncio.run(scenario())

    assert observed == [(None, None)]
    assert state_path.exists()
    assert worker_module.service_acorn_runtime is None
    assert worker_module.service_acorn is None


def test_retire_requires_existing_recovery_state(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="No service Acorn recovery state"):
        asyncio.run(worker_module.retire_worker(worker_settings(tmp_path)))


def test_retire_recovers_then_sweeps_and_burns(tmp_path, monkeypatch) -> None:
    state_path = tmp_path / "service-acorn.json"
    state_path.write_text("recovery", encoding="utf-8")
    runtime = SimpleNamespace(acorn=SimpleNamespace(), state_path=state_path)
    calls: list[tuple[str, object]] = []

    async def fake_start(settings):
        calls.append(("start", settings))
        return runtime

    async def fake_stop(received_runtime, settings):
        calls.append(("stop", received_runtime))
        state_path.unlink()
        return {"status": "OK"}

    monkeypatch.setattr(worker_module, "start_service_acorn", fake_start)
    monkeypatch.setattr(worker_module, "stop_service_acorn", fake_stop)
    settings = worker_settings(tmp_path)

    result = asyncio.run(worker_module.retire_worker(settings))

    assert result == {"status": "OK"}
    assert calls == [("start", settings), ("stop", runtime)]
    assert not state_path.exists()


def test_worker_requires_explicit_enablement(tmp_path) -> None:
    settings = worker_settings(tmp_path, service_acorn_enabled=False)
    with pytest.raises(RuntimeError, match="SAFEBOX_SERVICE_ACORN_ENABLED=true"):
        asyncio.run(worker_module.run_worker(settings, stop_event=asyncio.Event()))


def test_worker_settings_do_not_require_web_cookie_key(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SAFEBOX_COOKIE_KEY", raising=False)
    monkeypatch.setenv("SAFEBOX_SERVICE_ACORN_ENABLED", "true")

    settings = ServiceAcornSettings.from_env()

    assert settings.service_acorn_enabled is True
    assert settings.service_acorn_gift_wrap_retention_seconds == 7 * 24 * 60 * 60


def test_worker_gift_wrap_retention_can_be_disabled(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SAFEBOX_SERVICE_ACORN_ENABLED", "true")
    monkeypatch.setenv("SAFEBOX_SERVICE_ACORN_GIFT_WRAP_RETENTION_SECONDS", "0")

    settings = ServiceAcornSettings.from_env()

    assert settings.service_acorn_gift_wrap_retention_seconds is None


@pytest.mark.parametrize("seconds", [3599, 2_592_001])
def test_worker_rejects_out_of_range_gift_wrap_retention(tmp_path, seconds) -> None:
    with pytest.raises(ValueError, match="between 3600 and 2592000"):
        worker_settings(
            tmp_path,
            service_acorn_gift_wrap_retention_seconds=seconds,
        )
