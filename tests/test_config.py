from dataclasses import replace
from pathlib import Path

import pytest

from snuetl.config import (
    ConfigError,
    DirectoryRoute,
    ensure_private_directory,
    load_config,
    migrate_legacy_layout,
    save_config,
)


def test_private_directory_does_not_chmod_an_already_private_path(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "private"
    path.mkdir(mode=0o700)
    chmod_calls: list[int] = []
    monkeypatch.setattr(Path, "chmod", lambda _path, mode: chmod_calls.append(mode))

    ensure_private_directory(path)

    assert chmod_calls == []


def test_private_directory_tightens_permissions_only_when_needed(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "shared"
    path.mkdir(mode=0o755)
    chmod_calls: list[int] = []
    original_chmod = Path.chmod

    def record_chmod(target: Path, mode: int) -> None:
        chmod_calls.append(mode)
        original_chmod(target, mode)

    monkeypatch.setattr(Path, "chmod", record_chmod)

    ensure_private_directory(path)

    assert chmod_calls == [0o700]
    assert path.stat().st_mode & 0o777 == 0o700


def test_defaults_without_file(tmp_path: Path) -> None:
    config = load_config(tmp_path / "missing.toml")
    assert config.base_url == "https://etl.snu.ac.kr/login"
    assert config.browser_channel is None
    assert config.retry_count == 3
    assert config.setup_complete is False


def test_loads_custom_values(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[general]
base_url = "https://etl.snu.ac.kr/login/"
download_dir = "~/etl-files"
state_dir = "~/etl-state"
[browser]
channel = "chrome"
executable_path = "/usr/bin/chromium"
headless = false
login_timeout_seconds = 120
[sync]
timeout_seconds = 12
retry_count = 2
excluded_course_ids = [123, "abc"]
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.base_url == "https://etl.snu.ac.kr/login"
    assert config.browser_channel is None
    assert config.browser_executable_path == Path("/usr/bin/chromium")
    assert config.headless is False
    assert config.excluded_course_ids == frozenset({"123", "abc"})


def test_rejects_non_https_url(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[general]\nbase_url = "http://etl.snu.ac.kr"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_rejects_string_boolean(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[browser]\nheadless = "false"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(path)


def test_save_config_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "private" / "config.toml"
    original = load_config(tmp_path / "missing.toml")
    saved = replace(
        original,
        download_dir=tmp_path / "강의 자료",
        browser_executable_path=Path("/usr/bin/chromium"),
        setup_complete=True,
    )
    save_config(saved, path)
    loaded = load_config(path)
    assert loaded.download_dir == tmp_path / "강의 자료"
    assert loaded.browser_executable_path == Path("/usr/bin/chromium")
    assert loaded.setup_complete is True
    assert path.stat().st_mode & 0o777 == 0o600


def test_directory_routes_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    original = load_config(tmp_path / "missing.toml")
    route = DirectoryRoute(
        route_id="week-two",
        destination=tmp_path / "classes" / "week-2",
        course_id="101",
        semester_code="2026-2",
        kind="files",
        remote_folder=("자료", "Week 2"),
    )
    save_config(replace(original, directory_routes=(route,)), path)

    assert load_config(path).directory_routes == (route,)


def test_rejects_unconstrained_directory_route(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[[directory.routes]]\nid = "bad"\ndestination = "/tmp/target"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="must constrain"):
        load_config(path)


def test_migrates_legacy_config_and_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    old_path = tmp_path / "config" / "snu-etl-sync" / "config.toml"
    old_state = tmp_path / "state" / "snu-etl-sync"
    old_state.mkdir(parents=True)
    (old_state / "trusted-cookie").write_text("secret", encoding="utf-8")
    old = replace(
        load_config(tmp_path / "missing.toml"),
        state_dir=old_state,
        setup_complete=True,
    )
    save_config(old, old_path)

    assert migrate_legacy_layout() is True
    migrated = load_config(tmp_path / "config" / "snuetl" / "config.toml")
    assert migrated.state_dir == tmp_path / "state" / "snuetl"
    assert (migrated.state_dir / "trusted-cookie").read_text(encoding="utf-8") == "secret"
