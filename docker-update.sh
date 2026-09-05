#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SETUP_SCRIPT="${SCRIPT_DIR}/docker-setup.sh"
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
Usage: ./docker-update.sh [--yes] [--force-rebuild] [--env-file PATH]
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
[[ -x "$SETUP_SCRIPT" ]] || die "docker-setup.sh is missing or not executable"
[[ -f "$ENV_FILE" ]] || \
    die "environment file not found: ${ENV_FILE}; run ./docker-setup.sh for first deployment"

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

update_available=true
if [[ "$local_revision" == "$remote_revision" ]]; then
    update_available=false
    log "[3/6] The checkout already matches ${REMOTE_NAME}/${DEPLOY_BRANCH}."
    if ! "$FORCE_REBUILD"; then
        log "No rebuild is necessary. Use --force-rebuild to recreate this deployment anyway."
        exit 0
    fi
    log "A rebuild was explicitly requested; the current source will be redeployed."
else
    if ! git -C "$SCRIPT_DIR" merge-base --is-ancestor "$local_revision" "$remote_revision"; then
        die "local ${DEPLOY_BRANCH} and ${REMOTE_NAME}/${DEPLOY_BRANCH} have diverged; refusing to reset or overwrite either history"
    fi
    log "[3/6] Incoming commits:"
    git -C "$SCRIPT_DIR" log --oneline --no-decorate "${local_revision}..${remote_revision}"
fi

if ! "$ASSUME_YES"; then
    [[ -t 0 ]] || die "confirmation requires a terminal; rerun with --yes after reviewing the commits"
    if "$update_available"; then
        prompt="Fast-forward to the fetched revision and rebuild the container? [y/N]: "
    else
        prompt="Rebuild the container from the current revision? [y/N]: "
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

[[ -x "$SETUP_SCRIPT" ]] || \
    die "the updated revision does not contain an executable docker-setup.sh"
declare -a setup_arguments=(--yes --env-file "$ENV_FILE")
if [[ -n "$PROJECT_NAME" ]]; then
    setup_arguments+=(--project-name "$PROJECT_NAME")
fi

log "[5/6] Building the updated image and replacing the Compose container."
log "docker-setup.sh will verify paths, build without stale cache, preserve mounted data, and wait for health."
if ! "$SETUP_SCRIPT" "${setup_arguments[@]}"; then
    die "container deployment failed at revision ${remote_revision}; inspect the setup output above"
fi

log "[6/6] Docker update completed successfully."
if "$update_available"; then
    log "Revision changed: ${local_revision} -> ${remote_revision}"
else
    log "Revision rebuilt: ${remote_revision}"
fi
log "Configuration, state, downloads, and videos remained on their host bind mounts."
