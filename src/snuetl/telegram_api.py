from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx

from .config import Config, ensure_private_directory


class TelegramAPIError(RuntimeError):
    pass


def save_telegram_token(config: Config, token: str) -> Path:
    value = token.strip()
    if not value or any(character.isspace() for character in value):
        raise ValueError("Telegram bot token is empty or malformed")
    ensure_private_directory(config.state_dir)
    path = config.telegram_token_path
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)
    return path


def load_telegram_token(config: Config) -> str | None:
    try:
        value = config.telegram_token_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


class TelegramAPI:
    def __init__(self, token: str):
        # httpx logs request URLs at INFO; Telegram embeds the credential in them.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        self._token = token
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=10.0))

    async def __aenter__(self) -> TelegramAPI:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    async def call(self, method: str, **parameters: object) -> Any:
        # Telegram requires the token in the URL. Do not expose httpx exceptions:
        # their request URL would otherwise include that token.
        try:
            response = await self._client.post(
                f"https://api.telegram.org/bot{self._token}/{method}", json=parameters
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise TelegramAPIError(f"Telegram {method} request failed") from None
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            description = (
                str(payload.get("description", "request rejected"))
                if isinstance(payload, dict)
                else "invalid response"
            )
            description = description.replace(self._token, "[REDACTED]")
            raise TelegramAPIError(f"Telegram {method}: {description[:300]}")
        return payload.get("result")

    async def get_me(self) -> dict[str, Any]:
        result = await self.call("getMe")
        if not isinstance(result, dict) or not result.get("is_bot"):
            raise TelegramAPIError("Telegram token does not belong to a bot")
        return result

    async def require_polling(self) -> None:
        result = await self.call("getWebhookInfo")
        if isinstance(result, dict) and result.get("url"):
            raise TelegramAPIError(
                "This bot already uses a webhook. Use a dedicated BotFather bot or remove its webhook before Telegram setup."
            )

    async def get_updates(
        self, *, offset: int | None = None, timeout: int = 25
    ) -> list[dict[str, Any]]:
        parameters: dict[str, object] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            parameters["offset"] = offset
        result = await self.call("getUpdates", **parameters)
        if not isinstance(result, list):
            raise TelegramAPIError("Telegram getUpdates returned an invalid response")
        return [item for item in result if isinstance(item, dict)]

    async def send_message(self, chat_id: int, text: str) -> None:
        for chunk in split_message(text):
            await self.call(
                "sendMessage", chat_id=chat_id, text=chunk, disable_web_page_preview=True
            )

    async def set_commands(self, chat_id: int) -> None:
        commands = [
            {"command": command, "description": description}
            for command, description in (
                ("status", "Show SNUETL status"),
                ("upcoming", "Show work due soon"),
                ("missing", "Show missing submissions"),
                ("activity", "Show new course activity"),
                ("announcements", "Show recent announcements"),
                ("modules", "Show module progress"),
                ("feedback", "Show recent grading feedback"),
                ("dashboard", "Show course workload and grades"),
                ("sync", "Download changed course files"),
                ("jobs", "Show recent jobs"),
                ("cancel", "Cancel a job by ID"),
                ("help", "Show commands"),
            )
        ]
        await self.call(
            "setMyCommands",
            commands=commands,
            scope={"type": "chat", "chat_id": chat_id},
        )


def split_message(value: str, limit: int = 3900) -> list[str]:
    if not value:
        return [" "]
    parts: list[str] = []
    remaining = value
    while len(remaining) > limit:
        split = remaining.rfind("\n", 0, limit + 1)
        if split <= 0:
            split = limit
        parts.append(remaining[:split])
        remaining = remaining[split:].lstrip("\n")
    if remaining:
        parts.append(remaining)
    return parts
