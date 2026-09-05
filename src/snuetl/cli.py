from __future__ import annotations

import argparse
import logging
import platform
import shutil
import sys
import uuid
from dataclasses import asdict, replace
from pathlib import Path

from rich.prompt import Confirm
from rich.table import Table

from . import __version__
from .agent import emit, emit_error, envelope
from .browser_profiles import select_profile, switch_profile
from .catalog import inspect_catalog, refresh_catalog
from .cli_parser import CliUsageError, build_parser
from .cli_runtime import handle_cli_exception
from .config import Config, DirectoryRoute, default_config_path, load_config, save_config
from .credentials import load_credentials
from .directory_manager import (
    DirectoryPermissionReport,
    configured_directory_permission_reports,
    configured_video_directory,
    directory_migration_conflicts,
    directory_permission_data,
    directory_routes_data,
    execute_directory_migration,
    has_separate_video_directory,
    plan_directory_migration,
    remove_directory_route,
    repair_legacy_layout,
    resolve_cached_course_id,
    set_video_directory,
    upsert_directory_route,
    validate_managed_root,
)
from .logging_utils import configure_logging
from .onboarding import (
    browser_available,
    display_available,
    ensure_browser,
    prompt_login,
    run_doctor,
    run_setup,
)
from .profile import profile_lock
from .puller import PullSummary, discover_pull_plan, execute_pull, plan_data
from .query import execute_query
from .routing import parse_remote_folder
from .runtime import container_supervisor_is_active, is_container_runtime
from .scheduler import (
    discord_service_is_active,
    discord_service_is_enabled,
    linger_status,
    timer_is_enabled,
)
from .sql_shell import run_sql_shell
from .state import StateStore
from .syncer import synchronize
from .ui import (
    choose_checkbox,
    choose_checkboxes,
    console,
    interactive_terminal,
    print_banner,
    print_catalog,
    print_commands,
    print_query_result,
    print_schema,
)
from .uninstaller import build_inventory, execute_uninstall, inventory_data
from .versioning import get_version_info, update_self

LOGGER = logging.getLogger(__name__)

# Compatibility alias for callers that previously inspected the private parser.
_parser = build_parser


def _headless_choice(args: argparse.Namespace, config: Config | None = None) -> bool:
    if getattr(args, "headed", False):
        return False
    if getattr(args, "headless", False):
        return True
    return config.headless if config is not None else not display_available()


def _login(config: Config, *, headless: bool, config_path: Path | None) -> int:
    checked_config = ensure_browser(config)
    if checked_config != config:
        save_config(checked_config, config_path)
    config = checked_config
    with profile_lock(config.lock_path):
        prompt_login(config, headless=headless)
    console.print("[green]SNU authentication is ready.[/green]")
    return 0


def _status(config: Config) -> int:
    profile_dir = config.profile_dir
    database_path = config.database_path
    auth_ready = profile_dir.exists() and config.auth_state_path.exists()
    saved = load_credentials(config)
    console.print(
        f"Authentication: {'[green]ready[/green]' if auth_ready else '[yellow]login required[/yellow]'} "
        f"[dim]({config.state_dir})[/dim]"
    )
    console.print(
        "Automatic re-login: "
        + (f"[green]enabled[/green] [dim]({saved.username})[/dim]" if saved else "disabled")
    )
    if not database_path.exists():
        console.print("Last sync: never")
        console.print("Tracked: 0 active courses, 0 files")
        return 0
    with StateStore(database_path) as store:
        courses, downloaded_files = store.counts()
        remote_files, articles, assignments, quizzes = store.catalog_counts()
        last = store.last_run()
        if last is None:
            console.print("Last sync: never")
        else:
            finished = last["finished_at"] or "still running/interrupted"
            console.print(f"Last sync: {last['status']} at {finished}")
            console.print(
                "Last result: "
                f"{last['downloaded']} downloaded, {last['updated']} updated, "
                f"{last['unchanged']} unchanged, {last['failed']} failed"
            )
        console.print(
            f"Catalog: {courses} active courses, {remote_files} remote files, "
            f"{articles} articles, {assignments} assignments, {quizzes} quizzes"
        )
        console.print(f"Downloaded: {downloaded_files} files tracked locally")
    return 0


def _logout(config: Config, confirmed: bool) -> int:
    profile_dir = config.profile_dir
    auth_paths = (
        config.auth_state_path,
        config.session_metadata_path,
        config.credentials_path,
    )
    if not profile_dir.exists() and not any(path.exists() for path in auth_paths):
        LOGGER.info("dedicated browser authentication is already absent")
        return 0
    if not confirmed and not Confirm.ask(
        f"Remove browser authentication and saved credentials under {config.state_dir}? "
        "This cannot be undone.",
        default=False,
    ):
        LOGGER.info("logout cancelled")
        return 0
    with profile_lock(config.lock_path):
        if profile_dir.exists():
            shutil.rmtree(profile_dir)
        for path in auth_paths:
            path.unlink(missing_ok=True)
    LOGGER.info(
        "removed browser authentication and saved credentials; "
        "downloaded files and sync history remain"
    )
    return 0


def _version(check: bool) -> int:
    info = get_version_info(check=check)
    table = Table(title="snuetl version", show_header=False)
    table.add_row("Installed", f"[cyan]{info.installed}[/cyan]")
    table.add_row("Source", info.source)
    if info.latest:
        table.add_row("Latest published", info.latest)
    console.print(table)
    if info.check_message:
        console.print(info.check_message)
    return 0


def _json_requested(argv: list[str]) -> bool:
    return "--json" in argv


def _status_data(config: Config) -> dict[str, object]:
    auth_ready = config.profile_dir.exists() and config.auth_state_path.exists()
    saved = load_credentials(config)
    data: dict[str, object] = {
        "authentication_ready": auth_ready,
        "automatic_relogin": saved is not None,
        "saved_username": saved.username if saved else None,
        "state_dir": str(config.state_dir),
        "download_dir": str(config.download_dir),
        "video_directory": str(configured_video_directory(config)),
        "video_directory_separate": has_separate_video_directory(config),
        "headless": config.headless,
        "directory_routes": directory_routes_data(config),
        "last_sync": None,
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
        data.update(
            courses=courses,
            remote_files=remote_files,
            articles=articles,
            assignments=assignments,
            quizzes=quizzes,
            downloaded_files=downloaded,
            last_sync=dict(last) if last is not None else None,
        )
    return data


def _catalog_data(result: object, kind: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    if kind == "courses":
        for course in result.courses:
            rows.append(
                {
                    "semester_code": course.semester.semester_code if course.semester else None,
                    "course_id": course.remote_id,
                    "course_name": course.display_name,
                    "url": course.url,
                }
            )
    elif kind == "files":
        for course, remote in result.files:
            rows.append(
                {
                    "semester_code": course.semester.semester_code if course.semester else None,
                    "course_id": course.remote_id,
                    "course_name": course.display_name,
                    "file_id": remote.remote_id,
                    "file_name": remote.name,
                    "remote_path": remote.display_path,
                    "content_type": remote.content_type,
                    "size_bytes": remote.size,
                    "updated_at": remote.updated_at,
                    "download_url": remote.download_url,
                }
            )
    else:
        for course, item in result.items:
            rows.append(
                {
                    "semester_code": course.semester.semester_code if course.semester else None,
                    "course_id": course.remote_id,
                    "course_name": course.display_name,
                    "content_id": item.remote_id,
                    "content_type": item.kind,
                    "title": item.title,
                    "url": item.url,
                    "published_at": item.published_at,
                    "due_at": item.due_at,
                }
            )
    return rows


def _query_data(result: object) -> dict[str, object]:
    return {
        "columns": list(result.columns),
        "rows": [dict(zip(result.columns, row, strict=True)) for row in result.rows],
        "truncated": result.truncated,
    }


def _capabilities() -> dict[str, object]:
    return {
        "cli": "snuetl",
        "version": __version__,
        "schema_version": "1",
        "commands": {
            "inspect": ["courses", "files", "articles", "assignments", "quizzes"],
            "sql": {
                "interactive": "snuetl sql",
                "noninteractive": ["--execute", "--file", "stdin"],
            },
            "pull": ["files", "articles", "syllabus", "videos", "all"],
            "discord": [
                "status",
                "doctor",
                "courses",
                "files",
                "articles",
                "assignments",
                "quizzes",
                "refresh",
                "sync",
                "pull",
                "login",
                "jobs",
                "cancel",
            ],
            "discord_management": ["guide", "setup", "status", "enable", "disable", "owner"],
            "maintenance": [
                "refresh",
                "headless",
                "directory",
                "profile",
                "status",
                "doctor",
                "version",
                "update",
                "uninstall",
                "discord",
            ],
        },
        "agent_flags": ["--json", "--no-input", "--yes", "--dry-run", "--video-id"],
        "exit_codes": {
            "0": "success",
            "1": "command failure",
            "2": "authentication required",
            "3": "configuration or local-state error",
            "4": "partial result",
            "5": "input required",
        },
    }


def _doctor_data(config: Config, config_path: Path | None) -> dict[str, object]:
    path = config_path or default_config_path()
    machine = platform.machine() or "unknown"
    browser_ok, browser_detail = browser_available(config)
    saved = load_credentials(config)
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
    checks = [
        {"name": "configuration", "ok": path.exists(), "detail": str(path)},
        {
            "name": "architecture",
            "ok": machine.casefold() in {"x86_64", "amd64", "aarch64", "arm64"},
            "detail": f"{platform.system()} {machine}",
        },
        {"name": "chromium", "ok": browser_ok, "detail": browser_detail},
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
            "name": "download_directory",
            "ok": config.download_dir.exists(),
            "detail": str(config.download_dir),
        },
        {
            "name": "directory_permissions",
            "ok": permissions_ok,
            "detail": permissions_detail,
        },
        {
            "name": "automatic_relogin",
            "ok": saved is not None,
            "detail": saved.username if saved else "disabled",
            "optional": True,
        },
    ]
    if platform.system() == "Linux":
        if is_container_runtime():
            checks.append(
                {
                    "name": "container_supervisor",
                    "ok": container_supervisor_is_active(),
                    "detail": "Docker Compose; synchronization is Discord-triggered",
                }
            )
        else:
            checks.append(
                {
                    "name": "systemd_timer",
                    "ok": timer_is_enabled(),
                    "detail": "15-minute timer",
                    "optional": True,
                }
            )
        if config.discord.configured:
            linger = linger_status()
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


def _run_discord_command(config: Config, args: argparse.Namespace) -> int:
    from .discord_setup import (
        change_discord_owner,
        configure_discord,
        discord_setup_guide,
        discord_status,
        print_discord_setup_guide,
        print_discord_setup_success,
        restart_discord_if_enabled,
        toggle_discord,
    )

    action = args.discord_command or "setup"
    if action == "guide":
        data = discord_setup_guide()
        if args.json:
            emit(envelope("discord guide", data=data))
        else:
            print_discord_setup_guide()
        return 0
    if action == "run":
        from .discord_bot import run_discord_bot

        run_discord_bot(config)
        return 0
    if action == "status":
        data = discord_status(config)
        ready = bool(
            data["configured"]
            and data["token_present"]
            and data["service_enabled"]
            and data["service_active"]
        )
        emit(envelope("discord status", data=data, ok=ready)) if args.json else console.print_json(
            data=data
        )
        return 0 if ready else 1
    if action in {"enable", "disable"}:
        updated = toggle_discord(config, action == "enable", args.config)
        data = discord_status(updated)
        emit(envelope(f"discord {action}", data=data)) if args.json else console.print_json(
            data=data
        )
        return 0
    if action == "owner":
        if args.discord_owner_command == "list":
            data = {"owner_ids": sorted(config.discord.owner_ids)}
        else:
            updated = change_discord_owner(
                config, args.user_id, add=args.discord_owner_command == "add"
            )
            save_config(updated, args.config)
            restart_discord_if_enabled(updated)
            data = {"owner_ids": sorted(updated.discord.owner_ids), "restart_required": True}
        emit(envelope("discord owner", data=data)) if args.json else console.print_json(data=data)
        return 0
    if args.no_input or args.json:
        required = (
            args.token_file
            and args.application_id
            and args.guild_id
            and args.channel_id
            and args.owner_id
            and args.yes
        )
        if not required:
            if args.json:
                emit_error(
                    "discord setup",
                    "INPUT_REQUIRED",
                    "Discord setup needs pairing details.",
                    "Pass --token-file, --application-id, --guild-id, --channel-id, --owner-id, and --yes.",
                )
            return 5
    _updated, data = configure_discord(
        config,
        config_path=args.config,
        token_file=args.token_file,
        application_id=args.application_id,
        guild_id=args.guild_id,
        channel_id=args.channel_id,
        owner_ids=tuple(args.owner_id),
        confirmed=args.yes,
    )
    if args.json:
        emit(envelope("discord setup", data=data))
    else:
        print_discord_setup_success(data)
    return 0


def _refresh_discord_directory_access(config: Config, config_path: Path) -> None:
    if not config.discord.enabled or not discord_service_is_enabled():
        return
    from .scheduler import install_discord_service

    install_discord_service(
        config_path,
        state_dir=config.state_dir,
        download_dir=config.download_dir,
        write_dirs=tuple(route.destination for route in config.directory_routes),
    )


def _directory_permission_warnings(
    reports: list[DirectoryPermissionReport],
) -> list[dict[str, object]]:
    warnings: list[dict[str, object]] = []
    for report in reports:
        if report.safe:
            continue
        warnings.append(
            {
                "code": "UNSAFE_DIRECTORY_PERMISSIONS",
                "message": f"{report.path}: {'; '.join(report.issues)}",
                "remediation": list(report.remediation),
            }
        )
    return warnings


def _print_directory_permissions(reports: list[DirectoryPermissionReport]) -> None:
    for report in reports:
        mode = f"{report.mode:04o}" if report.mode is not None else "missing"
        if report.safe:
            verb = "Secured" if report.changed else "Safe permissions"
            previous = (
                f" (changed from {report.previous_mode:04o})"
                if report.changed and report.previous_mode is not None
                else ""
            )
            console.print(
                f"[green]✓[/green] {verb}: [dim]{report.path}[/dim] [green]{mode}[/green]{previous}"
            )
            continue
        console.print(
            f"[yellow]! Unsafe directory permissions:[/yellow] [dim]{report.path}[/dim] ({mode})"
        )
        for issue in report.issues:
            console.print(f"  [yellow]•[/yellow] {issue}")
        for remediation in report.remediation:
            console.print(f"  Fix: {remediation}", markup=False, highlight=False)


def _run_directory(config: Config, args: argparse.Namespace) -> int:
    operation = args.operation
    if args.use_default and operation != "videos":
        raise ValueError("--default is only valid with 'snuetl directory videos'")
    show_video = operation == "videos" and args.value is None and not args.use_default
    if operation in {None, "list"} or show_video:
        reports = configured_directory_permission_reports(config)
        status = {
            "managed_root": str(config.download_dir),
            "video_directory": str(configured_video_directory(config)),
            "video_directory_separate": has_separate_video_directory(config),
            "automatic_migration": True,
            "routes": directory_routes_data(config),
            "permissions_safe": all(report.safe for report in reports),
            "permissions": [directory_permission_data(report) for report in reports],
        }
        warnings = _directory_permission_warnings(reports)
        if args.json:
            emit(envelope("directory", data=status, warnings=warnings))
        else:
            _print_directory_permissions(reports)
            console.print_json(data=status)
        return 0

    config_path = (args.config or default_config_path()).expanduser().resolve()
    updated = config
    root = config.download_dir
    action = operation
    if operation == "set":
        if not args.value:
            raise ValueError("directory set requires a destination path")
        root = validate_managed_root(Path(args.value))
        updated = replace(config, download_dir=root)
        if configured_video_directory(updated) == root:
            updated = set_video_directory(updated, root)
    elif operation == "videos":
        if args.value and args.use_default:
            raise ValueError("provide a video path or --default, not both")
        destination = config.download_dir if args.use_default else Path(str(args.value))
        updated = set_video_directory(config, destination)
    elif operation == "bind":
        if not args.value:
            raise ValueError("directory bind requires a destination path")
        remote_folder = parse_remote_folder(args.remote_folder or "")
        route = DirectoryRoute(
            route_id=args.name or f"route-{uuid.uuid4().hex[:8]}",
            destination=validate_managed_root(Path(args.value)),
            course_id=resolve_cached_course_id(config, args.course),
            semester_code=args.semester,
            kind=args.kind,
            remote_folder=remote_folder,
        )
        updated = upsert_directory_route(config, route)
    elif operation == "unbind":
        if not args.value:
            raise ValueError("directory unbind requires a route name")
        updated = remove_directory_route(config, args.value)
    elif args.value is not None:
        raise ValueError(f"unknown directory operation: {operation}")
    else:
        # Backwards-compatible `snuetl directory PATH` form.
        action = "set"
        root = validate_managed_root(Path(operation))
        updated = replace(config, download_dir=root)

    permission_reports = configured_directory_permission_reports(updated)
    entries = [] if args.no_migrate else plan_directory_migration(updated, updated.download_dir)
    conflicts = directory_migration_conflicts(entries)
    data: dict[str, object] = {
        "action": action,
        "managed_root": str(updated.download_dir),
        "routes": directory_routes_data(updated),
        "video_directory": str(configured_video_directory(updated)),
        "video_directory_separate": has_separate_video_directory(updated),
        "permissions_safe": all(report.safe for report in permission_reports),
        "permissions": [directory_permission_data(report) for report in permission_reports],
        "migration": {
            "automatic": not args.no_migrate,
            "planned": len(entries),
            "conflicts": conflicts,
        },
        "entries": [
            {
                "record_type": item.record_type,
                "course_id": item.course_id,
                "source_id": item.source_id,
                "source": str(item.source),
                "destination": str(item.destination),
            }
            for item in entries
            if item.source != item.destination
        ],
    }
    if conflicts:
        warnings = _directory_permission_warnings(permission_reports)
        if args.json or args.dry_run:
            if args.json:
                emit(envelope("directory", data=data, ok=False, warnings=warnings))
            else:
                _print_directory_permissions(permission_reports)
                console.print_json(data=data)
            return 3
        raise ValueError(
            "automatic migration stopped before moving anything: " + "; ".join(conflicts)
        )
    if args.dry_run:
        warnings = _directory_permission_warnings(permission_reports)
        if args.json:
            emit(envelope("directory", data=data, warnings=warnings))
        else:
            _print_directory_permissions(permission_reports)
            console.print_json(data=data)
        return 0

    permission_reports = configured_directory_permission_reports(updated, secure=True)
    data["permissions_safe"] = all(report.safe for report in permission_reports)
    data["permissions"] = [directory_permission_data(report) for report in permission_reports]
    summary = execute_directory_migration(updated, entries)
    save_config(updated, config_path)
    _refresh_discord_directory_access(updated, config_path)
    data["migration"] = {
        "automatic": not args.no_migrate,
        **asdict(summary),
        "conflicts": [],
    }
    if args.json:
        emit(envelope("directory", data=data, ok=summary.failed == 0))
    else:
        _print_directory_permissions(permission_reports)
        console.print_json(data=data)
    return 4 if summary.failed else 0


def _read_sql(args: argparse.Namespace) -> str | None:
    if args.execute is not None:
        return args.execute
    if args.file is not None:
        return args.file.read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        value = sys.stdin.read()
        return value if value.strip() else None
    return None


def _video_choices(plan: object) -> list[tuple[str, str]]:
    choices = [
        (item.remote_id, f"{course.display_name}: {item.title}")
        for course, item in plan.modules
        if (item.item_type.casefold() == "externaltool" or item.external_url) and item.published
    ]
    choices.extend(
        (remote.remote_id, f"{course.display_name}: {remote.name}")
        for course, remote in plan.files
        if (remote.content_type or "").casefold().startswith(("video/", "audio/"))
    )
    return choices


def _run_profile(config: Config, args: argparse.Namespace) -> int:
    headless = _headless_choice(args, config)
    labels: list[str]
    selected: str | None = None
    if args.name:
        labels, selected = select_profile(
            config,
            lambda _: args.name,
            headless=headless,
        )
    elif not (args.no_input or args.json) and interactive_terminal():
        labels, selected = select_profile(
            config,
            lambda available: choose_checkbox(
                "Choose eTL profile",
                tuple((label, label) for label in available),
                require_space=True,
            ),
            headless=headless,
        )
    else:
        labels = switch_profile(config, None, headless=headless)
    if args.json:
        emit(envelope("profile", data={"profiles": labels, "selected": selected}))
    else:
        console.print("Available eTL profiles:")
        for label in labels:
            marker = "[green]✓[/green]" if label == selected else " "
            console.print(f" {marker} {label}")
        if not labels:
            console.print("  [yellow]No switchable profiles were found.[/yellow]")
    return 0


def _select_videos(plan: object, args: argparse.Namespace) -> tuple[bool, tuple[str, ...] | None]:
    choices = _video_choices(plan)
    if "videos" not in plan.kinds or not choices:
        return True, None
    if args.video_ids:
        wanted = set(args.video_ids)
        unknown = wanted - {video_id for video_id, _ in choices}
        if unknown:
            raise ValueError(f"unknown video ID(s): {', '.join(sorted(unknown))}")
        return True, tuple(video_id for video_id, _ in choices if video_id in wanted)
    if args.yes:
        return True, tuple(video_id for video_id, _ in choices)
    if args.dry_run:
        return True, None
    if args.no_input or args.json or not interactive_terminal():
        return False, None
    selected = choose_checkboxes(
        "Select videos",
        tuple((label, video_id) for video_id, label in choices),
        include_all=True,
    )
    return True, selected


def _print_pull_summary(summary: PullSummary, root: Path) -> None:
    console.print(
        "Pull complete: "
        f"{summary.created} created, {summary.updated} updated, "
        f"{summary.unchanged} unchanged, {summary.skipped} skipped, "
        f"{summary.failed} failed."
    )
    if summary.artifacts:
        console.print("Saved artifacts:")
        for artifact in summary.artifacts:
            console.print(f"  {artifact}", markup=False, highlight=False)
    else:
        console.print(f"No artifacts written under {root}", markup=False, highlight=False)
    if summary.warnings:
        console.print("Warnings:")
        for warning in summary.warnings:
            code = str(warning.get("code") or "PULL_WARNING")
            message = str(warning.get("message") or "Unknown warning")
            console.print(f"  {code}: {message}", markup=False, highlight=False)


def _run_uninstall(config: Config, args: argparse.Namespace) -> int:
    config_path = (args.config or default_config_path()).expanduser().resolve()
    inventory = build_inventory(config, config_path)
    data = inventory_data(inventory)
    if args.dry_run:
        data["selected"] = {
            "delete_files": args.delete_files,
            "purge_config": args.purge_config or args.purge,
            "purge_state": args.purge_state or args.purge,
            "remove_shared_deps": args.remove_shared_deps,
        }
        if args.json:
            emit(envelope("uninstall", data=data))
        else:
            console.print_json(data=data)
        return 0

    delete_files = args.delete_files
    purge_config = args.purge_config or args.purge
    purge_state = args.purge_state or args.purge
    remove_shared = args.remove_shared_deps
    if not args.yes:
        if args.no_input or args.json:
            emit_error(
                "uninstall",
                "INPUT_REQUIRED",
                "Uninstall requires confirmation.",
                "Pass --yes after reviewing --dry-run.",
            ) if args.json else LOGGER.error("uninstall requires --yes in --no-input mode")
            return 5
        console.print_json(data=data)
        delete_files = not Confirm.ask("Keep downloaded course files?", default=True)
        purge_config = not Confirm.ask("Keep configuration?", default=True)
        purge_state = not Confirm.ask("Keep login, credentials, cache, and history?", default=True)
        remove_shared = Confirm.ask("Remove dependencies installed by snuetl setup?", default=False)
        if not Confirm.ask("Uninstall snuetl with these choices?", default=False):
            console.print("[yellow]Uninstall cancelled.[/yellow]")
            return 0
    summary = execute_uninstall(
        inventory,
        delete_files=delete_files,
        purge_config=purge_config,
        purge_state=purge_state,
        remove_shared_deps=remove_shared,
    )
    if args.json:
        emit(envelope("uninstall", data=asdict(summary), warnings=summary.warnings))
    else:
        console.print("[green]Uninstall completed.[/green]")
        for warning in summary.warnings:
            console.print(f"[yellow]! {warning['message']}[/yellow]")
    return 4 if summary.warnings else 0


def main(argv: list[str] | None = None) -> int:
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = build_parser().parse_args(actual_argv)
    except CliUsageError as exc:
        if _json_requested(actual_argv):
            emit_error("parse", "INVALID_ARGUMENT", str(exc), "Run snuetl --help for valid syntax.")
        else:
            LOGGER.error("%s", exc)
        return 3
    configure_logging(args.verbose)
    try:
        if args.command == "version":
            if args.json:
                info = get_version_info(check=args.check)
                emit(envelope("version", data=asdict(info)))
                return 0
            return _version(args.check)
        if args.command == "update":
            if args.no_input:
                # pipx is non-interactive, but make the intention explicit for agents.
                pass
            result = update_self()
            if args.json:
                emit(envelope("update", data={"version": result}))
            else:
                console.print(f"[green]✓[/green] snuetl {result}")
                console.print("Run [cyan]snuetl version[/cyan] to confirm the installed version.")
            return 0
        if args.command == "schema":
            if args.json:
                from .query import SCHEMA

                selected = [
                    asdict(item) for item in SCHEMA if args.table is None or item.name == args.table
                ]
                if not selected:
                    raise ValueError(f"unknown canonical table: {args.table}")
                emit(envelope("schema", data=selected))
            else:
                print_schema(args.table)
            return 0
        if args.command == "capabilities":
            if args.json:
                emit(envelope("capabilities", data=_capabilities()))
            else:
                console.print_json(data=_capabilities())
            return 0
        config = load_config(args.config)
        if args.command == "discord":
            return _run_discord_command(config, args)
        if args.command is None:
            auth_ready = (
                config.profile_dir.exists() and config.auth_state_path.exists()
            ) or load_credentials(config) is not None
            if not config.setup_complete or not auth_ready:
                if args.no_input or args.json:
                    if args.json:
                        emit_error(
                            "snuetl",
                            "INPUT_REQUIRED",
                            "Guided setup requires terminal input.",
                            "Run snuetl setup interactively.",
                        )
                    else:
                        LOGGER.error("guided setup requires terminal input")
                    return 5
                return run_setup(args.config)
            if args.json:
                emit(
                    envelope(
                        "snuetl",
                        data={"status": _status_data(config), "capabilities": _capabilities()},
                    )
                )
                return 0
            print_banner()
            _status(config)
            print_commands()
            return 0
        if args.command in {"setup", "onboard", "configure"}:
            if args.no_input or args.json:
                if args.json:
                    emit_error(
                        args.command,
                        "INPUT_REQUIRED",
                        "Guided setup requires terminal input.",
                        "Run snuetl setup without --json or --no-input.",
                    )
                else:
                    LOGGER.error("guided setup requires terminal input")
                return 5
            forced = None
            if args.headless:
                forced = True
            elif args.headed:
                forced = False
            return run_setup(args.config, force_headless=forced)
        if args.command == "headless":
            mode = args.mode
            if mode is None and not (args.no_input or args.json):
                enabled = Confirm.ask(
                    "Use headless Chromium by default?",
                    default=config.headless,
                )
                mode = "on" if enabled else "off"
            updated = config
            if mode is not None:
                updated = replace(config, headless=mode == "on")
                save_config(updated, args.config)
            data = {
                "headless": updated.headless,
                "browser_mode": "headless" if updated.headless else "visible",
            }
            emit(envelope("headless", data=data)) if args.json else console.print_json(data=data)
            return 0
        if args.command == "login":
            if args.no_input or args.json:
                if args.json:
                    emit_error(
                        "login",
                        "INPUT_REQUIRED",
                        "Login may require credentials and 2FA.",
                        "Run snuetl login interactively.",
                    )
                else:
                    LOGGER.error("login requires terminal input")
                return 5
            return _login(config, headless=_headless_choice(args, config), config_path=args.config)
        if args.command == "sync":
            summary = synchronize(config, headless=_headless_choice(args, config))
            if args.json:
                emit(envelope("sync", data=asdict(summary)))
                return 4 if summary.failed else 0
            LOGGER.info(
                "sync complete courses=%d downloaded=%d updated=%d unchanged=%d failed=%d",
                summary.courses,
                summary.downloaded,
                summary.updated,
                summary.unchanged,
                summary.failed,
            )
            return 1 if summary.failed else 0
        if args.command == "status":
            if args.json:
                emit(envelope("status", data=_status_data(config)))
                return 0
            return _status(config)
        if args.command == "profile":
            return _run_profile(config, args)
        if args.command == "doctor":
            if args.json:
                data = _doctor_data(config, args.config)
                emit(envelope("doctor", data=data, ok=bool(data["ready"])))
                return 0 if data["ready"] else 1
            return run_doctor(config, args.config)
        if args.command == "refresh":
            summary = refresh_catalog(config, headless=_headless_choice(args, config))
            if args.json:
                emit(envelope("refresh", data=asdict(summary)))
                return 0
            table = Table(title="Catalog refresh complete")
            table.add_column("Courses", justify="right")
            table.add_column("Files", justify="right")
            table.add_column("Articles", justify="right")
            table.add_column("Assignments", justify="right")
            table.add_column("Quizzes", justify="right")
            table.add_row(
                str(summary.courses),
                str(summary.files),
                str(summary.articles),
                str(summary.assignments),
                str(summary.quizzes),
            )
            console.print(table)
            return 0
        if args.command in {"query", "sql"}:
            if args.refresh:
                summary = refresh_catalog(config, headless=_headless_choice(args, config))
                LOGGER.info(
                    "catalog refreshed courses=%d files=%d articles=%d assignments=%d quizzes=%d",
                    summary.courses,
                    summary.files,
                    summary.articles,
                    summary.assignments,
                    summary.quizzes,
                )
            sql = _read_sql(args)
            if sql is None:
                if sys.stdin.isatty() and not args.json:
                    return run_sql_shell(
                        config.database_path,
                        refresh=lambda: refresh_catalog(
                            config, headless=_headless_choice(args, config)
                        ),
                        output_format=args.format,
                        limit=args.limit,
                    )
                if args.json:
                    emit_error(
                        args.command,
                        "INPUT_REQUIRED",
                        "No SQL statement was supplied.",
                        "Use --execute, --file, or pipe SQL on stdin.",
                    )
                    return 5
                print_schema()
                return 0
            result = execute_query(config.database_path, sql, limit=args.limit)
            if args.json:
                emit(envelope(args.command, data=_query_data(result)))
            else:
                print_query_result(result, args.format)
            return 0
        if args.command == "pull":
            # Videos are intentionally opt-in; `pull all` handles course files,
            # articles, and syllabi only.
            kinds = ("files", "articles", "syllabus") if args.kind == "all" else (args.kind,)
            if args.max_height <= 0 or args.jobs <= 0:
                raise ValueError("--max-height and --jobs must be positive")
            # Normalize folders produced by older releases before this pull so
            # files, articles, syllabi, and videos share one course directory.
            pull_config = replace(config, headless=_headless_choice(args, config))
            repair_legacy_layout(validate_managed_root(args.directory or config.download_dir))
            plan = discover_pull_plan(
                pull_config,
                kinds,
                course_selectors=tuple(args.course),
                semesters=tuple(args.semester),
            )
            selected_ok, selected_ids = _select_videos(plan, args)
            if not selected_ok:
                if args.json:
                    choices = _video_choices(plan)
                    emit_error(
                        "pull",
                        "INPUT_REQUIRED",
                        "Video selection is required.",
                        "Pass --video-id for each selected video or --yes for all; available IDs: "
                        + ", ".join(video_id for video_id, _ in choices),
                    )
                else:
                    LOGGER.error(
                        "video selection required; use the interactive selector or pass --video-id/--yes"
                    )
                return 5
            if selected_ids is not None:
                plan = replace(plan, selected_video_ids=selected_ids)
            root = validate_managed_root(args.directory or config.download_dir)
            summary = execute_pull(
                pull_config,
                plan,
                root=root,
                dry_run=args.dry_run,
                force=args.force,
                max_height=None if args.best else args.max_height,
                captions=not args.no_captions,
                jobs=args.jobs,
                progress=(
                    None
                    if args.json
                    else lambda message: console.print(message, markup=False, highlight=False)
                ),
            )
            data = {"plan": plan_data(plan), "result": asdict(summary), "managed_root": str(root)}
            if args.json:
                emit(envelope("pull", data=data, warnings=summary.warnings))
            elif args.dry_run:
                console.print_json(data=data)
            else:
                _print_pull_summary(summary, root)
            return 4 if summary.partial else 0
        if args.command == "directory":
            return _run_directory(config, args)
        if args.command in {"courses", "files", "articles", "assignments", "quizzes"}:
            result = inspect_catalog(
                config,
                kind=args.command,
                course_query=getattr(args, "course", None),
                headless=_headless_choice(args, config),
            )
            if args.json:
                emit(envelope(args.command, data=_catalog_data(result, args.command)))
            else:
                print_catalog(result, args.command)
            return 0
        if args.command == "logout":
            if (args.no_input or args.json) and not args.yes:
                if args.json:
                    emit_error(
                        "logout",
                        "INPUT_REQUIRED",
                        "Logout requires confirmation.",
                        "Pass --yes to remove authentication.",
                    )
                return 5
            result = _logout(config, args.yes)
            if args.json:
                emit(envelope("logout", data={"authentication_removed": True}))
            return result
        if args.command == "uninstall":
            return _run_uninstall(config, args)
        raise AssertionError(f"unexpected command {args.command}")
    except (Exception, KeyboardInterrupt) as exc:
        return handle_cli_exception(args, exc)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
