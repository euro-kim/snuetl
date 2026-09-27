# SNUETL 0.9.1

Settings now live in the dashboard, including a visible uninstall action. Tray and dashboard settings buttons open the same page. File activity uses colored type badges; manual sync shows its running state and records completion even when no files changed.

The Windows client checks GitHub weekly for stable core updates. Downloads are streamed and size/SHA-256 verified before setup launches. Automatic API configuration is selected by default in setup. The optional ZIP is discovered independently across releases, so a 0.9.1 EXE can reuse the existing 0.9.0 sign-in component. Installed add-ons are retained on upgrade. Core-only builds no longer rebuild or embed Chromium.

Private test repositories are supported using a repository-scoped, read-only GitHub token saved through Settings in Windows Credential Manager. Git SSH authentication is not used by the installed application. The core remains usable if GitHub access or the optional download fails.

## Validation

- 30 focused Python tests and packaged browser-free backend smoke test passed.
- 28 .NET unit tests passed, including core-only release/add-on fallback, stable version selection, authenticated asset downloads, corrupted/truncated downloads, private 404 messages, and missing digests.
- Native Windows integration passed: embedded Settings, saved interval, weekly/retry scheduling, scrolling preservation, icon sizes, older add-on reuse and failed-install preservation, cloud hydration, uninstall file preservation and Explorer cleanup.
- Rendered dashboard Settings and tray activity inspected at 100% DPI.
- Git SSH confirms the remote `v0.9.0` tag. Release attachment names/content could not be inspected without a GitHub API token. Live authenticated download and clean-machine upgrade/uninstall still require user testing; fixture tests cover their release-discovery and integrity logic.
- Existing 0.9.0 idle-resource measurements are documented separately and are not new measurements of 0.9.1.
