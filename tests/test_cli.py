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
