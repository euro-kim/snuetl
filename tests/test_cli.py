import json
from dataclasses import replace
from pathlib import Path

from snuetl.cli import main
from snuetl.config import load_config, save_config


def test_bare_configured_command_shows_dashboard(tmp_path: Path, capsys) -> None:
    path = tmp_path / "config.toml"
    config = replace(
        load_config(tmp_path / "missing.toml"),
        state_dir=tmp_path / "state",
        download_dir=tmp_path / "downloads",
        setup_complete=True,
    )
    config.profile_dir.mkdir(parents=True)
    config.auth_state_path.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    save_config(config, path)
    assert main(["--config", str(path)]) == 0
    output = capsys.readouterr().out
    assert "snuetl" in output
    assert "Available commands" in output
    assert "snuetl courses" in output


def test_sql_requires_explicit_noninteractive_source(capsys) -> None:
    assert main(["sql", "select 1", "--json"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "INVALID_ARGUMENT"


def test_capabilities_has_versioned_agent_contract(capsys) -> None:
    assert main(["capabilities", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "1"
    assert payload["data"]["commands"]["pull"] == [
        "files",
        "articles",
        "syllabus",
        "videos",
        "all",
    ]
