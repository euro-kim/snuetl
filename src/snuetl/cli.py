from __future__ import annotations

import argparse
import logging
import platform
import shutil
import sys
from dataclasses import asdict, replace
from pathlib import Path

from rich.prompt import Confirm
from rich.table import Table

from . import __version__
from .agent import emit, emit_error, envelope
from .catalog import inspect_catalog, refresh_catalog
from .config import Config, ConfigError, default_config_path, load_config, save_config
from .credentials import load_credentials
from .directory_manager import (
    execute_directory_migration,
    plan_directory_migration,
    validate_managed_root,
)
from .errors import AuthenticationRequired, SnuetlError
from .logging_utils import configure_logging, redact
from .onboarding import (
    browser_available,
    display_available,
    ensure_browser,
    prompt_login,
    run_doctor,
    run_setup,
)
from .profile import profile_lock
from .puller import discover_pull_plan, execute_pull, plan_data
from .query import execute_query
from .scheduler import timer_is_enabled
from .sql_shell import run_sql_shell
from .state import StateStore
from .syncer import synchronize
from .uninstaller import build_inventory, execute_uninstall, inventory_data
from .ui import (
    console,
    print_banner,
    print_catalog,
    print_commands,
    print_query_result,
    print_schema,
)
from .versioning import get_version_info, update_self

LOGGER = logging.getLogger(__name__)


class CliUsageError(ValueError):
    pass


class SnuetlArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliUsageError(message)


def _parser() -> argparse.ArgumentParser:
    parser = SnuetlArgumentParser(
        prog="snuetl", description="Synchronize files from Seoul National University eTL"
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="configuration file (default: ~/.config/snuetl/config.toml)",
    )
    parser.add_argument("--verbose", action="store_true", help="enable diagnostic logs")
    parser.add_argument("--json", action="store_true", help="emit a versioned JSON envelope")
    parser.add_argument(
        "--no-input", action="store_true", help="never prompt; fail when input is required"
    )
    parser.add_argument("--version", action="version", version=f"snuetl {__version__}")
    subparsers = parser.add_subparsers(dest="command", parser_class=SnuetlArgumentParser)

    def add_agent_flags(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS
        )
        command.add_argument(
            "--no-input",
            action="store_true",
            default=argparse.SUPPRESS,
            help=argparse.SUPPRESS,
        )

    def add_display_mode(command: argparse.ArgumentParser) -> None:
        group = command.add_mutually_exclusive_group()
        group.add_argument("--headless", action="store_true", help="do not show Chromium")
        group.add_argument("--headed", action="store_true", help="show Chromium")

    for name in ("setup", "onboard", "configure"):
        setup = subparsers.add_parser(name, help="run guided setup")
        add_display_mode(setup)
        add_agent_flags(setup)

    login = subparsers.add_parser("login", help="refresh trusted-browser authentication")
    add_display_mode(login)
    add_agent_flags(login)

    sync = subparsers.add_parser("sync", help="download new and revised course files")
    sync.add_argument("--headed", action="store_true", help="show the browser for troubleshooting")
    add_agent_flags(sync)

    status = subparsers.add_parser("status", help="show local enrollment and last-sync state")
    add_agent_flags(status)
    doctor = subparsers.add_parser("doctor", help="check dependencies and configuration")
    add_agent_flags(doctor)
    schema = subparsers.add_parser("schema", help="show canonical SQL tables and fields")
    schema.add_argument("table", nargs="?", help="optional canonical table name")
    add_agent_flags(schema)
    version = subparsers.add_parser("version", help="show version and installation source")
    version.add_argument("--check", action="store_true", help="check for a published update")
    add_agent_flags(version)
    update = subparsers.add_parser("update", help="update snuetl using pipx")
    add_agent_flags(update)
    capabilities = subparsers.add_parser(
        "capabilities", help="describe the stable CLI contract for agents"
    )
    add_agent_flags(capabilities)

    refresh = subparsers.add_parser(
        "refresh", help="cache all paginated catalog data for SQL queries"
    )
    add_display_mode(refresh)
    add_agent_flags(refresh)

    for name in ("query", "sql"):
        query = subparsers.add_parser(name, help="open or execute read-only catalog SQL")
        source = query.add_mutually_exclusive_group()
        source.add_argument("--execute", "-e", help="execute one SQL statement")
        source.add_argument("--file", type=Path, help="execute SQL read from a file")
        query.add_argument(
            "--refresh",
            action="store_true",
            help="refresh every remote catalog table before querying",
        )
        query.add_argument(
            "--format",
            choices=("table", "json", "jsonl", "csv"),
            default="table",
            help="output format (default: table)",
        )
        query.add_argument(
            "--limit",
            type=int,
            default=200,
            help="maximum output rows; 0 means all (default: 200)",
        )
        add_display_mode(query)
        add_agent_flags(query)

    pull = subparsers.add_parser("pull", help="pull course content into the managed directory")
    pull.add_argument(
        "kind", nargs="?", choices=("all", "files", "articles", "syllabus", "videos"), default="all"
    )
    pull.add_argument("--course", action="append", default=[], help="course ID or unique title")
    pull.add_argument("--semester", action="append", default=[], help="canonical semester code")
    pull.add_argument("--directory", type=Path, help="override the managed root for this pull")
    pull.add_argument("--dry-run", action="store_true", help="discover and estimate without writing")
    pull.add_argument("--yes", action="store_true", help="accept bulk video download consent")
    pull.add_argument("--force", action="store_true", help="overwrite locally edited generated content")
    pull.add_argument("--best", action="store_true", help="download the best available video quality")
    pull.add_argument("--max-height", type=int, default=1080, help="maximum video height")
    pull.add_argument("--no-captions", action="store_true", help="do not download video captions")
    pull.add_argument("--jobs", type=int, default=1, help="video download concurrency (default: 1)")
    add_agent_flags(pull)

    directory = subparsers.add_parser("directory", help="show or configure the managed pull root")
    directory.add_argument("path", nargs="?", type=Path)
    directory.add_argument("--move", action="store_true", help="move tracked files to the new layout")
    directory.add_argument("--dry-run", action="store_true", help="show the migration without changing it")
    directory.add_argument("--yes", action="store_true", help="confirm the requested migration")
    add_agent_flags(directory)

    for name, description in (
        ("courses", "list active courses"),
        ("files", "list files without downloading them"),
        ("articles", "list announcements and course-page titles"),
        ("assignments", "list assignments and due dates"),
    ):
        command = subparsers.add_parser(name, help=description)
        if name != "courses":
            command.add_argument(
                "course",
                nargs="?",
                help="course ID or a unique part of its name; defaults to all courses",
            )
        add_display_mode(command)
        add_agent_flags(command)

    logout = subparsers.add_parser(
        "logout", help="remove browser authentication and saved credentials"
    )
    logout.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    add_agent_flags(logout)

    uninstall = subparsers.add_parser("uninstall", help="remove snuetl and selected local data")
    uninstall.add_argument("--dry-run", action="store_true", help="show everything that would be removed")
    uninstall.add_argument("--yes", action="store_true", help="confirm using the selected safe defaults")
    uninstall.add_argument("--delete-files", action="store_true", help="delete tracked pulled course content")
    uninstall.add_argument("--purge-config", action="store_true", help="remove configuration")
    uninstall.add_argument("--purge-state", action="store_true", help="remove login, credentials, and history")
    uninstall.add_argument("--purge", action="store_true", help="remove both configuration and state")
    uninstall.add_argument(
        "--remove-shared-deps",
        action="store_true",
        help="remove Chromium/FFmpeg only when recorded as installed by snuetl",
    )
    add_agent_flags(uninstall)
    return parser


def _headless_choice(args: argparse.Namespace) -> bool:
    if getattr(args, "headed", False):
        return False
    if getattr(args, "headless", False):
        return True
    return not display_available()


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
        remote_files, articles, assignments = store.catalog_counts()
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
            f"{articles} articles, {assignments} assignments"
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
        "last_sync": None,
        "courses": 0,
        "remote_files": 0,
        "articles": 0,
        "assignments": 0,
        "downloaded_files": 0,
    }
    if not config.database_path.exists():
        return data
    with StateStore(config.database_path) as store:
        courses, downloaded = store.counts()
        remote_files, articles, assignments = store.catalog_counts()
        last = store.last_run()
        data.update(
            courses=courses,
            remote_files=remote_files,
            articles=articles,
            assignments=assignments,
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
            "inspect": ["courses", "files", "articles", "assignments"],
            "sql": {"interactive": "snuetl sql", "noninteractive": ["--execute", "--file", "stdin"]},
            "pull": ["files", "articles", "syllabus", "videos", "all"],
            "maintenance": ["refresh", "directory", "status", "doctor", "version", "update", "uninstall"],
        },
        "agent_flags": ["--json", "--no-input", "--yes", "--dry-run"],
        "exit_codes": {"0": "success", "1": "command failure", "2": "authentication required", "3": "configuration or local-state error", "4": "partial result", "5": "input required"},
    }


def _doctor_data(config: Config, config_path: Path | None) -> dict[str, object]:
    path = config_path or default_config_path()
    machine = platform.machine() or "unknown"
    browser_ok, browser_detail = browser_available(config)
    saved = load_credentials(config)
    checks = [
        {"name": "configuration", "ok": path.exists(), "detail": str(path)},
        {
            "name": "architecture",
            "ok": machine.casefold() in {"x86_64", "amd64", "aarch64", "arm64"},
            "detail": f"{platform.system()} {machine}",
        },
        {"name": "chromium", "ok": browser_ok, "detail": browser_detail},
        {"name": "ffmpeg", "ok": shutil.which("ffmpeg") is not None, "detail": shutil.which("ffmpeg") or "not installed"},
        {
            "name": "authentication_state",
            "ok": config.profile_dir.exists() and config.auth_state_path.exists(),
            "detail": str(config.state_dir),
        },
        {"name": "download_directory", "ok": config.download_dir.exists(), "detail": str(config.download_dir)},
        {"name": "automatic_relogin", "ok": saved is not None, "detail": saved.username if saved else "disabled", "optional": True},
    ]
    if platform.system() == "Linux":
        checks.append({"name": "systemd_timer", "ok": timer_is_enabled(), "detail": "15-minute timer", "optional": True})
    required = [check for check in checks if not check.get("optional")]
    return {"ready": all(bool(check["ok"]) for check in required), "checks": checks}


def _read_sql(args: argparse.Namespace) -> str | None:
    if args.execute is not None:
        return args.execute
    if args.file is not None:
        return args.file.read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        value = sys.stdin.read()
        return value if value.strip() else None
    return None


def _confirm_pull(plan: object, args: argparse.Namespace) -> bool:
    count = len(plan.video_items) + len(plan.uploaded_media)
    if "videos" not in plan.kinds or not count or args.dry_run or args.yes:
        return True
    if args.no_input or args.json:
        return False
    known = sum(remote.size or 0 for _, remote in plan.uploaded_media)
    size = f"; at least {known / (1024 ** 3):.2f} GiB is known" if known else ""
    return Confirm.ask(f"Download {count} video item(s){size}?", default=False)


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
            emit_error("uninstall", "INPUT_REQUIRED", "Uninstall requires confirmation.", "Pass --yes after reviewing --dry-run.") if args.json else LOGGER.error("uninstall requires --yes in --no-input mode")
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
        args = _parser().parse_args(actual_argv)
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

                selected = [asdict(item) for item in SCHEMA if args.table is None or item.name == args.table]
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
        if args.command is None:
            auth_ready = (
                config.profile_dir.exists() and config.auth_state_path.exists()
            ) or load_credentials(config) is not None
            if not config.setup_complete or not auth_ready:
                if args.no_input or args.json:
                    if args.json:
                        emit_error("snuetl", "INPUT_REQUIRED", "Guided setup requires terminal input.", "Run snuetl setup interactively.")
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
                    emit_error(args.command, "INPUT_REQUIRED", "Guided setup requires terminal input.", "Run snuetl setup without --json or --no-input.")
                else:
                    LOGGER.error("guided setup requires terminal input")
                return 5
            forced = None
            if args.headless:
                forced = True
            elif args.headed:
                forced = False
            return run_setup(args.config, force_headless=forced)
        if args.command == "login":
            if args.no_input or args.json:
                if args.json:
                    emit_error("login", "INPUT_REQUIRED", "Login may require credentials and 2FA.", "Run snuetl login interactively.")
                else:
                    LOGGER.error("login requires terminal input")
                return 5
            return _login(config, headless=_headless_choice(args), config_path=args.config)
        if args.command == "sync":
            summary = synchronize(config, headless=False if args.headed else None)
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
        if args.command == "doctor":
            if args.json:
                data = _doctor_data(config, args.config)
                emit(envelope("doctor", data=data, ok=bool(data["ready"])))
                return 0 if data["ready"] else 1
            return run_doctor(config, args.config)
        if args.command == "refresh":
            summary = refresh_catalog(config, headless=_headless_choice(args))
            if args.json:
                emit(envelope("refresh", data=asdict(summary)))
                return 0
            table = Table(title="Catalog refresh complete")
            table.add_column("Courses", justify="right")
            table.add_column("Files", justify="right")
            table.add_column("Articles", justify="right")
            table.add_column("Assignments", justify="right")
            table.add_row(
                str(summary.courses),
                str(summary.files),
                str(summary.articles),
                str(summary.assignments),
            )
            console.print(table)
            return 0
        if args.command in {"query", "sql"}:
            if args.refresh:
                summary = refresh_catalog(config, headless=_headless_choice(args))
                LOGGER.info(
                    "catalog refreshed courses=%d files=%d articles=%d assignments=%d",
                    summary.courses,
                    summary.files,
                    summary.articles,
                    summary.assignments,
                )
            sql = _read_sql(args)
            if sql is None:
                if sys.stdin.isatty() and not args.json:
                    return run_sql_shell(
                        config.database_path,
                        refresh=lambda: refresh_catalog(
                            config, headless=_headless_choice(args)
                        ),
                        output_format=args.format,
                        limit=args.limit,
                    )
                if args.json:
                    emit_error(args.command, "INPUT_REQUIRED", "No SQL statement was supplied.", "Use --execute, --file, or pipe SQL on stdin.")
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
            kinds = ("files", "articles", "syllabus", "videos") if args.kind == "all" else (args.kind,)
            if args.max_height <= 0 or args.jobs <= 0:
                raise ValueError("--max-height and --jobs must be positive")
            plan = discover_pull_plan(
                config,
                kinds,
                course_selectors=tuple(args.course),
                semesters=tuple(args.semester),
            )
            if not _confirm_pull(plan, args):
                if args.json:
                    emit_error("pull", "INPUT_REQUIRED", "Video downloads require confirmation.", "Review with --dry-run, then pass --yes.")
                else:
                    LOGGER.error("video downloads require confirmation; pass --yes")
                return 5
            root = validate_managed_root(args.directory or config.download_dir)
            summary = execute_pull(
                config,
                plan,
                root=root,
                dry_run=args.dry_run,
                force=args.force,
                max_height=None if args.best else args.max_height,
                captions=not args.no_captions,
                jobs=args.jobs,
            )
            data = {"plan": plan_data(plan), "result": asdict(summary), "managed_root": str(root)}
            if args.json:
                emit(envelope("pull", data=data, warnings=summary.warnings))
            else:
                console.print_json(data=data)
            return 4 if summary.partial else 0
        if args.command == "directory":
            if args.path is None:
                data = {"managed_root": str(config.download_dir)}
                emit(envelope("directory", data=data)) if args.json else console.print(str(config.download_dir))
                return 0
            root = validate_managed_root(args.path)
            entries = plan_directory_migration(config, root) if args.move else []
            if args.move and entries and not args.dry_run and not args.yes:
                if args.no_input or args.json:
                    if args.json:
                        emit_error("directory", "INPUT_REQUIRED", "Moving tracked files requires confirmation.", "Review with --dry-run, then pass --yes.")
                    return 5
                if not Confirm.ask(f"Move {len(entries)} tracked artifact(s) to {root}?", default=False):
                    return 0
            summary = execute_directory_migration(config, entries, dry_run=args.dry_run)
            if not args.dry_run:
                save_config(replace(config, download_dir=root), args.config)
            data = {
                "managed_root": str(root),
                "migration": asdict(summary),
                "entries": [
                    {
                        "record_type": item.record_type,
                        "course_id": item.course_id,
                        "source_id": item.source_id,
                        "source": str(item.source),
                        "destination": str(item.destination),
                    }
                    for item in entries
                ],
            }
            emit(envelope("directory", data=data)) if args.json else console.print_json(data=data)
            return 4 if summary.failed else 0
        if args.command in {"courses", "files", "articles", "assignments"}:
            result = inspect_catalog(
                config,
                kind=args.command,
                course_query=getattr(args, "course", None),
                headless=_headless_choice(args),
            )
            if args.json:
                emit(envelope(args.command, data=_catalog_data(result, args.command)))
            else:
                print_catalog(result, args.command)
            return 0
        if args.command == "logout":
            if (args.no_input or args.json) and not args.yes:
                if args.json:
                    emit_error("logout", "INPUT_REQUIRED", "Logout requires confirmation.", "Pass --yes to remove authentication.")
                return 5
            result = _logout(config, args.yes)
            if args.json:
                emit(envelope("logout", data={"authentication_removed": True}))
            return result
        if args.command == "uninstall":
            return _run_uninstall(config, args)
        raise AssertionError(f"unexpected command {args.command}")
    except AuthenticationRequired as exc:
        if getattr(args, "json", False):
            emit_error(args.command or "snuetl", "AUTHENTICATION_REQUIRED", str(redact(exc)), "Run snuetl login interactively.")
        else:
            LOGGER.error("authentication required: %s", exc)
        return 2
    except (ConfigError, OSError, ValueError) as exc:
        if getattr(args, "json", False):
            emit_error(args.command or "snuetl", "LOCAL_STATE_ERROR", str(redact(exc)), "Check the command arguments and run snuetl doctor.")
        else:
            LOGGER.error("configuration or local-state error: %s", exc)
        return 3
    except SnuetlError as exc:
        if getattr(args, "json", False):
            emit_error(args.command or "snuetl", "COMMAND_FAILED", str(redact(exc)), "Retry with --verbose or run snuetl doctor.")
        else:
            LOGGER.error("command failed: %s", exc)
        return 1
    except Exception as exc:
        # Browser installation/launch errors and unexpected LMS changes should
        # be concise in scheduled logs. --verbose enables the underlying
        # libraries' diagnostic output without exposing terminal credentials.
        if getattr(args, "json", False):
            emit_error(args.command or "snuetl", "UNEXPECTED_FAILURE", str(redact(exc)), "Retry with --verbose and report the failure if it persists.")
        else:
            LOGGER.error("unexpected failure: %s", exc)
        return 1
    except KeyboardInterrupt:
        if getattr(args, "json", False):
            emit_error(args.command or "snuetl", "CANCELLED", "Cancelled by user.", "Run the command again when ready.")
        else:
            LOGGER.error("cancelled")
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
