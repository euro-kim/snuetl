from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

CONTAINER_RUNTIME = "container"
_DEFAULT_RUNTIME_DIR = Path("/run/snuetl")
_STATUS_MAX_AGE_SECONDS = 15.0


def is_container_runtime() -> bool:
    return os.environ.get("SNUETL_RUNTIME", "").strip().casefold() == CONTAINER_RUNTIME


def container_runtime_dir() -> Path:
    configured = os.environ.get("SNUETL_RUNTIME_DIR")
    return Path(configured).expanduser() if configured else _DEFAULT_RUNTIME_DIR


def container_status_path() -> Path:
    return container_runtime_dir() / "status.json"


def container_reload_path() -> Path:
    return container_runtime_dir() / "reload"


def request_container_reload() -> None:
    if not is_container_runtime():
        return
    directory = container_runtime_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    container_reload_path().touch()


def read_container_status() -> dict[str, Any] | None:
    if not is_container_runtime():
        return None
    try:
        data = json.loads(container_status_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    updated_at = data.get("updated_at")
    if not isinstance(updated_at, (int, float)):
        return None
    if time.time() - float(updated_at) > _STATUS_MAX_AGE_SECONDS:
        return None
    return data


def container_supervisor_is_active() -> bool:
    status = read_container_status()
    return bool(status and status.get("supervisor_active"))


def container_discord_is_active() -> bool:
    status = read_container_status()
    return bool(status and status.get("discord_active"))


def container_telegram_is_active() -> bool:
    status = read_container_status()
    return bool(status and status.get("telegram_active"))
