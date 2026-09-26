# SNUETL for Windows

SNUETL for Windows is a download-only Windows 10/11 x64 Cloud Files provider. It
shows active-course files, announcements, pages, and syllabi in File Explorer,
hydrates file contents on demand, and never sends local file changes to Canvas.

## Layout

- `src/Snuetl.Windows.App`: .NET 8 WPF tray application and Cloud Files provider.
- `backend`: the private JSON-RPC worker that reuses the repository's Python Canvas code.
- `tests`: .NET reconciliation tests and Python backend tests.
- `installer`: WiX v5 per-user MSI and EXE bundle definitions.
- `build.ps1`: native Windows build and packaging entry point.

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
