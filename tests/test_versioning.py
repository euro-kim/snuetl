from pathlib import Path

from snuetl import versioning


def test_version_info_uses_source_fallback(monkeypatch) -> None:
    monkeypatch.setattr(versioning, "installed_version", lambda: "1.2.3")
    monkeypatch.setattr(versioning, "installation_source", lambda: "/code/etl")
    info = versioning.get_version_info()
    assert info.installed == "1.2.3"
    assert info.source == "/code/etl"


def test_local_pipx_update_reinstalls_original_checkout(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        versioning,
        "direct_url",
        lambda: {"url": tmp_path.as_uri(), "dir_info": {}},
    )
    monkeypatch.setattr(versioning.shutil, "which", lambda name: "/usr/bin/pipx")

    def fake_run(command, **kwargs):
        calls.append(command)

    monkeypatch.setattr(versioning.subprocess, "run", fake_run)
    result = versioning.update_self()
    assert result == f"reinstalled from {tmp_path}"
    assert calls == [["/usr/bin/pipx", "install", "--force", "--include-deps", str(tmp_path)]]
