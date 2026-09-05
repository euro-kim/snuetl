from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


def _systemd_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def install_user_timer(config_path: Path, *, interval_minutes: int = 15) -> Path:
    if shutil.which("systemctl") is None:
        raise RuntimeError("systemctl is not available on this machine")
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    executable = Path(sys.executable).resolve()
    command = " ".join(
        (
            _systemd_quote(str(executable)),
            "-m snuetl",
            "--config",
            _systemd_quote(str(config_path.resolve())),
            "sync",
        )
    )
    service = f"""[Unit]
Description=Synchronize files from SNU eTL
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={command}
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
"""
    timer = f"""[Unit]
Description=Synchronize SNU eTL every {interval_minutes} minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec={interval_minutes}min
RandomizedDelaySec=60
Persistent=true
Unit=snuetl.service

[Install]
WantedBy=timers.target
"""
    service_path = unit_dir / "snuetl.service"
    timer_path = unit_dir / "snuetl.timer"
    for path, body in ((service_path, service), (timer_path, timer)):
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(body, encoding="utf-8")
        os.replace(temporary, path)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "snuetl.timer"], check=True)
    return timer_path


def timer_is_enabled() -> bool:
    if shutil.which("systemctl") is None:
        return False
    result = subprocess.run(
        ["systemctl", "--user", "is-enabled", "snuetl.timer"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


DISCORD_SERVICE_NAME = "snuetl-discord.service"


@dataclass(frozen=True, slots=True)
class LingerStatus:
    enabled: bool
    detail: str


def discord_service_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / DISCORD_SERVICE_NAME


def install_discord_service(
    config_path: Path,
    *,
    state_dir: Path | None = None,
    download_dir: Path | None = None,
    write_dirs: tuple[Path, ...] = (),
) -> Path:
    if shutil.which("systemctl") is None:
        raise RuntimeError("systemctl is not available on this machine")
    path = discord_service_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    executable = Path(sys.executable).resolve()
    command = " ".join(
        (
            _systemd_quote(str(executable)),
            "-m snuetl",
            "--config",
            _systemd_quote(str(config_path.resolve())),
            "discord run",
        )
    )
    writable = [config_path.resolve().parent]
    if state_dir is not None:
        writable.append(state_dir.resolve())
    if download_dir is not None:
        writable.append(download_dir.resolve())
    writable.extend(path.resolve() for path in write_dirs)
    write_paths = " ".join(_systemd_quote(str(path)) for path in dict.fromkeys(writable))
    body = f"""[Unit]
Description=snuetl Discord remote-control bot
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Type=simple
ExecStart={command}
Restart=on-failure
RestartSec=5s
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={write_paths}
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true

[Install]
WantedBy=default.target
"""
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(body, encoding="utf-8")
    os.replace(temporary, path)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", DISCORD_SERVICE_NAME], check=True)
    return path


def discord_service_is_enabled() -> bool:
    if shutil.which("systemctl") is None:
        return False
    result = subprocess.run(
        ["systemctl", "--user", "is-enabled", DISCORD_SERVICE_NAME],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def set_discord_service_enabled(enabled: bool) -> None:
    if shutil.which("systemctl") is None:
        raise RuntimeError("systemctl is not available on this machine")
    action = "enable" if enabled else "disable"
    subprocess.run(["systemctl", "--user", action, "--now", DISCORD_SERVICE_NAME], check=True)


def restart_discord_service() -> None:
    if shutil.which("systemctl") is None:
        raise RuntimeError("systemctl is not available on this machine")
    subprocess.run(["systemctl", "--user", "restart", DISCORD_SERVICE_NAME], check=True)


def remove_discord_service() -> bool:
    path = discord_service_path()
    systemctl = shutil.which("systemctl")
    if systemctl:
        subprocess.run(
            [systemctl, "--user", "disable", "--now", DISCORD_SERVICE_NAME],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    existed = path.exists()
    path.unlink(missing_ok=True)
    if systemctl:
        subprocess.run([systemctl, "--user", "daemon-reload"], check=False)
        subprocess.run([systemctl, "--user", "reset-failed"], check=False)
    return existed


def linger_status(username: str | None = None) -> LingerStatus:
    loginctl = shutil.which("loginctl")
    user = username or os.environ.get("USER") or ""
    if loginctl is None or not user:
        return LingerStatus(False, "loginctl or current user is unavailable")
    result = subprocess.run(
        [loginctl, "show-user", user, "--property=Linger", "--value"],
        capture_output=True,
        text=True,
        check=False,
    )
    enabled = result.returncode == 0 and result.stdout.strip().casefold() == "yes"
    detail = "enabled" if enabled else f"run: sudo loginctl enable-linger {user}"
    return LingerStatus(enabled, detail)
