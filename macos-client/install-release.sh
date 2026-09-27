#!/usr/bin/env bash
set -euo pipefail

version="${1:-}"
if [[ ! "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.]+)?$ ]]; then
  echo 'Usage: install-release.sh vX.Y.Z' >&2
  exit 2
fi
architecture="$(uname -m)"
case "$architecture" in
  arm64|x86_64) ;;
  *) echo "Unsupported Mac architecture: $architecture" >&2; exit 2 ;;
esac

archive="snuetl-codex-macos-$architecture.dmg"
base="https://github.com/euro-kim/snuetl/releases/download/$version"
temporary="$(mktemp -d)"
mount="$temporary/mount"
mkdir -p "$mount"
cleanup() {
  hdiutil detach "$mount" -quiet 2>/dev/null || true
  rm -rf "$temporary"
}
trap cleanup EXIT

curl --fail --location --silent --show-error "$base/$archive" -o "$temporary/$archive"
curl --fail --location --silent --show-error "$base/SHA256SUMS.txt" -o "$temporary/SHA256SUMS.txt"
expected="$(awk -v name="$archive" '$2 == name { print $1 }' "$temporary/SHA256SUMS.txt")"
actual="$(shasum -a 256 "$temporary/$archive" | awk '{ print $1 }')"
if [[ -z "$expected" || "$expected" != "$actual" ]]; then
  echo 'Release checksum verification failed' >&2
  exit 1
fi

hdiutil attach "$temporary/$archive" -nobrowse -readonly -mountpoint "$mount" -quiet
open -W "$mount/Install SNUETL Codex.app"
