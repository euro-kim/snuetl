from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from types import TracebackType
from typing import Any

from .adapters import DiscoveryService
from .browser import open_authenticated_browser
from .browser_auth import (
    authenticated_entry_url,
    ensure_authenticated_page,
    persist_auth_state,
)
from .config import Config
from .profile import profile_lock


@dataclass(slots=True)
class AuthenticatedLmsSession:
    """Own one locked, authenticated browser session and its discovery service."""

    config: Config
    headless: bool | None = None
    context: Any = None
    page: Any = None
    landing_url: str = ""
    discovery: DiscoveryService | None = None
    persist_on_open: bool = True
    profile_lock_factory: Callable[..., Any] = profile_lock
    browser_factory: Callable[..., Any] = open_authenticated_browser
    entry_url_factory: Callable[[Config], str] = authenticated_entry_url
    authenticator: Callable[[Config, Any, Any], None] = ensure_authenticated_page
    discovery_factory: Callable[..., DiscoveryService] = DiscoveryService
    _stack: ExitStack | None = None

    def __enter__(self) -> AuthenticatedLmsSession:
        stack = ExitStack()
        self._stack = stack
        try:
            stack.enter_context(self.profile_lock_factory(self.config.lock_path))
            effective_headless = self.config.headless if self.headless is None else self.headless
            self.context, self.page = stack.enter_context(
                self.browser_factory(self.config, headless=effective_headless)
            )
            self.page.goto(self.entry_url_factory(self.config), wait_until="domcontentloaded")
            self.authenticator(self.config, self.context, self.page)
            self.landing_url = self.page.url
            self.discovery = self.discovery_factory(
                self.context,
                self.page,
                timeout_seconds=self.config.timeout_seconds,
            )
            # Authentication may refresh cookies while opening the LMS. Save them
            # immediately so later course-specific errors cannot discard the session.
            if self.persist_on_open:
                self.persist(self.landing_url)
            return self
        except BaseException:
            stack.close()
            self._stack = None
            raise

    def persist(self, landing_url: str | None = None) -> None:
        if self.context is None:
            raise RuntimeError("LMS session is not open")
        persist_auth_state(self.context, self.config, landing_url or self.landing_url)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        stack, self._stack = self._stack, None
        if stack is not None:
            stack.close()
