from __future__ import annotations

import os
import platform
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from rich.prompt import Confirm, Prompt
from rich.table import Table

from .browser import _playwright, interactive_login
from .catalog import inspect_catalog
from .config import Config, default_config_path, load_config, save_config
from .errors import AuthenticationRequired
from .profile import profile_lock
from .scheduler import install_user_timer, timer_is_enabled
from .ui import console, print_banner, print_catalog, print_commands


def display_available() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def browser_available(config: Config) -> tuple[bool, str]:
    try:
        with _playwright()() as playwright:
            kwargs: dict[str, object] = {"headless": True}
            if config.browser_channel:
                kwargs["channel"] = config.browser_channel
            browser = playwright.chromium.launch(**kwargs)
            browser.close()
        return True, "ready"
    except Exception as exc:
        return False, str(exc).splitlines()[0]


def ensure_browser(config: Config) -> Config:
    ready, _ = browser_available(config)
    if ready:
        console.print("[green]✓[/green] Chromium runtime is ready")
        return config

    if config.browser_channel:
        console.print(
            f"[yellow]![/yellow] Browser channel {config.browser_channel!r} is unavailable; "
            "switching to Playwright Chromium"
        )
        config = replace(config, browser_channel=None)

    console.print("[cyan]Installing the managed Chromium browser…[/cyan]")
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    ready, reason = browser_available(config)
    if ready:
        console.print("[green]✓[/green] Chromium installed")
        return config

    if platform.system() == "Linux" and Confirm.ask(
        "Chromium needs Ubuntu system libraries. Install them now (sudo may prompt)?",
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
    console.print(
        "Credentials and verification codes are used only for this login and are never saved."
    )
    username = ""
    while not username:
        username = Prompt.ask("SNU ID").strip()
    password = ""
    while not password:
        password = Prompt.ask("SNU password", password=True)

    def method_provider() -> str:
        return Prompt.ask(
            "Send the additional-verification code by",
            choices=["email", "phone"],
            default="email",
        )

    def code_provider() -> str:
        return Prompt.ask("Verification code", password=True)

    interactive_login(
        config,
        username,
        password,
        headless=headless,
        two_factor_method_provider=method_provider,
        verification_code_provider=code_provider,
    )
    console.print("[green]✓[/green] SNU login and trusted-browser enrollment verified")


def run_setup(config_path: Path | None = None, *, force_headless: bool | None = None) -> int:
    path = config_path or default_config_path()
    config = load_config(path)
    print_banner("Guided setup")
    console.print("This wizard checks the browser, configures storage, and verifies SNU access.\n")

    console.rule("[bold]1. Environment")
    config = ensure_browser(config)

    console.rule("[bold]2. Storage")
    download_answer = Prompt.ask(
        "Where should course files be stored?", default=str(config.download_dir)
    )
    download_dir = Path(download_answer).expanduser().resolve()
    download_dir.mkdir(parents=True, exist_ok=True)
    config = replace(config, download_dir=download_dir, headless=True, setup_complete=False)
    save_config(config, path)
    console.print(f"[green]✓[/green] Configuration staged at [dim]{path}[/dim]")

    if force_headless is None:
        login_headless = not display_available()
        if not login_headless:
            show = Confirm.ask("Show the browser during SNU login?", default=True)
            login_headless = not show
    else:
        login_headless = force_headless

    needs_login = not config.profile_dir.exists() or not config.auth_state_path.exists()
    if not needs_login:
        needs_login = Confirm.ask("Refresh SNU authentication now?", default=False)
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

    console.rule("[bold]4. Automation")
    if platform.system() == "Linux" and Confirm.ask(
        "Enable automatic synchronization every 15 minutes?", default=True
    ):
        try:
            timer_path = install_user_timer(path, interval_minutes=15)
            console.print(f"[green]✓[/green] Enabled systemd timer at [dim]{timer_path}[/dim]")
        except Exception as exc:
            console.print(f"[yellow]![/yellow] Could not enable the timer: {exc}")
            console.print("You can retry later with [cyan]snuetl setup[/cyan].")

    console.rule("[bold green]Setup complete")
    print_commands()
    return 0


def run_doctor(config: Config, config_path: Path | None = None) -> int:
    path = config_path or default_config_path()
    checks: list[tuple[str, bool, str]] = []
    checks.append(("Configuration", path.exists(), str(path)))
    browser_ok, browser_detail = browser_available(config)
    checks.append(("Chromium", browser_ok, browser_detail))
    auth_ready = config.profile_dir.exists() and config.auth_state_path.exists()
    checks.append(("Authentication state", auth_ready, str(config.state_dir)))
    checks.append(("Download directory", config.download_dir.exists(), str(config.download_dir)))
    core_check_count = len(checks)
    if platform.system() == "Linux":
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
