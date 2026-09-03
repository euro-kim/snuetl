from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from rich.prompt import Confirm
from rich.table import Table

from . import __version__
from .catalog import inspect_catalog, refresh_catalog
from .config import Config, ConfigError, load_config, save_config
from .errors import AuthenticationRequired, SnuetlError
from .logging_utils import configure_logging
from .onboarding import display_available, ensure_browser, prompt_login, run_doctor, run_setup
from .profile import profile_lock
from .query import execute_query
from .state import StateStore
from .syncer import synchronize
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="snuetl", description="Synchronize files from Seoul National University eTL"
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="configuration file (default: ~/.config/snuetl/config.toml)",
    )
    parser.add_argument("--verbose", action="store_true", help="enable diagnostic logs")
    parser.add_argument("--version", action="version", version=f"snuetl {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    def add_display_mode(command: argparse.ArgumentParser) -> None:
        group = command.add_mutually_exclusive_group()
        group.add_argument("--headless", action="store_true", help="do not show Chromium")
        group.add_argument("--headed", action="store_true", help="show Chromium")

    for name in ("setup", "onboard", "configure"):
        setup = subparsers.add_parser(name, help="run guided setup")
        add_display_mode(setup)

    login = subparsers.add_parser("login", help="refresh trusted-browser authentication")
    add_display_mode(login)

    sync = subparsers.add_parser("sync", help="download new and revised course files")
    sync.add_argument("--headed", action="store_true", help="show the browser for troubleshooting")

    subparsers.add_parser("status", help="show local enrollment and last-sync state")
    subparsers.add_parser("doctor", help="check dependencies and configuration")
    subparsers.add_parser("schema", help="show canonical SQL tables and fields")
    version = subparsers.add_parser("version", help="show version and installation source")
    version.add_argument("--check", action="store_true", help="check for a published update")
    subparsers.add_parser("update", help="update snuetl using pipx")

    refresh = subparsers.add_parser(
        "refresh", help="cache all paginated catalog data for SQL queries"
    )
    add_display_mode(refresh)

    query = subparsers.add_parser("query", help="run read-only SQL over cached eTL data")
    query.add_argument("sql", nargs="?", help="quoted SQL statement, or '-' to read stdin")
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

    logout = subparsers.add_parser("logout", help="remove the dedicated trusted-browser profile")
    logout.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
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
    console.print("[green]Trusted browser enrollment is ready.[/green]")
    return 0


def _status(config: Config) -> int:
    profile_dir = config.profile_dir
    database_path = config.database_path
    auth_ready = profile_dir.exists() and config.auth_state_path.exists()
    console.print(
        f"Authentication: {'[green]ready[/green]' if auth_ready else '[yellow]login required[/yellow]'} "
        f"[dim]({config.state_dir})[/dim]"
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
    auth_paths = (config.auth_state_path, config.session_metadata_path)
    if not profile_dir.exists() and not any(path.exists() for path in auth_paths):
        LOGGER.info("dedicated browser authentication is already absent")
        return 0
    if not confirmed and not Confirm.ask(
        f"Remove trusted-browser authentication under {config.state_dir}? This cannot be undone.",
        default=False,
    ):
        LOGGER.info("logout cancelled")
        return 0
    with profile_lock(config.lock_path):
        if profile_dir.exists():
            shutil.rmtree(profile_dir)
        for path in auth_paths:
            path.unlink(missing_ok=True)
    LOGGER.info("removed browser authentication; downloaded files and sync history remain")
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging(args.verbose)
    try:
        if args.command == "version":
            return _version(args.check)
        if args.command == "update":
            result = update_self()
            console.print(f"[green]✓[/green] snuetl {result}")
            console.print("Run [cyan]snuetl version[/cyan] to confirm the installed version.")
            return 0
        if args.command == "schema":
            print_schema()
            return 0
        config = load_config(args.config)
        if args.command is None:
            auth_ready = config.profile_dir.exists() and config.auth_state_path.exists()
            if not config.setup_complete or not auth_ready:
                return run_setup(args.config)
            print_banner()
            _status(config)
            print_commands()
            return 0
        if args.command in {"setup", "onboard", "configure"}:
            forced = None
            if args.headless:
                forced = True
            elif args.headed:
                forced = False
            return run_setup(args.config, force_headless=forced)
        if args.command == "login":
            return _login(config, headless=_headless_choice(args), config_path=args.config)
        if args.command == "sync":
            summary = synchronize(config, headless=False if args.headed else None)
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
            return _status(config)
        if args.command == "doctor":
            return run_doctor(config, args.config)
        if args.command == "refresh":
            summary = refresh_catalog(config, headless=_headless_choice(args))
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
        if args.command == "query":
            if args.sql is None:
                print_schema()
                return 0
            if args.refresh:
                summary = refresh_catalog(config, headless=_headless_choice(args))
                LOGGER.info(
                    "catalog refreshed courses=%d files=%d articles=%d assignments=%d",
                    summary.courses,
                    summary.files,
                    summary.articles,
                    summary.assignments,
                )
            sql = sys.stdin.read() if args.sql == "-" else args.sql
            result = execute_query(config.database_path, sql, limit=args.limit)
            print_query_result(result, args.format)
            return 0
        if args.command in {"courses", "files", "articles", "assignments"}:
            result = inspect_catalog(
                config,
                kind=args.command,
                course_query=getattr(args, "course", None),
                headless=_headless_choice(args),
            )
            print_catalog(result, args.command)
            return 0
        if args.command == "logout":
            return _logout(config, args.yes)
        raise AssertionError(f"unexpected command {args.command}")
    except AuthenticationRequired as exc:
        LOGGER.error("authentication required: %s", exc)
        return 2
    except (ConfigError, OSError, ValueError) as exc:
        LOGGER.error("configuration or local-state error: %s", exc)
        return 3
    except SnuetlError as exc:
        LOGGER.error("command failed: %s", exc)
        return 1
    except Exception as exc:
        # Browser installation/launch errors and unexpected LMS changes should
        # be concise in scheduled logs. --verbose enables the underlying
        # libraries' diagnostic output without exposing terminal credentials.
        LOGGER.error("unexpected failure: %s", exc)
        return 1
    except KeyboardInterrupt:
        LOGGER.error("cancelled")
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
