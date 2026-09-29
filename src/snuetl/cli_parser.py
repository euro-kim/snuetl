from __future__ import annotations

import argparse
from pathlib import Path
from typing import NoReturn

from . import __version__


class CliUsageError(ValueError):
    pass


class SnuetlArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise CliUsageError(message)


def build_parser() -> argparse.ArgumentParser:
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
            "--no-input", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS
        )

    def add_display_mode(command: argparse.ArgumentParser) -> None:
        group = command.add_mutually_exclusive_group()
        group.add_argument("--headless", action="store_true", help="do not show the browser")
        group.add_argument("--headed", action="store_true", help="show the browser")

    for name in ("setup", "onboard", "configure"):
        command = subparsers.add_parser(name, help="run guided setup")
        add_display_mode(command)
        add_agent_flags(command)

    login = subparsers.add_parser("login", help="refresh trusted-browser authentication")
    add_display_mode(login)
    add_agent_flags(login)

    api = subparsers.add_parser("api", help="manage personal Canvas API access")
    add_agent_flags(api)
    api_subparsers = api.add_subparsers(dest="api_command", parser_class=SnuetlArgumentParser)
    for name in ("setup", "rotate"):
        action = api_subparsers.add_parser(name, help=f"{name} Canvas API access")
        add_display_mode(action)
        add_agent_flags(action)
    add_agent_flags(api_subparsers.add_parser("status", help="show Canvas API access status"))

    headless = subparsers.add_parser("headless", help="show or set the default browser mode")
    headless.add_argument(
        "mode",
        nargs="?",
        choices=("on", "off"),
        help="use headless (on) or visible (off) browser automation by default",
    )
    add_agent_flags(headless)

    sync = subparsers.add_parser("sync", help="download new and revised course files")
    add_display_mode(sync)
    add_agent_flags(sync)

    for name, help_text in (
        ("status", "show local enrollment and last-sync state"),
        ("doctor", "check dependencies, configuration, and runtime"),
    ):
        add_agent_flags(subparsers.add_parser(name, help=help_text))

    schema = subparsers.add_parser("schema", help="show canonical SQL tables and fields")
    schema.add_argument("table", nargs="?", help="optional canonical table name")
    add_agent_flags(schema)
    version = subparsers.add_parser("version", help="show version and installation source")
    version.add_argument("--check", action="store_true", help="check for a published update")
    add_agent_flags(version)
    add_agent_flags(subparsers.add_parser("update", help="update snuetl using pipx"))
    add_agent_flags(
        subparsers.add_parser("capabilities", help="describe the stable CLI contract for agents")
    )

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
        refresh_mode = query.add_mutually_exclusive_group()
        refresh_mode.add_argument(
            "--refresh",
            action="store_true",
            help="require a successful remote refresh before querying (also the default attempt)",
        )
        refresh_mode.add_argument(
            "--no-refresh",
            action="store_true",
            help="query local cached data without contacting Canvas",
        )
        query.add_argument(
            "--format",
            choices=("table", "json", "jsonl", "csv"),
            default="table",
            help="output format (default: table)",
        )
        query.add_argument(
            "--limit", type=int, default=200, help="maximum output rows; 0 means all (default: 200)"
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
    pull.add_argument(
        "--dry-run", action="store_true", help="discover and estimate without writing"
    )
    pull.add_argument("--yes", action="store_true", help="accept bulk video download consent")
    pull.add_argument(
        "--video-id",
        "--video",
        dest="video_ids",
        action="append",
        default=[],
        help="select a video by ID (repeatable; for Hermes and unattended use)",
    )
    pull.add_argument(
        "--force", action="store_true", help="overwrite locally edited generated content"
    )
    pull.add_argument(
        "--best", action="store_true", help="download the best available video quality"
    )
    pull.add_argument("--max-height", type=int, default=1080, help="maximum video height")
    pull.add_argument("--no-captions", action="store_true", help="do not download video captions")
    pull.add_argument("--jobs", type=int, default=1, help="video download concurrency (default: 1)")
    add_display_mode(pull)
    add_agent_flags(pull)

    profile = subparsers.add_parser("profile", help="list or switch the active eTL identity")
    profile.add_argument("name", nargs="?", help="profile label (substring match is allowed)")
    add_display_mode(profile)
    add_agent_flags(profile)

    directory = subparsers.add_parser(
        "directory", help="audit or securely configure pull roots and routing rules"
    )
    directory.add_argument(
        "operation",
        nargs="?",
        help="a new default path, or one of: set, videos, list, bind, unbind",
    )
    directory.add_argument("value", nargs="?", help="path or route name for the selected operation")
    directory.add_argument(
        "--default",
        dest="use_default",
        action="store_true",
        help="with 'videos', store videos under the regular course root",
    )
    directory.add_argument("--name", help="stable name for a binding (existing names are replaced)")
    directory.add_argument("--course", help="limit a binding to a cached course ID or unique title")
    directory.add_argument("--semester", help="limit a binding to a canonical semester code")
    directory.add_argument(
        "--kind",
        choices=("files", "articles", "syllabus", "videos"),
        help="limit a binding to one content kind",
    )
    directory.add_argument("--remote-folder", help="limit a file binding to an eTL folder prefix")
    directory.add_argument(
        "--no-migrate",
        action="store_true",
        help="change routing without moving already tracked artifacts",
    )
    directory.add_argument(
        "--move", action="store_true", help="compatibility flag; migration is now automatic"
    )
    directory.add_argument(
        "--dry-run", action="store_true", help="show the migration without changing it"
    )
    directory.add_argument(
        "--yes", action="store_true", help="compatibility flag; explicit changes no longer prompt"
    )
    add_agent_flags(directory)

    for name, description in (
        ("courses", "list active courses"),
        ("files", "list files without downloading them"),
        ("articles", "list announcement and course-page titles"),
        ("assignments", "list assignment titles and due dates"),
        ("quizzes", "list quiz titles and due dates"),
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

    for name, description in (
        ("upcoming", "list upcoming incomplete work"),
        ("missing", "list overdue missing submissions"),
        ("submissions", "list your assignment submission states"),
        ("grades", "show your available course grades"),
        ("calendar", "list course calendar events"),
        ("discussions", "list course discussion topics"),
        ("activity", "list new activity in active courses"),
        ("announcements", "list recent course announcements"),
        ("modules", "show course modules and your progress"),
        ("feedback", "show recently graded work and instructor feedback"),
        ("dashboard", "summarize active courses, imminent work, missing work, and grades"),
    ):
        command = subparsers.add_parser(name, help=description)
        if name in {
            "submissions", "grades", "discussions", "activity", "announcements",
            "modules", "feedback", "dashboard",
        }:
            command.add_argument("course", nargs="?", help="course ID or unique title")
        if name in {"upcoming", "calendar", "activity", "announcements", "dashboard"}:
            command.add_argument("--from", dest="start_date", help="start date (YYYY-MM-DD)")
            command.add_argument("--to", dest="end_date", help="end date (YYYY-MM-DD)")
        if name == "feedback":
            command.add_argument("--since", dest="start_date", help="graded since (YYYY-MM-DD)")
        command.add_argument(
            "--save", action="store_true",
            help="save a private Markdown snapshot and queryable SQLite rows",
        )
        add_agent_flags(command)

    logout = subparsers.add_parser(
        "logout", help="remove browser authentication and saved credentials"
    )
    logout.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    add_agent_flags(logout)

    uninstall = subparsers.add_parser("uninstall", help="remove snuetl and selected local data")
    uninstall.add_argument(
        "--dry-run", action="store_true", help="show everything that would be removed"
    )
    uninstall.add_argument(
        "--yes", action="store_true", help="confirm using the selected safe defaults"
    )
    uninstall.add_argument(
        "--delete-files", action="store_true", help="delete tracked pulled course content"
    )
    uninstall.add_argument("--purge-config", action="store_true", help="remove configuration")
    uninstall.add_argument(
        "--purge-state", action="store_true", help="remove login, credentials, and history"
    )
    uninstall.add_argument(
        "--purge", action="store_true", help="remove both configuration and state"
    )
    uninstall.add_argument(
        "--remove-shared-deps",
        action="store_true",
        help="remove Playwright browser/FFmpeg only when recorded as installed by snuetl",
    )
    add_agent_flags(uninstall)

    discord = subparsers.add_parser("discord", help="configure and run Discord remote control")
    discord_subparsers = discord.add_subparsers(
        dest="discord_command", parser_class=SnuetlArgumentParser
    )

    def add_discord_setup_flags(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--token-file", type=Path, help="read the Discord bot token from an owner-only file"
        )
        command.add_argument("--application-id", type=int, help="Discord application ID")
        command.add_argument("--guild-id", type=int, help="Discord server ID")
        command.add_argument("--channel-id", type=int, help="the only accepted Discord channel ID")
        command.add_argument(
            "--owner-id",
            type=int,
            action="append",
            default=[],
            help="authorized Discord user ID (repeatable)",
        )
        command.add_argument("--yes", action="store_true", help="confirm non-interactive setup")
        add_agent_flags(command)

    add_discord_setup_flags(discord)
    add_discord_setup_flags(
        discord_subparsers.add_parser("setup", help="pair the bot and install its user service")
    )
    add_agent_flags(
        discord_subparsers.add_parser("guide", help="show precise Discord Portal setup steps")
    )
    add_agent_flags(
        discord_subparsers.add_parser("run", help="run the Discord bot in the foreground")
    )
    add_agent_flags(
        discord_subparsers.add_parser("status", help="show Discord configuration and daemon state")
    )
    add_agent_flags(
        discord_subparsers.add_parser("enable", help="enable and start the Discord daemon")
    )
    add_agent_flags(
        discord_subparsers.add_parser("disable", help="stop and disable the Discord daemon")
    )
    owner = discord_subparsers.add_parser("owner", help="manage authorized Discord owners")
    owner_subparsers = owner.add_subparsers(
        dest="discord_owner_command", parser_class=SnuetlArgumentParser, required=True
    )
    add_agent_flags(owner_subparsers.add_parser("list", help="list authorized owner IDs"))
    for owner_action in ("add", "remove"):
        owner_command = owner_subparsers.add_parser(
            owner_action, help=f"{owner_action} an authorized owner"
        )
        owner_command.add_argument("user_id", type=int)
        add_agent_flags(owner_command)

    telegram = subparsers.add_parser("telegram", help="configure and run Telegram remote control")
    telegram_subparsers = telegram.add_subparsers(
        dest="telegram_command", parser_class=SnuetlArgumentParser
    )
    add_agent_flags(telegram)
    for name, description in (
        ("setup", "pair a private Telegram chat and install its service"),
        ("guide", "show BotFather setup steps"),
        ("run", "run the Telegram bot in the foreground"),
        ("status", "show Telegram configuration and daemon state"),
        ("enable", "enable and start the Telegram daemon"),
        ("disable", "stop and disable the Telegram daemon"),
    ):
        add_agent_flags(telegram_subparsers.add_parser(name, help=description))
    alerts = telegram_subparsers.add_parser("alerts", help="enable or disable daily digests")
    alerts.add_argument("mode", choices=("on", "off"))
    add_agent_flags(alerts)
    return parser
