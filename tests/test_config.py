from dataclasses import replace
from pathlib import Path

import pytest

from snuetl.config import ConfigError, load_config, migrate_legacy_layout, save_config


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
    assert config.browser_channel == "chrome"
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
    saved = replace(original, download_dir=tmp_path / "강의 자료", setup_complete=True)
    save_config(saved, path)
    loaded = load_config(path)
    assert loaded.download_dir == tmp_path / "강의 자료"
    assert loaded.setup_complete is True
    assert path.stat().st_mode & 0o777 == 0o600


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
