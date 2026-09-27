from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from snuetl import canvas_api, codex_desktop


class MemoryCredentials:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.values[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        del self.values[(service, account)]


@pytest.fixture
def desktop_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MemoryCredentials:
    credentials = MemoryCredentials()
    monkeypatch.setattr(codex_desktop, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(codex_desktop, "_credential_backend", lambda: credentials)
    return credentials


def _token() -> canvas_api.CanvasToken:
    return canvas_api.CanvasToken(
        "1~secret-example-token-123456789",
        "https://myetl.snu.ac.kr",
        "42",
        "77",
        (datetime.now(UTC) + timedelta(days=365)).isoformat(),
    )


def test_desktop_secret_is_only_in_os_credential_store(
    desktop_store: MemoryCredentials, capsys: pytest.CaptureFixture[str]
) -> None:
    token = _token()
    codex_desktop._save_token(token)
    assert token.value not in codex_desktop._metadata_path().read_text(encoding="utf-8")
    assert token.value in desktop_store.values.values()
    assert codex_desktop._load_token() == token
    assert codex_desktop.main(["status"]) == 0
    assert token.value not in capsys.readouterr().out


def test_desktop_upcoming_uses_saved_token_without_leaking_it(
    desktop_store: MemoryCredentials,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = _token()
    codex_desktop._save_token(token)

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {token.value}"
        assert request.url.host == "myetl.snu.ac.kr"
        assert request.url.path == "/api/v1/planner/items"
        return httpx.Response(
            200,
            json=[{"course_id": 17, "plannable_type": "assignment", "plannable": {"title": "Essay"}}],
        )

    original = httpx.Client
    monkeypatch.setattr(
        canvas_api.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    assert codex_desktop.main(["upcoming"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["data"][0]["title"] == "Essay"
    assert token.value not in output


def test_desktop_missing_connection_is_actionable(
    desktop_store: MemoryCredentials, capsys: pytest.CaptureFixture[str]
) -> None:
    assert codex_desktop.main(["missing"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error"]["code"] == "NOT_CONNECTED"
    assert "$snuetl connect" in output["error"]["message"]


def test_desktop_rejects_wrong_origin_metadata(
    desktop_store: MemoryCredentials, capsys: pytest.CaptureFixture[str]
) -> None:
    metadata = {
        "origin": "https://another.example.org",
        "user_id": "42",
        "token_id": "77",
        "expires_at": _token().expires_at,
    }
    codex_desktop._metadata_path().write_text(json.dumps(metadata), encoding="utf-8")
    assert codex_desktop.main(["status"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "ACCOUNT_DATA"


def test_connect_creates_token_without_returning_secret(
    desktop_store: MemoryCredentials,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from playwright import sync_api

    class Browser:
        closed = False

        def new_context(self, **_kwargs: object) -> object:
            return SimpleNamespace(new_page=lambda: object())

        def close(self) -> None:
            self.closed = True

    browser = Browser()

    class Playwright:
        def __enter__(self) -> object:
            return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **_kwargs: browser))

        def __exit__(self, *_args: object) -> None:
            return None

    token = _token()
    monkeypatch.setattr(sync_api, "sync_playwright", Playwright)
    monkeypatch.setattr(
        codex_desktop, "_login", lambda _context, _page: (token.origin, token.user_id)
    )
    monkeypatch.setattr(
        codex_desktop,
        "_create_token_in_ui",
        lambda _page, _origin, _date, **_kwargs: (
            token.value,
            token.token_id,
            token.expires_at,
        ),
    )
    monkeypatch.setattr(codex_desktop, "_profile", lambda _token: {"id": token.user_id})
    assert codex_desktop.main(["connect"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["data"]["connected"] is True
    assert token.value not in output
    assert codex_desktop._load_token() == token
    assert browser.closed is True


def test_connect_revokes_new_token_if_secure_storage_fails(
    desktop_store: MemoryCredentials,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from playwright import sync_api

    token = _token()
    revoked: list[canvas_api.CanvasToken] = []

    class Browser:
        def new_context(self, **_kwargs: object) -> object:
            return SimpleNamespace(new_page=lambda: object())

        def close(self) -> None:
            pass

    class Playwright:
        def __enter__(self) -> object:
            return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **_kwargs: Browser()))

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(sync_api, "sync_playwright", Playwright)
    monkeypatch.setattr(codex_desktop, "_login", lambda *_args: (token.origin, token.user_id))
    monkeypatch.setattr(
        codex_desktop,
        "_create_token_in_ui",
        lambda *_args, **_kwargs: (token.value, token.token_id, token.expires_at),
    )
    monkeypatch.setattr(codex_desktop, "_profile", lambda _token: {"id": token.user_id})
    monkeypatch.setattr(codex_desktop, "_save_token", lambda _token: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(codex_desktop, "_revoke_in_browser", lambda _page, item: revoked.append(item))

    assert codex_desktop.main(["connect"]) == 1
    assert revoked == [token]
    assert token.value not in capsys.readouterr().out

@pytest.mark.parametrize("creation_fails", [False, True])
def test_connect_preserves_result_when_browser_cleanup_fails(
    desktop_store: MemoryCredentials,
    monkeypatch: pytest.MonkeyPatch,
    creation_fails: bool,
) -> None:
    from playwright import sync_api

    from snuetl.errors import DiscoveryError

    token = _token()

    class Browser:
        def new_context(self, **_kwargs: object) -> object:
            return SimpleNamespace(new_page=lambda: object())

        def close(self) -> None:
            raise RuntimeError("Connection closed while reading from the driver")

    class Playwright:
        def __enter__(self) -> object:
            return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **_kwargs: Browser()))

        def __exit__(self, *_args: object) -> None:
            return None

    def create(*_args: object, **_kwargs: object) -> tuple[str, str, str]:
        if creation_fails:
            raise DiscoveryError("Canvas token form is not supported")
        return token.value, token.token_id, token.expires_at

    monkeypatch.setattr(sync_api, "sync_playwright", Playwright)
    monkeypatch.setattr(codex_desktop, "_login", lambda *_args: (token.origin, token.user_id))
    monkeypatch.setattr(codex_desktop, "_create_token_in_ui", create)
    monkeypatch.setattr(codex_desktop, "_profile", lambda _token: {"id": token.user_id})
    if creation_fails:
        with pytest.raises(DiscoveryError, match="token form"):
            codex_desktop._browser_operation(rotate=False, disconnect=False)
    else:
        assert codex_desktop._browser_operation(rotate=False, disconnect=False)["connected"]
        assert codex_desktop._load_token() == token
