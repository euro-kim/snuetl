# SNUETL for Windows 0.9.3

Fixes the apparent freeze when choosing **Use automatic login** before a browser appears.

- Settings stay responsive, with a persistent progress panel visible across settings tabs.
- Downloads show percentage and transferred MB. Verification, browser installation, opening the browser and token verification each show their current stage.
- **Cancel** interrupts downloads, installation waits and browser login. A cancelled installation preserves the existing browser component.
- A stalled GitHub transfer reports an error after 45 seconds without data instead of waiting indefinitely.
- The sign-in component launches independently of the backend worker. Browser waits show elapsed time and are bounded; course sync continues in the background once connected.

Run `SNUETLSetup-0.9.3.exe` to upgrade. Settings, credentials and course files are preserved. This installer still uses the existing 0.9.0 browser ZIP; no new browser download is needed when that component is already installed with a matching recorded checksum.

Validation covers stalled transfers, cancellation, browser-process exit and timeout, progress rendering, responsive settings, preservation of installed components, native file hydration and uninstall cleanup. Interactive SNU account authentication still requires the account holder. The installer is unsigned.
