import stat
from dataclasses import replace
from pathlib import Path

from snuetl import onboarding
from snuetl.catalog import CatalogResult
from snuetl.config import load_config, save_config
from snuetl.directory_manager import configured_video_directory, has_separate_video_directory
from snuetl.models import Course, Semester
from snuetl.state import StateStore


def test_detects_arm64_aliases(monkeypatch) -> None:
    monkeypatch.setattr(onboarding.platform, "machine", lambda: "aarch64")
    assert onboarding.is_arm64() is True
    monkeypatch.setattr(onboarding.platform, "machine", lambda: "arm64")
    assert onboarding.is_arm64() is True
    monkeypatch.setattr(onboarding.platform, "machine", lambda: "armv7l")
    assert onboarding.is_arm64() is False


def test_raspberry_pi_uses_available_system_chromium(tmp_path: Path, monkeypatch) -> None:
    config = load_config(tmp_path / "missing.toml")
    chromium = Path("/usr/bin/chromium")
    calls = []
    monkeypatch.setattr(onboarding, "is_arm64", lambda: True)
    monkeypatch.setattr(onboarding, "is_raspberry_pi", lambda: True)
    monkeypatch.setattr(onboarding, "find_system_chromium", lambda: chromium)

    def available(candidate):
        calls.append(candidate)
        return (candidate.browser_executable_path == chromium, "test")

    monkeypatch.setattr(onboarding, "browser_available", available)
    selected = onboarding.ensure_browser(config)
    assert selected.browser_executable_path == chromium
    assert selected.browser_engine == "chromium"
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


def test_default_setup_installs_only_playwright_firefox(tmp_path: Path, monkeypatch) -> None:
    config = load_config(tmp_path / "missing.toml")
    availability = iter(((False, "missing"), (True, "ready")))
    commands: list[list[str]] = []
    monkeypatch.setattr(onboarding, "browser_available", lambda _config: next(availability))
    monkeypatch.setattr(onboarding, "is_raspberry_pi", lambda: False)
    monkeypatch.setattr(onboarding.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        onboarding.subprocess,
        "run",
        lambda command, check: commands.append(command),
    )
    monkeypatch.setattr(onboarding, "record_playwright_browser", lambda _config: None)

    assert onboarding.ensure_browser(config) == config
    assert commands == [[onboarding.sys.executable, "-m", "playwright", "install", "firefox"]]


def test_setup_prompts_for_and_secures_video_storage_separately(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.toml"
    config = replace(
        load_config(tmp_path / "missing.toml"),
        state_dir=tmp_path / "state",
        download_dir=tmp_path / "old-downloads",
    )
    config.profile_dir.mkdir(parents=True)
    config.auth_state_path.write_text("{}", encoding="utf-8")
    save_config(config, config_path)
    course = Course(
        "101",
        "Systems",
        "https://lms.test/courses/101",
        semester=Semester("s1", "2026-2", "2026년 2학기", 2026),
    )
    source = config.download_dir / "old" / "lecture.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"video")
    with StateStore(config.database_path) as store, store.transaction():
        store.upsert_course(course)
        store.record_artifact(
            artifact_type="video",
            course_id="101",
            source_id="v1",
            local_path=source,
            source_revision="revision-1",
            sha256="digest",
            size_bytes=5,
            status="ok",
        )
    regular = tmp_path / "course-files"
    videos = tmp_path / "large-videos"
    answers = iter((str(regular), str(videos)))
    prompts: list[str] = []

    def answer(prompt, **_kwargs):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr(onboarding, "ensure_browser", lambda value: value)
    monkeypatch.setattr(onboarding, "ensure_ffmpeg", lambda _value: None)
    monkeypatch.setattr(onboarding.Prompt, "ask", answer)
    monkeypatch.setattr(onboarding, "load_credentials", lambda _value: object())
    monkeypatch.setattr(onboarding, "_confirm", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(onboarding, "inspect_catalog", lambda *_args, **_kwargs: CatalogResult(()))
    monkeypatch.setattr(onboarding, "print_catalog", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(onboarding, "print_commands", lambda: None)

    assert onboarding.run_setup(config_path, force_headless=True) == 0

    configured = load_config(config_path)
    assert prompts[0].startswith("Where should regular course files")
    assert prompts[1].startswith("Where should large video downloads")
    assert configured.download_dir == regular
    assert configured.canvas_api_enabled is False
    assert configured_video_directory(configured) == videos
    assert has_separate_video_directory(configured) is True
    assert stat.S_IMODE(regular.stat().st_mode) == 0o700
    assert stat.S_IMODE(videos.stat().st_mode) == 0o700
    destination = videos / "2026-2" / "Systems--101" / "videos" / "lecture.mp4"
    assert destination.read_bytes() == b"video"
    assert not source.exists()
    with StateStore(config.database_path) as store:
        artifact = store.get_artifact("video", "101", "v1")
        assert artifact is not None
        assert artifact["local_path"] == str(destination)
