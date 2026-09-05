from __future__ import annotations

import os
import shutil
import subprocess
import sys
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .profile import profile_lock
from .provenance import InstallProvenance, load_provenance
from .state import StateStore


@dataclass(frozen=True, slots=True)
class UninstallInventory:
    config_path: Path
    state_dir: Path
    download_dir: Path
    service_path: Path
    timer_path: Path
    tracked_files: tuple[Path, ...]
    provenance: InstallProvenance
    pipx_install: bool
    managed_roots: tuple[Path, ...] = ()

    @property
    def discord_service_path(self) -> Path:
        return self.service_path.with_name("snuetl-discord.service")


@dataclass(slots=True)
class UninstallSummary:
    timer_removed: bool = False
    files_removed: int = 0
    config_removed: bool = False
    state_removed: bool = False
    shared_removed: list[str] = field(default_factory=list)
    package_removed: bool = False
    warnings: list[dict[str, str]] = field(default_factory=list)


def _pipx_install() -> bool:
    prefix = Path(sys.prefix).resolve()
    return (
        prefix.name == "snuetl"
        and prefix.parent.name == "venvs"
        and shutil.which("pipx") is not None
    )


def build_inventory(config: Config, config_path: Path) -> UninstallInventory:
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    tracked: list[Path] = []
    if config.database_path.exists():
        with StateStore(config.database_path) as store:
            tracked = store.list_artifact_paths()
    return UninstallInventory(
        config_path=config_path,
        state_dir=config.state_dir,
        download_dir=config.download_dir,
        service_path=unit_dir / "snuetl.service",
        timer_path=unit_dir / "snuetl.timer",
        tracked_files=tuple(dict.fromkeys(path.resolve() for path in tracked)),
        provenance=load_provenance(config),
        pipx_install=_pipx_install(),
        managed_roots=tuple(
            dict.fromkeys(
                [
                    config.download_dir.resolve(),
                    *(route.destination.resolve() for route in config.directory_routes),
                ]
            )
        ),
    )


def inventory_data(value: UninstallInventory) -> dict[str, object]:
    existing = [path for path in value.tracked_files if path.is_file()]
    return {
        "config_path": str(value.config_path),
        "config_exists": value.config_path.exists(),
        "state_dir": str(value.state_dir),
        "state_exists": value.state_dir.exists(),
        "download_dir": str(value.download_dir),
        "tracked_files": len(existing),
        "tracked_bytes": sum(path.stat().st_size for path in existing),
        "systemd_units": [
            str(path)
            for path in (value.service_path, value.timer_path, value.discord_service_path)
            if path.exists()
        ],
        "playwright_browser_installed_by_snuetl": value.provenance.playwright_browser,
        "apt_packages_installed_by_snuetl": value.provenance.apt_packages,
        "pipx_install": value.pipx_install,
    }


def _remove_timer(inventory: UninstallInventory, summary: UninstallSummary) -> None:
    systemctl = shutil.which("systemctl")
    if systemctl:
        units: list[str] = []
        if inventory.timer_path.exists() or inventory.service_path.exists():
            units.append("snuetl.timer")
        if inventory.discord_service_path.exists():
            units.append("snuetl-discord.service")
        for unit in units:
            subprocess.run(
                [systemctl, "--user", "disable", "--now", unit],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    inventory.timer_path.unlink(missing_ok=True)
    inventory.service_path.unlink(missing_ok=True)
    inventory.discord_service_path.unlink(missing_ok=True)
    if systemctl:
        subprocess.run([systemctl, "--user", "daemon-reload"], check=False)
        subprocess.run([systemctl, "--user", "reset-failed"], check=False)
    summary.timer_removed = True


def _safe_managed_path(path: Path, root: Path) -> bool:
    resolved = path.resolve()
    managed = root.resolve()
    return managed not in {Path("/"), Path.home().resolve()} and managed in resolved.parents


def _managed_roots(inventory: UninstallInventory) -> tuple[Path, ...]:
    return inventory.managed_roots or (inventory.download_dir,)


def _remove_tracked_files(inventory: UninstallInventory, summary: UninstallSummary) -> None:
    for path in inventory.tracked_files:
        if not any(_safe_managed_path(path, root) for root in _managed_roots(inventory)):
            summary.warnings.append(
                {
                    "code": "UNSAFE_FILE_PATH",
                    "message": f"Retained path outside managed root: {path}",
                }
            )
            continue
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
                summary.files_removed += 1
        except OSError as exc:
            summary.warnings.append({"code": "FILE_REMOVE_FAILED", "message": f"{path}: {exc}"})
    for directory in sorted(
        {path.parent for path in inventory.tracked_files},
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        current = directory
        containing_root = next(
            (root for root in _managed_roots(inventory) if _safe_managed_path(directory, root)),
            None,
        )
        while containing_root is not None and _safe_managed_path(current, containing_root):
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent


def _remove_shared_dependencies(inventory: UninstallInventory, summary: UninstallSummary) -> None:
    if inventory.provenance.playwright_browser:
        result = subprocess.run([sys.executable, "-m", "playwright", "uninstall"], check=False)
        if result.returncode == 0:
            summary.shared_removed.append("Playwright browsers for this installation")
        else:
            summary.warnings.append(
                {"code": "PLAYWRIGHT_REMOVE_FAILED", "message": "Playwright browser removal failed"}
            )
    packages = inventory.provenance.apt_packages
    if packages:
        apt_get = shutil.which("apt-get")
        prefix: list[str] = []
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            sudo = shutil.which("sudo")
            if sudo:
                prefix = [sudo]
            else:
                apt_get = None
        if apt_get:
            result = subprocess.run([*prefix, apt_get, "remove", "-y", *packages], check=False)
            if result.returncode == 0:
                summary.shared_removed.extend(f"apt:{package}" for package in packages)
            else:
                summary.warnings.append(
                    {"code": "APT_REMOVE_FAILED", "message": "Recorded apt package removal failed"}
                )
        else:
            summary.warnings.append(
                {"code": "APT_UNAVAILABLE", "message": "Recorded apt packages were retained"}
            )


def _validated_private_tree(path: Path) -> Path:
    resolved = path.resolve()
    if resolved in {Path("/"), Path.home().resolve()} or len(resolved.parts) < 3:
        raise ValueError(f"refusing to remove unsafe application directory: {resolved}")
    return resolved


def execute_uninstall(
    inventory: UninstallInventory,
    *,
    delete_files: bool,
    purge_config: bool,
    purge_state: bool,
    remove_shared_deps: bool,
    remove_package: bool = True,
) -> UninstallSummary:
    summary = UninstallSummary()
    _remove_timer(inventory, summary)
    with profile_lock(inventory.state_dir / "profile.lock"):
        if delete_files:
            _remove_tracked_files(inventory, summary)
        if remove_shared_deps:
            _remove_shared_dependencies(inventory, summary)
        if purge_config:
            inventory.config_path.unlink(missing_ok=True)
            summary.config_removed = True
            with suppress(OSError):
                inventory.config_path.parent.rmdir()
        if purge_state and inventory.state_dir.exists():
            shutil.rmtree(_validated_private_tree(inventory.state_dir))
            summary.state_removed = True
    if remove_package:
        if inventory.pipx_install:
            pipx = shutil.which("pipx")
            result = subprocess.run([str(pipx), "uninstall", "snuetl"], check=False)
            summary.package_removed = result.returncode == 0
            if not summary.package_removed:
                summary.warnings.append(
                    {"code": "PIPX_REMOVE_FAILED", "message": "pipx could not remove snuetl"}
                )
        else:
            summary.warnings.append(
                {
                    "code": "MANUAL_PACKAGE_REMOVAL",
                    "message": "This is not a verified pipx installation; remove it with its original package manager.",
                }
            )
    return summary
