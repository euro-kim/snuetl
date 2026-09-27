# SNUETL 0.9.0 for Windows

SNUETL now separates the browser-free core client from optional automatic account setup. Existing account credentials, local folders, selected icons and refresh intervals remain in place during an upgrade.

## What's included

- A resizable native dashboard: Overview, Courses, Deadlines, Notifications, Files & activity, and Settings. Academic content is read through Canvas APIs; links open in the Windows default browser only on request.
- Separate academic alerts and file activity in the compact tray. File names open using Windows associations (including normal cloud hydration); directory links select the file in Explorer or open its nearest surviving parent.
- Full-resolution SNU crest cropping, centered white tiles at 16, 20, 24, 32, 48, 64 and 256 pixels, and preserved original icon images. Existing SNU icons migrate when online.
- Opt-in categories and course overrides for announcements, assignments/deadline changes, reminders, grades/feedback, discussions and file updates. Academic snapshots, baselines, revisions and notification history persist by account. Reminder defaults are 24 hours and one hour; current submission/deadline data cancels obsolete reminders.
- Inbox WinRT notifications with a per-user app identity, Start Menu shortcut properties and protocol activation into Notifications. Windows suppression is honored. Notification availability is shown in Settings.
- Startup registration uses the installed executable and preserves Windows-disabled startup entries. The selected checking interval applies on battery too.
- An optional sign-in ZIP containing Playwright, its driver, and one headed Chromium distribution. The installer checkbox is unchecked; Settings also supports download, verified local ZIP installation and removal. Core downloads are independent, and a failed optional download does not roll back the core.
- Bounded hydration, a separate backend download lane, streaming copies/hashes, cached course listings, bounded/debounced filesystem work, batched activity writes, and Canvas 429 backoff.
- Uninstall removes the Explorer entry, app/protocol/notification/startup registrations, credential entries, cache, reminders and add-on. Downloaded files, local edits and local directories survive.

## Install and update

Run `SNUETLSetup-0.9.0.exe`. No Python or .NET installation is required. Existing automatic-sign-in installations remain installed; new core installations can use a Canvas API token without an add-on. Choose notification categories in Settings; none are enabled automatically.

The optional component's expected download URL and SHA-256 are pinned in `signin-component.json`. Until the matching GitHub release asset is published, use Settings → Components → Install from ZIP and select `SNUETL-SignIn-0.9.0-win-x64.zip` from the same build. A ZIP from another build is rejected if its checksum differs.

Release assets are unsigned unless the build is supplied with `SNUETL_SIGN_COMMAND`. `release.json` records component versions, sizes and checksums.

## Validation scope

See `VALIDATION-0.9.0.md` for measured results and remaining clean-machine checks. A local build is not evidence of a real Windows sign-out/sign-in, a clean-machine install, or delivery under every Windows notification policy.
