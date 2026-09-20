---
name: snuetl
description: Read the signed-in user's SNU eTL Canvas courses, deadlines, missing work, submissions, grades, calendar, and discussions through the local SNUETL desktop helper.
---

# SNU eTL

Use the installed `snuetl-codex` helper for live SNU eTL information. The helper runs on the user's Mac or native Windows computer and returns JSON. Do not read its credential store, Canvas token, browser page, or private metadata directly.

On macOS, run `"$HOME/Library/Application Support/SNUETL Codex/bin/snuetl-codex" <command>`.

On native Windows, run `& "$env:LOCALAPPDATA\SNUETL Codex\bin\snuetl-codex.exe" <command>` in PowerShell. Do not route Windows calls through WSL.

When the user asks to connect, run `connect`. For an ordinary data request, run `status`; if it reports `ready: false`, run `connect`, let the user finish SNU login in the opened browser, then continue with the request. Use only the data command that answers the request:

| Request | Helper command |
| --- | --- |
| Active courses | `courses` |
| Upcoming incomplete work | `upcoming [--from YYYY-MM-DD] [--to YYYY-MM-DD]` |
| Missing work | `missing` |
| Assignment submission state | `submissions [--course NAME_OR_ID]` |
| Grades | `grades [--course NAME_OR_ID]` |
| Calendar | `calendar [--from YYYY-MM-DD] [--to YYYY-MM-DD]` |
| Discussion topics | `discussions [--course NAME_OR_ID]` |

Use `rotate` or `disconnect` only when the user asks. Login and MFA happen in the helper's browser; never ask the user to paste a token or password into chat. If a call reports `TOKEN_EXPIRED`, `TOKEN_REVOKED`, or `NOT_CONNECTED`, run `connect` and retry that request once. The helper returns live API data; mention when Canvas omits a grade or due date instead of guessing.
