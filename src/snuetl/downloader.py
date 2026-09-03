from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .errors import AuthenticationRequired
from .models import DownloadResult, RemoteFile


class DownloadError(RuntimeError):
    pass


class AuthenticatedDownloader:
    def __init__(
        self,
        browser_context: Any,
        *,
        user_agent: str,
        timeout_seconds: float,
        retry_count: int,
        transport: httpx.BaseTransport | None = None,
    ):
        cookies = httpx.Cookies()
        for item in browser_context.cookies():
            cookies.set(
                item["name"],
                item["value"],
                domain=item.get("domain") or None,
                path=item.get("path") or "/",
            )
        self.client = httpx.Client(
            cookies=cookies,
            headers={"User-Agent": user_agent},
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
            transport=transport,
        )
        self.retry_count = retry_count

    def __enter__(self) -> AuthenticatedDownloader:
        return self

    def __exit__(self, *_: object) -> None:
        self.client.close()

    def download(self, remote: RemoteFile, destination: Path) -> DownloadResult:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.part")
        temporary.unlink(missing_ok=True)
        last_error: Exception | None = None
        for attempt in range(self.retry_count + 1):
            try:
                with self.client.stream("GET", remote.download_url) as response:
                    final_host = (urlsplit(str(response.url)).hostname or "").lower()
                    if response.status_code == 401 or final_host in {
                        "sso.snu.ac.kr",
                        "nsso.snu.ac.kr",
                    }:
                        raise AuthenticationRequired(
                            "the eTL session expired while downloading a file"
                        )
                    if response.status_code == 403:
                        raise DownloadError("download authorization was rejected")
                    if response.status_code == 429 or response.status_code >= 500:
                        raise DownloadError(f"temporary HTTP {response.status_code}")
                    response.raise_for_status()
                    digest = hashlib.sha256()
                    size = 0
                    with temporary.open("wb") as handle:
                        for chunk in response.iter_bytes():
                            handle.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                        handle.flush()
                        os.fsync(handle.fileno())
                    if remote.size is not None and size != remote.size:
                        raise DownloadError(
                            f"downloaded size {size} does not match expected size {remote.size}"
                        )
                    os.replace(temporary, destination)
                    return DownloadResult(
                        size=size,
                        sha256=digest.hexdigest(),
                        etag=response.headers.get("etag"),
                    )
            except (httpx.HTTPError, OSError, DownloadError) as exc:
                last_error = exc
                temporary.unlink(missing_ok=True)
                if attempt < self.retry_count:
                    time.sleep(min(2**attempt, 15))
        raise DownloadError(str(last_error) if last_error else "download failed")
