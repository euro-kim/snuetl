from __future__ import annotations

import os
import shutil
import subprocess
import sys
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
