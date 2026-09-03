# snuetl

`snuetl` downloads files from the Files area of every active SNU eTL course. It
uses a dedicated Chromium profile so SNU's trusted-browser setting survives between
runs. The password and one-time verification code are never stored.

> This is an unofficial personal automation. Use it only for courses and materials
> your account is authorized to access, and avoid aggressive polling.

## Install and first run

Python 3.11 or newer is required. Install the command with its Python dependencies:

```bash
pipx install --include-deps .
snuetl
```

Running bare `snuetl` on a fresh machine opens a guided terminal setup. It:

- checks Playwright and automatically downloads its managed Chromium;
- offers to install missing Ubuntu browser libraries (sudo may prompt);
- asks where course files should be stored;
- prompts privately for SNU ID and password;
- asks whether to send the 2FA code by email or phone;
- prompts privately for the received code;
- enables **이 브라우저에서 추가 인증 사용 안함**;
- verifies the login by fetching the active course list; and
- offers to enable synchronization every 15 minutes.

No configuration file needs to be created manually. Re-run the wizard later with
`snuetl setup`, `snuetl onboard`, or `snuetl configure`. On a server without a
display, the same wizard automatically uses headless Chromium; `snuetl setup --headed`
and `--headless` are available when an explicit mode is needed.

For development from a checkout, a virtual environment also works:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
snuetl
```

## Authentication

Enrollment must run on the same machine that will perform synchronization:

```bash
snuetl login
```

The command selects SNU's ID/password tab, requests the chosen email/SMS code, enters
the terminal-provided code, and checks the trusted-browser option automatically. The
browser is visible on desktop systems and headless on servers by default.

No password or verification code is written to disk. The browser profile under
`~/.local/state/snuetl/browser-profile` does contain login and trusted-device
cookies. Its permissions are restricted, but it must be protected like a credential.
Do not copy it into this repository or a backup shared with other users.

Do not copy a desktop browser profile to a server. Run the terminal wizard directly on
the server so SNU trusts that machine's dedicated profile.

## Commands

Bare `snuetl` displays current local status and the command menu after onboarding.

```text
snuetl setup                    guided setup or repair
snuetl login                    refresh SNU authentication
snuetl courses                  list active course IDs and names
snuetl files [COURSE]           list file titles, folders, sizes, and dates
snuetl articles [COURSE]        list announcement and course-page titles
snuetl assignments [COURSE]     list assignment titles and due dates
snuetl refresh                  cache every paginated catalog table
snuetl query SQL                run read-only SQL over cached metadata
snuetl schema                   show canonical tables and field names
snuetl sync                     download new and revised files
snuetl status                   show the last sync and tracked counts
snuetl doctor                   check browser, config, profile, and timer
snuetl version [--check]        show the installed and published versions
snuetl update                   update the pipx installation
snuetl logout                   remove the trusted-browser profile
```

`COURSE` may be an exact course ID or a unique portion of its title. Leave it out to
show content from every active course. Add `--headed` to browser-backed commands for
diagnostics.

The human-readable tables show semester codes separately (`2026-2`, `SNUON`), use
canonical course IDs, and keep file names and folders distinct in storage. The table
view intentionally summarizes timestamps and sizes so it remains usable in narrow
terminals; SQL, JSON, JSONL, and CSV retain the complete values.

## SQL catalog

Refresh every metadata table without downloading files:

```bash
snuetl refresh
snuetl schema
```

Then query the local catalog using standard read-only SQLite SQL:

```bash
snuetl query "SELECT * FROM files WHERE course_id = '306087'"

snuetl query \
  "SELECT semester_code, count(*) AS courses
     FROM courses
    GROUP BY semester_code"

snuetl query \
  "SELECT semester_code, course_name, file_name, folder_path, size_bytes
     FROM files
    WHERE semester_code = '2026-2'
    ORDER BY updated_at DESC"

snuetl query \
  "SELECT course_name, title, published_at
     FROM announcements
    ORDER BY published_at DESC"
```

Use `snuetl query --refresh "SELECT ..."` to refresh all remote metadata immediately
before a query. Query output defaults to a terminal table and 200 rows. Use `--limit 0`
for every result row, or `--format json`, `jsonl`, or `csv` for scripts. A query can also
be read from standard input with `snuetl query -`.

The canonical read-only tables are `semesters`, `courses`, `files`, `articles`,
`announcements`, `pages`, `assignments`, `catalog_status`, and `sync_runs`. Run
`snuetl schema` for their canonical field names. For example, use `course_id` rather
than the ambiguous `class_id`, `file_id` for the remote file identifier, and
`size_bytes` for exact size.

`files` represents remote catalog files whether or not they have been downloaded.
`download_status`, `local_path`, and `sha256` describe local synchronization state.
`articles` combines announcements and course pages while the `announcements` and
`pages` views allow type-specific queries.

SNU's API pagination is consumed completely by following every `rel="next"` link. A
catalog scope is committed only after its pages have been fetched, and freshness can
be inspected with `SELECT * FROM catalog_status`. Running `courses`, `files`,
`articles`, or `assignments` also refreshes the corresponding cached metadata; the
dedicated `refresh` command updates all of them in one authenticated browser session.

`snuetl update` remembers how pipx installed the package. A PyPI installation is
upgraded from PyPI, a VCS installation is fetched again, and a local-path installation
is rebuilt from that checkout. For a local checkout, fetch source changes with Git first;
the updater deliberately does not modify your working tree.

## Synchronize

```bash
snuetl sync
snuetl status
```

The first run downloads all existing files. Later runs download new files and atomically
replace revised files. Removed remote files are retained locally. Use
`snuetl sync --headed` to troubleshoot navigation with a visible browser.

Files are stored as:

```text
<download directory>/<course name>-<course id>/<ETL folders>/<filename>
```

Exit codes are `0` for success, `1` for partial/runtime failure, `2` for missing or
expired authentication, and `3` for configuration/local-state errors. Logs deliberately
omit query strings and redact common secret fields.

The synchronizer first tries the authenticated Canvas-compatible API used by LearningX.
If it is not exposed by the deployment, it falls back to semantic DOM selectors. It
never uses fixed screen coordinates.

## Automatic synchronization

The setup wizard can create and enable a systemd user timer using the exact Python
environment and configuration path used during onboarding. To install the static unit
templates manually instead:

```bash
mkdir -p ~/.config/systemd/user
cp deployment/snuetl.service deployment/snuetl.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now snuetl.timer
systemctl --user list-timers snuetl.timer
```

Inspect runs with:

```bash
journalctl --user -u snuetl.service
```

If the timer reports exit code `2`, run `snuetl login` again.
`snuetl logout` removes only the dedicated trusted-browser profile; it leaves downloaded
files, configuration, and synchronization history untouched.

## Development

```bash
python -m pip install -e '.[test]'
pytest
```

Live account tests are intentionally not automatic. HTML/JSON fixtures should be redacted
before committing, and traces must never be recorded on the SNU credential or verification
screens.
