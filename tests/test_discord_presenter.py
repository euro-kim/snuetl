from datetime import UTC, datetime
from pathlib import Path

from snuetl.discord_presenter import (
    catalog_embed,
    catalog_page_count,
    discord_timestamp,
    doctor_embed,
    embed_to_text,
    exception_embed,
    finished_job_embed,
    human_size,
    jobs_embed,
    status_embed,
)
from snuetl.errors import AuthenticationRequired
from snuetl.state import DiscordJob


def embed_text(embed) -> str:
    fields = "\n".join(f"{field.name}\n{field.value}" for field in embed.fields)
    return f"{embed.title}\n{embed.description}\n{fields}\n{embed.footer.text}"


def test_discord_timestamp_is_native_and_human_size_is_readable() -> None:
    assert discord_timestamp("2026-09-05T01:00:00+00:00") == "<t:1788570000:f>"
    assert discord_timestamp(datetime(2026, 9, 5, 1, tzinfo=UTC), "R") == "<t:1788570000:R>"
    assert discord_timestamp("not-a-date") == "Not available"
    assert human_size(1536) == "1.5 KB"


def test_status_embed_explains_read_only_result() -> None:
    embed = status_embed(
        {
            "authentication_ready": True,
            "automatic_relogin": True,
            "saved_username": "student",
            "download_dir": "/srv/snuetl",
            "video_directory": "/mnt/large-videos",
            "video_directory_separate": True,
            "courses": 3,
            "remote_files": 40,
            "articles": 5,
            "assignments": 7,
            "downloaded_files": 20,
            "quizzes": 2,
            "last_sync": {
                "status": "completed",
                "finished_at": "2026-09-05T01:00:00+00:00",
                "downloaded": 2,
                "updated": 1,
                "failed": 0,
            },
            "discord": {"service_active": True},
        }
    )
    text = embed_text(embed)
    assert embed.title == "snuetl status"
    assert "No files were changed" in text
    assert "3** courses" in text
    assert "2** quizzes" in text
    assert "<t:1788570000:f>" in text
    assert "/srv/snuetl" in text
    assert "/mnt/large-videos" in text
    assert "separate" in text


def test_doctor_embed_summarizes_checks_without_json() -> None:
    embed = doctor_embed(
        {
            "ready": False,
            "checks": [
                {"name": "configuration", "ok": True, "detail": "/config.toml"},
                {"name": "chromium", "ok": False, "detail": "not installed"},
            ],
        }
    )
    text = embed_text(embed)
    assert embed.title == "Diagnostics found issues"
    assert "✅ Configuration" in text
    assert "⚠️ Chromium" in text
    assert "1 passed · 1 need attention" in text


def test_catalog_embed_paginates_and_formats_assignment_dates() -> None:
    rows = [
        {
            "course": "Distributed Systems",
            "title": f"Assignment {index}",
            "due_at": "2026-09-05T01:00:00+00:00",
        }
        for index in range(7)
    ]
    assert catalog_page_count(rows) == 2
    first = catalog_embed("assignments", rows)
    second = catalog_embed("assignments", rows, page=1)
    assert len(first.fields) == 5
    assert len(second.fields) == 2
    assert "Found **7** items" in str(first.description)
    assert "<t:1788570000:f>" in str(first.fields[0].value)
    assert first.footer.text == "Page 1 of 2 · 7 total"
    quiz = catalog_embed("quizzes", [{"course": "Systems", "title": "Midterm Quiz"}])
    quiz_text = embed_text(quiz)
    assert quiz.title == "Quizzes"
    assert "❓ Midterm Quiz" in quiz_text


def test_finished_pull_embed_tells_user_what_changed(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    artifact = root / "Course" / "notes.md"
    job = DiscordJob(
        job_id="abc123",
        command="pull",
        arguments={"kind": "articles", "course": "Course"},
        requester_id=1,
        status="completed",
        created_at="2026-09-05T01:00:00+00:00",
        started_at="2026-09-05T01:00:10+00:00",
        finished_at="2026-09-05T01:01:15+00:00",
    )
    embed = finished_job_embed(
        job,
        {
            "result": {
                "created": 2,
                "updated": 1,
                "unchanged": 3,
                "conflicts": 0,
                "skipped": 0,
                "failed": 0,
                "artifacts": [str(artifact)],
            },
            "managed_root": str(root),
        },
        root,
    )
    text = embed_text(embed)
    assert embed.title == "Pull complete"
    assert "Pulled the requested course content" in text
    assert "✨ 2 created" in text
    assert "Course/notes.md" in text
    assert str(root) not in text
    assert "1m 5s" in text


def test_jobs_embed_uses_relative_discord_time() -> None:
    job = DiscordJob("abc", "sync", {}, 1, "running", "2026-09-05T01:00:00+00:00")
    text = embed_text(jobs_embed([job]))
    assert "Sync · Running" in text
    assert "<t:1788570000:R>" in text
    assert "abc" in text


def test_exception_embed_is_actionable_and_hides_internal_details() -> None:
    login = exception_embed(AuthenticationRequired("session expired"), "ref-login")
    assert login.title == "SNU login required"
    assert "/snuetl login" in embed_text(login)

    unexpected = exception_embed(
        TypeError("expected view parameter to be of type View, not NoneType"), "ref-internal"
    )
    text = embed_text(unexpected)
    assert unexpected.title == "Unexpected command error"
    assert "NoneType" not in text
    assert "ref-internal" in text
    fallback = embed_to_text(unexpected)
    assert "Unexpected command error" in fallback
    assert len(fallback) <= 1900
