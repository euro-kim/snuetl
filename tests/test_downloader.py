from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from snuetl.downloader import AuthenticatedDownloader
from snuetl.errors import AuthenticationRequired
from snuetl.models import RemoteFile


class _Context:
    @staticmethod
    def cookies() -> list[dict[str, str]]:
        return []


def test_streams_and_atomically_finishes_download(tmp_path: Path) -> None:
    body = b"lecture notes"

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"ETag": '"revision-1"'}, content=body)

    remote = RemoteFile(
        remote_id="1",
        course_id="2",
        name="notes.pdf",
        folder_path=(),
        download_url="https://lms.test/notes.pdf",
        size=len(body),
    )
    destination = tmp_path / "notes.pdf"
    with AuthenticatedDownloader(
        _Context(),
        user_agent="test",
        timeout_seconds=5,
        retry_count=0,
        transport=httpx.MockTransport(respond),
    ) as downloader:
        result = downloader.download(remote, destination)
    assert destination.read_bytes() == body
    assert result.etag == '"revision-1"'
    assert result.size == len(body)
    assert not (tmp_path / ".notes.pdf.part").exists()


def test_authentication_error_does_not_replace_existing_file(tmp_path: Path) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=b"not authenticated")

    destination = tmp_path / "notes.pdf"
    destination.write_bytes(b"previous valid copy")
    remote = RemoteFile("1", "2", "notes.pdf", (), "https://lms.test/notes.pdf")
    with (
        AuthenticatedDownloader(
            _Context(),
            user_agent="test",
            timeout_seconds=5,
            retry_count=0,
            transport=httpx.MockTransport(respond),
        ) as downloader,
        pytest.raises(AuthenticationRequired),
    ):
        downloader.download(remote, destination)
    assert destination.read_bytes() == b"previous valid copy"
    assert not (tmp_path / ".notes.pdf.part").exists()
