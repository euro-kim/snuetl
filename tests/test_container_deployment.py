from __future__ import annotations

import json
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
