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


def _windows_credentials() -> list[tuple[str, str, int]]:
    # pywin32-ctypes (used by keyring) does not expose CredEnumerate.
    import ctypes
    from ctypes import wintypes
    class Credential(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                    ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                    ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                    ("CredentialBlob", ctypes.c_void_p), ("Persist", wintypes.DWORD),
                    ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                    ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]
    api = ctypes.WinDLL("advapi32", use_last_error=True)
    pointer = ctypes.POINTER(ctypes.POINTER(Credential))()
    count = wintypes.DWORD()
    api.CredEnumerateW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                  ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(type(pointer))]
    api.CredEnumerateW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    if not api.CredEnumerateW(None, 0, ctypes.byref(count), ctypes.byref(pointer)):
        error = ctypes.get_last_error()
        if error == 1168:
            return []
        raise ctypes.WinError(error)
    try:
        return [(pointer[i].contents.TargetName, pointer[i].contents.UserName,
                 pointer[i].contents.Type) for i in range(count.value)]
    finally:
        api.CredFree(pointer)


def _cleanup_credentials(keep: CanvasToken | None = None) -> None:
    """Remove this client's credentials, including keyring's upgrade leftovers."""
    if sys.platform != "win32":
        return
    from keyring.backends.Windows import win32cred
    for target, username, credential_type in _windows_credentials():
        if credential_type != win32cred.CRED_TYPE_GENERIC:
            continue
        if target != SERVICE and not target.endswith("@" + SERVICE):
            continue
        if keep is not None and target == SERVICE and username == _account(keep):
            continue
        win32cred.CredDelete(Type=win32cred.CRED_TYPE_GENERIC, TargetName=target)


def _save_token(token: CanvasToken) -> None:
    path = _metadata_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(".canvas-account.json.tmp")
    previous_secret = None
    try:
        backend = _credential_backend()
        previous_secret = backend.get_password(SERVICE, _account(token))
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
            if previous_secret is not None:
                _credential_backend().set_password(SERVICE, _account(token), previous_secret)
            else:
                _credential_backend().delete_password(SERVICE, _account(token))
        raise DesktopError("CREDENTIAL_STORE", "Could not save the Canvas token securely") from exc
    try:
        _cleanup_credentials(keep=token)
    except Exception as exc:
        # The new key and metadata are committed. Never delete the working key
        # because cleanup failed; retry on the next replacement or disconnect.
        raise DesktopError("CREDENTIAL_CLEANUP", "The new key is saved, but Windows could not remove older SNUETL keys. Retry connecting to finish cleanup.") from exc



def _delete_token(token: CanvasToken) -> None:
    try:
        _credential_backend().delete_password(SERVICE, _account(token))
    except Exception as exc:
        raise DesktopError("CREDENTIAL_STORE", "Could not remove the saved Canvas token") from exc
    _cleanup_credentials()
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
