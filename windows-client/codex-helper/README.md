# Optional Windows Codex helper

This experimental helper exposes read-only Canvas queries to the Codex skill. It is separate from the Windows Explorer/tray client in the parent directory. It is unofficial and MIT-licensed; see the [project disclaimer](../../README.md#license-and-disclaimer).

For a new helper-only checkout (without the WPF client source):

```powershell
git clone --filter=blob:none --sparse --single-branch https://github.com/euro-kim/snuetl.git snuetl-windows-helper
cd snuetl-windows-helper
git sparse-checkout set --cone src windows-client/codex-helper .agents/skills/snuetl
```

See the [main README](../../README.md#download-only-the-client-you-need) for private-repository authentication and updates. Cone mode also includes small files in parent directories, such as the Windows README, but omits sibling source/build directories.

From the repository root, on native Windows x64 with Python 3.11+, .NET SDK and WiX 5:

```powershell
pwsh windows-client/codex-helper/build.ps1
```

The MSI is written to `windows-client/codex-helper/dist/snuetl-codex-windows-x64.msi`. Open it to install, start a new Codex chat, then use `$snuetl connect` and complete SNU login in the visible browser. Use `$snuetl disconnect` to disconnect. Installers are unsigned during testing.

`install-release.ps1 vX.Y.Z` supports public releases with checksums. For this private repository, download the MSI through an authenticated GitHub session or build locally. This helper does not provide Explorer placeholders; use the [main Windows client](../README.md) for that.
