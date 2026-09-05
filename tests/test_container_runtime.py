from __future__ import annotations

import json
import signal
import stat
import time
from dataclasses import replace
from pathlib import Path

from snuetl import container_runtime, runtime, scheduler
from snuetl.config import DiscordSettings, load_config, save_config
from snuetl.discord_config import save_discord_token


def _container_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SNUETL_RUNTIME", "container")
    monkeypatch.setenv("SNUETL_RUNTIME_DIR", str(tmp_path / "run"))


def test_bootstrap_uses_fixed_internal_paths_and_preserves_existing_config(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    config_path = home / ".config" / "snuetl" / "config.toml"
    downloads = tmp_path / "data" / "downloads"
    videos = tmp_path / "data" / "videos"
    downloads.mkdir(parents=True, mode=0o755)
    downloads.chmod(0o755)
    videos.mkdir(parents=True, mode=0o755)
    videos.chmod(0o755)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(container_runtime, "_DOWNLOAD_DIR", downloads)
    monkeypatch.setattr(container_runtime, "_VIDEO_DIR", videos)

    assert container_runtime.bootstrap_container_config(config_path) == config_path
    configured = load_config(config_path)
    assert configured.download_dir == downloads
    assert configured.state_dir == home / ".local" / "state" / "snuetl"
    assert configured.headless is True
    assert configured.setup_complete is False
    assert configured.directory_routes[0].destination == videos
    assert configured.directory_routes[0].kind == "videos"
    assert stat.S_IMODE(configured.state_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(configured.download_dir.stat().st_mode) == 0o755
    assert stat.S_IMODE(videos.stat().st_mode) == 0o755

    save_config(replace(configured, download_dir=tmp_path / "custom"), config_path)
    container_runtime.bootstrap_container_config(config_path)
    assert load_config(config_path).download_dir == tmp_path / "custom"


def test_container_status_requires_a_fresh_supervisor_heartbeat(
    tmp_path: Path, monkeypatch
) -> None:
    _container_environment(monkeypatch, tmp_path)
    container_runtime._write_status(discord_active=True, discord_pid=42)

    assert runtime.container_supervisor_is_active() is True
    assert runtime.container_discord_is_active() is True

    status_path = runtime.container_status_path()
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    payload["updated_at"] = time.time() - 60
    status_path.write_text(json.dumps(payload), encoding="utf-8")
    assert runtime.container_supervisor_is_active() is False
    assert runtime.container_discord_is_active() is False


def test_scheduler_delegates_discord_lifecycle_to_container_supervisor(
    tmp_path: Path, monkeypatch
) -> None:
    _container_environment(monkeypatch, tmp_path)
    container_runtime._write_status(discord_active=True, discord_pid=42)

    assert scheduler.timer_is_enabled() is False
    assert scheduler.discord_service_is_enabled() is True
    assert scheduler.discord_service_is_active() is True
    assert scheduler.install_discord_service(tmp_path / "config.toml") is None
    assert runtime.container_reload_path().exists()
    assert scheduler.linger_status().enabled is True
    try:
        scheduler.install_user_timer(tmp_path / "config.toml")
    except RuntimeError as exc:
        assert "not enabled" in str(exc)
    else:
        raise AssertionError("Docker deployment unexpectedly installed a timer")


class _FakeDiscordProcess:
    pid = 321

    def __init__(self) -> None:
        self.return_code: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.return_code

    def terminate(self) -> None:
        self.terminated = True
        self.return_code = 0

    def wait(self, timeout: float | None = None) -> int:
        assert timeout is None or timeout > 0
        return self.return_code or 0

    def kill(self) -> None:
        self.return_code = -9


def test_supervisor_remains_healthy_and_idle_before_setup(
    tmp_path: Path, monkeypatch
) -> None:
    _container_environment(monkeypatch, tmp_path)
    config_path = tmp_path / "config.toml"
    save_config(load_config(tmp_path / "missing.toml"), config_path)
    monkeypatch.setattr(container_runtime, "bootstrap_container_config", lambda: config_path)

    handlers = {}
    statuses: list[tuple[bool, int | None]] = []
    monkeypatch.setattr(signal, "signal", lambda number, handler: handlers.setdefault(number, handler))
    monkeypatch.setattr(
        container_runtime.subprocess,
        "Popen",
        lambda _command: (_ for _ in ()).throw(AssertionError("started before setup")),
    )
    monkeypatch.setattr(
        container_runtime,
        "_write_status",
        lambda *, discord_active, discord_pid: statuses.append((discord_active, discord_pid)),
    )
    monkeypatch.setattr(
        container_runtime.time,
        "sleep",
        lambda _seconds: handlers[signal.SIGTERM](signal.SIGTERM, None),
    )

    assert container_runtime.run_supervisor() == 0
    assert statuses == [(False, None)]


def test_supervisor_starts_only_discord_and_stops_it_on_shutdown(
    tmp_path: Path, monkeypatch
) -> None:
    _container_environment(monkeypatch, tmp_path)
    config_path = tmp_path / "config" / "config.toml"
    config = replace(
        load_config(tmp_path / "missing.toml"),
        state_dir=tmp_path / "state",
        download_dir=tmp_path / "downloads",
        setup_complete=True,
        discord=DiscordSettings(True, 100, 200, 300, frozenset({400})),
    )
    save_config(config, config_path)
    save_discord_token(config, "secret.bot.token")
    monkeypatch.setattr(container_runtime, "bootstrap_container_config", lambda: config_path)

    handlers = {}
    monkeypatch.setattr(signal, "signal", lambda number, handler: handlers.setdefault(number, handler))
    process = _FakeDiscordProcess()
    commands: list[list[str]] = []

    def start(command: list[str]):
        commands.append(command)
        return process

    monkeypatch.setattr(container_runtime.subprocess, "Popen", start)
    monkeypatch.setattr(
        container_runtime.time,
        "sleep",
        lambda _seconds: handlers[signal.SIGTERM](signal.SIGTERM, None),
    )

    assert container_runtime.run_supervisor() == 0
    assert len(commands) == 1
    assert commands[0][-3:] == ["discord", "run", "--no-input"]
    assert "sync" not in commands[0]
    assert process.terminated is True
    assert not runtime.container_status_path().exists()


def test_healthcheck_fails_without_supervisor(tmp_path: Path, monkeypatch) -> None:
    _container_environment(monkeypatch, tmp_path)
    assert container_runtime.main(["--healthcheck"]) == 1
