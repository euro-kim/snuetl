from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

import snuetl_windows_backend as backend  # noqa: E402


def test_windows_component_sanitization() -> None:
    assert backend.sanitize_component('bad<>:"/\\|?* name. ', "fallback") == "bad_________ name"
    assert backend.sanitize_component("CON.txt", "fallback") == "_CON.txt"
    assert backend.sanitize_component("..", "fallback") == "fallback"


def test_markdown_keeps_remote_asset_links() -> None:
    from snuetl.models import ContentItem, Course

    course = Course("7", "Algorithms", "https://myetl.snu.ac.kr/courses/7")
    item = ContentItem(
        "9",
        "7",
        "announcement",
        "Welcome",
        "https://myetl.snu.ac.kr/courses/7/announcements/9",
        body_html='<h1>Hello</h1><img src="/files/image.png"><script>bad()</script>',
    )
    body = backend._markdown_body(course, item).decode()
    assert "content_type: announcement" in body
    assert "# Hello" in body
    assert "https://myetl.snu.ac.kr/files/image.png" in body
    assert "bad()" not in body


def test_cached_content_hydration_is_revision_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SNUETL_WINDOWS_DATA_DIR", str(tmp_path))
    content = b"hello\n"
    digest, cache_path = backend._write_content_cache(content)
    manifest = {
        "schema_version": 1,
        "entries": [
            {
                "relative_path": "2026-1/Course--1/articles/page/hello.md",
                "kind": "page",
                "course_id": "1",
                "source_id": "2",
                "revision": digest,
                "size": len(content),
                "cache_path": cache_path,
                "download_url": None,
            }
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = backend.hydrate(
        {"v": 1, "kind": "page", "course_id": "1", "source_id": "2", "revision": digest}
    )
    assert Path(result["path"]).read_bytes() == content
    assert result["sha256"] == digest

    with pytest.raises(backend.BackendError, match="stale"):
        backend.hydrate(
            {"v": 1, "kind": "page", "course_id": "1", "source_id": "2", "revision": "old"}
        )


def test_protocol_returns_structured_errors_without_traceback() -> None:
    source = io.StringIO('{"id":4,"method":"no.such.method","params":{}}\n')
    target = io.StringIO()
    assert backend.serve(source, target) == 0
    response = json.loads(target.getvalue())
    assert response == {
        "id": 4,
        "ok": False,
        "error": {"code": "UNKNOWN_METHOD", "message": "Unknown backend method: no.such.method"},
    }


def test_manual_token_is_validated_before_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved = []

    class Response:
        status_code = 200
        text = '{"id":42}'

    monkeypatch.setattr(backend.httpx, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(backend, "_prepare_desktop_helper", lambda: None)
    monkeypatch.setattr(backend.codex_desktop, "_save_token", saved.append)
    value = "1~secret_example_token_123456789"
    result = backend.auth_manual(value)
    assert result["user_id"] == "42"
    assert result["manual"] is True
    assert saved[0].value == value
    assert saved[0].token_id.startswith("manual:")


def test_known_remote_size_does_not_make_an_extra_request(monkeypatch: pytest.MonkeyPatch) -> None:
    from snuetl.canvas_api import CanvasToken
    from snuetl.models import RemoteFile

    monkeypatch.setattr(backend.httpx, "Client", lambda **kwargs: pytest.fail("unexpected HTTP"))
    remote = RemoteFile("1", "2", "notes.pdf", (), "https://myetl.snu.ac.kr/file", size=42)
    token = CanvasToken("1~secret_example_token_123456789", "https://myetl.snu.ac.kr", "7", "8", "2099-01-01T00:00:00+00:00")
    assert backend._remote_size(remote, token) == 42


@pytest.mark.parametrize("prefix", ["", "Bearer ", "Authorization: Bearer "])
def test_manual_token_header_is_normalized(monkeypatch, prefix):
    saved = []
    value = "1~secret_example_token_123456789"
    def get(url, **kwargs):
        assert kwargs["headers"]["Authorization"] == f"Bearer {value}"
        return backend.httpx.Response(200, json={"id": 42})
    monkeypatch.setattr(backend.httpx, "get", get)
    monkeypatch.setattr(backend, "_prepare_desktop_helper", lambda: None)
    monkeypatch.setattr(backend.codex_desktop, "_save_token", saved.append)
    backend.auth_manual(f"  {prefix}{value}\n")
    assert saved[0].value == value


def test_manual_token_missing_prefix_is_not_guessed(monkeypatch):
    monkeypatch.setattr(backend.httpx, "get", lambda *a, **k: pytest.fail("must validate before HTTP"))
    with pytest.raises(backend.BackendError, match="complete Canvas token"):
        backend.auth_manual("secret_example_token_123456789")


@pytest.mark.parametrize("status", [{"ready": False}, {"ready": True, "valid": False}, {"ready": True, "valid": True}])
def test_auto_login_uses_independent_bundle_and_requires_valid_saved_token(tmp_path, monkeypatch, status):
    monkeypatch.setenv("SNUETL_WINDOWS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "wrong-browser-path")
    executable = backend.addon_executable()
    executable.parent.mkdir(parents=True)
    executable.touch()
    def run(args, **kwargs):
        assert args == [str(executable)]
        assert kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
        assert kwargs["env"]["PLAYWRIGHT_BROWSERS_PATH"] == "0"
        assert kwargs["env"]["SNUETL_WINDOWS_DATA_DIR"] == str(tmp_path)
        return backend.subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(backend.subprocess, "run", run)
    monkeypatch.setattr(backend, "auth_status", lambda **kw: status)
    if status.get("valid"):
        assert backend.auth_auto() == status
    else:
        with pytest.raises(backend.BackendError, match="valid saved Canvas token"):
            backend.auth_auto()


def test_auto_login_timeout_has_actionable_error(tmp_path, monkeypatch):
    monkeypatch.setenv("SNUETL_WINDOWS_DATA_DIR", str(tmp_path))
    executable = backend.addon_executable()
    executable.parent.mkdir(parents=True)
    executable.touch()
    def run(*args, **kwargs):
        raise backend.subprocess.TimeoutExpired("signin", 660)
    monkeypatch.setattr(backend.subprocess, "run", run)
    with pytest.raises(backend.BackendError, match="timed out") as error:
        backend.auth_auto()
    assert error.value.code == "SIGNIN_TIMEOUT"
