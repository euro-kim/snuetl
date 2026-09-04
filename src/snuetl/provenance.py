from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .config import Config, ensure_private_directory


@dataclass(slots=True)
class InstallProvenance:
    playwright_browser: bool = False
    apt_packages: list[str] = field(default_factory=list)


def load_provenance(config: Config) -> InstallProvenance:
    try:
        value = json.loads(config.provenance_path.read_text(encoding="utf-8"))
        packages = value.get("apt_packages", [])
        return InstallProvenance(
            playwright_browser=bool(value.get("playwright_browser", False)),
            apt_packages=[str(item) for item in packages if isinstance(item, str)],
        )
    except (OSError, ValueError, TypeError):
        return InstallProvenance()


def save_provenance(config: Config, value: InstallProvenance) -> None:
    ensure_private_directory(config.state_dir)
    temporary = config.provenance_path.with_name(f".{config.provenance_path.name}.tmp")
    temporary.write_text(
        json.dumps(
            {
                "playwright_browser": value.playwright_browser,
                "apt_packages": sorted(set(value.apt_packages)),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    os.replace(temporary, config.provenance_path)


def record_playwright_browser(config: Config) -> None:
    value = load_provenance(config)
    value.playwright_browser = True
    save_provenance(config, value)


def record_apt_package(config: Config, package: str) -> None:
    value = load_provenance(config)
    if package not in value.apt_packages:
        value.apt_packages.append(package)
    save_provenance(config, value)
