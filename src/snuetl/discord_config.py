from __future__ import annotations

import os
from pathlib import Path

from .config import Config, ensure_private_directory


def save_discord_token(config: Config, token: str) -> Path:
    value = token.strip()
    if not value or any(character.isspace() for character in value):
        raise ValueError("Discord bot token is empty or malformed")
    ensure_private_directory(config.state_dir)
    path = config.discord_token_path
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)
    return path


def load_discord_token(config: Config) -> str | None:
    try:
        value = config.discord_token_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def delete_discord_token(config: Config) -> None:
    config.discord_token_path.unlink(missing_ok=True)
