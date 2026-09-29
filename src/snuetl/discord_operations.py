from __future__ import annotations

import platform
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .browser import interactive_login
from .catalog import inspect_catalog, refresh_catalog
from .config import Config, default_config_path
from .credentials import load_credentials
from .directory_manager import (
    configured_directory_permission_reports,
    configured_video_directory,
    has_separate_video_directory,
    repair_legacy_layout,
    validate_managed_root,
)
from .onboarding import browser_available
from .puller import discover_pull_plan, execute_pull, plan_data
from .runtime import is_container_runtime
from .scheduler import discord_service_is_active, discord_service_is_enabled, linger_status
from .state import StateStore
from .syncer import synchronize


@dataclass(frozen=True, slots=True)
class LoginCallbacks:
    method: str
    code_provider: Callable[[], str]


def status_data(config: Config) -> dict[str, object]:
    saved = load_credentials(config)
    data: dict[str, object] = {
        "authentication_ready": config.profile_dir.exists() and config.auth_state_path.exists(),
        "automatic_relogin": saved is not None,
        "saved_username": saved.username if saved else None,
        "state_dir": str(config.state_dir),
        "download_dir": str(config.download_dir),
        "video_directory": str(configured_video_directory(config)),
        "video_directory_separate": has_separate_video_directory(config),
        "headless": True,
        "last_sync": None,
        "catalog_refreshed_at": None,
        "active_jobs": 0,
        "queued_jobs": 0,
        "courses": 0,
        "remote_files": 0,
        "articles": 0,
        "assignments": 0,
        "downloaded_files": 0,
        "quizzes": 0,
        "discord": {
            "enabled": config.discord.enabled,
            "configured": config.discord.configured,
            "service_manager": "docker_compose" if is_container_runtime() else "systemd",
            "service_enabled": discord_service_is_enabled()
            if platform.system() == "Linux"
            else False,
            "service_active": discord_service_is_active()
            if platform.system() == "Linux"
            else False,
        },
    }
    if not config.database_path.exists():
        return data
    with StateStore(config.database_path) as store:
        courses, downloaded = store.counts()
        remote_files, articles, assignments, quizzes = store.catalog_counts()
        last = store.last_run()
        refreshed = store.db.execute(
            "SELECT MAX(refreshed_at) AS refreshed_at FROM catalog_refreshes"
        ).fetchone()
        job_counts = store.db.execute(
            """SELECT
                 SUM(CASE WHEN status IN ('running', 'cancel_requested') THEN 1 ELSE 0 END)
                   AS active_jobs,
                 SUM(CASE WHEN status='queued' THEN 1 ELSE 0 END) AS queued_jobs
               FROM discord_jobs"""
        ).fetchone()
        data.update(
            courses=courses,
            remote_files=remote_files,
            articles=articles,
            assignments=assignments,
            quizzes=quizzes,
            downloaded_files=downloaded,
            last_sync=dict(last) if last is not None else None,
            catalog_refreshed_at=refreshed["refreshed_at"] if refreshed is not None else None,
            active_jobs=_number_or_zero(job_counts["active_jobs"]),
            queued_jobs=_number_or_zero(job_counts["queued_jobs"]),
        )
    return data


def _number_or_zero(value: object) -> int:
    try:
        return int(str(value or 0))
    except (TypeError, ValueError):
        return 0


def doctor_data(config: Config, config_path: Path | None = None) -> dict[str, object]:
    path = config_path or default_config_path()
    machine = platform.machine() or "unknown"
    browser_ok, browser_detail = browser_available(config)
    saved = load_credentials(config)
    linger = linger_status() if platform.system() == "Linux" else None
    permission_reports = configured_directory_permission_reports(config)
    permissions_ok = all(report.safe for report in permission_reports)
    permissions_detail = (
        ", ".join(f"{report.path} ({report.mode:04o})" for report in permission_reports)
        if permissions_ok
        else " | ".join(
            f"{report.path}: {'; '.join((*report.issues, *report.remediation))}"
            for report in permission_reports
            if not report.safe
        )
    )
    checks: list[dict[str, object]] = [
        {"name": "configuration", "ok": path.exists(), "detail": str(path)},
        {
            "name": "architecture",
            "ok": machine.casefold() in {"x86_64", "amd64", "aarch64", "arm64"},
            "detail": f"{platform.system()} {machine}",
        },
        {"name": "browser", "ok": browser_ok, "detail": browser_detail},
        {
            "name": "ffmpeg",
            "ok": shutil.which("ffmpeg") is not None,
            "detail": shutil.which("ffmpeg") or "not installed",
        },
        {
            "name": "authentication_state",
            "ok": config.profile_dir.exists() and config.auth_state_path.exists(),
            "detail": str(config.state_dir),
        },
        {
            "name": "managed_directory_permissions",
            "ok": permissions_ok,
            "detail": permissions_detail,
        },
        {
            "name": "saved_credentials",
            "ok": saved is not None,
            "detail": saved.username if saved else "required for remote re-login",
        },
        {
            "name": "discord_configuration",
            "ok": config.discord.configured,
            "detail": "bound" if config.discord.configured else "run snuetl discord",
            "optional": True,
        },
    ]
    if linger is not None:
        checks.extend(
            [
                {
                    "name": "discord_service",
                    "ok": discord_service_is_enabled() and discord_service_is_active(),
                    "detail": "Docker Compose supervisor"
                    if is_container_runtime()
                    else "user systemd service",
                    "optional": True,
                },
                {
                    "name": "container_restart_policy"
                    if is_container_runtime()
                    else "user_linger",
                    "ok": linger.enabled,
                    "detail": linger.detail,
                    "optional": True,
                },
            ]
        )
    required = [check for check in checks if not check.get("optional")]
    return {"ready": all(bool(check["ok"]) for check in required), "checks": checks}


def catalog_rows(config: Config, kind: str, course: str | None = None) -> list[dict[str, object]]:
    result = inspect_catalog(config, kind=kind, course_query=course, headless=True)
    rows: list[dict[str, object]] = []
    if kind == "courses":
        for course_value in result.courses:
            rows.append(
                {
                    "semester": course_value.semester.semester_code
                    if course_value.semester
                    else None,
                    "course_id": course_value.remote_id,
                    "course": course_value.display_name,
                    "course_code": course_value.course_code,
                    "starts_at": course_value.starts_at,
                    "ends_at": course_value.ends_at,
                }
            )
    elif kind == "files":
        for owner, remote_value in result.files:
            rows.append(
                {
                    "course": owner.display_name,
                    "file_id": remote_value.remote_id,
                    "name": remote_value.name,
                    "path": remote_value.display_path,
                    "size_bytes": remote_value.size,
                    "updated_at": remote_value.updated_at,
                    "content_type": remote_value.content_type,
                }
            )
    else:
        for owner, item_value in result.items:
            rows.append(
                {
                    "course": owner.display_name,
                    "content_id": item_value.remote_id,
                    "title": item_value.title,
                    "content_type": item_value.kind,
                    "due_at": item_value.due_at,
                    "published_at": item_value.published_at,
                    "updated_at": item_value.updated_at,
                }
            )
    return rows


def execute_job(
    config: Config,
    command: str,
    arguments: dict[str, object],
    *,
    progress: Callable[[str], None],
    login_callbacks: LoginCallbacks | None = None,
) -> dict[str, object]:
    config = replace(config, headless=True)
    if command == "sync":
        progress(f"Starting file synchronization into {config.download_dir}…")
        return asdict(synchronize(config, headless=True, progress=progress))
    if command == "refresh":
        progress("Refreshing courses, files, articles, assignments, quizzes, and videos…")
        return asdict(refresh_catalog(config, headless=True, progress=progress))
    if command == "login":
        saved = load_credentials(config)
        if saved is None:
            raise RuntimeError("Saved SNU credentials are required; run snuetl login locally")
        if login_callbacks is None:
            raise RuntimeError("Discord verification broker is unavailable")
        progress(
            f"Signing in with saved credentials; waiting for {login_callbacks.method} verification…"
        )
        landing = interactive_login(
            config,
            saved.username,
            saved.password,
            headless=True,
            trust_browser=saved.trust_browser,
            two_factor_method_provider=lambda: login_callbacks.method,
            verification_code_provider=login_callbacks.code_provider,
        )
        return {"authenticated": True, "landing_url": landing}
    if command != "pull":
        raise ValueError(f"unsupported Discord job: {command}")

    kind = str(arguments.get("kind", "all"))
    if kind not in {"all", "files", "articles", "syllabus", "videos"}:
        raise ValueError("invalid pull kind")
    kinds = ("files", "articles", "syllabus") if kind == "all" else (kind,)
    course = str(arguments.get("course") or "").strip()
    semester = str(arguments.get("semester") or "").strip()
    filters = ", ".join(
        value
        for value in (
            f"content={kind}",
            f"course={course}" if course else "all courses",
            f"semester={semester}" if semester else "all semesters",
        )
    )
    progress(f"Discovering pull candidates ({filters})…")
    plan = discover_pull_plan(
        config,
        kinds,
        course_selectors=(course,) if course else (),
        semesters=(semester,) if semester else (),
    )
    selected = arguments.get("video_ids")
    if isinstance(selected, list):
        selected_ids = tuple(str(value) for value in selected)
        raw_videos = plan_data(plan)["videos"]
        available = (
            {str(value["video_id"]) for value in raw_videos if isinstance(value, dict)}
            if isinstance(raw_videos, list)
            else set()
        )
        unknown = set(selected_ids) - available
        if unknown:
            raise ValueError(
                "selected videos are no longer available: " + ", ".join(sorted(unknown))
            )
        plan = replace(plan, selected_video_ids=selected_ids)
    elif kind == "videos":
        raise ValueError("video selection is required before queueing a Discord pull")
    planned = plan_data(plan)
    progress(
        "Pull plan ready: "
        f"{planned['courses']} course(s), {planned['files']} file(s), "
        f"{planned['articles']} article(s), {planned['syllabus_files']} syllabus file(s), "
        f"{planned['video_items']} video(s)."
    )
    root = validate_managed_root(config.download_dir)
    repair_legacy_layout(root)
    summary = execute_pull(
        config,
        plan,
        root=root,
        dry_run=bool(arguments.get("dry_run", False)),
        force=bool(arguments.get("force", False)),
        max_height=None
        if bool(arguments.get("best", False))
        else int(str(arguments.get("max_height", 1080))),
        captions=not bool(arguments.get("no_captions", False)),
        jobs=1,
        progress=progress,
    )
    return {"plan": plan_data(plan), "result": asdict(summary), "managed_root": str(root)}
