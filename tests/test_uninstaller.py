from pathlib import Path

from snuetl import uninstaller
from snuetl.provenance import InstallProvenance
from snuetl.uninstaller import UninstallInventory, execute_uninstall


def test_uninstall_removes_only_tracked_files_inside_managed_root(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    tracked = root / "course" / "tracked.pdf"
    untracked = root / "course" / "notes.txt"
    outside = tmp_path / "outside.pdf"
    routed_root = tmp_path / "classes"
    routed = routed_root / "tracked-video.mp4"
    routed_user_file = routed_root / "D" / "assignment.txt"
    for path in (tracked, untracked, outside, routed, routed_user_file):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"data")
    inventory = UninstallInventory(
        config_path=tmp_path / "config" / "config.toml",
        state_dir=tmp_path / "state",
        download_dir=root,
        service_path=tmp_path / "units" / "snuetl.service",
        timer_path=tmp_path / "units" / "snuetl.timer",
        tracked_files=(tracked, routed, outside),
        provenance=InstallProvenance(),
        pipx_install=False,
        managed_roots=(root, routed_root),
    )
    summary = execute_uninstall(
        inventory,
        delete_files=True,
        purge_config=False,
        purge_state=False,
        remove_shared_deps=False,
        remove_package=False,
    )
    assert not tracked.exists()
    assert untracked.exists()
    assert not routed.exists()
    assert routed_user_file.exists()
    assert outside.exists()
    assert summary.files_removed == 2
    assert summary.warnings[0]["code"] == "UNSAFE_FILE_PATH"


def test_uninstall_stops_and_removes_discord_daemon(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    discord_service = unit_dir / "snuetl-discord.service"
    discord_service.write_text("unit", encoding="utf-8")
    monkeypatch.setattr(uninstaller.shutil, "which", lambda name: "/usr/bin/systemctl")

    def fake_run(command, **kwargs):
        calls.append(command)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(uninstaller.subprocess, "run", fake_run)
    inventory = UninstallInventory(
        config_path=tmp_path / "config.toml",
        state_dir=tmp_path / "state",
        download_dir=tmp_path / "downloads",
        service_path=unit_dir / "snuetl.service",
        timer_path=unit_dir / "snuetl.timer",
        tracked_files=(),
        provenance=InstallProvenance(),
        pipx_install=False,
    )
    execute_uninstall(
        inventory,
        delete_files=False,
        purge_config=False,
        purge_state=False,
        remove_shared_deps=False,
        remove_package=False,
    )

    assert not discord_service.exists()
    assert any(call[-1] == "snuetl-discord.service" for call in calls)
