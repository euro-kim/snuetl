#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"

python3 -m pip install -e '.[codex-build]'
export PLAYWRIGHT_BROWSERS_PATH=0
export MACOSX_DEPLOYMENT_TARGET=14.0
python3 -m playwright install chromium
pyinstaller --clean --noconfirm --onedir --name snuetl-codex --paths src \
  --distpath macos-client/dist --workpath macos-client/build --specpath macos-client/build \
  --hidden-import keyring.backends.macOS --collect-data tzdata \
  --exclude-module snuetl.lms_session \
  src/snuetl/codex_entry.py

architecture="$(uname -m)"
case "$architecture" in
  arm64|x86_64) ;;
  *) echo "Unsupported Mac architecture: $architecture" >&2; exit 1 ;;
esac

app="$repo_root/macos-client/dist/Install SNUETL Codex.app"
rm -rf "$app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources/payload"
cp -R "$repo_root/macos-client/dist/snuetl-codex" "$app/Contents/Resources/payload/bin"
cp -R "$repo_root/.agents/skills/snuetl" "$app/Contents/Resources/payload/skill"
swiftc macos-client/Installer.swift -framework AppKit -o "$app/Contents/MacOS/installer"
version="$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>installer</string>
  <key>CFBundleIdentifier</key><string>kr.ac.snu.snuetl.codex.installer</string>
  <key>CFBundleName</key><string>Install SNUETL Codex</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$version</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
hdiutil create -volname 'SNUETL Codex' -srcfolder "$app" -ov -format UDZO \
  "$repo_root/macos-client/dist/snuetl-codex-macos-$architecture.dmg"
