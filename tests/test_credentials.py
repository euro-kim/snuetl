from dataclasses import replace
from pathlib import Path

from snuetl.browser import ensure_authenticated_page
from snuetl.config import load_config
from snuetl.credentials import (
    SavedCredentials,
    delete_credentials,
    load_credentials,
    save_credentials,
)


def _config(tmp_path: Path):
    return replace(load_config(tmp_path / "missing.toml"), state_dir=tmp_path / "state")


def test_saved_credentials_round_trip_with_private_permissions(tmp_path: Path) -> None:
    config = _config(tmp_path)
    credentials = SavedCredentials("student", "secret", False)
    save_credentials(config, credentials)
    assert load_credentials(config) == credentials
    assert config.credentials_path.stat().st_mode & 0o777 == 0o600
    assert config.state_dir.stat().st_mode & 0o777 == 0o700
    delete_credentials(config)
    assert load_credentials(config) is None


def test_expired_session_uses_saved_credentials(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    save_credentials(config, SavedCredentials("student", "secret", True))
    calls = []
    monkeypatch.setattr("snuetl.browser.browser_looks_authenticated", lambda context, page: False)
    monkeypatch.setattr(
        "snuetl.browser._login_page",
        lambda candidate, context, page, username, password, **kwargs: calls.append(
            (candidate, context, page, username, password, kwargs)
        ),
    )
    context = object()
    page = object()
    ensure_authenticated_page(config, context, page)
    assert calls[0][3:5] == ("student", "secret")
    assert calls[0][5]["trust_browser"] is True
