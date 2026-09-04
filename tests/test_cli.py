import json
from argparse import Namespace
from dataclasses import replace
from pathlib import Path

from snuetl import cli
from snuetl.cli import main
from snuetl.config import load_config, save_config
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


def test_pull_videos_has_no_profile_selection_option() -> None:
    args = cli._parser().parse_args(["pull", "videos"])

    assert not hasattr(args, "profile")


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
