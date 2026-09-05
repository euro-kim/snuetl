from __future__ import annotations

import json
import os
import shutil
import stat
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    pass


def _expand(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


@dataclass(frozen=True, slots=True)
class DiscordSettings:
    enabled: bool = False
    application_id: int | None = None
    guild_id: int | None = None
    channel_id: int | None = None
    owner_ids: frozenset[int] = frozenset()

    @property
    def configured(self) -> bool:
        return bool(self.application_id and self.guild_id and self.channel_id and self.owner_ids)


@dataclass(frozen=True, slots=True)
class DirectoryRoute:
    route_id: str
    destination: Path
    course_id: str | None = None
    semester_code: str | None = None
    kind: str | None = None
    remote_folder: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Config:
    base_url: str
    download_dir: Path
    state_dir: Path
    browser_channel: str | None
    browser_executable_path: Path | None
    headless: bool
    timeout_seconds: float
    login_timeout_seconds: float
    retry_count: int
    excluded_course_ids: frozenset[str]
    setup_complete: bool
    discord: DiscordSettings = field(default_factory=DiscordSettings)
    directory_routes: tuple[DirectoryRoute, ...] = ()

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

    @property
    def credentials_path(self) -> Path:
        return self.state_dir / "credentials.json"

    @property
    def provenance_path(self) -> Path:
        return self.state_dir / "install-provenance.json"

    @property
    def discord_token_path(self) -> Path:
        return self.state_dir / "discord-token"


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
    discord = _get_table(data, "discord")
    directory = _get_table(data, "directory")

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

    executable_value = browser.get("executable_path", "")
    if executable_value in (None, ""):
        executable_path = None
    elif isinstance(executable_value, str):
        executable_path = _expand(executable_value)
        # Playwright does not accept both a branded channel and an explicit binary.
        channel = None
    else:
        raise ConfigError("browser.executable_path must be a filesystem path")

    headless_value = browser.get("headless", True)
    if not isinstance(headless_value, bool):
        raise ConfigError("browser.headless must be true or false")
    setup_value = general.get("setup_complete", False)
    if not isinstance(setup_value, bool):
        raise ConfigError("general.setup_complete must be true or false")

    discord_enabled = discord.get("enabled", False)
    if not isinstance(discord_enabled, bool):
        raise ConfigError("discord.enabled must be true or false")

    def optional_snowflake(name: str) -> int | None:
        value = discord.get(name)
        if value in (None, ""):
            return None
        if isinstance(value, bool):
            raise ConfigError(f"discord.{name} must be a Discord ID")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"discord.{name} must be a Discord ID") from exc
        if parsed <= 0:
            raise ConfigError(f"discord.{name} must be positive")
        return parsed

    owner_values = discord.get("owner_ids", [])
    if not isinstance(owner_values, list):
        raise ConfigError("discord.owner_ids must be a list of Discord user IDs")
    owner_ids: set[int] = set()
    for value in owner_values:
        if isinstance(value, bool):
            raise ConfigError("discord.owner_ids must contain Discord user IDs")
        try:
            owner_id = int(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError("discord.owner_ids must contain Discord user IDs") from exc
        if owner_id <= 0:
            raise ConfigError("discord.owner_ids must contain positive IDs")
        owner_ids.add(owner_id)

    route_values = directory.get("routes", [])
    if not isinstance(route_values, list):
        raise ConfigError("directory.routes must be an array of tables")
    routes: list[DirectoryRoute] = []
    route_ids: set[str] = set()
    for value in route_values:
        if not isinstance(value, dict):
            raise ConfigError("each directory route must be a TOML table")
        route_id = str(value.get("id", "")).strip()
        destination = str(value.get("destination", "")).strip()
        if not route_id or not destination:
            raise ConfigError("directory routes require id and destination")
        if route_id in route_ids:
            raise ConfigError(f"duplicate directory route id: {route_id}")
        route_ids.add(route_id)
        kind = str(value.get("kind", "")).strip() or None
        if kind not in {None, "files", "articles", "syllabus", "videos"}:
            raise ConfigError(f"invalid directory route kind: {kind}")
        remote_value = value.get("remote_folder", [])
        if not isinstance(remote_value, list) or not all(
            isinstance(item, str) and item for item in remote_value
        ):
            raise ConfigError("directory route remote_folder must be a list of path parts")
        route = DirectoryRoute(
            route_id=route_id,
            destination=_expand(destination),
            course_id=str(value.get("course_id", "")).strip() or None,
            semester_code=str(value.get("semester_code", "")).strip() or None,
            kind=kind,
            remote_folder=tuple(remote_value),
        )
        try:
            from .routing import validate_route

            route = validate_route(route)
        except ValueError as exc:
            raise ConfigError(f"invalid directory route {route_id!r}: {exc}") from exc
        routes.append(route)

    return Config(
        base_url=base_url,
        download_dir=_expand(general.get("download_dir", "~/Downloads/snuetl")),
        state_dir=_expand(general.get("state_dir", default_state_dir())),
        browser_channel=channel,
        browser_executable_path=executable_path,
        headless=headless_value,
        timeout_seconds=timeout,
        login_timeout_seconds=login_timeout,
        retry_count=retries,
        excluded_course_ids=frozenset(str(item) for item in excluded),
        setup_complete=setup_value,
        discord=DiscordSettings(
            enabled=discord_enabled,
            application_id=optional_snowflake("application_id"),
            guild_id=optional_snowflake("guild_id"),
            channel_id=optional_snowflake("channel_id"),
            owner_ids=frozenset(owner_ids),
        ),
        directory_routes=tuple(routes),
    )


def save_config(config: Config, path: Path | None = None) -> Path:
    config_path = path or default_config_path()
    ensure_private_directory(config_path.parent)

    def quoted(value: object) -> str:
        return json.dumps(str(value), ensure_ascii=False)

    channel = config.browser_channel or "bundled"
    executable_path = str(config.browser_executable_path or "")
    excluded = ", ".join(quoted(item) for item in sorted(config.excluded_course_ids))
    route_tables = ""
    for route in config.directory_routes:
        remote_folder = ", ".join(quoted(part) for part in route.remote_folder)
        route_tables += (
            "\n[[directory.routes]]\n"
            f"id = {quoted(route.route_id)}\n"
            f"destination = {quoted(route.destination)}\n"
            f"course_id = {quoted(route.course_id or '')}\n"
            f"semester_code = {quoted(route.semester_code or '')}\n"
            f"kind = {quoted(route.kind or '')}\n"
            f"remote_folder = [{remote_folder}]\n"
        )
    body = (
        "[general]\n"
        f"base_url = {quoted(config.base_url)}\n"
        f"download_dir = {quoted(config.download_dir)}\n"
        f"state_dir = {quoted(config.state_dir)}\n"
        f"setup_complete = {'true' if config.setup_complete else 'false'}\n\n"
        "[browser]\n"
        f"channel = {quoted(channel)}\n"
        f"executable_path = {quoted(executable_path)}\n"
        f"headless = {'true' if config.headless else 'false'}\n"
        f"login_timeout_seconds = {config.login_timeout_seconds:g}\n\n"
        "[sync]\n"
        f"timeout_seconds = {config.timeout_seconds:g}\n"
        f"retry_count = {config.retry_count}\n"
        f"excluded_course_ids = [{excluded}]\n"
        "\n[discord]\n"
        f"enabled = {'true' if config.discord.enabled else 'false'}\n"
        f"application_id = {quoted(config.discord.application_id or '')}\n"
        f"guild_id = {quoted(config.discord.guild_id or '')}\n"
        f"channel_id = {quoted(config.discord.channel_id or '')}\n"
        "owner_ids = ["
        + ", ".join(quoted(value) for value in sorted(config.discord.owner_ids))
        + "]\n"
        + route_tables
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
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        path.chmod(0o700)
