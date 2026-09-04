from pathlib import Path

from snuetl import onboarding
from snuetl.config import load_config


def test_detects_arm64_aliases(monkeypatch) -> None:
    monkeypatch.setattr(onboarding.platform, "machine", lambda: "aarch64")
    assert onboarding.is_arm64() is True
    monkeypatch.setattr(onboarding.platform, "machine", lambda: "arm64")
    assert onboarding.is_arm64() is True
    monkeypatch.setattr(onboarding.platform, "machine", lambda: "armv7l")
    assert onboarding.is_arm64() is False


def test_arm64_uses_available_system_chromium(tmp_path: Path, monkeypatch) -> None:
    config = load_config(tmp_path / "missing.toml")
    chromium = Path("/usr/bin/chromium")
    calls = []
    monkeypatch.setattr(onboarding, "is_arm64", lambda: True)
    monkeypatch.setattr(onboarding, "is_raspberry_pi", lambda: False)
    monkeypatch.setattr(onboarding, "find_system_chromium", lambda: chromium)

    def available(candidate):
        calls.append(candidate)
        return (candidate.browser_executable_path == chromium, "test")

    monkeypatch.setattr(onboarding, "browser_available", available)
    selected = onboarding.ensure_browser(config)
    assert selected.browser_executable_path == chromium
    assert selected.browser_channel is None
    assert len(calls) == 2


def test_raspberry_pi_installer_uses_sudo_and_apt(monkeypatch) -> None:
    paths = {
        "apt-get": "/usr/bin/apt-get",
        "sudo": "/usr/bin/sudo",
    }
    commands = []
    chromium = Path("/usr/bin/chromium")
    monkeypatch.setattr(onboarding.shutil, "which", paths.get)
    monkeypatch.setattr(onboarding.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(
        onboarding.subprocess,
        "run",
        lambda command, check: commands.append((command, check)),
    )
    monkeypatch.setattr(onboarding, "find_system_chromium", lambda: chromium)

    assert onboarding._install_raspberry_pi_chromium() == chromium
    assert commands == [
        (["/usr/bin/sudo", "/usr/bin/apt-get", "update"], True),
        (["/usr/bin/sudo", "/usr/bin/apt-get", "install", "-y", "chromium"], True),
    ]
