"""Native macOS and Windows Canvas helper for the SNUETL Codex skill.

The command prints JSON only. Browser login and OS credential storage happen in this
process; neither the token nor the login page is exposed to the agent as tool output.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import suppress
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

from .canvas_adapter import _response_json
from .canvas_api import (
    CanvasClient,
    CanvasToken,
    _browser_token_id,
    _create_token_in_ui,
    live_data,
)
from .config import Config
from .errors import AuthenticationRequired, DiscoveryError

SERVICE = "snuetl-codex"
PURPOSE = "snuetl-codex"
LOGIN_URL = "https://etl.snu.ac.kr/login"
CANVAS_HOSTS = {"etl.snu.ac.kr", "myetl.snu.ac.kr"}
COMMANDS = ("courses", "upcoming", "missing", "submissions", "grades", "calendar", "discussions")


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


def _login(context: Any, page: Any) -> tuple[str, str]:
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        if page.is_closed():
            raise DesktopError("LOGIN_CANCELLED", "The login browser was closed")
        parts = urlsplit(page.url)
        if parts.scheme == "https" and parts.hostname in CANVAS_HOSTS and parts.port is None:
            origin = f"https://{parts.hostname}"
            try:
                response = context.request.get(f"{origin}/api/v1/users/self/profile", timeout=5000)
                if response.status == 200:
                    profile = _response_json(response)
                    if isinstance(profile, dict) and profile.get("id") is not None:
                        return origin, str(profile["id"])
            except Exception:
                pass  # Login and MFA may still be in progress.
        page.wait_for_timeout(1200)
    raise DesktopError("LOGIN_TIMEOUT", "SNU login was not completed within ten minutes")


def _revoke_in_browser(page: Any, token: CanvasToken) -> None:
    page.goto(token.origin + "/profile/settings", wait_until="domcontentloaded")
    if page.locator("#access_tokens_holder").count() == 0:
        raise DesktopError("REVOKE_FAILED", "Canvas token settings are unavailable")
    link = page.locator(
        f"tr.access_token a.delete_key_link[rel$='/profile/tokens/{quote(token.token_id, safe='')}']"
    )
    if link.count() == 0:
        return  # Already revoked.
    page.once("dialog", lambda dialog: dialog.accept())
    link.first.click()
    link.first.wait_for(state="detached", timeout=10000)


def _browser_operation(*, rotate: bool, disconnect: bool) -> dict[str, Any]:
    old = _load_token()
    if disconnect and old is None:
        return {"connected": False}
    if not rotate and not disconnect and old is not None:
        try:
            _profile(old)
            return {"connected": True, "origin": old.origin, "user_id": old.user_id, "expires_at": old.expires_at}
        except (AuthenticationRequired, DiscoveryError, DesktopError):
            pass

    _credential_backend()  # Fail before creating a Canvas token if secure storage is unavailable.

    try:
        if getattr(sys, "frozen", False):
            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise DesktopError("BROWSER_MISSING", "The bundled setup browser is unavailable") from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        try:
            context = browser.new_context(accept_downloads=False)
            page = context.new_page()
            origin, user_id = _login(context, page)
            if old and (old.origin != origin or old.user_id != user_id):
                raise DesktopError("ACCOUNT_MISMATCH", "Sign in with the connected SNU eTL account")
            if disconnect:
                assert old is not None
                _revoke_in_browser(page, old)
                _delete_token(old)
                return {"connected": False, "revoked": True}

            expires = datetime.now(UTC) + timedelta(days=365)
            value, token_id, actual_expiry = _create_token_in_ui(
                page, origin, expires.date(), purpose_name=PURPOSE
            )
            if not token_id:
                try:
                    token_id = _browser_token_id(
                        context, page, origin, user_id, purpose_name=PURPOSE
                    )
                except Exception as exc:
                    raise DesktopError(
                        "TOKEN_ID_UNKNOWN",
                        "Canvas created a token, but its ID could not be confirmed; review Canvas Account Settings",
                    ) from exc
            new = CanvasToken(value, origin, user_id, token_id, actual_expiry or expires.isoformat())
            try:
                _profile(new)
                _save_token(new)
            except Exception as exc:
                try:
                    _revoke_in_browser(page, new)
                except Exception:
                    raise DesktopError(
                        "TOKEN_CLEANUP",
                        "The new Canvas token may still be active; review Canvas Account Settings",
                    ) from exc
                raise
            result: dict[str, Any] = {
                "connected": True,
                "origin": origin,
                "user_id": user_id,
                "expires_at": new.expires_at,
            }
            if old and old.token_id != new.token_id:
                try:
                    _revoke_in_browser(page, old)
                    _credential_backend().delete_password(SERVICE, _account(old))
                except Exception:
                    result["warning"] = "The previous token may still be active; review Canvas Account Settings"
            return result
        finally:
            browser.close()


def _query(args: argparse.Namespace) -> list[dict[str, Any]]:
    token = _load_token()
    if token is None:
        raise DesktopError("NOT_CONNECTED", "Run '$snuetl connect' in Codex first")
    expiry = datetime.fromisoformat(token.expires_at.replace("Z", "+00:00"))
    if expiry <= datetime.now(UTC):
        raise DesktopError("TOKEN_EXPIRED", "Canvas access expired; run '$snuetl connect'")
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    start_value = getattr(args, "start", None)
    end_value = getattr(args, "end", None)
    start = date.fromisoformat(start_value) if start_value else today
    end = date.fromisoformat(end_value) if end_value else None
    if end is not None and end < start:
        raise DesktopError("INVALID_DATE", "The end date must be on or after the start date")
    if args.command == "courses":
        with CanvasClient(_config(), token) as client:
            return [
                {"id": item.get("id"), "name": item.get("name"), "course_code": item.get("course_code")}
                for item in client.pages("/api/v1/courses?enrollment_state=active&state[]=available&per_page=100")
            ]
    return live_data(
        _config(), args.command, token=token, course=getattr(args, "course", None), start=start, end=end
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="snuetl-codex")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("connect", "rotate", "disconnect", "status", *COMMANDS):
        item = sub.add_parser(command)
        if command == "status":
            item.add_argument("--verify", action="store_true")
        if command in {"submissions", "grades", "discussions"}:
            item.add_argument("--course")
        if command in {"upcoming", "calendar"}:
            item.add_argument("--from", dest="start")
            item.add_argument("--to", dest="end")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "status":
            data = _status(verify=args.verify)
        elif args.command in {"connect", "rotate", "disconnect"}:
            data = _browser_operation(rotate=args.command == "rotate", disconnect=args.command == "disconnect")
        else:
            data = _query(args)
        print(json.dumps({"ok": True, "command": args.command, "data": data}, ensure_ascii=False))
        return 0
    except DesktopError as exc:
        print(json.dumps({"ok": False, "command": args.command, "error": {"code": exc.code, "message": str(exc)}}))
        return 1
    except AuthenticationRequired:
        print(json.dumps({"ok": False, "command": args.command, "error": {"code": "TOKEN_REVOKED", "message": "Canvas access failed; run '$snuetl connect'"}}))
        return 1
    except (DiscoveryError, ValueError):
        print(json.dumps({"ok": False, "command": args.command, "error": {"code": "CANVAS_ERROR", "message": "The Canvas request failed or returned unexpected data"}}))
        return 1
    except Exception:
        print(json.dumps({"ok": False, "command": args.command, "error": {"code": "INTERNAL_ERROR", "message": "The desktop helper failed; check the installation"}}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
