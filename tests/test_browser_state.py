from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from snuetl import browser
from snuetl.browser import (
    _restore_auth_state,
    authenticated_entry_url,
    persist_auth_state,
    select_profile,
)
from snuetl.config import load_config


class _Context:
    def __init__(self) -> None:
        self.added_cookies = []
        self.scripts = []

    def storage_state(self):
        return {
            "cookies": [
                {
                    "name": "session",
                    "value": "secret",
                    "domain": ".snu.ac.kr",
                    "path": "/",
                    "expires": -1,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                }
            ],
            "origins": [],
        }

    def add_cookies(self, cookies):
        self.added_cookies.extend(cookies)

    def add_init_script(self, script):
        self.scripts.append(script)


def test_persistent_browser_uses_configured_firefox_without_chromium_flags(
    tmp_path: Path, monkeypatch
) -> None:
    config = replace(load_config(tmp_path / "missing.toml"), state_dir=tmp_path / "state")
    launched: dict[str, object] = {}

    class Context(_Context):
        pages = []

        @staticmethod
        def set_default_timeout(_timeout: float) -> None:
            pass

        @staticmethod
        def new_page() -> object:
            return object()

        @staticmethod
        def close() -> None:
            pass

    def launch(**kwargs: object) -> Context:
        launched.update(kwargs)
        return Context()

    playwright = SimpleNamespace(firefox=SimpleNamespace(launch_persistent_context=launch))
    monkeypatch.setattr(browser, "_playwright", lambda: lambda: nullcontext(playwright))

    with browser.persistent_browser(config, headless=True):
        pass

    assert launched["headless"] is True
    assert "args" not in launched


def test_persists_and_restores_session_cookies(tmp_path: Path) -> None:
    config = replace(load_config(tmp_path / "missing.toml"), state_dir=tmp_path / "state")
    context = _Context()
    persist_auth_state(context, config, "https://myetl.snu.ac.kr/")
    assert config.auth_state_path.stat().st_mode & 0o777 == 0o600
    assert authenticated_entry_url(config) == "https://myetl.snu.ac.kr/"

    restored = _Context()
    _restore_auth_state(restored, config)
    assert restored.added_cookies[0]["name"] == "session"


def test_rejects_non_snu_landing_url(tmp_path: Path) -> None:
    config = replace(load_config(tmp_path / "missing.toml"), state_dir=tmp_path / "state")
    persist_auth_state(_Context(), config, "https://example.com/phishing")
    assert authenticated_entry_url(config) == config.base_url


def test_normalizes_myetl_external_tool_landing_to_dashboard(tmp_path: Path) -> None:
    config = replace(load_config(tmp_path / "missing.toml"), state_dir=tmp_path / "state")
    persist_auth_state(
        _Context(),
        config,
        "https://myetl.snu.ac.kr/accounts/1/external_tools/102?launch_type=global_navigation",
    )
    assert authenticated_entry_url(config) == "https://myetl.snu.ac.kr/"


def test_profile_is_discovered_and_selected_in_one_browser_session(
    tmp_path: Path, monkeypatch
) -> None:
    config = replace(load_config(tmp_path / "missing.toml"), state_dir=tmp_path / "state")

    class Item:
        clicked = False

        def click(self) -> None:
            self.clicked = True

    class Page:
        url = "https://myetl.snu.ac.kr/"

        @staticmethod
        def goto(*args, **kwargs) -> None:
            pass

        @staticmethod
        def wait_for_timeout(*args, **kwargs) -> None:
            pass

    context = _Context()
    page = Page()
    graduate = Item()
    monkeypatch.setattr(browser, "profile_lock", lambda _: nullcontext())
    monkeypatch.setattr(
        browser,
        "open_authenticated_browser",
        lambda *args, **kwargs: nullcontext((context, page)),
    )
    monkeypatch.setattr(browser, "ensure_authenticated_page", lambda *args: None)
    monkeypatch.setattr(
        browser,
        "profile_options",
        lambda _: [("Undergraduate", Item()), ("Graduate School", graduate)],
    )
    monkeypatch.setattr(browser, "persist_auth_state", lambda *args: None)

    labels, selected = select_profile(config, lambda _: "Graduate", headless=True)

    assert labels == ["Undergraduate", "Graduate School"]
    assert selected == "Graduate School"
    assert graduate.clicked is True


def test_confirming_current_disabled_profile_succeeds_without_clicking(
    tmp_path: Path, monkeypatch
) -> None:
    config = replace(load_config(tmp_path / "missing.toml"), state_dir=tmp_path / "state")

    class CurrentItem:
        clicked = False

        @staticmethod
        def is_disabled() -> bool:
            return True

        def click(self) -> None:
            self.clicked = True

    class Page:
        url = "https://myetl.snu.ac.kr/"

        @staticmethod
        def goto(*args, **kwargs) -> None:
            pass

    current = CurrentItem()
    monkeypatch.setattr(browser, "profile_lock", lambda _: nullcontext())
    monkeypatch.setattr(
        browser,
        "open_authenticated_browser",
        lambda *args, **kwargs: nullcontext((_Context(), Page())),
    )
    monkeypatch.setattr(browser, "ensure_authenticated_page", lambda *args: None)
    monkeypatch.setattr(browser, "profile_options", lambda _: [("Current profile", current)])

    labels, selected = select_profile(config, lambda _: "Current profile", headless=True)

    assert labels == ["Current profile"]
    assert selected == "Current profile"
    assert current.clicked is False
