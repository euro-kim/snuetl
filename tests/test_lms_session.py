from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace

from snuetl.config import load_config
from snuetl.lms_session import AuthenticatedLmsSession


def test_session_owns_lock_browser_authentication_and_discovery(tmp_path) -> None:
    events: list[str] = []
    config = replace(load_config(tmp_path / "missing.toml"), state_dir=tmp_path / "state")

    class Page:
        url = "https://lms.test/start"

        def goto(self, url, **_kwargs):
            events.append(f"goto:{url}")
            self.url = url

    class Context:
        pass

    @contextmanager
    def lock(_path):
        events.append("lock:open")
        try:
            yield
        finally:
            events.append("lock:close")

    @contextmanager
    def browser(_config, **_kwargs):
        events.append("browser:open")
        try:
            yield Context(), Page()
        finally:
            events.append("browser:close")

    def authenticate(_config, _context, _page):
        events.append("authenticate")

    def discovery(_context, _page, **_kwargs):
        events.append("discovery")
        return object()

    with AuthenticatedLmsSession(
        config,
        persist_on_open=False,
        profile_lock_factory=lock,
        browser_factory=browser,
        entry_url_factory=lambda _config: "https://lms.test/home",
        authenticator=authenticate,
        discovery_factory=discovery,
    ) as session:
        assert session.discovery is not None

    assert events == [
        "lock:open",
        "browser:open",
        "goto:https://lms.test/home",
        "authenticate",
        "discovery",
        "browser:close",
        "lock:close",
    ]
