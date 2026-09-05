import asyncio
import threading
from dataclasses import replace
from pathlib import Path

import discord
import pytest

from snuetl.config import DiscordSettings, load_config, save_config
from snuetl.credentials import SavedCredentials, save_credentials
from snuetl.discord_access import authorize_discord_interaction
from snuetl.discord_bot import VerificationBroker, _send_followup_embed
from snuetl.discord_config import load_discord_token, save_discord_token
from snuetl.discord_jobs import DiscordJobQueue
from snuetl.discord_setup import (
    change_discord_owner,
    discord_setup_guide,
    invite_url,
)
from snuetl.discord_ui import (
    merge_video_page_selection,
    video_page,
    video_page_count,
)
from snuetl.scheduler import LingerStatus
from snuetl.state import StateStore


def discord_config(tmp_path: Path):
    return replace(
        load_config(tmp_path / "missing.toml"),
        state_dir=tmp_path / "state",
        download_dir=tmp_path / "downloads",
        setup_complete=True,
        discord=DiscordSettings(
            enabled=True,
            application_id=100,
            guild_id=200,
            channel_id=300,
            owner_ids=frozenset({400}),
        ),
    )


def test_discord_config_and_token_round_trip(tmp_path: Path) -> None:
    config = discord_config(tmp_path)
    path = tmp_path / "config.toml"
    save_config(config, path)
    loaded = load_config(path)
    assert loaded.discord == config.discord

    token_path = save_discord_token(loaded, "secret.bot.token")
    assert load_discord_token(loaded) == "secret.bot.token"
    assert token_path.stat().st_mode & 0o777 == 0o600
    assert "secret.bot.token" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("guild", "channel", "owner", "allowed"),
    [
        (200, 300, 400, True),
        (None, 300, 400, False),
        (201, 300, 400, False),
        (200, 301, 400, False),
        (200, 300, 401, False),
    ],
)
def test_discord_access_is_bound_to_one_scope(guild, channel, owner, allowed) -> None:
    settings = DiscordSettings(True, 100, 200, 300, frozenset({400}))
    decision = authorize_discord_interaction(
        settings, guild_id=guild, channel_id=channel, user_id=owner
    )
    assert decision.allowed is allowed


def test_discord_job_lifecycle_and_restart_policy(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    with StateStore(database) as store:
        first = store.create_discord_job("a", "sync", {}, 400, channel_id=300)
        assert first.status == "queued"
        running = store.next_discord_job()
        assert running is not None and running.status == "running"
        assert store.request_discord_job_cancel("a")
        assert store.get_discord_job("a").status == "cancel_requested"
        store.interrupt_discord_jobs()
        assert store.get_discord_job("a").status == "interrupted"


def test_discord_queue_caps_waiting_jobs_and_rejects_secrets(tmp_path: Path) -> None:
    with StateStore(tmp_path / "state.db") as store:
        with pytest.raises(ValueError, match="secrets"):
            store.create_discord_job("secret", "login", {"password": "no"}, 1)
        for number in range(10):
            store.create_discord_job(str(number), "sync", {}, 1)
        with pytest.raises(ValueError, match="full"):
            store.create_discord_job("overflow", "sync", {}, 1)


def test_discord_job_queue_serializes_work(tmp_path: Path, monkeypatch) -> None:
    config = discord_config(tmp_path)
    calls: list[str] = []

    def execute(_config, command, _arguments, *, progress, login_callbacks):
        calls.append(f"start:{command}")
        progress(command)
        calls.append(f"finish:{command}")
        return {"command": command}

    monkeypatch.setattr("snuetl.discord_jobs.execute_job", execute)

    async def scenario() -> None:
        finished = asyncio.Event()
        results = []

        async def on_progress(_job, _message):
            return None

        async def on_finished(job, result):
            results.append((job.status, result))
            if len(results) == 2:
                finished.set()

        queue = DiscordJobQueue(config, on_progress=on_progress, on_finished=on_finished)
        queue.start()
        queue.enqueue("sync", {}, 400, 300)
        queue.enqueue("refresh", {}, 400, 300)
        await asyncio.wait_for(finished.wait(), 3)
        await queue.stop()
        assert [status for status, _ in results] == ["completed", "completed"]

    asyncio.run(scenario())
    assert calls == ["start:sync", "finish:sync", "start:refresh", "finish:refresh"]


def test_verification_broker_bridges_threads_without_retaining_code() -> None:
    broker = VerificationBroker(1)
    received: list[str] = []
    worker = threading.Thread(target=lambda: received.append(broker.provide()))
    worker.start()
    assert broker.submit("123456")
    worker.join(timeout=2)
    assert received == ["123456"]
    broker.close()
    assert broker.submit("999999") is False


def test_owner_management_never_removes_last_owner(tmp_path: Path) -> None:
    config = discord_config(tmp_path)
    updated = change_discord_owner(config, 401, add=True)
    assert updated.discord.owner_ids == frozenset({400, 401})
    updated = change_discord_owner(updated, 400, add=False)
    assert updated.discord.owner_ids == frozenset({401})
    with pytest.raises(ValueError, match="final"):
        change_discord_owner(updated, 401, add=False)


def test_noninteractive_setup_validates_and_persists_without_token_in_config(
    tmp_path: Path, monkeypatch
) -> None:
    from snuetl import discord_setup

    config = replace(discord_config(tmp_path), discord=DiscordSettings())
    save_credentials(config, SavedCredentials("student", "password"))
    token_file = tmp_path / "bot-token"
    token_file.write_text("secret.bot.token", encoding="utf-8")
    token_file.chmod(0o600)
    config_path = tmp_path / "config.toml"
    validated: list[tuple[str, int]] = []

    async def validate(token: str, application_id: int, guild_id: int, channel_id: int) -> None:
        validated.append((token, application_id))

    monkeypatch.setattr(discord_setup, "_validate_binding", validate)
    monkeypatch.setattr(
        discord_setup,
        "install_discord_service",
        lambda path, **kwargs: tmp_path / "snuetl-discord.service",
    )
    monkeypatch.setattr(discord_setup, "discord_service_is_enabled", lambda: True)
    monkeypatch.setattr(discord_setup, "discord_service_is_active", lambda: True)
    monkeypatch.setattr(
        discord_setup,
        "linger_status",
        lambda: LingerStatus(True, "enabled"),
    )

    configured, data = discord_setup.configure_discord(
        config,
        config_path=config_path,
        token_file=token_file,
        application_id=100,
        guild_id=200,
        channel_id=300,
        owner_ids=(400,),
        confirmed=True,
    )

    assert validated == [("secret.bot.token", 100)]
    assert configured.discord.configured
    assert data["service_enabled"] is True
    assert load_discord_token(configured) == "secret.bot.token"
    assert "secret.bot.token" not in config_path.read_text(encoding="utf-8")


def test_noninteractive_setup_rejects_group_readable_token_file(
    tmp_path: Path,
) -> None:
    from snuetl.discord_setup import configure_discord

    config = replace(discord_config(tmp_path), discord=DiscordSettings())
    save_credentials(config, SavedCredentials("student", "password"))
    token_file = tmp_path / "bot-token"
    token_file.write_text("secret.bot.token", encoding="utf-8")
    token_file.chmod(0o640)
    with pytest.raises(RuntimeError, match="owner-only"):
        configure_discord(
            config,
            config_path=tmp_path / "config.toml",
            token_file=token_file,
            application_id=100,
            guild_id=200,
            channel_id=300,
            owner_ids=(400,),
            confirmed=True,
        )


def test_invite_uses_only_required_bot_scope_and_permissions() -> None:
    url = invite_url(123)
    assert "client_id=123" in url
    assert "bot+applications.commands" in url
    assert "integration_type=0" in url
    assert "permissions=84992" in url


def test_setup_guide_is_precise_and_least_privilege() -> None:
    guide = discord_setup_guide()
    installation = guide["installation"]
    assert guide["developer_portal"] == "https://discord.com/developers/applications"
    assert installation["context"] == "Guild Install"
    assert installation["scopes"] == ["bot", "applications.commands"]
    assert installation["permissions_integer"] == 84992
    assert [value["name"] for value in installation["permissions"]] == [
        "View Channel",
        "Send Messages",
        "Embed Links",
        "Read Message History",
    ]
    assert "Administrator" not in {value["name"] for value in installation["permissions"]}
    assert not any(guide["bot_settings"]["privileged_gateway_intents"].values())
    assert guide["bot_settings"]["require_oauth2_code_grant"] is False


@pytest.mark.parametrize(("count", "pages"), [(0, 1), (1, 1), (25, 1), (26, 2)])
def test_mobile_video_pagination_respects_discord_select_limit(count: int, pages: int) -> None:
    videos = [{"video_id": str(index)} for index in range(count)]
    assert video_page_count(videos) == pages
    assert len(video_page(videos, 0)) <= 25
    if count == 26:
        assert video_page(videos, 1) == [{"video_id": "25"}]


def test_video_selection_is_preserved_across_pages() -> None:
    videos = [{"video_id": str(index)} for index in range(26)]
    selected: set[str] = set()
    merge_video_page_selection(selected, video_page(videos, 0), ["0", "24"])
    merge_video_page_selection(selected, video_page(videos, 1), ["25"])
    assert selected == {"0", "24", "25"}
    merge_video_page_selection(selected, video_page(videos, 0), ["24"])
    assert selected == {"24", "25"}


def test_followup_omits_none_view_for_discord_webhook() -> None:
    class Followup:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def send(self, **kwargs):
            self.calls.append(kwargs)
            return object()

    class Interaction:
        def __init__(self) -> None:
            self.followup = Followup()

    async def scenario() -> None:
        interaction = Interaction()
        embed = discord.Embed(title="Assignments")
        await _send_followup_embed(interaction, embed, wait=True)
        assert "view" not in interaction.followup.calls[0]
        view = discord.ui.View()
        await _send_followup_embed(interaction, embed, view=view, wait=True)
        assert interaction.followup.calls[1]["view"] is view

    asyncio.run(scenario())
