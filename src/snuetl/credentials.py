from __future__ import annotations

import json
import os
from dataclasses import dataclass

from .config import Config, ensure_private_directory


@dataclass(frozen=True, slots=True)
class SavedCredentials:
    username: str
    password: str
    trust_browser: bool = True


def load_credentials(config: Config) -> SavedCredentials | None:
    path = config.credentials_path
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        username = value["username"]
        password = value["password"]
        trust_browser = value.get("trust_browser", True)
        if not isinstance(username, str) or not username:
            return None
        if not isinstance(password, str) or not password:
            return None
        if not isinstance(trust_browser, bool):
            return None
        return SavedCredentials(username, password, trust_browser)
    except (OSError, ValueError, TypeError, KeyError):
        return None


def save_credentials(config: Config, credentials: SavedCredentials) -> None:
    ensure_private_directory(config.state_dir)
    temporary = config.credentials_path.with_name(f".{config.credentials_path.name}.tmp")
    temporary.write_text(
        json.dumps(
            {
                "username": credentials.username,
                "password": credentials.password,
                "trust_browser": credentials.trust_browser,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    os.replace(temporary, config.credentials_path)


def delete_credentials(config: Config) -> None:
    config.credentials_path.unlink(missing_ok=True)
