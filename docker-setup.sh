#!/usr/bin/env bash

set -Eeuo pipefail
IFS=$'\n\t'

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly COMPOSE_FILE="${SCRIPT_DIR}/compose.yaml"
readonly ENV_EXAMPLE="${SCRIPT_DIR}/.env.example"
readonly -a ENVIRONMENT_KEYS=(
    SNUETL_CONTAINER_NAME
    SNUETL_UID
    SNUETL_GID
    SNUETL_TIMEZONE
    SNUETL_CONFIG_DIR
    SNUETL_STATE_DIR
    SNUETL_DOWNLOAD_DIR
    SNUETL_VIDEO_DIR
)
readonly -a DIRECTORY_KEYS=(
    SNUETL_CONFIG_DIR
    SNUETL_STATE_DIR
    SNUETL_DOWNLOAD_DIR
    SNUETL_VIDEO_DIR
)

ENV_FILE="${SCRIPT_DIR}/.env"
PROJECT_NAME=""
ASSUME_YES=false
declare -a DOCKER_COMMAND=(docker)

log() {
    printf '[snuetl] %s\n' "$*"
}

warn() {
    printf '[snuetl] WARNING: %s\n' "$*" >&2
}

die() {
    printf '[snuetl] ERROR: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage: ./docker-setup.sh [--yes] [--env-file PATH] [--project-name NAME]

Prepare host directories, rebuild the image without stale cache, and start snuetl
with Docker Compose in detached mode.

Options:
  --env-file PATH  Use a specific Compose environment file (default: .env).
  --project-name    Set an explicit Compose project name for a separate instance.
  --yes            Skip the interactive environment-review confirmation.
  -h, --help       Show this help message.
EOF
}

while (($# > 0)); do
    case "$1" in
        --env-file)
            (($# >= 2)) || die "--env-file requires a path"
            ENV_FILE="$2"
            shift 2
            ;;
        --yes)
            ASSUME_YES=true
            shift
            ;;
        --project-name)
            (($# >= 2)) || die "--project-name requires a name"
            PROJECT_NAME="$2"
            shift 2
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

command -v realpath >/dev/null 2>&1 || die "realpath is required"
command -v awk >/dev/null 2>&1 || die "awk is required"
command -v stat >/dev/null 2>&1 || die "stat is required"
command -v find >/dev/null 2>&1 || die "find is required"
[[ -f "$COMPOSE_FILE" ]] || die "compose.yaml was not found next to this script"
[[ -f "$ENV_EXAMPLE" ]] || die ".env.example was not found next to this script"

if [[ "$ENV_FILE" != /* ]]; then
    ENV_FILE="${SCRIPT_DIR}/${ENV_FILE}"
fi
ENV_FILE="$(realpath -m -- "$ENV_FILE")"

if ((EUID == 0)); then
    if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != root ]]; then
        HOST_UID="${SUDO_UID:-$(id -u "$SUDO_USER")}"
        HOST_GID="${SUDO_GID:-$(id -g "$SUDO_USER")}"
        HOST_USER="$SUDO_USER"
        warn "The script was started with sudo; host ownership will remain with ${HOST_USER}."
    else
        die "run this script as a non-root login user; it requests sudo only when necessary"
    fi
else
    HOST_UID="$(id -u)"
    HOST_GID="$(id -g)"
    HOST_USER="$(id -un)"
fi
readonly HOST_UID HOST_GID HOST_USER

run_privileged() {
    if ((EUID == 0)); then
        "$@"
        return
    fi
    command -v sudo >/dev/null 2>&1 || die "sudo is required to repair ownership for: $*"
    sudo "$@"
}

if [[ ! -e "$ENV_FILE" ]]; then
    cp -- "$ENV_EXAMPLE" "$ENV_FILE"
    chmod 0600 "$ENV_FILE"
    if ((EUID == 0)); then
        chown "${HOST_UID}:${HOST_GID}" "$ENV_FILE"
    fi
    log "Created ${ENV_FILE} from .env.example."
else
    [[ -f "$ENV_FILE" ]] || die "environment path is not a regular file: ${ENV_FILE}"
    log "Preserving existing environment file: ${ENV_FILE}"
fi

open_env_editor() {
    local editor_setting="${VISUAL:-${EDITOR:-}}"
    local -a editor_command=()

    if [[ -n "$editor_setting" ]]; then
        IFS=' ' read -r -a editor_command <<<"$editor_setting"
    elif command -v nano >/dev/null 2>&1; then
        editor_command=(nano)
    elif command -v vi >/dev/null 2>&1; then
        editor_command=(vi)
    else
        warn "No terminal editor was found. Edit ${ENV_FILE} in another terminal."
        return
    fi
    "${editor_command[@]}" "$ENV_FILE"
}

confirm_environment() {
    local answer

    "$ASSUME_YES" && return
    [[ -t 0 ]] || die "interactive confirmation requires a terminal; use --yes after reviewing ${ENV_FILE}"

    printf '\nReview %s now. Customize the container name, timezone, and all host paths.\n' "$ENV_FILE"
    printf 'Use absolute paths for server deployments; do not use ~ or variable expansion.\n\n'
    while true; do
        read -r -p "Is the environment file customized and ready? [y]es/[e]dit/[q]uit: " answer
        case "${answer,,}" in
            y|yes)
                return
                ;;
            e|edit)
                open_env_editor
                ;;
            q|quit|n|no|"")
                log "Setup cancelled without changing the Docker deployment."
                exit 0
                ;;
            *)
                printf 'Enter y to continue, e to edit the file, or q to quit.\n'
                ;;
        esac
    done
}

trim_value() {
    local value="$1"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    printf '%s' "$value"
}

read_env_value() {
    local key="$1"
    local value
    local -a matches=()

    mapfile -t matches < <(
        awk -v key="$key" 'index($0, key "=") == 1 { print substr($0, length(key) + 2) }' \
            "$ENV_FILE"
    )
    ((${#matches[@]} == 1)) || \
        die "${ENV_FILE} must contain exactly one ${key}=... setting"
    value="$(trim_value "${matches[0]%$'\r'}")"
    if ((${#value} >= 2)); then
        if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]] || \
            [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
            value="${value:1:${#value}-2}"
        fi
    fi
    [[ -n "$value" ]] || die "${key} cannot be empty in ${ENV_FILE}"
    printf '%s' "$value"
}

set_env_value() {
    local key="$1"
    local value="$2"
    local temporary

    temporary="$(mktemp "${ENV_FILE}.tmp.XXXXXX")"
    if ! awk -v key="$key" -v value="$value" '
        BEGIN { updated = 0 }
        index($0, key "=") == 1 {
            if (!updated) {
                print key "=" value
                updated = 1
            }
            next
        }
        { print }
        END {
            if (!updated) print key "=" value
        }
    ' "$ENV_FILE" >"$temporary"; then
        rm -f -- "$temporary"
        die "could not update ${key} in ${ENV_FILE}"
    fi
    chmod 0600 "$temporary"
    mv -f -- "$temporary" "$ENV_FILE"
    if ((EUID == 0)); then
        chown "${HOST_UID}:${HOST_GID}" "$ENV_FILE"
    fi
}

confirm_environment

configured_uid="$(read_env_value SNUETL_UID)"
configured_gid="$(read_env_value SNUETL_GID)"
if [[ "$configured_uid" != "$HOST_UID" ]]; then
    log "SNUETL_UID=${configured_uid} does not match host UID ${HOST_UID}; fixing ${ENV_FILE}."
    set_env_value SNUETL_UID "$HOST_UID"
fi
if [[ "$configured_gid" != "$HOST_GID" ]]; then
    log "SNUETL_GID=${configured_gid} does not match host GID ${HOST_GID}; fixing ${ENV_FILE}."
    set_env_value SNUETL_GID "$HOST_GID"
fi

for key in "${ENVIRONMENT_KEYS[@]}"; do
    read_env_value "$key" >/dev/null
done

command -v docker >/dev/null 2>&1 || die "Docker Engine with the Compose plugin is required"
if docker info >/dev/null 2>&1; then
    DOCKER_COMMAND=(docker)
elif command -v sudo >/dev/null 2>&1 && sudo -v && sudo docker info >/dev/null 2>&1; then
    DOCKER_COMMAND=(sudo docker)
    log "Docker daemon access requires sudo; Docker commands will use it."
else
    die "cannot access the Docker daemon as ${HOST_USER}, directly or with sudo"
fi

"${DOCKER_COMMAND[@]}" compose version >/dev/null 2>&1 || \
    die "the Docker Compose plugin is not available"

docker_security_options="$(
    "${DOCKER_COMMAND[@]}" info --format '{{json .SecurityOptions}}' 2>/dev/null || true
)"
if [[ "$docker_security_options" == *rootless* || "$docker_security_options" == *userns* ]]; then
    die "rootless Docker and userns-remap cannot use this image's non-root UID with host-owned bind mounts; use a standard rootful Docker daemon"
fi

container_name="$(read_env_value SNUETL_CONTAINER_NAME)"
[[ "$container_name" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || \
    die "SNUETL_CONTAINER_NAME contains characters Docker does not accept: ${container_name}"
if [[ -n "$PROJECT_NAME" && ! "$PROJECT_NAME" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
    die "--project-name must start with a lowercase letter or digit and contain only lowercase letters, digits, hyphens, and underscores"
fi

canonical_host_path() {
    local key="$1"
    local configured="$2"
    local candidate canonical

    [[ "$configured" != '~'* ]] || die "${key} must not use ~; use an absolute path"
    [[ "$configured" != *'$'* ]] || die "${key} must not contain variable expansion"
    if [[ "$configured" == /* ]]; then
        candidate="$configured"
    else
        candidate="${SCRIPT_DIR}/${configured}"
    fi
    canonical="$(realpath -m -- "$candidate")"
    case "$canonical" in
        /|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/var)
            die "${key} resolves to an unsafe top-level path: ${canonical}"
            ;;
    esac
    if [[ "$SCRIPT_DIR" == "$canonical" || "$SCRIPT_DIR" == "$canonical/"* ]]; then
        die "${key} must not be the repository or one of its parent directories: ${canonical}"
    fi
    printf '%s' "$canonical"
}

declare -a HOST_PATHS=()
for key in "${DIRECTORY_KEYS[@]}"; do
    configured_path="$(read_env_value "$key")"
    host_path="$(canonical_host_path "$key" "$configured_path")"
    for previous_path in "${HOST_PATHS[@]}"; do
        if [[ "$host_path" == "$previous_path" || "$host_path" == "$previous_path/"* || \
            "$previous_path" == "$host_path/"* ]]; then
            die "persistent paths must be separate and non-nested: ${host_path} conflicts with ${previous_path}"
        fi
    done
    HOST_PATHS+=("$host_path")
    log "${key} -> ${host_path}"
done

ensure_host_directory() {
    local key="$1"
    local path="$2"
    local private="$3"
    local owner_uid owner_gid mode mode_value access_bits new_mode mismatch probe

    if [[ -e "$path" && ! -d "$path" ]]; then
        die "${key} points to a non-directory: ${path}"
    fi
    if [[ ! -d "$path" ]]; then
        log "Creating ${path}."
        if ! mkdir -p -m 0700 -- "$path" 2>/dev/null; then
            log "Elevated permission is required to create ${path}."
            if ! run_privileged mkdir -p -m 0700 -- "$path"; then
                die "could not create ${path}"
            fi
        fi
    fi

    owner_uid="$(stat -c '%u' -- "$path")"
    owner_gid="$(stat -c '%g' -- "$path")"
    mode="$(stat -c '%a' -- "$path")"

    if "$private"; then
        mismatch=""
        if [[ "$owner_uid" != "$HOST_UID" || "$owner_gid" != "$HOST_GID" ]]; then
            mismatch="$path"
        elif ! mismatch="$(
            find "$path" -xdev \( ! -uid "$HOST_UID" -o ! -gid "$HOST_GID" \) -print -quit \
                2>/dev/null
        )"; then
            mismatch="$path"
        fi
        if [[ -n "$mismatch" ]]; then
            log "Private data under ${path} does not match ${HOST_UID}:${HOST_GID}; repairing ownership recursively."
            if ! run_privileged chown -R -- "${HOST_UID}:${HOST_GID}" "$path"; then
                die "could not repair ownership under ${path}"
            fi
        fi

        if [[ "$mode" != 700 ]]; then
            log "Private directory ${path} has mode ${mode}; changing it to 700."
            if ! chmod 0700 -- "$path" 2>/dev/null; then
                if ! run_privileged chmod 0700 -- "$path"; then
                    die "could not set mode 700 on ${path}; check filesystem mount options"
                fi
            fi
        fi

        owner_uid="$(stat -c '%u' -- "$path")"
        owner_gid="$(stat -c '%g' -- "$path")"
        mode="$(stat -c '%a' -- "$path")"
        [[ "$owner_uid" == "$HOST_UID" && "$owner_gid" == "$HOST_GID" && "$mode" == 700 ]] || \
            die "could not enforce owner ${HOST_UID}:${HOST_GID} and mode 700 on ${path}; check filesystem mount options"
    else
        mode_value=$((8#$mode))
        if [[ "$owner_uid" == "$HOST_UID" ]]; then
            access_bits=$(((mode_value >> 6) & 7))
            new_mode=$((mode_value | 0700))
        elif [[ "$owner_gid" == "$HOST_GID" ]]; then
            access_bits=$(((mode_value >> 3) & 7))
            new_mode=$((mode_value | 0070))
        else
            access_bits=$((mode_value & 7))
            new_mode=$((mode_value | 0700))
        fi
        if ((EUID != 0)) && [[ -r "$path" && -w "$path" && -x "$path" ]]; then
            # Preserve ACL-based access even when the traditional mode bits do not show it.
            access_bits=7
        fi

        if ((access_bits != 7)); then
            if [[ "$owner_uid" != "$HOST_UID" && "$owner_gid" != "$HOST_GID" ]]; then
                log "${path} is not usable through its current owner or group; changing ownership to ${HOST_UID}:${HOST_GID}."
                if ! run_privileged chown -R -- "${HOST_UID}:${HOST_GID}" "$path"; then
                    die "could not repair ownership under ${path}"
                fi
                new_mode=$((mode_value | 0700))
            fi
            if ((new_mode != mode_value)); then
                printf -v new_mode '%04o' "$new_mode"
                log "${path} lacks required read/write/traverse access; changing mode ${mode} to ${new_mode}."
                if ! chmod "$new_mode" -- "$path" 2>/dev/null; then
                    if ! run_privileged chmod "$new_mode" -- "$path"; then
                        die "could not make ${path} usable; check filesystem mount options"
                    fi
                fi
            else
                log "The ownership correction makes ${path} usable; preserving mode ${mode}."
            fi
        else
            log "${path} is already usable with owner ${owner_uid}:${owner_gid} and mode ${mode}; preserving both."
        fi
    fi

    probe="${path}/.snuetl-write-check.$$"
    if ! (umask 077 && : >"$probe") 2>/dev/null; then
        die "${path} is not writable by ${HOST_USER} even after permission repair"
    fi
    rm -f -- "$probe"
}

for index in "${!DIRECTORY_KEYS[@]}"; do
    private=false
    case "${DIRECTORY_KEYS[$index]}" in
        SNUETL_CONFIG_DIR|SNUETL_STATE_DIR)
            private=true
            ;;
    esac
    ensure_host_directory "${DIRECTORY_KEYS[$index]}" "${HOST_PATHS[$index]}" "$private"
done

compose() {
    local -a project_arguments=()
    if [[ -n "$PROJECT_NAME" ]]; then
        project_arguments=(-p "$PROJECT_NAME")
    fi
    env \
        -u SNUETL_CONTAINER_NAME \
        -u SNUETL_UID \
        -u SNUETL_GID \
        -u SNUETL_TIMEZONE \
        -u SNUETL_CONFIG_DIR \
        -u SNUETL_STATE_DIR \
        -u SNUETL_DOWNLOAD_DIR \
        -u SNUETL_VIDEO_DIR \
        "${DOCKER_COMMAND[@]}" compose \
        --env-file "$ENV_FILE" \
        --project-directory "$SCRIPT_DIR" \
        -f "$COMPOSE_FILE" \
        "${project_arguments[@]}" \
        "$@"
}

log "Validating the rendered Compose configuration."
compose config --quiet

managed_container="$(compose ps --all -q snuetl)"
existing_container="$(
    "${DOCKER_COMMAND[@]}" ps -aq --filter "name=^/${container_name}$" 2>/dev/null || true
)"
[[ -z "$existing_container" || "$existing_container" == "$managed_container" ]] || \
    die "container name ${container_name} is already used outside this Compose project; rename it in ${ENV_FILE} or remove that container explicitly"

log "Building a fresh image with the current UID/GID and latest base image."
compose build --pull --no-cache snuetl

log "Replacing the previous Compose container; persistent host data is preserved."
compose down --remove-orphans

log "Starting ${container_name} in detached mode."
compose up -d --force-recreate --remove-orphans snuetl

container_id="$(compose ps --all -q snuetl)"
[[ -n "$container_id" ]] || die "Compose did not create the snuetl container"

log "Waiting for the container supervisor to become healthy."
for attempt in {1..45}; do
    container_state="$(
        "${DOCKER_COMMAND[@]}" inspect --format '{{.State.Status}}' "$container_id" 2>/dev/null || true
    )"
    health_state="$(
        "${DOCKER_COMMAND[@]}" inspect \
            --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
            "$container_id" 2>/dev/null || true
    )"
    if [[ "$container_state" == running && "$health_state" == healthy ]]; then
        printf '\n'
        compose ps
        log "Deployment is healthy. Continue with:"
        printf '  docker compose exec snuetl snuetl setup --headless\n'
        printf '  docker compose exec snuetl snuetl discord\n'
        printf '  docker compose exec snuetl snuetl doctor\n'
        exit 0
    fi
    if [[ "$container_state" == exited || "$container_state" == dead || \
        "$container_state" == restarting ]]; then
        printf '\n' >&2
        compose logs --tail 100 snuetl >&2 || true
        die "container entered state ${container_state} before becoming healthy"
    fi
    printf '.'
    sleep 2
done

printf '\n' >&2
compose logs --tail 100 snuetl >&2 || true
die "container did not become healthy within 90 seconds"
