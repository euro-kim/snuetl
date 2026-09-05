from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_compose_keeps_one_named_interactive_hardened_container() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    service = compose["services"]["snuetl"]

    assert service["container_name"] == "${SNUETL_CONTAINER_NAME:-snuetl}"
    assert service["stdin_open"] is True
    assert service["tty"] is True
    assert service["init"] is True
    assert service["restart"] == "unless-stopped"
    assert service["read_only"] is True
    assert service["ipc"] == "host"
    assert "ports" not in service
    assert "privileged" not in service
    assert "no-new-privileges:true" in service["security_opt"]


def test_compose_mounts_every_persistent_directory_at_a_fixed_internal_path() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    volumes = compose["services"]["snuetl"]["volumes"]
    targets = {volume["target"] for volume in volumes}

    assert targets == {
        "/home/snuetl/.config/snuetl",
        "/home/snuetl/.local/state/snuetl",
        "/data/downloads",
        "/data/videos",
    }
    assert all(volume["bind"]["create_host_path"] is False for volume in volumes)


def test_docker_setup_script_owns_the_complete_bootstrap_workflow() -> None:
    script_path = ROOT / "docker-setup.sh"
    script = script_path.read_text(encoding="utf-8")

    assert script_path.stat().st_mode & 0o111
    assert 'cp -- "$ENV_EXAMPLE" "$ENV_FILE"' in script
    assert "set_env_value SNUETL_UID" in script
    assert "set_env_value SNUETL_GID" in script
    assert 'run_privileged chown -R -- "${HOST_UID}:${HOST_GID}" "$path"' in script
    assert 'run_privileged chmod 0700 -- "$path"' in script
    assert "compose down --remove-orphans" in script
    assert "compose build --pull --no-cache snuetl" in script
    assert "compose up -d --force-recreate --remove-orphans snuetl" in script
    assert "docker system prune" not in script
    assert "compose down -v" not in script


def test_docker_setup_script_prepares_storage_and_starts_a_fresh_image(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    for name in ("docker-setup.sh", ".env.example", "compose.yaml"):
        shutil.copy2(ROOT / name, project / name)

    env_file = project / ".env"
    env_file.write_text(
        (ROOT / ".env.example")
        .read_text(encoding="utf-8")
        .replace("SNUETL_UID=1000", "SNUETL_UID=99999")
        .replace("SNUETL_GID=1000", "SNUETL_GID=99999"),
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_log = tmp_path / "docker.log"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >>"$FAKE_DOCKER_LOG"
if [[ "$1" == info && "${2:-}" == --format ]]; then
    printf '["name=seccomp"]\\n'
elif [[ "$1" == inspect && "$*" == *Health* ]]; then
    printf 'healthy\\n'
elif [[ "$1" == inspect ]]; then
    printf 'running\\n'
elif [[ "$1" == compose && "$*" == *'ps --all -q snuetl'* ]]; then
    printf 'fake-container-id\\n'
fi
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["FAKE_DOCKER_LOG"] = str(docker_log)
    result = subprocess.run(
        ["bash", str(project / "docker-setup.sh"), "--yes"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    values = {
        key: value
        for key, value in (
            line.split("=", 1)
            for line in env_file.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")
        )
    }
    assert values["SNUETL_UID"] == str(os.getuid())
    assert values["SNUETL_GID"] == str(os.getgid())
    for name in ("config", "state", "downloads", "videos"):
        directory = project / "docker-data" / name
        assert directory.stat().st_mode & 0o777 == 0o700

    commands = docker_log.read_text(encoding="utf-8")
    assert "down --remove-orphans" in commands
    assert "build --pull --no-cache snuetl" in commands
    assert "up -d --force-recreate --remove-orphans snuetl" in commands
    assert "ps --all -q snuetl" in commands
    assert "Docker daemon access requires sudo" not in result.stdout
    assert "Deployment is healthy" in result.stdout


def test_container_build_installs_runtime_and_drops_root() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12-bookworm" in dockerfile
    assert "playwright install --with-deps chromium" in dockerfile
    assert "apt-get install --yes --no-install-recommends ffmpeg" in dockerfile
    assert "USER snuetl" in dockerfile
    assert 'CMD ["python", "-m", "snuetl.container_runtime"]' in dockerfile


def test_env_template_contains_only_non_secret_deployment_settings() -> None:
    values = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value

    assert set(values) == {
        "SNUETL_CONTAINER_NAME",
        "SNUETL_UID",
        "SNUETL_GID",
        "SNUETL_TIMEZONE",
        "SNUETL_CONFIG_DIR",
        "SNUETL_STATE_DIR",
        "SNUETL_DOWNLOAD_DIR",
        "SNUETL_VIDEO_DIR",
    }
    assert not any("password" in key.casefold() or "token" in key.casefold() for key in values)


def test_playwright_seccomp_profile_supports_x86_and_arm_user_namespaces() -> None:
    profile = json.loads(
        (ROOT / "deployment" / "docker" / "seccomp_profile.json").read_text(encoding="utf-8")
    )

    architectures = {entry["architecture"] for entry in profile["archMap"]}
    assert {"SCMP_ARCH_X86_64", "SCMP_ARCH_AARCH64"} <= architectures
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    assert any(
        {"clone", "setns", "unshare"} <= set(entry["names"])
        and entry["action"] == "SCMP_ACT_ALLOW"
        for entry in profile["syscalls"]
    )
