from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from rich.prompt import Confirm, Prompt
from rich.table import Table

from .browser import _playwright, interactive_login
from .catalog import inspect_catalog
from .config import Config, default_config_path, load_config, save_config
from .credentials import SavedCredentials, load_credentials, save_credentials
from .directory_manager import (
    configured_directory_permission_reports,
    configured_video_directory,
    directory_migration_conflicts,
    execute_directory_migration,
    has_separate_video_directory,
    plan_directory_migration,
    set_video_directory,
)
from .errors import AuthenticationRequired
from .profile import profile_lock
from .provenance import record_apt_package, record_playwright_browser
from .runtime import container_supervisor_is_active, is_container_runtime
from .scheduler import discord_service_is_enabled, install_discord_service, timer_is_enabled
from .ui import (
    choose_checkbox,
    console,
    interactive_terminal,
    print_banner,
    print_catalog,
    print_commands,
)


def _confirm(prompt: str, *, default: bool) -> bool:
    """Use navigable checkboxes on a TTY and retain a stream-safe fallback."""
    if not interactive_terminal():
        return Confirm.ask(prompt, default=default)
    selected = choose_checkbox(
        prompt,
        (("Yes", True), ("No", False)),
        default=0 if default else 1,
        require_space=True,
    )
    return bool(selected)


def _choose(prompt: str, choices: tuple[str, ...], *, default: str) -> str:
    if not interactive_terminal():
        return Prompt.ask(prompt, choices=list(choices), default=default)
    selected = choose_checkbox(
        prompt,
        tuple((choice, choice) for choice in choices),
        default=choices.index(default),
        require_space=True,
    )
    return selected or default


def display_available() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def is_arm64() -> bool:
    return platform.machine().casefold() in {"aarch64", "arm64"}


def is_raspberry_pi() -> bool:
    if not is_arm64() or platform.system() != "Linux":
        return False
    for path in (Path("/proc/device-tree/model"), Path("/sys/firmware/devicetree/base/model")):
        try:
            if "raspberry pi" in path.read_text(encoding="utf-8", errors="ignore").casefold():
                return True
        except OSError:
            continue
    try:
        os_release = Path("/etc/os-release").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "raspbian" in os_release.casefold()


def find_system_chromium() -> Path | None:
    for name in ("chromium", "chromium-browser"):
        executable = shutil.which(name)
        if executable:
            return Path(executable).resolve()
    return None


def _with_system_chromium(config: Config, executable: Path) -> Config:
    return replace(config, browser_channel=None, browser_executable_path=executable)


def _install_raspberry_pi_chromium(config: Config | None = None) -> Path | None:
    apt_get = shutil.which("apt-get")
    if apt_get is None:
        return None
    prefix: list[str] = []
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        sudo = shutil.which("sudo")
        if sudo is None:
            return None
        prefix = [sudo]
    console.print("[cyan]Installing Raspberry Pi OS Chromium…[/cyan]")
    try:
        subprocess.run([*prefix, apt_get, "update"], check=True)
        subprocess.run([*prefix, apt_get, "install", "-y", "chromium"], check=True)
    except subprocess.CalledProcessError as exc:
        console.print(
            f"[yellow]![/yellow] Raspberry Pi Chromium installation failed "
            f"(exit {exc.returncode}); trying Playwright Chromium"
        )
        return None
    if config is not None:
        record_apt_package(config, "chromium")
    return find_system_chromium()


def ensure_ffmpeg(config: Config) -> bool:
    if shutil.which("ffmpeg"):
        return True
    if platform.system() != "Linux" or shutil.which("apt-get") is None:
        console.print(
            "[yellow]![/yellow] FFmpeg is unavailable; video pulls that need merging will fail"
        )
        return False
    if not _confirm("Install FFmpeg for lecture-video downloads (sudo may prompt)?", default=True):
        return False
    apt_get = shutil.which("apt-get")
    prefix: list[str] = []
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        sudo = shutil.which("sudo")
        if sudo is None:
            console.print("[yellow]![/yellow] sudo is unavailable; install FFmpeg manually")
            return False
        prefix = [sudo]
    try:
        subprocess.run([*prefix, str(apt_get), "update"], check=True)
        subprocess.run([*prefix, str(apt_get), "install", "-y", "ffmpeg"], check=True)
    except subprocess.CalledProcessError:
        console.print("[yellow]![/yellow] FFmpeg installation failed")
        return False
    record_apt_package(config, "ffmpeg")
    console.print("[green]✓[/green] FFmpeg is ready")
    return True


def browser_available(config: Config) -> tuple[bool, str]:
    try:
        with _playwright()() as playwright:
            kwargs: dict[str, object] = {"headless": True}
            if config.browser_executable_path:
                kwargs["executable_path"] = str(config.browser_executable_path)
            elif config.browser_channel:
                kwargs["channel"] = config.browser_channel
            browser = playwright.chromium.launch(**kwargs)
            browser.close()
        if config.browser_executable_path:
            return True, f"ready (system Chromium: {config.browser_executable_path})"
        return True, f"ready (Playwright Chromium; {platform.machine()})"
    except Exception as exc:
        return False, str(exc).splitlines()[0]


def ensure_browser(config: Config) -> Config:
    ready, _ = browser_available(config)
    if ready:
        console.print("[green]✓[/green] Chromium runtime is ready")
        return config

    if config.browser_executable_path:
        console.print(
            f"[yellow]![/yellow] Chromium at {config.browser_executable_path} is unavailable"
        )
        config = replace(config, browser_executable_path=None)

    if config.browser_channel:
        console.print(
            f"[yellow]![/yellow] Browser channel {config.browser_channel!r} is unavailable; "
            "switching to Playwright Chromium"
        )
        config = replace(config, browser_channel=None, browser_executable_path=None)

    system_chromium = find_system_chromium() if is_arm64() else None
    if system_chromium is not None:
        system_config = _with_system_chromium(config, system_chromium)
        ready, _ = browser_available(system_config)
        if ready:
            console.print(
                f"[green]✓[/green] Using ARM64 system Chromium at [dim]{system_chromium}[/dim]"
            )
            return system_config

    if is_raspberry_pi() and _confirm(
        "Install the Raspberry Pi OS Chromium package now (sudo may prompt)?",
        default=True,
    ):
        executable = _install_raspberry_pi_chromium(config)
        if executable is not None:
            system_config = _with_system_chromium(config, executable)
            ready, _ = browser_available(system_config)
            if ready:
                console.print(
                    f"[green]✓[/green] Raspberry Pi ARM64 Chromium installed at "
                    f"[dim]{executable}[/dim]"
                )
                return system_config

    architecture = platform.machine() or "unknown architecture"
    console.print(f"[cyan]Installing Playwright Chromium for {architecture}…[/cyan]")
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        record_playwright_browser(config)
    except subprocess.CalledProcessError:
        if is_arm64():
            executable = find_system_chromium()
            if executable is not None:
                return _with_system_chromium(config, executable)
        raise
    ready, reason = browser_available(config)
    if ready:
        console.print("[green]✓[/green] Chromium installed")
        return config

    if platform.system() == "Linux" and _confirm(
        "Chromium needs Linux system libraries. Install them now (sudo may prompt)?",
        default=True,
    ):
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
            check=True,
        )
        ready, reason = browser_available(config)
    if not ready:
        raise RuntimeError(f"Chromium could not start: {reason}")
    console.print("[green]✓[/green] Chromium and system libraries are ready")
    return config


def prompt_login(config: Config, *, headless: bool) -> None:
    console.rule("[bold]SNU authentication")
    saved = load_credentials(config)
    use_saved = saved is not None and _confirm(
        f"Use the saved SNU credentials for {saved.username}?",
        default=True,
    )
    if use_saved and saved is not None:
        username = saved.username
        password = saved.password
    else:
        username = ""
        while not username:
            username = Prompt.ask("SNU ID", default=saved.username if saved else None).strip()
        password = ""
        while not password:
            password = Prompt.ask("SNU password", password=True)

    save_login = _confirm(
        "Save the SNU ID and password in a plaintext owner-only file for automatic re-login? "
        f"(owner-only file: {config.credentials_path})",
        default=True,
    )
    trust_browser = _confirm(
        "Trust this device so SNU does not request 2FA on routine re-logins?",
        default=saved.trust_browser if use_saved and saved is not None else True,
    )

    def method_provider() -> str:
        return _choose(
            "Send the additional-verification code by",
            ("email", "phone"),
            default="email",
        )

    def code_provider() -> str:
        return Prompt.ask("Verification code", password=True)

    interactive_login(
        config,
        username,
        password,
        headless=headless,
        trust_browser=trust_browser,
        two_factor_method_provider=method_provider,
        verification_code_provider=code_provider,
    )
    if save_login:
        save_credentials(config, SavedCredentials(username, password, trust_browser))
        console.print(
            "[green]✓[/green] Credentials saved for automatic re-login "
            f"[dim]({config.credentials_path})[/dim]"
        )
    elif saved is not None:
        config.credentials_path.unlink(missing_ok=True)
        console.print("[yellow]![/yellow] Previously saved credentials removed")
    trust_status = "trusted-device enrollment" if trust_browser else "login"
    console.print(f"[green]✓[/green] SNU {trust_status} verified")


def run_setup(config_path: Path | None = None, *, force_headless: bool | None = None) -> int:
    path = config_path or default_config_path()
    config = load_config(path)
    print_banner("Guided setup")
    console.print("This wizard checks the browser, configures storage, and verifies SNU access.\n")

    console.rule("[bold]1. Environment")
    config = ensure_browser(config)
    ensure_ffmpeg(config)

    console.rule("[bold]2. Storage")
    download_answer = Prompt.ask(
        "Where should regular course files be stored?", default=str(config.download_dir)
    )
    download_dir = Path(download_answer).expanduser().resolve()
    config = replace(config, download_dir=download_dir, setup_complete=False)
    video_default = (
        configured_video_directory(config) if has_separate_video_directory(config) else download_dir
    )
    video_answer = Prompt.ask(
        "Where should large video downloads be stored? "
        "(Use the regular course directory to keep them together)",
        default=str(video_default),
    )
    video_dir = Path(video_answer).expanduser().resolve()
    config = set_video_directory(config, video_dir)
    permission_reports = configured_directory_permission_reports(config, secure=True)
    for permissions in permission_reports:
        mode = f"{permissions.mode:04o}" if permissions.mode is not None else "unknown"
        if permissions.changed:
            previous = (
                f" from {permissions.previous_mode:04o}"
                if permissions.previous_mode is not None
                else ""
            )
            console.print(
                f"[green]✓[/green] Prepared storage directory{previous} with usable mode "
                f"[green]{mode}[/green]: [dim]{permissions.path}[/dim]"
            )
        else:
            console.print(
                f"[green]✓[/green] Storage permissions are usable "
                f"([green]{mode}[/green], preserved): [dim]{permissions.path}[/dim]"
            )
    migration_entries = plan_directory_migration(config, config.download_dir)
    migration_conflicts = directory_migration_conflicts(migration_entries)
    if migration_conflicts:
        raise ValueError(
            "storage setup stopped before moving anything: " + "; ".join(migration_conflicts)
        )
    migration = execute_directory_migration(config, migration_entries)
    save_config(config, path)
    if config.discord.enabled and platform.system() == "Linux" and discord_service_is_enabled():
        install_discord_service(
            path,
            state_dir=config.state_dir,
            download_dir=config.download_dir,
            write_dirs=tuple(route.destination for route in config.directory_routes),
        )
    console.print(f"[green]✓[/green] Configuration staged at [dim]{path}[/dim]")
    if migration.moved:
        console.print(
            f"[green]✓[/green] Moved {migration.moved} tracked item(s) into the new storage layout"
        )
    if migration.failed:
        raise RuntimeError(
            f"{migration.failed} tracked item(s) could not be migrated; configuration and "
            "successful moves were saved. Run 'snuetl directory --dry-run' for details."
        )

    if force_headless is None:
        login_headless = not display_available()
        if not login_headless:
            show = _confirm("Show the browser during SNU login?", default=True)
            login_headless = not show
    else:
        login_headless = force_headless

    needs_login = (
        not config.profile_dir.exists()
        or not config.auth_state_path.exists()
        or load_credentials(config) is None
    )
    if not needs_login:
        needs_login = _confirm("Refresh SNU authentication now?", default=False)
    if needs_login:
        with profile_lock(config.lock_path):
            prompt_login(config, headless=login_headless)

    console.rule("[bold]3. Live verification")
    try:
        result = inspect_catalog(config, kind="courses", headless=True)
    except AuthenticationRequired:
        console.print("[yellow]![/yellow] Browser trust is missing or expired; signing in again.")
        with profile_lock(config.lock_path):
            prompt_login(config, headless=login_headless)
        result = inspect_catalog(config, kind="courses", headless=True)
    print_catalog(result, "courses")

    config = replace(config, setup_complete=True)
    save_config(config, path)

    console.rule("[bold green]Setup complete")
    print_commands()
    return 0


def run_doctor(config: Config, config_path: Path | None = None) -> int:
    path = config_path or default_config_path()
    checks: list[tuple[str, bool, str]] = []
    checks.append(("Configuration", path.exists(), str(path)))
    machine = platform.machine() or "unknown"
    supported_architecture = machine.casefold() in {"x86_64", "amd64", "aarch64", "arm64"}
    architecture_detail = f"{platform.system()} {machine}"
    if is_arm64():
        architecture_detail += " (ARM64 supported)"
    checks.append(("Architecture", supported_architecture, architecture_detail))
    browser_ok, browser_detail = browser_available(config)
    checks.append(("Chromium", browser_ok, browser_detail))
    ffmpeg = shutil.which("ffmpeg")
    checks.append(("FFmpeg", ffmpeg is not None, ffmpeg or "not installed (video pulls limited)"))
    auth_ready = config.profile_dir.exists() and config.auth_state_path.exists()
    checks.append(("Authentication state", auth_ready, str(config.state_dir)))
    checks.append(("Download directory", config.download_dir.exists(), str(config.download_dir)))
    permission_reports = configured_directory_permission_reports(config)
    permissions_ok = all(report.safe for report in permission_reports)
    permission_detail = (
        ", ".join(f"{report.path} ({report.mode:04o})" for report in permission_reports)
        if permissions_ok
        else " | ".join(
            f"{report.path}: {'; '.join((*report.issues, *report.remediation))}"
            for report in permission_reports
            if not report.safe
        )
    )
    checks.append(("Managed directory permissions", permissions_ok, permission_detail))
    core_check_count = len(checks)
    saved = load_credentials(config)
    checks.append(
        (
            "Automatic re-login (optional)",
            saved is not None,
            f"saved for {saved.username}" if saved else "credentials not saved",
        )
    )
    if platform.system() == "Linux":
        if is_container_runtime():
            active = container_supervisor_is_active()
            checks.append(
                (
                    "Docker Compose supervisor",
                    active,
                    "active; synchronization is Discord-triggered"
                    if active
                    else "not active",
                )
            )
        else:
            enabled = timer_is_enabled()
            checks.append(
                (
                    "15-minute timer (optional)",
                    enabled,
                    "enabled" if enabled else "not enabled",
                )
            )

    table = Table(title="snuetl doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Details")
    for name, passed, detail in checks:
        table.add_row(
            name, "[green]PASS[/green]" if passed else "[yellow]NEEDS ATTENTION[/yellow]", detail
        )
    console.print(table)
    if all(passed for _, passed, _ in checks[:core_check_count]):
        console.print("[green]Everything looks ready.[/green]")
        return 0
    console.print("Run [cyan]snuetl setup[/cyan] to repair missing setup items.")
    return 1
