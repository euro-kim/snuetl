from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCKER = ROOT / "docker"


def test_compose_keeps_one_named_interactive_hardened_container() -> None:
    compose = yaml.safe_load((DOCKER / "compose.yaml").read_text(encoding="utf-8"))
    service = compose["services"]["snuetl"]

    assert service["container_name"] == "${SNUETL_CONTAINER_NAME:-snuetl}"
    assert service["stdin_open"] is True
    assert service["tty"] is True
    assert service["init"] is True
    assert service["restart"] == "unless-stopped"
    assert service["read_only"] is True
    assert service["image"] == "${SNUETL_IMAGE:-snuetl:local}"
    assert service["build"]["context"] == "."
    assert (ROOT / service["build"]["dockerfile"]).is_file()
    assert "SNUETL_SOURCE_REVISION" in service["build"]["args"]
    assert "ipc" not in service
    assert "ports" not in service
    assert "privileged" not in service
    assert "no-new-privileges:true" in service["security_opt"]


def test_compose_mounts_every_persistent_directory_at_a_fixed_internal_path() -> None:
    compose = yaml.safe_load((DOCKER / "compose.yaml").read_text(encoding="utf-8"))
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
    script_path = DOCKER / "docker-setup.sh"
    script = script_path.read_text(encoding="utf-8")

    assert 'cp -- "$ENV_EXAMPLE" "$ENV_FILE"' in script
    assert "set_env_value SNUETL_UID" in script
    assert "set_env_value SNUETL_GID" in script
    assert 'run_privileged chown -R -- "${HOST_UID}:${HOST_GID}" "$path"' not in script
    assert 'run_privileged chmod 0700 -- "$path"' not in script
    assert 'install -d -m 0700 -- "$path"' in script
    assert "compose down --remove-orphans" not in script
    assert "compose build --pull --no-cache snuetl" in script
    assert "compose up -d --no-build --force-recreate --remove-orphans snuetl" in script
    assert script.index("compose build --pull --no-cache snuetl") < script.index(
        "compose up -d --no-build"
    )
    assert "docker system prune" not in script
    assert "compose down -v" not in script


def test_docker_update_script_guards_and_explains_the_update_lifecycle() -> None:
    script_path = DOCKER / "docker-update.sh"
    script = script_path.read_text(encoding="utf-8")

    assert "status --porcelain --untracked-files=normal" in script
    assert 'fetch --prune "$REMOTE_NAME" "$DEPLOY_BRANCH"' in script
    assert 'merge-base --is-ancestor "$local_revision" "$remote_revision"' in script
    assert '"$deployed_revision" == "$remote_revision"' in script
    assert '"$old_image_id" == "$tag_image_id"' in script
    assert 'tag "$old_image_id" "$image_tag"' in script
    assert 'merge --ff-only "$remote_revision"' in script
    assert 'git -C "$SCRIPT_DIR" reset' not in script
    assert 'setup_arguments=(--yes --env-file "$ENV_FILE")' in script
    assert "--force-rebuild" in script
    assert "Revision changed" in script


def test_docker_update_script_fast_forwards_and_delegates_rebuild(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "docker").mkdir(parents=True)
    shutil.copy2(DOCKER / "docker-update.sh", project / "docker" / "docker-update.sh")
    (project / ".env").write_text(
        "SNUETL_CONTAINER_NAME=snuetl\nSNUETL_IMAGE=snuetl:snuetl\n", encoding="utf-8"
    )

    setup_log = tmp_path / "setup.log"
    fake_setup = project / "docker" / "docker-setup.sh"
    fake_setup.write_text(
        """#!/usr/bin/env bash
printf '%s\\n' "$*" >"$FAKE_SETUP_LOG"
printf 'fake setup completed\\n'
""",
        encoding="utf-8",
    )
    fake_setup.chmod(0o755)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    git_log = tmp_path / "git.log"
    fake_git = fake_bin / "git"
    fake_git.write_text(
        """#!/usr/bin/env bash
set -eu
arguments=("$@")
if [[ "${arguments[0]}" == -C ]]; then
    arguments=("${arguments[@]:2}")
fi
command="${arguments[*]}"
printf '%s\\n' "$command" >>"$FAKE_GIT_LOG"
case "$command" in
    'rev-parse --show-toplevel') printf '%s\\n' "$FAKE_PROJECT" ;;
    'symbolic-ref --quiet --short HEAD') printf 'main\\n' ;;
    'status --porcelain --untracked-files=normal') ;;
    'remote get-url origin') printf 'git@example.test:snuetl.git\\n' ;;
    'rev-parse HEAD') printf '%s\\n' "$FAKE_LOCAL_REVISION" ;;
    'rev-parse FETCH_HEAD') printf '%s\\n' "$FAKE_REMOTE_REVISION" ;;
    'log --oneline --no-decorate '*) printf '2222222 feat: update image\\n' ;;
esac
""",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
if [[ "$*" == *'ps --all -q snuetl'* && -f "$FAKE_SETUP_LOG" ]]; then echo new-container; fi
if [[ "$*" == *'inspect --format {{.Image}} new-container'* ]]; then echo new-image; fi
if [[ "$*" == *'image inspect --format {{index .Config.Labels "org.opencontainers.image.revision"}} new-image'* ]]; then echo "$FAKE_REMOTE_REVISION"; fi
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    local_revision = "1" * 40
    remote_revision = "2" * 40
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "FAKE_GIT_LOG": str(git_log),
            "FAKE_SETUP_LOG": str(setup_log),
            "FAKE_PROJECT": str(project),
            "FAKE_LOCAL_REVISION": local_revision,
            "FAKE_REMOTE_REVISION": remote_revision,
        }
    )
    result = subprocess.run(
        [
            "bash",
            str(project / "docker" / "docker-update.sh"),
            "--yes",
            "--project-name",
            "snuetl-test",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert "Incoming commits" in result.stdout
    assert f"Revision changed: {local_revision} -> {remote_revision}" in result.stdout
    assert "--yes --env-file" in setup_log.read_text(encoding="utf-8")
    assert "--project-name snuetl-test" in setup_log.read_text(encoding="utf-8")
    commands = git_log.read_text(encoding="utf-8")
    assert "fetch --prune origin main" in commands
    assert f"merge-base --is-ancestor {local_revision} {remote_revision}" in commands
    assert f"merge --ff-only {remote_revision}" in commands


@pytest.mark.parametrize("stale_image", [False, True])
def test_docker_update_checks_running_image_even_when_source_is_current(
    tmp_path: Path, stale_image: bool
) -> None:
    project = tmp_path / "project"
    (project / "docker").mkdir(parents=True)
    shutil.copy2(DOCKER / "docker-update.sh", project / "docker" / "docker-update.sh")
    (project / ".env").write_text(
        "SNUETL_CONTAINER_NAME=snuetl\nSNUETL_IMAGE=snuetl:snuetl\n", encoding="utf-8"
    )
    setup_marker = tmp_path / "setup-was-called"
    fake_setup = project / "docker" / "docker-setup.sh"
    fake_setup.write_text(
        '#!/usr/bin/env bash\ntouch "$FAKE_SETUP_MARKER"\n',
        encoding="utf-8",
    )
    fake_setup.chmod(0o755)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        """#!/usr/bin/env bash
set -eu
arguments=("$@")
if [[ "${arguments[0]}" == -C ]]; then arguments=("${arguments[@]:2}"); fi
case "${arguments[*]}" in
    'rev-parse --show-toplevel') printf '%s\\n' "$FAKE_PROJECT" ;;
    'symbolic-ref --quiet --short HEAD') printf 'main\\n' ;;
    'status --porcelain --untracked-files=normal') ;;
    'remote get-url origin') printf 'git@example.test:snuetl.git\\n' ;;
    'rev-parse HEAD'|'rev-parse FETCH_HEAD') printf '%s\\n' "$FAKE_REVISION" ;;
esac
""",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
if [[ -f "$FAKE_SETUP_MARKER" ]]; then
  case "$*" in
    *'ps --all -q snuetl'*) echo new-container ;;
    *'inspect --format {{.Image}} new-container'*) echo new-image ;;
    *'image inspect --format {{index .Config.Labels "org.opencontainers.image.revision"}} new-image'*) echo "$FAKE_REVISION" ;;
  esac
  exit 0
fi
case "$*" in
  *'ps --all -q snuetl'*) echo current-container ;;
  *'inspect --format {{.Image}} current-container'*) echo current-image ;;
  *'inspect --format {{.State.Status}} current-container'*) echo running ;;
  *'inspect --format {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} current-container'*) echo healthy ;;
  *'image inspect --format {{index .Config.Labels "org.opencontainers.image.revision"}} current-image'*) echo "$FAKE_DEPLOYED_REVISION" ;;
  *'image inspect --format {{.Id}} snuetl:snuetl'*) echo current-image ;;
esac
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "FAKE_PROJECT": str(project),
            "FAKE_REVISION": "3" * 40,
            "FAKE_DEPLOYED_REVISION": ("2" if stale_image else "3") * 40,
            "FAKE_SETUP_MARKER": str(setup_marker),
        }
    )
    result = subprocess.run(
        ["bash", str(project / "docker" / "docker-update.sh"), "--yes"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    if stale_image:
        assert "deployed image is stale" in result.stdout
        assert setup_marker.exists()
    else:
        assert "no rebuild is necessary" in result.stdout
        assert not setup_marker.exists()


def test_docker_update_restores_prior_image_after_failed_deployment(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "docker").mkdir(parents=True)
    shutil.copy2(DOCKER / "docker-update.sh", project / "docker" / "docker-update.sh")
    (project / ".env").write_text(
        "SNUETL_CONTAINER_NAME=snuetl\nSNUETL_IMAGE=snuetl:snuetl\n", encoding="utf-8"
    )
    failed_marker = tmp_path / "failed-setup"
    setup = project / "docker" / "docker-setup.sh"
    setup.write_text('#!/usr/bin/env bash\ntouch "$FAILED_MARKER"\nexit 1\n', encoding="utf-8")
    setup.chmod(0o755)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    git = fake_bin / "git"
    git.write_text(
        """#!/usr/bin/env bash
args=("$@")
if [[ "${args[0]}" == -C ]]; then args=("${args[@]:2}"); fi
case "${args[*]}" in
  'rev-parse --show-toplevel') echo "$FAKE_PROJECT" ;;
  'symbolic-ref --quiet --short HEAD') echo main ;;
  'remote get-url origin') echo git@example.test:snuetl.git ;;
  'rev-parse HEAD'|'rev-parse FETCH_HEAD') echo "$FAKE_REVISION" ;;
esac
""",
        encoding="utf-8",
    )
    git.chmod(0o755)
    docker_log = tmp_path / "docker.log"
    docker = fake_bin / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case "$*" in
  *'ps --all -q snuetl'*) if [[ -f "$FAILED_MARKER" ]]; then echo restored-container; else echo old-container; fi ;;
  *'inspect --format {{.Image}} old-container'*) echo old-image ;;
  *'inspect --format {{.Image}} restored-container'*) echo old-image ;;
  *'inspect --format {{.State.Status}} old-container'*) echo running ;;
  *'inspect --format {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} old-container'*) echo healthy ;;
  *'inspect --format {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} restored-container'*) echo healthy ;;
  *'image inspect --format {{index .Config.Labels "org.opencontainers.image.revision"}} old-image'*) echo stale ;;
  *'image inspect --format {{.Id}} snuetl:snuetl'*) echo old-image ;;
esac
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "FAKE_PROJECT": str(project),
            "FAKE_REVISION": "3" * 40,
            "FAILED_MARKER": str(failed_marker),
            "FAKE_DOCKER_LOG": str(docker_log),
        }
    )
    result = subprocess.run(
        ["bash", str(project / "docker" / "docker-update.sh"), "--yes"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 1
    assert "was restored and is healthy" in result.stderr
    commands = docker_log.read_text(encoding="utf-8")
    assert "tag old-image snuetl:snuetl" in commands
    assert "up -d --no-build --force-recreate snuetl" in commands


@pytest.mark.parametrize("existing_private", [False, True])
def test_docker_setup_script_prepares_storage_and_starts_a_fresh_image(
    tmp_path: Path,
    existing_private: bool,
) -> None:
    project = tmp_path / "project"
    (project / "docker").mkdir(parents=True)
    shutil.copy2(ROOT / "pyproject.toml", project / "pyproject.toml")
    for name in ("docker-setup.sh", ".env.example", "compose.yaml"):
        shutil.copy2(DOCKER / name, project / "docker" / name)

    env_file = project / ".env"
    env_file.write_text(
        (DOCKER / ".env.example")
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

    for name in ("downloads", "videos"):
        directory = project / "docker-data" / name
        directory.mkdir(parents=True)
        directory.chmod(0o755)
    if existing_private:
        for name in ("config", "state"):
            directory = project / "docker-data" / name
            directory.mkdir(parents=True)
            directory.chmod(0o755)

    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["FAKE_DOCKER_LOG"] = str(docker_log)
    result = subprocess.run(
        ["bash", str(project / "docker" / "docker-setup.sh"), "--yes"],
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
    for name in ("config", "state"):
        directory = project / "docker-data" / name
        assert directory.stat().st_mode & 0o777 == (0o755 if existing_private else 0o700)
    for name in ("downloads", "videos"):
        directory = project / "docker-data" / name
        assert directory.stat().st_mode & 0o777 == 0o755

    commands = docker_log.read_text(encoding="utf-8")
    assert "down --remove-orphans" not in commands
    assert "build --pull --no-cache snuetl" in commands
    assert "up -d --no-build --force-recreate --remove-orphans snuetl" in commands
    assert "ps --all -q snuetl" in commands
    assert "Docker daemon access requires sudo" not in result.stdout
    assert "Deployment is healthy" in result.stdout


def test_container_build_installs_runtime_and_drops_root() -> None:
    dockerfile = (DOCKER / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12-bookworm" in dockerfile
    assert "playwright install --with-deps firefox" in dockerfile
    assert "apt-get install --yes --no-install-recommends ffmpeg" in dockerfile
    assert "USER snuetl" in dockerfile
    assert 'org.opencontainers.image.revision="${SNUETL_SOURCE_REVISION}"' in dockerfile
    assert 'CMD ["python", "-m", "snuetl.container_runtime"]' in dockerfile


def test_env_template_contains_only_non_secret_deployment_settings() -> None:
    values = {}
    for line in (DOCKER / ".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value

    assert set(values) == {
        "SNUETL_CONTAINER_NAME",
        "SNUETL_IMAGE",
        "SNUETL_UID",
        "SNUETL_GID",
        "SNUETL_TIMEZONE",
        "SNUETL_CONFIG_DIR",
        "SNUETL_STATE_DIR",
        "SNUETL_DOWNLOAD_DIR",
        "SNUETL_VIDEO_DIR",
    }
    assert not any("password" in key.casefold() or "token" in key.casefold() for key in values)
