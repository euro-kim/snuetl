# snuetl

`snuetl` downloads files from the Files area of every active SNU eTL course. It
uses a dedicated Chromium profile so SNU's trusted-browser setting survives between
runs. Setup can save the ID and password for automatic re-login; one-time verification
codes are never stored.

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
- asks whether to save them for automatic re-login (default: yes);
- explicitly asks whether this device should be trusted (default: yes);
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

### Raspberry Pi and Linux ARM64

`snuetl` supports 64-bit ARM Linux (`aarch64`/`arm64`) with Python 3.11 or newer.
The setup wizard detects the architecture and validates a real headless browser launch.
On supported ARM64 Ubuntu and Debian releases it can install Playwright's native ARM64
Chromium. On 64-bit Raspberry Pi OS it first uses an existing `chromium` package, or
offers to install it through `apt-get` when absent. The selected executable is saved as
`browser.executable_path`; authentication and scheduled runs then use that same binary.

Typical Raspberry Pi installation is unchanged:

```bash
pipx install --include-deps .
snuetl setup --headless
snuetl doctor
```

Setup prompts before invoking `sudo` for operating-system packages. Python packages and
the appropriate Playwright browser are installed automatically. A 64-bit Raspberry Pi
OS image is required; 32-bit `armv7l` is not supported. Native x86-64 and ARM64 browser
smoke tests are defined in `.github/workflows/ci.yml`.

## Authentication

Enrollment must run on the same machine that will perform synchronization:

```bash
snuetl login
```

The command selects SNU's ID/password tab, requests the chosen email/SMS code, and
enters the terminal-provided code. It explicitly asks whether to select SNU's
trusted-browser option; the default is yes. The browser is visible on desktop systems
and headless on servers by default.

If credential saving is accepted, the ID and password are written in plaintext to
`~/.local/state/snuetl/credentials.json`. The file mode is `0600` and its directory is
`0700`, but it must still be protected like a password. Declining removes any previously
saved credential. The browser profile under `~/.local/state/snuetl/browser-profile`
also contains login and trusted-device cookies. Do not copy either into this repository
or a backup shared with other users. Verification codes are never saved.

When a cookie session expires, browser-backed commands automatically sign in again with
the saved credentials. A trusted device normally avoids another 2FA challenge. If SNU
requires 2FA again, the unattended command exits promptly and asks you to run
`snuetl login`; it never attempts to store or guess a one-time code.

If SNU displays an optional password-change reminder, `snuetl` selects its “change
later” action and continues. If the change is mandatory or no safe later action can be
identified, it stops with instructions to change the password on SNU's website and then
run `snuetl login` to replace the saved credential.

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
snuetl sql                      open the interactive read-only SQL shell
snuetl query                    compatibility alias for the SQL shell
snuetl sql --execute SQL        run one SQL query over cached metadata
snuetl schema                   show canonical tables and field names
snuetl pull [KIND]              pull files, articles, syllabi, videos, or all
snuetl sync                     compatibility alias for pulling files
snuetl directory [PATH]         show or change the managed pull root
snuetl capabilities --json      describe the stable agent-facing contract
snuetl status                   show the last sync and tracked counts
snuetl doctor                   check browser, config, profile, and timer
snuetl version [--check]        show the installed and published versions
snuetl update                   update the pipx installation
snuetl logout                   remove browser authentication and saved credentials
snuetl uninstall                remove the app and optionally its local data
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
snuetl sql --execute "SELECT * FROM files WHERE course_id = '306087'"

snuetl sql --execute \
  "SELECT semester_code, count(*) AS courses
     FROM courses
    GROUP BY semester_code"

snuetl sql --execute \
  "SELECT semester_code, course_name, file_name, folder_path, size_bytes
     FROM files
    WHERE semester_code = '2026-2'
    ORDER BY updated_at DESC"

snuetl sql --execute \
  "SELECT course_name, title, published_at
     FROM announcements
    ORDER BY published_at DESC"
```

Use `snuetl sql --refresh --execute "SELECT ..."` to refresh all remote metadata immediately
before a query. Query output defaults to a terminal table and 200 rows. Use `--limit 0`
for every result row, or `--format json`, `jsonl`, or `csv` for scripts. SQL can also
be read from standard input, or from a file with `--file`.

Run bare `snuetl sql` (or the `query` alias) to enter the interactive environment:

```text
$ snuetl sql
snuetl SQL — read-only local eTL catalog
snuetl> .tables
snuetl> .columns files
snuetl> .limit 50
snuetl> SHOW TABLES;
snuetl> SELECT semester_code, course_name, file_name
   ...> FROM files
   ...> ORDER BY updated_at DESC;
snuetl> .mode json
snuetl> .refresh
snuetl> .quit
```

The shell supports tab completion where Python's `readline` module is available. Use
`.help` for all commands, `.schema [TABLE]` for canonical fields, `.status` for cache
freshness, and `.mode table|json|jsonl|csv` to change output without restarting.
`SHOW TABLES;` is accepted as an SQL-style alias for `.tables`. Press Ctrl+C to leave
query mode immediately.

The canonical read-only tables are `semesters`, `courses`, `files`, `articles`,
`announcements`, `pages`, `assignments`, `modules`, `module_items`, `videos`,
`syllabi`, `artifacts`, `catalog_status`, and `sync_runs`. Run
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

## Pull course content

Bare `snuetl pull` pulls all non-video content. Select one kind when needed:

```bash
snuetl pull files
snuetl pull articles --course 306087
snuetl pull syllabus --semester 2026-2
snuetl pull videos --course 306087 --dry-run
snuetl pull videos --course 306087 --video-id VIDEO_ID
```

Content is stored under
`ROOT/<semester>/<course-name>--<course-id>/{files,articles,syllabus,videos}`.
Articles are Markdown with YAML metadata and localized public assets. Locally edited
generated files are preserved; a changed remote copy is written beside them unless
`--force` is supplied. Syllabus pulls preserve the official HTML-derived Markdown,
a rendered PDF when possible, matching uploaded PDFs, and a source manifest.

Video pulling uses authenticated browser discovery and yt-dlp, defaults to 1080p and
downloads available captions. It does not bypass DRM. `pull all` deliberately excludes
videos. `pull videos` presents an arrow-key/spacebar checklist (including an All checkbox);
non-interactive callers such as Hermes can pass one or more `--video-id` values, or `--yes`
to select all. Use `--best` to remove the height cap, and inspect `--dry-run` first because
provider-reported sizes are often unavailable.

Interactive video pulls first open the eTL account/profile menu. Use the arrow keys to
choose an identity (such as undergraduate or graduate school), or pass `--profile PROFILE`
for a known label. The selected identity remains active in the trusted browser session.

`snuetl directory` prints the root. `snuetl directory PATH` changes it for future
pulls without moving existing data. Add `--move --dry-run` to inspect a checksum-safe
migration and then `--move --yes` to perform it.

Use `snuetl profile` to list and interactively switch eTL identities, or
`snuetl profile "Graduate"` to select one directly.

## Agent and script interface

Use `--json --no-input` for LLM tools and unattended scripts. JSON responses have a
versioned envelope with `schema_version`, `ok`, `command`, `data`, `warnings`, and
`meta`; failures add a structured `error`. Discover the current surface with:

```bash
snuetl capabilities --json
snuetl courses --json --no-input
snuetl sql --execute "SELECT * FROM assignments" --json --no-input
snuetl pull articles --dry-run --json --no-input
```

Exit codes are stable: 0 success, 1 command failure, 2 authentication required,
3 invalid configuration/local state, 4 partial result, 5 required input, and 130
Ctrl+C. Commands never prompt in `--no-input` mode.

## Synchronize files

```bash
snuetl sync
snuetl status
```

The first run downloads all existing files. Later runs download new files and atomically
replace revised files. Removed remote files are retained locally. Use
`snuetl sync --headed` to troubleshoot navigation with a visible browser.

Files are stored as:

```text
<download directory>/<semester>/<course name>--<course id>/files/<ETL folders>/<filename>
```

Files tracked before 0.7 stay in their original locations until you explicitly run a
`snuetl directory ... --move` migration. Logs deliberately omit query strings and
redact common secret fields.

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
`snuetl logout` removes the dedicated trusted-browser profile and the saved credential
file. It leaves downloaded files, configuration, and synchronization history untouched.

## Uninstall

Run `snuetl uninstall` for an inventory and guided cleanup. Safe defaults retain pulled
files, configuration, browser trust, saved credentials, cached metadata, and history.
The command disables its systemd timer and removes a verified pipx installation last.

For automation, inspect the exact inventory first:

```bash
snuetl uninstall --dry-run --json --no-input
snuetl uninstall --yes --json --no-input
```

Destructive choices are explicit: `--delete-files` removes only paths tracked in the
database under the configured managed root; `--purge-config`, `--purge-state`, or
`--purge` remove private app data. `--remove-shared-deps` only considers Chromium or
FFmpeg installations recorded as installed by the setup wizard. It never runs broad
directory deletion or removes unrelated files.

## Development

```bash
python -m pip install -e '.[test]'
pytest
```

Live account tests are intentionally not automatic. HTML/JSON fixtures should be redacted
before committing, and traces must never be recorded on the SNU credential or verification
screens.
