from pathlib import Path

from snuetl import scheduler


def test_installs_timer_with_current_python_and_config(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(scheduler.shutil, "which", lambda name: "/usr/bin/systemctl")
    monkeypatch.setattr(scheduler.sys, "executable", "/opt/snuetl env/bin/python")

    def fake_run(command, **kwargs):
        calls.append(command)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    config = tmp_path / "config.toml"
    path = scheduler.install_user_timer(config)
    service = (path.parent / "snuetl.service").read_text(encoding="utf-8")
    timer = path.read_text(encoding="utf-8")
    assert 'ExecStart="/opt/snuetl env/bin/python" -m snuetl' in service
    assert f'--config "{config}" sync' in service
    assert "OnUnitActiveSec=15min" in timer
    assert calls[-1][-2:] == ["--now", "snuetl.timer"]


def test_installs_hardened_discord_daemon_with_configured_write_paths(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(scheduler.shutil, "which", lambda name: "/usr/bin/systemctl")
    monkeypatch.setattr(scheduler.sys, "executable", "/opt/snuetl/bin/python")

    def fake_run(command, **kwargs):
        calls.append(command)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    config_path = tmp_path / "config" / "config.toml"
    service_path = scheduler.install_discord_service(
        config_path,
        state_dir=tmp_path / "state",
        download_dir=tmp_path / "downloads",
        write_dirs=(tmp_path / "routed",),
    )
    service = service_path.read_text(encoding="utf-8")
    assert "discord run" in service
    assert "Restart=on-failure" in service
    assert "NoNewPrivileges=true" in service
    assert str(tmp_path / "state") in service
    assert str(tmp_path / "downloads") in service
    assert str(tmp_path / "routed") in service
    assert calls[-1][-2:] == ["--now", "snuetl-discord.service"]
