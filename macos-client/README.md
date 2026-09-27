# SNUETL macOS helper

> **Experimental / testing only.** This is an unofficial project, not affiliated with, endorsed by, or supported by Seoul National University. Project code is MIT-licensed; see the [license and disclaimer](../README.md#license-and-disclaimer).

This directory contains the **Codex desktop helper**, not a macOS virtual-drive app. There is no Finder sync extension in this repository. Run build commands from the repository root.


The Codex skill is a separate, read-only Canvas API client. It does not install the Linux
synchronizer, run a daemon, or require WSL, Docker, Python, or a server on the user's
machine. The pilot supports macOS 14+ on Apple Silicon and Intel and native Windows x64.
The skill files live at `~/.agents/skills/snuetl` (on Windows,
`%USERPROFILE%\.agents\skills\snuetl`).

For a native installation, download the matching `snuetl-codex-macos-arm64.dmg`,
`snuetl-codex-macos-x86_64.dmg`, or `snuetl-codex-windows-x64.msi` from a versioned
release. On Mac, open the DMG and double-click **Install SNUETL Codex**. On Windows,
open the MSI, then use Codex desktop with its **Windows native** agent.
The personal pilot artifacts are unsigned, so the operating system may ask you to
confirm that you trust the downloaded installer.

Codex can also install the same native package for you. In a local Codex desktop chat,
ask: “Check out `euro-kim/snuetl` at release tag `vX.Y.Z`, inspect its matching
`macos-client/install-release.sh` or `windows-client/codex-helper/install-release.ps1`, and run
that script with `vX.Y.Z` to install the SNUETL Codex desktop skill on this computer.”
The scripts download the versioned installer, verify its SHA-256 checksum, and open it.
Replace `vX.Y.Z` with a published release tag. Start a new Codex chat if the new skill
does not appear immediately.

Then say **`$snuetl connect`**. A separate visible browser opens; complete SNU login and
MFA there. The helper creates a Canvas token through Account Settings, validates it,
and saves the secret in macOS Keychain or Windows Credential Manager. The login browser
closes without saving passwords or cookies. The token is never printed into Codex.
Each computer connects independently.

Ask questions such as “What is due this week?”, “Show missing assignments”, or “Show
my grades for Biology.” The helper retrieves live Canvas data on demand and returns
JSON for Codex to summarize. `$snuetl rotate` replaces the token; `$snuetl disconnect`
revokes it through Canvas and removes it locally. The desktop helper supports active
courses, upcoming work, missing submissions, submission states, grades, calendar
events, and discussion topics. Canvas fields that are hidden or unavailable remain
unknown in the answer.

Build the Mac DMG on the corresponding Mac architecture with
`bash macos-client/build.sh`; build the Windows MSI on native Windows x64 with
`windows-client/codex-helper/build.ps1`. The manual [desktop build workflow](../.github/workflows/codex-desktop.yml)
builds all three artifacts from native runners. Live SNU login is not exercised in CI;
perform a supervised connection test on each target OS before distributing a pilot.


## Partial source checkout

For a new checkout, select only the files this client needs:

```bash
git clone --filter=blob:none --sparse --single-branch https://github.com/euro-kim/snuetl.git snuetl-macos
cd snuetl-macos
git sparse-checkout set --cone src macos-client .agents/skills/snuetl
```

Run the setup/build commands below from this checkout root. See the [main README](../README.md#download-only-the-client-you-need) for private Git authentication, updating, and changing an existing checkout.

## Build outputs and private testing

Run `bash macos-client/build.sh` on macOS 14+ with Python 3.11+ and Xcode command-line tools. Build on each target architecture separately. Outputs stay under `macos-client/build/` and `macos-client/dist/`. The DMG installer is unsigned.

The release installer script uses public release URLs. While this repository is private, download an available DMG through your authenticated GitHub session, verify its release checksum, and open it manually, or build locally. The workflow is manual and produces artifacts only.
