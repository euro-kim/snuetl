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
