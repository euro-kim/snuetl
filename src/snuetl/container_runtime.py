from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

from .config import default_config_path, ensure_private_directory, load_config, save_config
from .directory_manager import secure_managed_directory, set_video_directory
from .discord_config import load_discord_token
from .runtime import (
    container_reload_path,
    container_runtime_dir,
    container_status_path,
    container_supervisor_is_active,
)

LOGGER = logging.getLogger("snuetl.container")
_DOWNLOAD_DIR = Path("/data/downloads")
_VIDEO_DIR = Path("/data/videos")
_POLL_SECONDS = 2.0
_INITIAL_BACKOFF_SECONDS = 5.0
_MAX_BACKOFF_SECONDS = 60.0
_STABLE_PROCESS_SECONDS = 60.0


def bootstrap_container_config(config_path: Path | None = None) -> Path:
    path = config_path or default_config_path()
    ensure_private_directory(path.parent)
    if not path.exists():
        config = load_config(path)
        config = replace(
            config,
            download_dir=_DOWNLOAD_DIR,
            state_dir=Path.home() / ".local" / "state" / "snuetl",
            headless=True,
        )
        config = set_video_directory(config, _VIDEO_DIR)
        save_config(config, path)
    config = load_config(path)
    ensure_private_directory(config.state_dir)
    for directory in (config.download_dir, _VIDEO_DIR):
        secure_managed_directory(directory)
    return path


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _runtime_signature(config_path: Path, token_path: Path | None) -> tuple[int | None, ...]:
    return (
        _mtime(config_path),
        _mtime(token_path) if token_path is not None else None,
        _mtime(container_reload_path()),
    )


def _write_status(*, discord_active: bool, discord_pid: int | None) -> None:
    directory = container_runtime_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {
        "updated_at": time.time(),
        "supervisor_active": True,
        "supervisor_pid": os.getpid(),
        "discord_active": discord_active,
        "discord_pid": discord_pid,
    }
    temporary = container_status_path().with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(temporary, container_status_path())


def _stop_process(process: subprocess.Popen[bytes], *, timeout_seconds: float = 10.0) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        LOGGER.warning("Discord did not stop within %.0f seconds; killing it", timeout_seconds)
        process.kill()
        process.wait()


def _discord_command(config_path: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "snuetl",
        "--config",
        str(config_path),
        "discord",
        "run",
        "--no-input",
    ]


def run_supervisor() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config_path = bootstrap_container_config()
    stopping = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    process: subprocess.Popen[bytes] | None = None
    signature: tuple[int | None, ...] | None = None
    next_start = 0.0
    backoff = _INITIAL_BACKOFF_SECONDS
    started_at: float | None = None
    waiting_message_printed = False
    LOGGER.info("snuetl container supervisor is ready")

    try:
        while not stopping:
            try:
                config = load_config(config_path)
                token_path = config.discord_token_path
                desired = bool(
                    config.setup_complete
                    and config.discord.enabled
                    and config.discord.configured
                    and load_discord_token(config)
                )
                current_signature = _runtime_signature(config_path, token_path)
            except Exception as exc:
                LOGGER.warning("Cannot load container configuration: %s", exc)
                desired = False
                current_signature = _runtime_signature(config_path, None)

            if signature is not None and current_signature != signature and process is not None:
                LOGGER.info("Configuration changed; restarting the Discord daemon")
                _stop_process(process)
                process = None
                next_start = 0.0
            signature = current_signature

            if process is not None:
                return_code = process.poll()
                if return_code is not None:
                    runtime = time.monotonic() - (started_at or time.monotonic())
                    LOGGER.warning("Discord daemon exited with code %d", return_code)
                    process = None
                    if runtime >= _STABLE_PROCESS_SECONDS:
                        backoff = _INITIAL_BACKOFF_SECONDS
                    next_start = time.monotonic() + backoff
                    backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
                elif not desired:
                    LOGGER.info("Discord is disabled or incomplete; stopping the daemon")
                    _stop_process(process)
                    process = None
                    next_start = 0.0

            if desired and process is None and time.monotonic() >= next_start:
                LOGGER.info("Starting the Discord daemon")
                process = subprocess.Popen(_discord_command(config_path))
                started_at = time.monotonic()
                waiting_message_printed = False
            elif not desired and not waiting_message_printed:
                LOGGER.info(
                    "Waiting for setup; run: docker compose exec snuetl snuetl setup --headless"
                )
                LOGGER.info(
                    "Then configure Discord with: docker compose exec snuetl snuetl discord"
                )
                waiting_message_printed = True

            active = process is not None and process.poll() is None
            discord_pid = process.pid if process is not None and active else None
            _write_status(discord_active=active, discord_pid=discord_pid)
            time.sleep(_POLL_SECONDS)
    finally:
        if process is not None:
            _stop_process(process)
        container_status_path().unlink(missing_ok=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the snuetl Docker supervisor")
    parser.add_argument("--healthcheck", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.healthcheck:
        return 0 if container_supervisor_is_active() else 1
    return run_supervisor()


if __name__ == "__main__":
    raise SystemExit(main())
