from types import SimpleNamespace
import pytest
import windows_auth as auth
from snuetl.canvas_api import CanvasToken


@pytest.fixture
def vault(tmp_path, monkeypatch):
    values = {}
    service = auth.SERVICE
    class Vault:
        def get_password(self, service, account):
            return values.get(service, (None, None))[1] if values.get(service, (None, None))[0] == account else values.get(account + "@" + service, (None, None))[1]
        def set_password(self, service, account, secret):
            if service in values:
                old_account, old_secret = values[service]
                values[old_account + "@" + service] = (old_account, old_secret)
            values[service] = (account, secret)
        def delete_password(self, service, account):
            for target in (service, account + "@" + service):
                if values.get(target, (None,))[0] == account:
                    del values[target]
    import keyring.backends.Windows as windows
    monkeypatch.setattr(auth, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(auth, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(auth, "_credential_backend", Vault)
    monkeypatch.setattr(auth, "_windows_credentials", lambda: [(target, account, 1) for target, (account, _) in values.items()])
    monkeypatch.setattr(windows, "win32cred", SimpleNamespace(CRED_TYPE_GENERIC=1, CredDelete=lambda **kw: values.pop(kw["TargetName"])), raising=False)
    return values


def token(id="new", secret="new-secret"):
    return CanvasToken(secret, "https://myetl.snu.ac.kr", "42", id, "2099-01-01T00:00:00+00:00")


def test_replacement_cleans_all_historical_keys_and_keeps_other_services(vault):
    vault[auth.SERVICE] = (auth._account(token("old")), "old-secret")
    vault["orphan@" + auth.SERVICE] = ("orphan", "orphan-secret")
    vault["github"] = ("github", "unrelated")
    auth._save_token(token())
    assert vault == {auth.SERVICE: (auth._account(token()), "new-secret"), "github": ("github", "unrelated")}
    assert auth._load_token() == token()
    assert "new-secret" not in auth._metadata_path().read_text()


def test_failed_metadata_commit_preserves_previous_connection(vault, monkeypatch):
    old = token("old", "old-secret")
    auth._save_token(old)
    monkeypatch.setattr(auth.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("failed")))
    with pytest.raises(auth.DesktopError, match="save"):
        auth._save_token(token())
    assert auth._load_token() == old


def test_cleanup_failure_keeps_committed_replacement(vault, monkeypatch):
    monkeypatch.setattr(auth, "_cleanup_credentials", lambda **kw: (_ for _ in ()).throw(OSError("failed")))
    with pytest.raises(auth.DesktopError) as caught:
        auth._save_token(token())
    assert caught.value.code == "CREDENTIAL_CLEANUP"
    assert auth._load_token() == token()


def test_disconnect_removes_orphaned_keys(vault):
    auth._save_token(token())
    vault["orphan@" + auth.SERVICE] = ("orphan", "old-secret")
    auth._delete_token(token())
    assert not vault
    assert not auth._metadata_path().exists()


@pytest.mark.parametrize("revoke_fails", [False, True])
def test_automatic_creation_revokes_every_owned_key_first(monkeypatch, revoke_fails):
    from snuetl import codex_desktop as desktop
    from playwright import sync_api
    events = []
    class Field:
        def __init__(self, text): self.text = text
        def count(self): return 1
        def inner_text(self): return self.text
        @property
        def first(self): return self
        def get_attribute(self, _): return self.text
    class Row:
        def __init__(self, purpose, id): self.purpose, self.id = purpose, id
        def locator(self, selector):
            return Field(self.purpose if selector == "td.purpose" else "/profile/tokens/" + self.id)
    rows = [Row("snuetl-windows", "1"), Row("other-app", "2"), Row("snuetl-windows", "3")]
    page = SimpleNamespace(goto=lambda *a, **k: None, locator=lambda selector: SimpleNamespace(count=lambda: 1, all=lambda: rows))
    browser = SimpleNamespace(new_context=lambda **kw: SimpleNamespace(new_page=lambda: page), close=lambda: None)
    class Runtime:
        def __enter__(self): return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kw: browser))
        def __exit__(self, *args): pass
    monkeypatch.setattr(sync_api, "sync_playwright", Runtime)
    monkeypatch.setattr(desktop, "PURPOSE", "snuetl-windows")
    monkeypatch.setattr(desktop, "_load_token", lambda: None)
    monkeypatch.setattr(desktop, "_credential_backend", lambda: None)
    monkeypatch.setattr(desktop, "_login", lambda *a: (token().origin, "42"))
    def revoke(page, item):
        events.append("revoke:" + item.token_id)
        if revoke_fails: raise desktop.DesktopError("REVOKE_FAILED", "failed")
    monkeypatch.setattr(desktop, "_revoke_in_browser", revoke)
    def create(*a, **kw):
        events.append("create")
        return token().value, token().token_id, token().expires_at
    monkeypatch.setattr(desktop, "_create_token_in_ui", create)
    monkeypatch.setattr(desktop, "_profile", lambda t: {})
    monkeypatch.setattr(desktop, "_save_token", lambda t: events.append("save"))
    if revoke_fails:
        with pytest.raises(desktop.DesktopError): desktop._browser_operation(rotate=True, disconnect=False)
        assert events == ["revoke:1"]
    else:
        assert desktop._browser_operation(rotate=True, disconnect=False)["connected"]
        assert events == ["revoke:1", "revoke:3", "create", "save"]
