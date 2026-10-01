# SNUETL Windows 0.9.6

This update fixes accumulation of eTL keys across Windows client upgrades and simplifies account setup.

## Account setup

Copy the complete access token from SNU eTL Account Settings and paste it into Connect account. The client supplies the HTTP authorization header. The numeric identifier and `~`, when issued by eTL, are part of the secret and cannot be reconstructed.

The replacement is validated before saving. Windows Credential Manager retains only the current SNUETL key after a successful replacement, including cleanup of orphaned entries from earlier installations. Other applications and GitHub release credentials are preserved. Automatic sign-in uses the same storage path. Cleanup failures are surfaced and retain the committed replacement for retry.

When automatic sign-in creates a key, it first revokes every existing key whose purpose is exactly `snuetl-windows`. Failure to revoke stops creation. This can require reconnecting if subsequent creation fails. Manually pasted keys replace local credentials; manually created or differently named keys must be revoked in eTL Account Settings.

## Branding and upgrades

The official SNU logo is bundled, works offline, and is the default for new installations. Upgrades migrate the previous default icon to SNU while preserving custom icon selections. Settings, account metadata, and sync folders remain outside the MSI payload.

Install `SNUETLSetup-0.9.6.exe` over the previous version. For automatic sign-in, update the separate sign-in component to 0.9.6 as well. This build is unsigned.

## Validation

- 45 Python backend, credential, browser authentication, and Canvas API tests passed.
- 48 .NET client tests passed.
- Release WPF compilation passed with zero warnings or errors.
- Packaged backend and bundled-browser smoke checks passed.
- Windows integration checks passed for UI rendering, icon sizes, settings persistence, Cloud Files hydration, uninstall preservation, and Explorer removal.
- Installer checks passed for version, upgrade isolation, shutdown sequence, and runtime directory cleanup.
- A separate existing Windows file-URI issue in `tests/test_versioning.py` was observed when broadening tests; this CLI updater is outside the Windows-client update path.
