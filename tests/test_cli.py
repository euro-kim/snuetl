import json
from argparse import Namespace
from dataclasses import replace
from pathlib import Path

import pytest

from snuetl import cli
from snuetl.cli import main
from snuetl.config import load_config, save_config
from snuetl.errors import DiscoveryError
from snuetl.models import Course, ModuleItem
from snuetl.puller import PullPlan, PullSummary


def test_bare_configured_command_shows_dashboard(tmp_path: Path, capsys) -> None:
    path = tmp_path / "config.toml"
    config = replace(
        load_config(tmp_path / "missing.toml"),
        state_dir=tmp_path / "state",
        download_dir=tmp_path / "downloads",
        setup_complete=True,
    )
    config.profile_dir.mkdir(parents=True)
    config.auth_state_path.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    save_config(config, path)
    assert main(["--config", str(path)]) == 0
    output = capsys.readouterr().out
    assert "snuetl" in output
    assert "Available commands" in output
    assert "snuetl courses" in output


def test_sql_requires_explicit_noninteractive_source(capsys) -> None:
    assert main(["sql", "select 1", "--json"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "INVALID_ARGUMENT"


def test_capabilities_has_versioned_agent_contract(capsys) -> None:
    assert main(["capabilities", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "1"
    assert payload["data"]["commands"]["pull"] == [
        "files",
        "articles",
        "syllabus",
        "videos",
        "all",
    ]
    assert "quizzes" in payload["data"]["commands"]["inspect"]


def test_pull_videos_has_no_profile_selection_option() -> None:
    args = cli._parser().parse_args(["pull", "videos"])

    assert not hasattr(args, "profile")


def test_quizzes_command_accepts_an_optional_course() -> None:
    args = cli._parser().parse_args(["quizzes", "Systems"])

    assert args.command == "quizzes"
    assert args.course == "Systems"


def test_canvas_api_and_live_commands_are_exposed() -> None:
    parser = cli.build_parser()
    api = parser.parse_args(["api", "setup", "--headless", "--json"])
    assert api.command == "api"
    assert api.api_command == "setup"
    assert api.headless is True
    assert api.json is True
    upcoming = parser.parse_args(["upcoming", "--from", "2026-09-01", "--to", "2026-09-30"])
    assert upcoming.start_date == "2026-09-01"
    assert upcoming.end_date == "2026-09-30"


def test_telegram_cli_exposes_guide_alerts_and_interactive_pairing(tmp_path: Path, capsys) -> None:
    parser = cli.build_parser()
    alerts = parser.parse_args(["telegram", "alerts", "off", "--json"])
    assert alerts.telegram_command == "alerts" and alerts.mode == "off"
    path = tmp_path / "config.toml"
    assert main(["--config", str(path), "telegram", "guide", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["botfather"] == "https://t.me/BotFather"
    assert main(["--config", str(path), "telegram", "setup", "--json"]) == 5
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "INPUT_REQUIRED"


def test_logout_preserves_authentication_when_canvas_revocation_fails(
    tmp_path: Path, monkeypatch
) -> None:
    config = replace(load_config(tmp_path / "missing.toml"), state_dir=tmp_path / "state")
    config.profile_dir.mkdir(parents=True)
    config.auth_state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli, "load_token", lambda _config: object())

    def fail_revocation(_config) -> None:
        raise DiscoveryError("revocation unconfirmed")

    monkeypatch.setattr(cli, "revoke_token", fail_revocation)
    with pytest.raises(DiscoveryError, match="revocation unconfirmed"):
        cli._logout(config, confirmed=True)
    assert config.profile_dir.exists()
    assert config.auth_state_path.exists()


def test_discord_cli_exposes_daemon_and_owner_commands() -> None:
    status = cli._parser().parse_args(["discord", "status", "--json"])
    owner = cli._parser().parse_args(["discord", "owner", "add", "123"])
    guide = cli._parser().parse_args(["discord", "guide", "--json"])

    assert status.discord_command == "status"
    assert status.json is True
    assert owner.discord_owner_command == "add"
    assert owner.user_id == 123
    assert guide.discord_command == "guide"
    assert guide.json is True


def test_discord_guide_is_available_before_setup(tmp_path: Path, capsys) -> None:
    config_path = tmp_path / "missing.toml"

    assert main(["--config", str(config_path), "discord", "guide", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["installation"]["permissions_integer"] == 84992
    assert payload["data"]["bot_settings"]["privileged_gateway_intents"] == {
        "presence": False,
        "server_members": False,
        "message_content": False,
    }


def test_headless_command_persists_default(tmp_path: Path, capsys) -> None:
    path = tmp_path / "config.toml"
    save_config(load_config(tmp_path / "missing.toml"), path)

    assert main(["--config", str(path), "headless", "off", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"] == {"headless": False, "browser_mode": "visible"}
    assert load_config(path).headless is False


def test_update_inside_container_points_to_host_updater(monkeypatch, capsys) -> None:
    monkeypatch.setenv("SNUETL_RUNTIME", "container")
    monkeypatch.setattr(cli, "update_self", lambda: pytest.fail("must not update in container"))

    assert main(["update", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "COMMAND_FAILED"
    assert "docker-update.sh" in payload["error"]["message"]


def test_directory_parser_supports_detailed_binding() -> None:
    args = cli._parser().parse_args(
        [
            "directory",
            "bind",
            "/srv/classes/A/B",
            "--name",
            "week-y",
            "--course",
            "101",
            "--semester",
            "2026-2",
            "--kind",
            "files",
            "--remote-folder",
            "x/y",
        ]
    )

    assert args.operation == "bind"
    assert args.value == "/srv/classes/A/B"
    assert args.remote_folder == "x/y"


def test_directory_parser_supports_dedicated_video_storage() -> None:
    separate = cli._parser().parse_args(["directory", "videos", "/mnt/large-videos"])
    together = cli._parser().parse_args(["directory", "videos", "--default"])

    assert (separate.operation, separate.value) == ("videos", "/mnt/large-videos")
    assert separate.use_default is False
    assert together.use_default is True


def test_video_checklist_selection_is_passed_to_pull_plan(monkeypatch) -> None:
    course = Course("course-1", "Course", "https://lms.test/courses/course-1")
    first = ModuleItem("module-1", "Week 1", "video-1", course.remote_id, "ExternalTool", "First")
    second = ModuleItem("module-1", "Week 1", "video-2", course.remote_id, "ExternalTool", "Second")
    plan = PullPlan(("videos",), (course,), modules=((course, first), (course, second)))
    args = Namespace(video_ids=[], yes=False, dry_run=False, no_input=False, json=False)
    monkeypatch.setattr(cli, "interactive_terminal", lambda: True)
    monkeypatch.setattr(cli, "choose_checkboxes", lambda *args, **kwargs: ("video-2",))

    accepted, selected_ids = cli._select_videos(plan, args)
    selected_plan = replace(plan, selected_video_ids=selected_ids)

    assert accepted
    assert [item.remote_id for _, item in selected_plan.video_items] == ["video-2"]


def test_human_pull_summary_only_prints_written_artifacts(tmp_path, capsys) -> None:
    artifact = tmp_path / "selected.mp4"
    summary = PullSummary(courses=5, updated=1, artifacts=[str(artifact)])

    cli._print_pull_summary(summary, tmp_path)

    output = capsys.readouterr().out
    assert "1 updated" in output
    assert str(artifact) in output
    assert '"plan"' not in output
    assert '"videos"' not in output
