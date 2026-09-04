#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf '%s\n' \
    'Usage: ./setup.sh [--install-only] [snuetl setup options]' \
    '' \
    'Installs pipx when needed, installs snuetl with all dependencies, and' \
    'starts the guided setup. Use --install-only for agents and automation.'
}

install_only=false
setup_args=()
for argument in "$@"; do
  case "$argument" in
    --install-only) install_only=true ;;
    -h|--help) usage; exit 0 ;;
    *) setup_args+=("$argument") ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'Python 3.11 or newer is required.' >&2
  exit 1
fi
if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  printf '%s\n' 'Python 3.11 or newer is required.' >&2
  exit 1
fi

if command -v pipx >/dev/null 2>&1; then
  pipx_command=("$(command -v pipx)")
elif python3 -m pipx --version >/dev/null 2>&1; then
  pipx_command=(python3 -m pipx)
else
  printf '%s\n' 'Installing pipx…'
  python3 -m pip install --user --upgrade pipx
  pipx_command=(python3 -m pipx)
fi

"${pipx_command[@]}" ensurepath >/dev/null
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
printf '%s\n' 'Installing snuetl and its browser/downloader dependencies…'
"${pipx_command[@]}" install --force --include-deps "$script_dir"

if "$install_only"; then
  printf '%s\n' 'snuetl is installed. Run `snuetl setup` when interactive login is available.'
  exit 0
fi

pipx_bin_dir="$("${pipx_command[@]}" environment --value PIPX_BIN_DIR 2>/dev/null || true)"
if [[ -z "$pipx_bin_dir" ]]; then
  pipx_bin_dir="$(python3 -c 'import site; print(site.USER_BASE + "/bin")')"
fi
exec "$pipx_bin_dir/snuetl" setup "${setup_args[@]}"
