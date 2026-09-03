from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

from . import __version__
from .errors import SnuetlError

PACKAGE_NAME = "snuetl"


class UpdateError(SnuetlError):
    pass


@dataclass(frozen=True, slots=True)
class VersionInfo:
    installed: str
    source: str
    latest: str | None = None
    check_message: str | None = None


def installed_version() -> str:
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return __version__


def direct_url() -> dict[str, object] | None:
    try:
        raw = metadata.distribution(PACKAGE_NAME).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def installation_source() -> str:
    info = direct_url()
    if not info or not isinstance(info.get("url"), str):
        return "Python package index"
    url = str(info["url"])
    parts = urlsplit(url)
    if parts.scheme == "file":
        return str(Path(unquote(parts.path)))
    vcs = info.get("vcs_info")
    if isinstance(vcs, dict) and vcs.get("vcs"):
        return f"{vcs['vcs']}+{url}"
    return url


def get_version_info(*, check: bool = False) -> VersionInfo:
    current = installed_version()
    source = installation_source()
    if not check:
        return VersionInfo(current, source)
    try:
        response = httpx.get(
            f"https://pypi.org/pypi/{PACKAGE_NAME}/json",
            timeout=10,
            follow_redirects=True,
        )
        if response.status_code == 404:
            return VersionInfo(
                current,
                source,
                check_message="No published release was found; this is currently a source installation.",
            )
        response.raise_for_status()
        payload = response.json()
        latest = str(payload["info"]["version"])
        message = "Up to date." if latest == current else f"Version {latest} is available."
        return VersionInfo(current, source, latest=latest, check_message=message)
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        return VersionInfo(current, source, check_message=f"Update check failed: {exc}")


def _update_spec() -> str | None:
    info = direct_url()
    if not info or not isinstance(info.get("url"), str):
        return None
    url = str(info["url"])
    parts = urlsplit(url)
    if parts.scheme == "file":
        path = Path(unquote(parts.path))
        if not path.exists():
            raise UpdateError(f"the original installation source no longer exists: {path}")
        return str(path)
    vcs = info.get("vcs_info")
    if isinstance(vcs, dict) and isinstance(vcs.get("vcs"), str):
        return f"{vcs['vcs']}+{url}"
    return url


def update_self() -> str:
    pipx = shutil.which("pipx")
    if pipx is None:
        raise UpdateError(
            "pipx is not available; update with the same tool used to install snuetl"
        )
    spec = _update_spec()
    if spec:
        command = [pipx, "install", "--force", "--include-deps", spec]
        description = f"reinstalled from {spec}"
    else:
        command = [pipx, "upgrade", PACKAGE_NAME]
        description = "upgraded from the Python package index"
    try:
        subprocess.run(command, check=True)
    except subprocess.CalledProcessError as exc:
        raise UpdateError(f"pipx update failed with exit code {exc.returncode}") from exc
    return description
