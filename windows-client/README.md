# SNUETL for Windows

> **Experimental / testing only.** This is an unofficial project, not affiliated with, endorsed by, or supported by Seoul National University. Project code is MIT-licensed; see the [license and disclaimer](../README.md#license-and-disclaimer).


SNUETL for Windows is a download-only Windows 10/11 x64 Cloud Files provider. It
shows active-course files, announcements, pages, and syllabi in File Explorer,
hydrates file contents on demand, and never sends local file changes to Canvas.

## Layout

- `src/Snuetl.Windows.App`: .NET 8 WPF tray application and Cloud Files provider.
- `backend`: the private JSON-RPC worker that reuses the repository's Python Canvas code.
- `tests`: .NET reconciliation tests and Python backend tests.
- `installer`: WiX v5 per-user MSI and EXE bundle definitions.
- `build.ps1`: native Windows build and packaging entry point.

## Partial source checkout

For a new checkout, select only the files this client needs:

```powershell
git clone --filter=blob:none --sparse --single-branch https://github.com/euro-kim/snuetl.git snuetl-windows
cd snuetl-windows
git sparse-checkout set --cone src tests windows-client .agents/skills/snuetl
```

Run the setup/build commands below from this checkout root. See the [main README](../README.md#download-only-the-client-you-need) for private Git authentication, updating, and changing an existing checkout.

## Development

The production build must run on Windows x64 with the Windows 10 SDK, .NET 8 SDK,
Python 3.12, PyInstaller, Playwright Chromium, and WiX v5 installed. From the
repository root:

```powershell
pwsh windows-client/build.ps1
```

The resulting pilot installer is `windows-client/dist/SNUETLSetup.exe`. It is
unsigned unless `SNUETL_SIGN_COMMAND` names a signing executable or script that
accepts the artifact path as its sole argument. Release signing should use a
trusted organization certificate.

Python tests can also run on non-Windows hosts:

```bash
pytest windows-client/tests/backend
```

The Cloud Files integration itself must be built and exercised on Windows. The
application checks the OS, architecture, filesystem, and Cloud Files platform
before registering a root.

## Local behavior

The default root is `%USERPROFILE%\SNUETL`; setup can select another local NTFS
folder. Files without an SNUETL placeholder identity are local-only and are never
uploaded or removed. When a local file occupies a remote path, the remote item is
created with an `__snuetl-<id>` suffix. Locally edited managed files are preserved,
with later server versions written as timestamped conflict copies.

## Test the Windows build

Run `windows-client/dist/SNUETLSetup.exe` to install the client for your Windows
user. Alternatively, launch `windows-client/build/publish/SNUETL.exe` directly;
keep the complete publish folder, including its `backend` directory, together.
The application and Python runtime are bundled, so the test machine does not
need .NET or Python installed.

1. Confirm the SNUETL window and tray icon appear on first launch.
2. Connect your SNU eTL account using browser sign-in or a manual API token.
3. Refresh and open the SNUETL folder. Open a remote file to verify that its
   contents download on demand.
4. Quit from the tray menu and relaunch to check that settings persist.

Building runs the Python backend tests, .NET tests, and a frozen-backend smoke
test that exercises redirected input/output without an account. Account sign-in
and Explorer hydration still require an interactive test with your account.

The build prefers `.venv/Scripts`, `.tools/dotnet`, and `.tools/wix` when present.
It installs Python build dependencies and Chromium, and installs WiX 5.0.2 into
`.tools/wix` if WiX is unavailable. CI uses Python 3.12; local builds can also use
Python 3.14 with a compatible PyInstaller version.

The client registers a top-level **SNUETL** entry in Explorer's navigation pane,
in the cloud-provider position above **This PC**. Explorer exposes online-only,
locally available, and pinned states for managed files, with the standard
**Always keep on this device** and **Free up space** actions. Registration stays
in place when the tray app exits and is removed when the sync root is unregistered.

## Version 0.8: appearance, activity and lifecycle

Left-click the tray icon for a scrollable history of the latest 500 sync, download,
update, pin/free-space and preservation events. Open the gear button for the
separate General, Appearance, and About & updates settings pages. General allows
a sync interval of 1–1,440 minutes; choosing Done reschedules the next check immediately.

First-run setup and Appearance offer the default SNUETL icon, a local PNG/JPEG/BMP/ICO
(up to 10 MB), or an on-demand download of the
[SNU logo](https://www.snu.ac.kr/webdata/uploads/kor/image/2022/09/snu_ui_download.png).
Images retain their proportions and are converted into six ICO sizes. The SNU
logo uses an opaque white background so it stays visible on dark taskbars. The same
icon is applied to Explorer and the notification area. Content-based filenames
refresh Windows' icon cache when the image changes. Theme colors follow the
[SNU identity guide](https://snurnd.snu.ac.kr/en/?q=node/29): blue `#0F0F70`,
beige `#DCDAB2`, and gray `#666666`.

`pyproject.toml` supplies the app, MSI and bundle version. The build also checks
that Python’s public `snuetl.__version__` matches it. Settings shows the installed version; the build emits both
`SNUETLSetup.exe` and `SNUETLSetup-<version>.exe`, plus `release.json` with build
versions and SHA-256 hashes. Run a newer setup EXE directly or choose it in
About & updates. Major upgrades preserve account credentials, appearance,
activity and synced content, stop the running app before replacing files, and
restart the updated client. Downgrades are blocked. Keep increasing the version
for each distributed release; same-version builds are not an update channel.

Uninstall uses a separate cleanup command, excluded from major upgrades. It
removes the provider's Explorer navigation entry (including its owned namespace
CLSID), startup registration, Windows Credential Manager entries, icons, settings,
activity and cache. Downloaded placeholders are converted into ordinary files;
local folders and local edits remain. Online-only placeholders are removed,
without deleting their originals from SNU eTL. A locked file makes cleanup fail
visibly instead of silently skipping preservation. Uninstall needs no network
access. Remote Canvas tokens can additionally be revoked from Canvas settings.

The offline Windows integration test creates a uniquely named temporary cloud
provider, hydrates fixture content, renames a downloaded file, removes the cached
index, and verifies uninstall preserves the local bytes and folders while
removing the Explorer registration. It also renders the UI to PNG for inspection.
It does not read or change your account or synced course folder.

```powershell
$env:SNUETL_BACKEND_COMMAND = '"C:\path\to\python.exe" -X utf8 "C:\path\to\snuetl\windows-client\tests\Snuetl.Windows.Integration\test_backend.py"'
dotnet run --project windows-client/tests/Snuetl.Windows.Integration -c Release -- windows-client/build/ui-preview
Remove-Item Env:SNUETL_BACKEND_COMMAND
```

## 0.9 architecture and release assets

The core worker imports `windows_auth.py`, which reads the same Credential Manager service and account metadata used by 0.8. Browser setup lives in the separate `signin_entry.py` process. `capabilities` reports optional-component availability; `auth.auto` returns `OPTIONAL_COMPONENT_MISSING` when absent. Disconnect removes the local credential without opening a browser; server-side revocation remains an explicit Canvas settings action.

`academic.snapshot` accepts `refresh` and `preferences` (`categories`, per-course `courses`, `reminder_hours`). It returns a cached dashboard with API datasets, courses, persistent events, incremental `new_events`, timestamp and actionable endpoint errors. Revisions are tracked by account/course/source identity; generated Markdown is never used to detect academic notifications. The first successful fetch of each category establishes its baseline. Failed categories retain previous data and baseline state.

`build.ps1` builds and tests the self-contained core, then packages the MSI/bootstrapper. Add `-BuildSignInAddon` only when you need to rebuild the optional Playwright/Chromium ZIP. Core-only releases can reuse an older ZIP; the online installer searches releases independently for the newest available sign-in component. The bundled manifest retains the known 0.9.0 ZIP for offline installation. Do not replace an existing ZIP with different bytes under the same checksum.

The installer checks **Use automatic login** by default. It finds the highest stable sign-in component across GitHub releases after core installation, including during upgrades. A matching installed checksum avoids another download, and a failed download retains the existing component. The automatic-login button also downloads the component when missing. Settings, including uninstall and updates, live inside the dashboard.

For manual login, paste the complete Canvas token including its issued numeric prefix and `~`, such as `1~` followed by the long secret. Do not invent or append characters. SNUETL adds `Bearer` to API requests automatically and also accepts a copied `Bearer` or `Authorization: Bearer` header. This Canvas token is separate from an optional GitHub release-access token.

Automatic login shows a persistent progress panel with download percentage/MB, verification and installation stages, and elapsed browser wait time. Cancel stops the active operation while preserving an existing installed component. Downloads time out after 45 seconds without data. The browser launches separately from the backend queue, and settings stay responsive while login runs. Course synchronization starts in the background after the account is verified.

### Private GitHub testing and updates

The release repository is `euro-kim/snuetl`. Git SSH access can verify tags but does not grant the installed client access to release attachments. In **Settings → About & updates → Private GitHub release access**, save a fine-grained GitHub token scoped to this repository with **Contents: read**. It is stored in Windows Credential Manager, never settings JSON or logs, and is removed by uninstall. Do not paste it into an issue or chat. Manual Canvas-token setup does not require GitHub access.

SNUETL checks for a newer stable Windows installer weekly, with a one-day retry delay after failures. **Check GitHub now** forces a check. Updates download only when requested and are verified against GitHub's SHA-256 asset digest or the release's `SHA256SUMS.txt`. Private downloads use GitHub's authenticated asset API; credentials are never attached to browser/CDN URLs. **Components → Download and install** finds the most recent release containing `SNUETL-SignIn-<version>-win-x64.zip`, even when newer releases contain only the EXE.

Upload `SNUETLSetup-<version>.exe` (or `SNUETLSetup.exe`) and `SHA256SUMS.txt` for a core release. Upload the sign-in ZIP only when it changes. Publishing and repository visibility remain under the maintainer's control.

See [0.9.1 release notes](RELEASE-0.9.1.md) and [0.9 baseline validation](VALIDATION-0.9.0.md).
