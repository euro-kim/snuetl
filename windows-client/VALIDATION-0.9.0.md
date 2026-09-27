# SNUETL 0.9.0 validation

Test machine: Windows 11 build 26200, AMD Ryzen 5 3400G (4 cores / 8 logical processors), approximately 16 GB RAM. Built with Python 3.14.2, .NET SDK 8.0.425 and WiX 5.0.2. Release payloads are self-contained x64.

## Passed locally

- 30 focused Python tests: existing account/token flows and API readers, academic baselines/revisions, restart deduplication, course overrides, deadline/submission changes, failed endpoint recovery, missing optional component, interactive download priority, rate-limit Retry-After handling, and streaming a 16 MB hydration file without `read_bytes`.
- 19 .NET tests: reconciliation, placeholder identity/state, path containment, preservation, concurrent activity and batched history persistence.
- Native WPF/Cloud Files integration: scroll/wheel handling, interval persistence/rescheduling, rendered tray/settings/dashboard, cloud-file hydration, icon changes while connected, downloaded/renamed file preservation after losing the index, local-directory preservation and complete test Explorer registration removal.
- Optional-component staging/install/removal and bad-checksum rollback using an isolated test directory.
- Frozen browser-free worker stdio smoke test. Core payload has no Playwright, Chromium, Node driver, bot or media downloader payload.
- Optional frozen sign-in worker starts its single headed Chromium distribution in an isolated headless self-test, without accessing eTL or credentials.
- Official crest downloaded from the original source; independently rendered PNG icon frames inspected at 16, 20, 24, 32, 48, 64 and 256 pixels. White background and full crest retained.
- Real connected account: all eight academic datasets loaded across five courses with no endpoint errors. First baseline produced zero notifications. The saved 15-minute interval and account survived; SNU icon migrated to style 2. Normal refresh used only the core worker, without an add-on or browser process.
- MSI inspection: matching product version, late major-upgrade removal, shutdown-before-replacement, uninstall isolation, empty runtime-directory removal, toast shortcut identity, activation CLSID and unchecked optional component.

## Measurements

Published core in background after a successful refresh, tray/dashboard never opened in that process, five courses, 15-minute interval, add-on absent. Nine samples across 45.24 seconds:

| Metric | Result |
|---|---:|
| Mean combined working set (UI + backend) | 165.1 MB / 157.4 MiB |
| Mean combined private bytes | 68.0 MB / 64.9 MiB |
| Combined CPU, percentage of one logical core | 0.00% at process timer resolution |
| State/history files changed while idle | 0 |
| Runtime processes | 2 |

This short idle sample meets the stated idle targets on this machine; it does not predict active refresh, browser setup, large-course or large-dashboard peaks. A separate WPF rendering harness using multiple windows and large image conversion used more memory, as expected. The core installer is approximately 106.3 MB; exact final sizes/checksums are in `dist/release.json`. The optional add-on is approximately 267.5 MB download / 590.3 MB installed.

## Still requires a dedicated Windows test environment

- A genuinely clean Windows VM with no Python/.NET/Playwright, interactive MSI upgrade from 0.8.0, real sign-out/sign-in, and end-to-end uninstall of the installed product. The live account was not uninstalled to simulate these checks.
- Native notification delivery/Action Center activation under enabled/disabled notifications and Do Not Disturb. Baseline/reminder logic is unit-tested, and MSI identity/handler registration is inspected; this is not equivalent to an installed notification-policy test.
- Physical light/dark taskbars at multiple monitor DPIs and keyboard-only navigation through all screens. Individual pixel-size crest assets and WPF views were inspected locally.
- Long-duration battery/idle measurements, large live-file download throughput, and a complete refresh HTTP-request count. Streaming and concurrency behavior are tested, but no throughput benchmark is claimed.
- Public optional-download success after publishing the exact matching release assets. Failed/checksum-invalid local installation preserves the core. Until publication, use the matching local ZIP in Settings → Components.

The repository-wide Python run could not collect 15 Linux-oriented modules on Windows because `fcntl` or `termios` is unavailable. The Windows-focused suite passes; unrelated Linux behavior was not adapted to hide that host limitation.
