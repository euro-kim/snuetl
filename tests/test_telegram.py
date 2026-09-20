import asyncio
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from snuetl.config import TelegramSettings, load_config, save_config
from snuetl.state import StateStore
from snuetl.telegram_api import (
    TelegramAPI,
    TelegramAPIError,
    load_telegram_token,
    save_telegram_token,
)
from snuetl.telegram_bot import TelegramBot
from snuetl.telegram_setup import _claim


def telegram_config(tmp_path: Path):
    return replace(
        load_config(tmp_path / "missing.toml"),
        state_dir=tmp_path / "state",
        download_dir=tmp_path / "downloads",
        setup_complete=True,
        telegram=TelegramSettings(enabled=True, owner_id=400, chat_id=400),
    )


class FakeAPI:
    def __init__(self):
        self.messages: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, message: str) -> None:
        self.messages.append((chat_id, message))


def update(
    update_id: int, *, user: int = 400, chat: int = 400, kind: str = "private", text: str = "/sync"
):
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat, "type": kind},
            "from": {"id": user},
            "text": text,
        },
    }


def test_config_and_token_are_private(tmp_path: Path) -> None:
    config = telegram_config(tmp_path)
    path = tmp_path / "config.toml"
    save_config(config, path)
    loaded = load_config(path)
    assert loaded.telegram == config.telegram
    token_path = save_telegram_token(loaded, "123456:secret")
    assert load_telegram_token(loaded) == "123456:secret"
    assert token_path.stat().st_mode & 0o777 == 0o600
    assert "123456:secret" not in path.read_text(encoding="utf-8")


def test_private_pairing_ignores_groups_and_other_codes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("snuetl.telegram_setup.secrets.token_urlsafe", lambda _: "pair-code")
    fake = FakeAPI()

    class PairingAPI:
        def __init__(self, token: str):
            assert token == "123456:secret"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get_me(self):
            return {"is_bot": True, "username": "snuetl_test_bot"}

        async def require_polling(self):
            return None

        async def get_updates(self, **_):
            return [
                update(1, kind="group", text="/start pair-code"),
                update(2, text="/start wrong"),
                update(3, user=987, chat=987, text="/start pair-code"),
            ]

        async def send_message(self, chat_id: int, message: str):
            await fake.send_message(chat_id, message)

    monkeypatch.setattr("snuetl.telegram_setup.TelegramAPI", PairingAPI)
    state = tmp_path / "state.db"
    with StateStore(state) as store:
        store.record_telegram_update(99999)
        store.mark_telegram_digest("2026-09-19")
    assert asyncio.run(_claim("123456:secret", state)) == (987, 987)
    with StateStore(state) as store:
        assert store.telegram_poll_offset() == 4
        assert store.telegram_digest_dates() == (None, None)
    assert fake.messages == [(987, "Telegram pairing confirmed. SNUETL is finishing setup.")]


def test_pairing_code_expires(tmp_path: Path, monkeypatch) -> None:
    times = iter([0.0, 601.0])
    monkeypatch.setattr(
        "snuetl.telegram_setup.time", SimpleNamespace(monotonic=lambda: next(times))
    )

    class IdleAPI:
        def __init__(self, _token):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get_me(self):
            return {"is_bot": True, "username": "test_bot"}

        async def require_polling(self):
            return None

    monkeypatch.setattr("snuetl.telegram_setup.TelegramAPI", IdleAPI)
    with pytest.raises(RuntimeError, match="expired"):
        asyncio.run(_claim("123456:secret", tmp_path / "state.db"))


def test_existing_discord_jobs_gain_gateway_on_migration(tmp_path: Path) -> None:
    database = tmp_path / "old.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE discord_jobs (
                job_id TEXT PRIMARY KEY, command TEXT NOT NULL, arguments_json TEXT NOT NULL,
                requester_id INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                started_at TEXT, finished_at TEXT, progress TEXT, channel_id INTEGER,
                message_id INTEGER, error TEXT
            )"""
        )
        connection.execute(
            "INSERT INTO discord_jobs(job_id,command,arguments_json,requester_id,status,created_at) VALUES ('old','sync','{}',1,'completed','2026-01-01')"
        )
    with StateStore(database) as store:
        old = store.get_discord_job("old")
        assert old is not None and old.gateway == "discord"


def test_poll_offset_resets_after_long_idle_period(tmp_path: Path) -> None:
    with StateStore(tmp_path / "state.db") as store:
        store.record_telegram_update(99999)
        assert store.telegram_poll_offset() == 100000
        old = (datetime.now(UTC) - timedelta(days=8)).isoformat()
        store.db.execute("UPDATE telegram_updates SET processed_at=?", (old,))
        store.db.commit()
        assert store.telegram_poll_offset() is None


def test_gateway_jobs_are_claimed_serially(tmp_path: Path) -> None:
    config = telegram_config(tmp_path)
    with StateStore(config.database_path) as store:
        store.create_discord_job("d1", "sync", {}, 100, channel_id=200)
        store.create_remote_job("telegram", "t1", "sync", {}, 400, channel_id=400)
        discord_job = store.next_discord_job()
        assert discord_job is not None and discord_job.gateway == "discord"
        assert store.next_remote_job("telegram") is None
        assert not store.request_remote_job_cancel("d1", "telegram")
        store.update_discord_job("d1", status="completed")
        telegram_job = store.next_remote_job("telegram")
        assert telegram_job is not None and telegram_job.gateway == "telegram"
        store.interrupt_remote_jobs("telegram")
        assert store.get_discord_job("d1").status == "completed"


def test_only_paired_user_can_queue_and_replay_is_idempotent(tmp_path: Path) -> None:
    config = telegram_config(tmp_path)
    api = FakeAPI()
    bot = TelegramBot(config, api)

    async def run():
        await bot.handle_update(update(10, user=401, chat=400))
        await bot.handle_update(update(10, kind="group"))
        await bot.handle_update(update(10))
        await bot.handle_update(update(10))

    asyncio.run(run())
    with StateStore(config.database_path) as store:
        jobs = store.list_remote_jobs("telegram")
    assert len(jobs) == 1
    assert jobs[0].requester_id == 400
    assert len(api.messages) == 2


def test_digest_uses_korea_time_and_persists_delivery(tmp_path: Path, monkeypatch) -> None:
    config = telegram_config(tmp_path)
    api = FakeAPI()
    monkeypatch.setattr("snuetl.telegram_bot.token_status", lambda _: {"ready": True})

    def fake_live_data(_config, command, **kwargs):
        if command == "upcoming":
            assert kwargs["start"].isoformat() == "2026-09-20"
            assert kwargs["end"].isoformat() == "2026-09-27"
            return [{"title": "Homework", "due_at": "2026-09-21T00:00:00Z"}]
        return []

    monkeypatch.setattr("snuetl.telegram_bot.live_data", fake_live_data)
    korea = ZoneInfo("Asia/Seoul")

    async def run():
        bot = TelegramBot(config, api)
        await bot.send_digest_if_due(datetime(2026, 9, 20, 8, 59, tzinfo=korea))
        assert api.messages == []
        await bot.send_digest_if_due(datetime(2026, 9, 20, 9, 0, tzinfo=korea))
        await TelegramBot(config, api).send_digest_if_due(
            datetime(2026, 9, 20, 12, 0, tzinfo=korea)
        )

    asyncio.run(run())
    assert len(api.messages) == 1
    assert "Homework" in api.messages[0][1]
    with StateStore(config.database_path) as store:
        assert store.telegram_digest_dates() == ("2026-09-20", None)


def test_digest_missing_api_sends_one_actionable_notice(tmp_path: Path, monkeypatch) -> None:
    config = telegram_config(tmp_path)
    api = FakeAPI()
    monkeypatch.setattr("snuetl.telegram_bot.token_status", lambda _: {"ready": False})
    moment = datetime(2026, 9, 20, 9, 0, tzinfo=ZoneInfo("Asia/Seoul"))

    async def run():
        await TelegramBot(config, api).send_digest_if_due(moment)
        await TelegramBot(config, api).send_digest_if_due(moment)

    asyncio.run(run())
    assert len(api.messages) == 1
    assert "snuetl api setup" in api.messages[0][1]


def test_transport_error_does_not_expose_bot_token() -> None:
    token = "123456:very-secret"

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"Could not connect to {request.url}", request=request)

    async def run():
        async with TelegramAPI(token) as api:
            await api._client.aclose()
            api._client = httpx.AsyncClient(transport=httpx.MockTransport(fail))
            with pytest.raises(TelegramAPIError) as error:
                await api.get_me()
            assert token not in str(error.value)

    asyncio.run(run())
