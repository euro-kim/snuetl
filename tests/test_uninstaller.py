from pathlib import Path

from snuetl.provenance import InstallProvenance
from snuetl.uninstaller import UninstallInventory, execute_uninstall


def test_uninstall_removes_only_tracked_files_inside_managed_root(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    tracked = root / "course" / "tracked.pdf"
    untracked = root / "course" / "notes.txt"
    outside = tmp_path / "outside.pdf"
    for path in (tracked, untracked, outside):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"data")
    inventory = UninstallInventory(
        config_path=tmp_path / "config" / "config.toml",
        state_dir=tmp_path / "state",
        download_dir=root,
        service_path=tmp_path / "units" / "snuetl.service",
        timer_path=tmp_path / "units" / "snuetl.timer",
        tracked_files=(tracked, outside),
        provenance=InstallProvenance(),
        pipx_install=False,
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
    assert outside.exists()
    assert summary.files_removed == 1
    assert summary.warnings[0]["code"] == "UNSAFE_FILE_PATH"
