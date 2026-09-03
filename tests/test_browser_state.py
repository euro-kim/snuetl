from dataclasses import replace
from pathlib import Path

from snuetl.browser import _restore_auth_state, authenticated_entry_url, persist_auth_state
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
