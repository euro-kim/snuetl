"""Browser-free Windows credential storage, compatible with the existing account format."""
from __future__ import annotations
import json, os, sys
from contextlib import suppress
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from snuetl.canvas_api import CanvasClient, CanvasToken
from snuetl.config import Config
from snuetl.errors import AuthenticationRequired, DiscoveryError
SERVICE = PURPOSE = "snuetl-windows"
LOGIN_URL = "https://etl.snu.ac.kr/login"
CANVAS_HOSTS = {"etl.snu.ac.kr", "myetl.snu.ac.kr"}
class DesktopError(Exception):
    """An error safe to show to a user and to Codex."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SNUETL Codex"
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA")
        if not root:
            raise DesktopError("SYSTEM_CONFIG", "Windows LOCALAPPDATA is unavailable")
        return Path(root) / "SNUETL Codex"
    raise DesktopError("UNSUPPORTED_OS", "Use the macOS or native Windows desktop helper")


def _credential_backend() -> Any:
    try:
        if sys.platform == "darwin":
            from keyring.backends.macOS import Keyring

            return Keyring()
        if sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring

            return WinVaultKeyring()
    except Exception as exc:
        raise DesktopError("CREDENTIAL_STORE", "The OS credential store is unavailable") from exc
    raise DesktopError("UNSUPPORTED_OS", "Use the macOS or native Windows desktop helper")


def _account(token: CanvasToken) -> str:
    return f"{token.origin}|{token.user_id}|{token.token_id}"


def _metadata_path() -> Path:
    return data_dir() / "canvas-account.json"


def _load_metadata() -> CanvasToken | None:
    try:
        data = json.loads(_metadata_path().read_text(encoding="utf-8"))
        token = CanvasToken(value="", **data)
        parts = urlsplit(token.origin)
        if (
            parts.scheme != "https"
            or parts.hostname not in CANVAS_HOSTS
            or parts.port is not None
            or not token.user_id
            or not token.token_id
        ):
            raise ValueError("invalid Canvas account")
        expiry = datetime.fromisoformat(token.expires_at.replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            raise ValueError("token expiry has no time zone")
        return token
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as exc:
        raise DesktopError("ACCOUNT_DATA", "Saved Canvas account data is unreadable") from exc


def _load_token() -> CanvasToken | None:
    metadata = _load_metadata()
    if metadata is None:
        return None
    try:
        secret = _credential_backend().get_password(SERVICE, _account(metadata))
    except Exception as exc:
        raise DesktopError("CREDENTIAL_STORE", "Cannot read the OS credential store") from exc
    return CanvasToken(secret, metadata.origin, metadata.user_id, metadata.token_id, metadata.expires_at) if secret else None


def _save_token(token: CanvasToken) -> None:
    path = _metadata_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(".canvas-account.json.tmp")
    try:
        backend = _credential_backend()
        backend.set_password(SERVICE, _account(token), token.value)
        metadata = asdict(token)
        metadata.pop("value")
        temporary.write_text(json.dumps(metadata), encoding="utf-8")
        if sys.platform == "darwin":
            temporary.chmod(0o600)
        os.replace(temporary, path)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        with suppress(Exception):
            _credential_backend().delete_password(SERVICE, _account(token))
        raise DesktopError("CREDENTIAL_STORE", "Could not save the Canvas token securely") from exc


def _delete_token(token: CanvasToken) -> None:
    try:
        _credential_backend().delete_password(SERVICE, _account(token))
    except Exception as exc:
        raise DesktopError("CREDENTIAL_STORE", "Could not remove the saved Canvas token") from exc
    _metadata_path().unlink(missing_ok=True)


def _config() -> Config:
    return Config(
        base_url=LOGIN_URL,
        download_dir=data_dir(),
        state_dir=data_dir(),
        browser_channel=None,
        browser_executable_path=None,
        headless=False,
        timeout_seconds=30,
        login_timeout_seconds=600,
        retry_count=2,
        excluded_course_ids=frozenset(),
        setup_complete=True,
        canvas_api_enabled=True,
    )


def _profile(token: CanvasToken) -> dict[str, Any]:
    with CanvasClient(_config(), token) as client:
        profile = client.request("GET", "/api/v1/users/self/profile")
    if not isinstance(profile, dict) or str(profile.get("id")) != token.user_id:
        raise DesktopError("ACCOUNT_MISMATCH", "The Canvas token belongs to another account")
    return profile


def _status(*, verify: bool) -> dict[str, Any]:
    metadata = _load_metadata()
    if metadata is None:
        return {"configured": False, "ready": False}
    token = _load_token()
    expiry = datetime.fromisoformat(metadata.expires_at.replace("Z", "+00:00"))
    expired = expiry <= datetime.now(UTC)
    result: dict[str, Any] = {
        "configured": token is not None,
        "ready": token is not None and not expired,
        "expired": expired,
        "origin": metadata.origin,
        "user_id": metadata.user_id,
        "expires_at": metadata.expires_at,
    }
    if verify and token and not expired:
        try:
            _profile(token)
            result["valid"] = True
        except (AuthenticationRequired, DiscoveryError, DesktopError):
            result["valid"] = False
    return result
