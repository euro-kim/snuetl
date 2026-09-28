# Windows client releases

## 0.9.2

- Select the highest stable Windows installer and browser add-on independently, including older release pages; historical missing checksums no longer block a newer release.
- Keep Use automatic login enabled in setup, fetch the latest stable add-on on install/upgrade, and download it on demand from the login button.
- Reuse the published 0.9.0 browser ZIP and its credential format; isolate its frozen runtime and verify saved credentials before reporting success.
- Explain complete Canvas tokens (`1~…`), preserve the issued prefix, and accept pasted Bearer headers without adding a duplicate prefix.

## 0.9.1

- Embedded dashboard Settings with explicit Windows uninstall action.
- Weekly GitHub update checks, verified installer downloads, and private-repository access stored in Credential Manager.
- Automatic API setup checked by default; reusable sign-in ZIP discovered across older releases.
- Core-only builds by default; optional browser ZIP rebuild through `-BuildSignInAddon`.
- Colored activity badges and manual-sync busy/completion feedback.

## 0.9.0

- Browser-free core and optional, checksum-pinned automatic-sign-in download with one Chromium distribution.
- Native dashboard, direct Canvas academic snapshots/events and opt-in notifications with per-course preferences.
- Centered SNU crest from preserved high-resolution artwork, clickable file/directory activity, and quiet installed-path startup.
- Bounded/coalesced background work, streaming hydration, rate-limit backoff, batched history and lazy window creation.
- Extended uninstall cleanup and versioned component size/checksum manifest.
- See [release notes](RELEASE-0.9.0.md) and [validation](VALIDATION-0.9.0.md).

## 0.8.0

- New tray activity panel with a persistent, scrollable history of the latest 500 file and sync events.
- Adjustable sync interval (1–1,440 minutes), applied immediately from General settings.
- White background for the SNU logo and explicit mouse-wheel/touch scrolling for activity history.
- Separate General, Appearance, and About & updates settings pages using SNU blue, beige and gray.
- Custom image icons and on-demand SNU logo download, shared by Explorer and the notification area.
- Uninstall converts downloaded cloud files into ordinary files and retains local folders and edits, including renamed files and missing-index recovery. It removes online-only placeholders, the Explorer navigation entry, startup registration, saved credentials and application data.
- Versioned setup files and a release manifest with SHA-256 checksums. Major upgrades preserve settings and credentials, stop the client before file replacement, and restart it afterward.
- Dedicated installer shutdown helper, old-version upgrade compatibility and explicit runtime-directory removal.
- Offline Windows integration tests for hydration, Explorer registration/removal, local-file preservation and UI rendering.

## 0.7.1

- Initial Windows Cloud Files client with browser sign-in, online-only placeholders, on-demand downloads, pinning and free-space actions.
