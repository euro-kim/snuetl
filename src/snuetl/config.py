from __future__ import annotations

import json
import os
import shutil
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    pass


def _expand(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


@dataclass(frozen=True, slots=True)
class Config:
    base_url: str
    download_dir: Path
    state_dir: Path
    browser_channel: str | None
    headless: bool
    timeout_seconds: float
    login_timeout_seconds: float
    retry_count: int
    excluded_course_ids: frozenset[str]
    setup_complete: bool

    @property
    def profile_dir(self) -> Path:
        return self.state_dir / "browser-profile"

    @property
    def database_path(self) -> Path:
        return self.state_dir / "state.db"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / "profile.lock"

    @property
    def auth_state_path(self) -> Path:
        return self.state_dir / "auth-state.json"

    @property
    def session_metadata_path(self) -> Path:
        return self.state_dir / "session.json"


def default_config_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return root / "snuetl" / "config.toml"


def legacy_config_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return root / "snu-etl-sync" / "config.toml"


def default_state_dir() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    root = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return root / "snuetl"


def legacy_state_dir() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME")
    root = Path(state_home).expanduser() if state_home else Path.home() / ".local" / "state"
    return root / "snu-etl-sync"


def _get_table(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a TOML table")
    return value


def load_config(path: Path | None = None) -> Config:
    if path is None:
        migrate_legacy_layout()
    config_path = path or default_config_path()
    data: dict[str, Any] = {}
    if config_path.exists():
        try:
            with config_path.open("rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"cannot read configuration: {exc}") from exc

    general = _get_table(data, "general")
    browser = _get_table(data, "browser")
    sync = _get_table(data, "sync")

    base_url = str(general.get("base_url", "https://etl.snu.ac.kr/login")).rstrip("/")
    if not base_url.startswith("https://"):
        raise ConfigError("general.base_url must use HTTPS")

    timeout = float(sync.get("timeout_seconds", 45))
    login_timeout = float(browser.get("login_timeout_seconds", 600))
    retries = int(sync.get("retry_count", 3))
    if timeout <= 0 or login_timeout <= 0:
        raise ConfigError("timeouts must be positive")
    if not 0 <= retries <= 10:
        raise ConfigError("sync.retry_count must be between 0 and 10")

    excluded = sync.get("excluded_course_ids", [])
    if not isinstance(excluded, list) or not all(isinstance(item, (str, int)) for item in excluded):
        raise ConfigError("sync.excluded_course_ids must be a list of IDs")

    channel_value = browser.get("channel", "bundled")
    if channel_value in (None, "", "bundled"):
        channel = None
    elif isinstance(channel_value, str):
        channel = channel_value
    else:
        raise ConfigError("browser.channel must be a string or 'bundled'")

    headless_value = browser.get("headless", True)
    if not isinstance(headless_value, bool):
        raise ConfigError("browser.headless must be true or false")
    setup_value = general.get("setup_complete", False)
    if not isinstance(setup_value, bool):
        raise ConfigError("general.setup_complete must be true or false")

    return Config(
        base_url=base_url,
        download_dir=_expand(general.get("download_dir", "~/Downloads/snuetl")),
        state_dir=_expand(general.get("state_dir", default_state_dir())),
        browser_channel=channel,
        headless=headless_value,
        timeout_seconds=timeout,
        login_timeout_seconds=login_timeout,
        retry_count=retries,
        excluded_course_ids=frozenset(str(item) for item in excluded),
        setup_complete=setup_value,
    )


def save_config(config: Config, path: Path | None = None) -> Path:
    config_path = path or default_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    config_path.parent.chmod(0o700)

    def quoted(value: object) -> str:
        return json.dumps(str(value), ensure_ascii=False)

    channel = config.browser_channel or "bundled"
    excluded = ", ".join(quoted(item) for item in sorted(config.excluded_course_ids))
    body = (
        "[general]\n"
        f"base_url = {quoted(config.base_url)}\n"
        f"download_dir = {quoted(config.download_dir)}\n"
        f"state_dir = {quoted(config.state_dir)}\n"
        f"setup_complete = {'true' if config.setup_complete else 'false'}\n\n"
        "[browser]\n"
        f"channel = {quoted(channel)}\n"
        f"headless = {'true' if config.headless else 'false'}\n"
        f"login_timeout_seconds = {config.login_timeout_seconds:g}\n\n"
        "[sync]\n"
        f"timeout_seconds = {config.timeout_seconds:g}\n"
        f"retry_count = {config.retry_count}\n"
        f"excluded_course_ids = [{excluded}]\n"
    )
    temporary = config_path.with_name(f".{config_path.name}.tmp")
    temporary.write_text(body, encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, config_path)
    return config_path


def migrate_legacy_layout() -> bool:
    """Move the pre-rename state into snuetl on first use."""
    new_config_path = default_config_path()
    old_config_path = legacy_config_path()
    if new_config_path.exists() or not old_config_path.exists():
        return False

    old_config = load_config(old_config_path)
    old_default_state = legacy_state_dir()
    new_state = default_state_dir()
    if old_config.state_dir == old_default_state:
        if old_default_state.exists() and not new_state.exists():
            new_state.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_default_state), str(new_state))
        old_config = replace(old_config, state_dir=new_state)
    save_config(old_config, new_config_path)
    return True


def ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
