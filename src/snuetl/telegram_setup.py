from __future__ import annotations

import asyncio
import getpass
import secrets
import time
from dataclasses import asdict, replace
from pathlib import Path

from rich.prompt import Prompt

from .config import Config, TelegramSettings, default_config_path, save_config
from .runtime import is_container_runtime
from .scheduler import (
    install_telegram_service,
    linger_status,
    restart_telegram_service,
    set_telegram_service_enabled,
    telegram_service_is_active,
    telegram_service_is_enabled,
)
from .state import StateStore
from .telegram_api import TelegramAPI, load_telegram_token, save_telegram_token
from .ui import console


def telegram_setup_guide() -> dict[str, object]:
    return {
        "botfather": "https://t.me/BotFather",
        "steps": [
            "In Telegram, open @BotFather and send /newbot.",
            "Create a dedicated bot and copy its token.",
            "Run snuetl telegram setup and paste the token into its hidden prompt.",
            "Open the pairing link shown by snuetl and press Start within ten minutes.",
        ],
        "security": "Only the paired private chat and Telegram user can control this SNU account.",
    }


def telegram_status(config: Config) -> dict[str, object]:
    return {
        "enabled": config.telegram.enabled,
        "configured": config.telegram.configured,
        "owner_id": config.telegram.owner_id,
        "chat_id": config.telegram.chat_id,
        "alerts_enabled": config.telegram.alerts_enabled,
        "digest_time": "09:00 Asia/Seoul",
        "token_present": load_telegram_token(config) is not None,
        "service_enabled": telegram_service_is_enabled(),
        "service_active": telegram_service_is_active(),
        "service_manager": "docker_compose" if is_container_runtime() else "systemd",
        "linger": asdict(linger_status()),
    }


async def _claim(token: str, state_path: Path) -> tuple[int, int]:
    code = secrets.token_urlsafe(12)
    async with TelegramAPI(token) as api:
        bot = await api.get_me()
        await api.require_polling()
        username = str(bot.get("username") or "")
        console.print("Open the following link in Telegram and press Start within 10 minutes:")
        console.print(f"https://t.me/{username}?start={code}", markup=False, highlight=False)
        deadline = time.monotonic() + 600
        offset: int | None = None
        while time.monotonic() < deadline:
            remaining = max(1, int(deadline - time.monotonic()))
            updates = await api.get_updates(offset=offset, timeout=min(25, remaining))
            for update in updates:
                update_id = update.get("update_id")
                if not isinstance(update_id, int):
                    continue
                offset = update_id + 1
                message = update.get("message")
                if not isinstance(message, dict):
                    continue
                chat = message.get("chat")
                sender = message.get("from")
                if not isinstance(chat, dict) or not isinstance(sender, dict):
                    continue
                if chat.get("type") != "private" or sender.get("is_bot"):
                    continue
                text = str(message.get("text") or "").strip()
                if not text.startswith("/start "):
                    continue
                supplied = text.split(maxsplit=1)[1]
                if not secrets.compare_digest(supplied, code):
                    continue
                user_id, chat_id = sender.get("id"), chat.get("id")
                if not isinstance(user_id, int) or not isinstance(chat_id, int):
                    continue
                with StateStore(state_path) as store:
                    store.reset_telegram_state()
                    store.record_telegram_update(update_id)
                await api.send_message(
                    chat_id, "Telegram pairing confirmed. SNUETL is finishing setup."
                )
                return user_id, chat_id
    raise RuntimeError(
        "Telegram pairing expired after ten minutes; run snuetl telegram setup again"
    )


def configure_telegram(
    config: Config, config_path: Path | None
) -> tuple[Config, dict[str, object]]:
    if not config.setup_complete:
        raise RuntimeError("Complete 'snuetl setup' before configuring Telegram")
    if config.telegram.enabled and config.telegram.configured:
        raise RuntimeError(
            "Disable the Telegram daemon before pairing again: snuetl telegram disable"
        )
    guide = telegram_setup_guide()
    console.print("Create a dedicated bot with @BotFather: https://t.me/BotFather")
    existing = load_telegram_token(config)
    if (
        existing
        and Prompt.ask("Reuse the saved Telegram bot token?", choices=["y", "n"], default="y")
        == "y"
    ):
        token = existing
    else:
        token = getpass.getpass("Paste the Telegram bot token (input hidden): ").strip()
    if not token or any(character.isspace() for character in token):
        raise ValueError("Telegram bot token is empty or malformed")
    owner_id, chat_id = asyncio.run(_claim(token, config.database_path))
    updated = replace(
        config,
        telegram=TelegramSettings(
            enabled=True,
            owner_id=owner_id,
            chat_id=chat_id,
            alerts_enabled=True,
        ),
    )
    path = config_path or default_config_path()
    save_telegram_token(updated, token)
    save_config(updated, path)
    service_path = install_telegram_service(
        path,
        state_dir=updated.state_dir,
        download_dir=updated.download_dir,
        write_dirs=tuple(route.destination for route in updated.directory_routes),
    )
    console.print(f"Paired Telegram user {owner_id} in private chat {chat_id}.")
    return updated, {
        **telegram_status(updated),
        "service_path": str(service_path) if service_path else "Docker Compose supervisor",
        "botfather": guide["botfather"],
    }


def toggle_telegram(config: Config, enabled: bool, config_path: Path | None) -> Config:
    if not config.telegram.configured or load_telegram_token(config) is None:
        raise RuntimeError("Telegram is not paired; run snuetl telegram setup")
    updated = replace(config, telegram=replace(config.telegram, enabled=enabled))
    path = config_path or default_config_path()
    save_config(updated, path)
    if enabled:
        install_telegram_service(
            path,
            state_dir=updated.state_dir,
            download_dir=updated.download_dir,
            write_dirs=tuple(route.destination for route in updated.directory_routes),
        )
    else:
        set_telegram_service_enabled(False)
    return updated


def toggle_alerts(config: Config, enabled: bool, config_path: Path | None) -> Config:
    if not config.telegram.configured:
        raise RuntimeError("Telegram is not paired; run snuetl telegram setup")
    updated = replace(config, telegram=replace(config.telegram, alerts_enabled=enabled))
    save_config(updated, config_path)
    if updated.telegram.enabled and telegram_service_is_enabled():
        restart_telegram_service()
    return updated
