#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

readonly ASSET_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# Keep existing .env, bind mounts, and Compose project identity rooted in the checkout.
readonly SCRIPT_DIR="$(cd -- "$ASSET_DIR/.." && pwd -P)"
readonly SETUP_SCRIPT="${ASSET_DIR}/docker-setup.sh"
readonly REMOTE_NAME=origin
readonly DEPLOY_BRANCH=main

ENV_FILE="${SCRIPT_DIR}/.env"
PROJECT_NAME=""
ASSUME_YES=false
FORCE_REBUILD=false

log() {
    printf '[snuetl-update] %s\n' "$*"
}

die() {
    printf '[snuetl-update] ERROR: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage: bash docker/docker-update.sh [--yes] [--force-rebuild] [--env-file PATH]
                          [--project-name NAME]

Fetch origin/main, show and fast-forward to reviewed upstream commits, then use
docker-setup.sh to rebuild and health-check the Docker Compose deployment.

Options:
  --env-file PATH   Use a specific Compose environment file (default: .env).
  --project-name    Select the Compose project name for a separate instance.
  --force-rebuild   Rebuild and recreate even when the checkout is already current.
  --yes             Apply incoming commits without an interactive confirmation.
  -h, --help        Show this help message.
EOF
}

while (($# > 0)); do
    case "$1" in
        --env-file)
            (($# >= 2)) || die "--env-file requires a path"
            ENV_FILE="$2"
            shift 2
            ;;
        --project-name)
            (($# >= 2)) || die "--project-name requires a name"
            PROJECT_NAME="$2"
            shift 2
            ;;
        --force-rebuild)
            FORCE_REBUILD=true
            shift
            ;;
        --yes)
            ASSUME_YES=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
done

if [[ "$ENV_FILE" != /* ]]; then
    ENV_FILE="${SCRIPT_DIR}/${ENV_FILE}"
fi

((EUID != 0)) || die "run this script as your normal login user, not with sudo"
command -v git >/dev/null 2>&1 || die "Git is required to update the source checkout"
command -v docker >/dev/null 2>&1 || die "Docker Engine is required to rebuild the deployment"
[[ -f "$SETUP_SCRIPT" ]] || die "docker-setup.sh is missing"
[[ -f "$ENV_FILE" ]] || \
    die "environment file not found: ${ENV_FILE}; run bash docker/docker-setup.sh for first deployment"

log "[1/6] Validating the deployment checkout and local configuration."
repository_root="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null)" || \
    die "this script must run from a Git checkout"
[[ "$(cd -- "$repository_root" && pwd -P)" == "$SCRIPT_DIR" ]] || \
    die "the script directory is not the root of its Git checkout"

current_branch="$(git -C "$SCRIPT_DIR" symbolic-ref --quiet --short HEAD 2>/dev/null)" || \
    die "the checkout has a detached HEAD; switch to ${DEPLOY_BRANCH} before updating"
[[ "$current_branch" == "$DEPLOY_BRANCH" ]] || \
    die "Docker updates deploy ${DEPLOY_BRANCH}; current branch is ${current_branch}"

working_changes="$(git -C "$SCRIPT_DIR" status --porcelain --untracked-files=normal)"
if [[ -n "$working_changes" ]]; then
    printf '%s\n' "$working_changes" >&2
    die "the checkout has local changes; commit, stash, or remove them before updating"
fi

remote_url="$(git -C "$SCRIPT_DIR" remote get-url "$REMOTE_NAME" 2>/dev/null)" || \
    die "Git remote ${REMOTE_NAME} is not configured"
local_revision="$(git -C "$SCRIPT_DIR" rev-parse HEAD)"
log "Current revision: ${local_revision}"
log "Update source: ${remote_url} (${REMOTE_NAME}/${DEPLOY_BRANCH})"

log "[2/6] Fetching the latest ${DEPLOY_BRANCH} revision from ${REMOTE_NAME}."
if ! git -C "$SCRIPT_DIR" fetch --prune "$REMOTE_NAME" "$DEPLOY_BRANCH"; then
    die "Git fetch failed; the running container has not been changed"
fi
remote_revision="$(git -C "$SCRIPT_DIR" rev-parse FETCH_HEAD 2>/dev/null)" || \
    die "the fetched ${DEPLOY_BRANCH} revision could not be resolved"
git -C "$SCRIPT_DIR" cat-file -e "${remote_revision}^{commit}" 2>/dev/null || \
    die "the fetched object is not a commit"
log "Available revision: ${remote_revision}"

update_available=false
if [[ "$local_revision" != "$remote_revision" ]]; then
    if git -C "$SCRIPT_DIR" merge-base --is-ancestor "$local_revision" "$remote_revision"; then
        update_available=true
        log "[3/6] Incoming commits:"
        git -C "$SCRIPT_DIR" log --oneline --no-decorate "${local_revision}..${remote_revision}"
    elif git -C "$SCRIPT_DIR" merge-base --is-ancestor "$remote_revision" "$local_revision"; then
        die "local ${DEPLOY_BRANCH} is ahead of ${REMOTE_NAME}/${DEPLOY_BRANCH}; refusing to replace local commits"
    else
        die "local ${DEPLOY_BRANCH} and ${REMOTE_NAME}/${DEPLOY_BRANCH} have diverged; refusing to reset or overwrite either history"
    fi
else
    log "[3/6] The checkout already matches ${REMOTE_NAME}/${DEPLOY_BRANCH}."
fi

declare -a DOCKER_COMMAND=(docker)
if ! docker info >/dev/null 2>&1; then
    if command -v sudo >/dev/null 2>&1 && sudo -v && sudo docker info >/dev/null 2>&1; then
        DOCKER_COMMAND=(sudo docker)
    else
        die "cannot access the Docker daemon"
    fi
fi

env_value() {
    local value
    value="$(awk -F= -v key="$1" '$1 == key {print substr($0, length(key) + 2); exit}' "$ENV_FILE")"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    if [[ "$value" == '"'*'"' || "$value" == "'"*"'" ]]; then
        value="${value:1:${#value}-2}"
    fi
    printf '%s' "$value"
}

container_name="$(env_value SNUETL_CONTAINER_NAME)"
[[ -n "$container_name" ]] || die "SNUETL_CONTAINER_NAME is missing from ${ENV_FILE}"
image_tag="$(env_value SNUETL_IMAGE)"
if [[ -z "$image_tag" || "$image_tag" == snuetl:local ]]; then
    image_tag="snuetl:${PROJECT_NAME:-$container_name}"
fi

compose() {
    local -a project_arguments=()
    if [[ -n "$PROJECT_NAME" ]]; then
        project_arguments=(-p "$PROJECT_NAME")
    fi
    env \
        -u SNUETL_CONTAINER_NAME -u SNUETL_IMAGE -u SNUETL_UID -u SNUETL_GID \
        -u SNUETL_TIMEZONE -u SNUETL_CONFIG_DIR -u SNUETL_STATE_DIR \
        -u SNUETL_DOWNLOAD_DIR -u SNUETL_VIDEO_DIR \
        "${DOCKER_COMMAND[@]}" compose --env-file "$ENV_FILE" \
        --project-directory "$SCRIPT_DIR" -f "$ASSET_DIR/compose.yaml" \
        "${project_arguments[@]}" "$@"
}

old_container_id="$(compose ps --all -q snuetl 2>/dev/null || true)"
old_image_id=""
deployed_revision=""
deployed_version=""
health_state="missing"
container_state="missing"
if [[ -n "$old_container_id" ]]; then
    old_image_id="$("${DOCKER_COMMAND[@]}" inspect --format '{{.Image}}' "$old_container_id" 2>/dev/null || true)"
    container_state="$("${DOCKER_COMMAND[@]}" inspect --format '{{.State.Status}}' "$old_container_id" 2>/dev/null || true)"
    health_state="$("${DOCKER_COMMAND[@]}" inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$old_container_id" 2>/dev/null || true)"
    if [[ -n "$old_image_id" ]]; then
        deployed_revision="$("${DOCKER_COMMAND[@]}" image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$old_image_id" 2>/dev/null || true)"
        deployed_version="$("${DOCKER_COMMAND[@]}" image inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' "$old_image_id" 2>/dev/null || true)"
    fi
fi
tag_image_id="$("${DOCKER_COMMAND[@]}" image inspect --format '{{.Id}}' "$image_tag" 2>/dev/null || true)"
log "Deployed image: ${old_image_id:-missing}; revision: ${deployed_revision:-unknown}; version: ${deployed_version:-unknown}; health: ${health_state}."

if ! "$update_available" && ! "$FORCE_REBUILD" && \
    [[ "$deployed_revision" == "$remote_revision" && "$old_image_id" == "$tag_image_id" && \
       "$container_state" == running && "$health_state" == healthy ]]; then
    log "Source and healthy deployed image both match ${remote_revision}; no rebuild is necessary."
    exit 0
fi

if [[ "$local_revision" == "$remote_revision" && "$deployed_revision" != "$remote_revision" ]]; then
    log "The host checkout is current, but the deployed image is stale or unlabelled; rebuilding."
fi

if ! "$ASSUME_YES"; then
    [[ -t 0 ]] || die "confirmation requires a terminal; rerun with --yes after reviewing the commits"
    if "$update_available"; then
        prompt="Fast-forward to the fetched revision and rebuild the container? [y/N]: "
    else
        prompt="Build and deploy the current revision? [y/N]: "
    fi
    read -r -p "$prompt" answer
    case "${answer,,}" in
        y|yes) ;;
        *)
            log "Update cancelled; the checkout and running container were not changed."
            exit 0
            ;;
    esac
fi

if "$update_available"; then
    log "[4/6] Fast-forwarding local ${DEPLOY_BRANCH}; no reset or force operation will be used."
    if ! git -C "$SCRIPT_DIR" merge --ff-only "$remote_revision"; then
        die "fast-forward failed; the running container has not been changed"
    fi
else
    log "[4/6] Keeping the current source revision for the forced rebuild."
fi

[[ -f "$SETUP_SCRIPT" ]] || \
    die "the updated revision does not contain docker/docker-setup.sh"
declare -a setup_arguments=(--yes --env-file "$ENV_FILE")
if [[ -n "$PROJECT_NAME" ]]; then
    setup_arguments+=(--project-name "$PROJECT_NAME")
fi

log "[5/6] Building the updated image and replacing the Compose container."
log "docker-setup.sh will verify paths, build without stale cache, preserve mounted data, and wait for health."
restore_previous_image() {
    local reason="$1"
    if [[ -n "$old_image_id" ]]; then
        log "${reason}; restoring prior image ${old_image_id}."
        "${DOCKER_COMMAND[@]}" tag "$old_image_id" "$image_tag" || die "failed to retag the prior image for rollback"
        current_id="$(compose ps --all -q snuetl 2>/dev/null || true)"
        if [[ "$current_id" == "$old_container_id" ]]; then
            current_health="$("${DOCKER_COMMAND[@]}" inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$current_id" 2>/dev/null || true)"
            if [[ "$current_health" == healthy ]]; then
                die "${reason}; previous container stayed healthy and its image tag was restored"
            fi
        fi
        compose up -d --no-build --force-recreate snuetl || die "failed to restart the prior image"
        restored_id="$(compose ps --all -q snuetl)"
        for attempt in {1..45}; do
            restored_image="$("${DOCKER_COMMAND[@]}" inspect --format '{{.Image}}' "$restored_id" 2>/dev/null || true)"
            restored_health="$("${DOCKER_COMMAND[@]}" inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$restored_id" 2>/dev/null || true)"
            if [[ "$restored_image" == "$old_image_id" && "$restored_health" == healthy ]]; then
                die "${reason}; prior image ${old_image_id} was restored and is healthy"
            fi
            sleep 2
        done
        die "${reason}; rollback was attempted but did not become healthy"
    fi
    die "${reason}; no prior image was available for rollback"
}

if ! bash "$SETUP_SCRIPT" "${setup_arguments[@]}"; then
    restore_previous_image "update failed at ${remote_revision}"
fi

new_container_id="$(compose ps --all -q snuetl)"
new_image_id="$("${DOCKER_COMMAND[@]}" inspect --format '{{.Image}}' "$new_container_id")"
new_revision="$("${DOCKER_COMMAND[@]}" image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$new_image_id")"
[[ "$new_revision" == "$remote_revision" ]] || \
    restore_previous_image "healthy image revision ${new_revision} does not match fetched revision ${remote_revision}"

log "[6/6] Docker update completed successfully."
if "$update_available"; then
    log "Revision changed: ${local_revision} -> ${remote_revision}"
else
    log "Revision rebuilt: ${remote_revision}"
fi
log "Configuration, state, downloads, and videos remained on their host bind mounts."
