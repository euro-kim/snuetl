# SNUETL for Windows 0.9.5

Automatic login now removes its temporary browser package after the saved Canvas API key is verified, freeing approximately 563 MiB. The key remains saved securely and course sync continues without the browser. The latest browser package downloads again when automatic login is needed. If Windows prevents cleanup, login still succeeds and the app explains how to remove the package in Components.

Before login, SNUETL explains that new keys request about 365 days of validity. After login and in Settings, it shows the saved expiration date, including for a reused key. Manually pasted keys instead show instructions to check their expiry in Canvas; their actual expiry cannot be read from the pasted key.

Available app updates now appear in a visible mini-panel banner that opens the update controls directly. The banner clears after upgrading.

Run `SNUETLSetup-0.9.5.exe` to upgrade. Existing credentials, settings, and course files are preserved. The existing 0.9.0 browser package remains compatible and unchanged.
