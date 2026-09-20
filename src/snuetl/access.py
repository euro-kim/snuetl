"""Prefer personal Canvas access for file bytes, opening Chromium only when needed."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .canvas_api import load_token, token_status
from .config import Config
from .downloader import AuthenticatedDownloader, TokenDownloader
from .lms_session import AuthenticatedLmsSession
from .models import DownloadResult, RemoteFile

LOGGER = logging.getLogger(__name__)


class PreferredDownloader:
    def __init__(
        self,
        config: Config,
        *,
        headless: bool | None = None,
        browser_context: object | None = None,
        user_agent: str | None = None,
        asset_context: PreferredAssetContext | None = None,
        session_factory: Callable[[], AuthenticatedLmsSession] | None = None,
    ):
        self.config = config
        self.headless = headless
        self.asset_context = asset_context
        self.session_factory = session_factory
        self.token_downloader: TokenDownloader | None = None
        self.session: AuthenticatedLmsSession | None = None
        self.browser_downloader: AuthenticatedDownloader | None = (
            AuthenticatedDownloader(
                browser_context,
                user_agent=user_agent or "snuetl",
                timeout_seconds=config.timeout_seconds,
                retry_count=config.retry_count,
            )
            if browser_context is not None
            else None
        )

    def __enter__(self) -> PreferredDownloader:
        token = load_token(self.config) if token_status(self.config).get("ready") else None
        if token is not None:
            self.token_downloader = TokenDownloader(
                token.origin,
                token.value,
                timeout_seconds=self.config.timeout_seconds,
                retry_count=self.config.retry_count,
            )
        return self

    def __exit__(self, *_args: object) -> None:
        if self.token_downloader is not None:
            self.token_downloader.__exit__()
        if self.browser_downloader is not None:
            self.browser_downloader.__exit__()
        if self.session is not None:
            self.session.__exit__(None, None, None)

    def _browser(self) -> AuthenticatedDownloader:
        if self.browser_downloader is None:
            if self.asset_context is not None:
                context = self.asset_context._browser_context()
                page = self.asset_context.session.page if self.asset_context.session else context.pages[0]
                self.browser_downloader = AuthenticatedDownloader(
                    context,
                    user_agent=page.evaluate("navigator.userAgent"),
                    timeout_seconds=self.config.timeout_seconds,
                    retry_count=self.config.retry_count,
                )
                return self.browser_downloader
            self.session = (
                self.session_factory()
                if self.session_factory is not None
                else AuthenticatedLmsSession(self.config, headless=self.headless)
            )
            self.session.__enter__()
            assert self.session.page is not None
            self.browser_downloader = AuthenticatedDownloader(
                self.session.context,
                user_agent=self.session.page.evaluate("navigator.userAgent"),
                timeout_seconds=self.config.timeout_seconds,
                retry_count=self.config.retry_count,
            )
        return self.browser_downloader

    def download(self, remote: RemoteFile, destination: Path) -> DownloadResult:
        if self.token_downloader is not None:
            try:
                return self.token_downloader.download(remote, destination)
            except Exception as exc:
                LOGGER.warning("Canvas token file download unavailable; trying browser cookies: %s", exc)
        return self._browser().download(remote, destination)


class PreferredAssetContext:
    """Small request adapter for Markdown asset localization."""

    def __init__(
        self,
        config: Config,
        *,
        browser_context: object | None = None,
        session_factory: Callable[[], AuthenticatedLmsSession] | None = None,
    ):
        self.config = config
        self.session_factory = session_factory
        self.request = self
        self.browser_context = browser_context
        self.session: AuthenticatedLmsSession | None = None
        token = load_token(config) if token_status(config).get("ready") else None
        self.origin = urlsplit(token.origin).netloc.lower() if token else None
        self.public = httpx.Client(timeout=45, follow_redirects=True)

        def restrict_authorization(request: httpx.Request) -> None:
            parts = urlsplit(str(request.url))
            if parts.scheme != "https" or parts.netloc.lower() != self.origin:
                request.headers.pop("Authorization", None)

        self.authorized = (
            httpx.Client(
                headers={"Authorization": f"Bearer {token.value}"},
                event_hooks={"request": [restrict_authorization]},
                timeout=45,
                follow_redirects=True,
            )
            if token else None
        )

    def __enter__(self) -> PreferredAssetContext:
        return self

    def __exit__(self, *_args: object) -> None:
        self.public.close()
        if self.authorized is not None:
            self.authorized.close()
        if self.session is not None:
            self.session.__exit__(None, None, None)

    def _browser_context(self) -> object:
        if self.browser_context is None:
            self.session = (
                self.session_factory()
                if self.session_factory is not None
                else AuthenticatedLmsSession(self.config)
            )
            self.session.__enter__()
            self.browser_context = self.session.context
        return self.browser_context

    def new_page(self) -> object:
        return self._browser_context().new_page()

    def get(self, url: str, *, timeout: int = 45_000) -> object:
        parts = urlsplit(url)
        client = (
            self.authorized
            if self.authorized is not None
            and parts.scheme == "https"
            and parts.netloc.lower() == self.origin
            else self.public
        )
        try:
            response = client.get(url, timeout=timeout / 1000)
            final_host = (urlsplit(str(response.url)).hostname or "").lower()
            if 200 <= response.status_code < 300 and final_host not in {
                "sso.snu.ac.kr", "nsso.snu.ac.kr"
            }:
                return _AssetResponse(response)
        except httpx.HTTPError as exc:
            LOGGER.debug("asset request needs browser cookies: %s", exc)
        return self._browser_context().request.get(url, timeout=timeout)


class _AssetResponse:
    def __init__(self, response: httpx.Response):
        self.response = response
        self.status = response.status_code

    def body(self) -> bytes:
        return self.response.content

    def text(self) -> str:
        return self.response.text
