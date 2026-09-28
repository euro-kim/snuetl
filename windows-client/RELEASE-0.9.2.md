# SNUETL for Windows 0.9.2

Download `SNUETLSetup-0.9.2.exe` and run it to install or upgrade. Existing settings, credentials, course files and the browser add-on are preserved during upgrades.

**Use automatic login** is checked by default. Setup launches the client to download and verify the latest stable sign-in ZIP from this repository. The account's automatic-login button also installs the component if needed, then opens the sign-in browser. A failed download leaves manual-token setup available and retains any previously installed component.

This is a core-installer release. It reuses the existing `SNUETL-SignIn-0.9.0-win-x64.zip` from release `v0.9.0`; no replacement browser package is required. Its SHA-256 is `43d04d53434b5d27e44f3c0e57b8e8975240175dfb3339a96d8bb3e9c3a86478`.

Update checks select the highest stable Windows version independently of browser releases. Downloads require matching file sizes and SHA-256 checksums. Public releases require no GitHub token.

For manual login, paste the complete Canvas token, including its issued numeric prefix and `~` (for example, `1~` followed by the long secret). Do not invent a prefix or append a suffix. SNUETL adds the HTTP `Bearer` prefix; copied Bearer headers are also accepted. Credentials are validated against SNU eTL before being saved in Windows Credential Manager.

The installer is unsigned. Interactive SNU SSO and a real account's token verification require account-holder testing.
