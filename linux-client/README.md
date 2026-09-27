# SNUETL Linux client

> **Experimental / testing only.** This is an unofficial project, not affiliated with, endorsed by, or supported by Seoul National University. Project code is MIT-licensed; see the [license and disclaimer](../README.md#license-and-disclaimer).

A self-hosted Linux CLI for course synchronization, local catalogs, optional Discord/Telegram access, and systemd scheduling. Python source and tests are shared at the repository root. Run checkout commands below from that root.

For container deployment, use the [Docker guide](../docker/README.md). For desktop clients, see [Windows](../windows-client/README.md) and [macOS](../macos-client/README.md).

## Contents

- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Raspberry Pi and Linux ARM64](#raspberry-pi-and-linux-arm64)
- [Authentication and secrets](#authentication-and-secrets)
- [Local storage](#local-storage)
- [Multiple users on one Linux server](#multiple-users-on-one-linux-server)
- [Command reference](#command-reference)
- [Discord remote control](#discord-remote-control)
- [Telegram remote control](#telegram-remote-control)
- [SQL catalog](#sql-catalog)
- [Pull course content](#pull-course-content)
- [Agent and script interface](#agent-and-script-interface)
- [Synchronize files](#synchronize-files)
- [Scheduled deployment (experimental)](#scheduled-deployment-experimental)
- [Uninstall](#uninstall)
- [Development](#development)

## Requirements

| Component | Requirement |
| --- | --- |
| Operating system | 64-bit Linux (x86-64 or ARM64) |
| Python | 3.11 or newer |
| Browser | Playwright Chromium, Google Chrome, or system Chromium on Raspberry Pi OS |
| Deployment runtime | Docker Compose v2, or systemd user services for a native install |
| Account | An authorized SNU eTL account |

Desktop operation is not required. Setup detects display availability and uses headless
Chromium on servers. The browser installation step may request `sudo` to install required
operating-system packages; the application itself runs as an unprivileged user.


## Quick start

Use a partial clone to fetch only Linux setup and shared code, then run the guided installer:

```bash
git clone --filter=blob:none --sparse --single-branch https://github.com/euro-kim/snuetl.git snuetl-linux
cd snuetl-linux
git sparse-checkout set --cone src linux-client
bash linux-client/setup.sh
```

For private-repository authentication, updates, or an existing checkout, see [partial checkout instructions](../README.md#download-only-the-client-you-need). Root package files are included automatically; no Windows client files are checked out.

The script installs pipx when needed, installs the command and its dependencies, and
starts setup. Agents and unattended installers can use `bash linux-client/setup.sh --install-only`, then
call the stable `snuetl ... --json --no-input` interface. Direct installation with
`pipx install --include-deps .` is also supported.

Running bare `snuetl` on a fresh machine opens a guided terminal setup. It:

- checks Playwright and automatically downloads its managed Chromium;
- offers to install missing Ubuntu browser libraries (sudo may prompt);
- asks where course files should be stored;
- prompts privately for SNU ID and password;
- asks whether to save them for automatic re-login (default: yes);
- explicitly asks whether this device should be trusted (default: yes);
- asks whether to send the 2FA code by email or phone;
- prompts privately for the received code;
- enables **이 브라우저에서 추가 인증 사용 안함**; and
- verifies the login by fetching the active course list.

Terminal choices use Up/Down to move, Space to check an option, and Enter to confirm.
Setup does not prompt to create an automatic synchronization schedule.

No configuration file needs to be created manually. Re-run the wizard later with
`snuetl setup`, `snuetl onboard`, or `snuetl configure`. On a server without a
display, the same wizard automatically uses headless Chromium; `snuetl setup --headed`
and `--headless` are available when an explicit mode is needed.

For development from a checkout, add the shared tests and create a virtual environment:

```bash
git sparse-checkout add tests
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
snuetl
```


## Raspberry Pi and Linux ARM64

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
OS image is required; 32-bit `armv7l` is not supported. Run `snuetl doctor` on the
target machine to validate its browser installation.


## Authentication and secrets

Enrollment must run on the same machine that will perform synchronization:

```bash
snuetl login
```

The command selects SNU's ID/password tab, requests the chosen email/SMS code, and
enters the terminal-provided code. It explicitly asks whether to select SNU's
trusted-browser option; the default is yes. Setup chooses a sensible initial browser
mode for the machine. Use `snuetl headless on` or `snuetl headless off` to save the
default for future browser-backed terminal commands; `--headless` and `--headed`
override it for one command. The Discord daemon always uses headless mode because it
cannot depend on a desktop session.

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


## Local storage

`snuetl` follows the XDG directory convention and does not create `~/.snuetl`:

- `~/.config/snuetl/config.toml` stores normal settings, including the download root,
  browser mode, directory routes, and remote gateway binding IDs.
- `~/.local/state/snuetl/` stores private runtime state: `state.db`, the browser
  profile and auth state, optional `credentials.json`, install provenance, and
  `discord-token` and `telegram-token`. Remote jobs are persisted in `state.db`.
- `~/Downloads/snuetl/` is the default pulled-content root; setup or
  `snuetl directory` can change it.

`XDG_CONFIG_HOME` and `XDG_STATE_HOME` relocate the first two roots. The config and
secret files are owner-only (`0600`) and the state directory is `0700`. Gateway
tokens are deliberately separate from TOML so they do not appear beside ordinary
configuration, but each token and the optional SNU credential file are plaintext secrets
and must be protected accordingly.


## Multiple users on one Linux server

Use one Linux user account for each independent SNU eTL account. A user's `snuetl`
installation is one isolated deployment containing that user's configuration, saved SNU
credentials, trusted-browser profile, SQLite catalog, download directory, Discord token,
and systemd user services. Do not share any of these files between users.

Install and enroll `snuetl` while logged in as each user so its files are created under
that user's home directory and SNU trusts the browser profile on the server:

```bash
# Run separately in each user's login session.
bash linux-client/setup.sh
snuetl setup --headless
snuetl discord
```

Create a separate Discord application and bot token for every Linux user. The bots may
join the same Discord server, but bind each one to a separate private channel, such as
`#snuetl-alice` and `#snuetl-bob`. Grant each bot access only to its own channel. Do not
reuse one bot token across users or daemon processes.

For unattended service after logout, an administrator must enable lingering separately
for each Linux account:

```bash
sudo loginctl enable-linger alice
sudo loginctl enable-linger bob
```

The values in `discord.owner_ids` are an allowlist for one deployment. Adding several
Discord owners gives all of them control over the same SNU account and files; it does not
map each Discord user to a separate SNU account.

Multiple independent deployments under a single Linux user are not supported. Although
`--config` and `general.state_dir` can separate most files, the generated systemd user
units have singleton names (`snuetl.service`, `snuetl.timer`, and
`snuetl-discord.service`) and a second setup would replace the first user's units. Create
another Linux user instead.

Keep each download root private by default. If users intentionally need common output,
export or copy selected files to a separately managed shared directory with an
appropriate Unix group; do not point multiple deployments at the same writable download
root or share their state directories.


## Command reference

Bare `snuetl` displays current local status and the command menu after onboarding.

```text
snuetl setup                    guided setup or repair
snuetl login                    refresh SNU authentication
snuetl api setup                create Canvas API access through Account Settings
snuetl api status               check API access without revealing the token
snuetl api rotate               replace the Canvas token
snuetl courses                  list active course IDs and names
snuetl files [COURSE]           list file titles, folders, sizes, and dates
snuetl articles [COURSE]        list announcement and course-page titles
snuetl assignments [COURSE]     list assignment titles and due dates
snuetl quizzes [COURSE]         list quiz titles and due dates
snuetl upcoming                 list incomplete work due in the next 30 days
snuetl missing                  list past-due missing submissions
snuetl submissions [COURSE]     list submission states and available scores
snuetl grades [COURSE]          show available course grades
snuetl calendar                 list course events for the next 30 days
snuetl discussions [COURSE]     list discussion topics
snuetl activity [COURSE]        list new course activity
snuetl announcements [COURSE]   list recent announcements with summaries
snuetl modules [COURSE]         show module items, progress, and locks
snuetl feedback [COURSE]        show recent grades, comments, and rubrics
snuetl dashboard [COURSE]       summarize near-term work and course grades
snuetl refresh                  cache every paginated catalog table
snuetl sql                      refresh data and open the read-only SQL shell
snuetl query                    compatibility alias for the SQL shell
snuetl sql --execute SQL        refresh and run one SQL query
snuetl sql --no-refresh         use cached SQL data without network access
snuetl schema                   show canonical tables and field names
snuetl pull [KIND]              pull files, articles, syllabi, videos, or all
snuetl sync                     compatibility alias for pulling files
snuetl headless [on|off]        show or set the default browser mode
snuetl directory                show or configure pull roots and routing rules
snuetl directory videos [PATH]  show or set the large-video storage root
snuetl capabilities --json      describe the stable agent-facing contract
snuetl status                   show the last sync and tracked counts
snuetl doctor                   check browser, config, profile, and runtime
snuetl version [--check]        show the installed and published versions
snuetl update                   update the pipx installation
snuetl logout                   revoke Canvas token and remove local authentication
snuetl uninstall                remove the app and optionally its local data
snuetl discord                  pair Discord and install its user daemon
snuetl discord guide            show exact Developer Portal setup steps
snuetl telegram                 pair a private Telegram chat and install its daemon
snuetl telegram guide           show BotFather setup steps
snuetl telegram alerts off      pause the daily Telegram digest
```

`COURSE` may be an exact course ID or a unique portion of its title. Leave it out to
show content from every active course. The saved headless preference applies to every
browser-backed terminal command; add `--headed` or `--headless` for a one-command
override.

Guided setup offers Canvas API access by default. The token is created through the
authenticated Account Settings page, expires after one year, and is saved at
`~/.local/state/snuetl/canvas-token.json` with mode `0600`. If you decline, run
`snuetl api setup` later. Personal-data commands fetch live results. They remain in
memory unless you pass `--save`, which writes an owner-only Markdown snapshot under
`<download-root>/canvas/` and rows to the local `canvas_snapshots` SQL view. This can
include grades, submissions, and instructor feedback, so protect that directory and
database as private data. `--save` refuses an existing `canvas/` directory that is
accessible to other users; it does not change its permissions. Use `--from YYYY-MM-DD` and
`--to YYYY-MM-DD` with `upcoming`, `calendar`, `activity`, `announcements`, or `dashboard` to change
the date range. `feedback --since YYYY-MM-DD` selects recently graded submissions.
Use `--json` for the full structured comments and rubric details. New API views honor
configured course exclusions. Logout revokes the snuetl token on Canvas before removing
local login state.

For example:

```bash
snuetl dashboard --json
snuetl announcements Biology --from 2026-09-01
snuetl modules Biology --json
snuetl feedback Biology --since 2026-09-01 --json
snuetl grades --save
snuetl sql --execute "SELECT course_id, data_json FROM canvas_snapshots WHERE command = 'grades'"
```

### Personal token and browser-cookie access

| Feature or command | Personal Canvas token | Browser cookies | Relationship and local output |
| --- | --- | --- | --- |
| `setup`, `login`, `api setup`, `api rotate` | Created or replaced through Account Settings | Required for interactive SNU login and token creation | Browser workflow establishes access. |
| `api status` | Validates a saved token with the Canvas profile API | None | Reports token health without printing the secret. |
| `courses`, `files`, `articles`, `assignments`, `quizzes` | Tried first for the complete Canvas catalog | Canvas API with cookies, then page discovery when token access fails | Successful results update the SQLite catalog. |
| `refresh`, `sql`, `query` | Tried first for all catalog metadata | Canvas API with cookies, then page discovery if token refresh fails | Entering SQL refreshes the catalog automatically unless `--no-refresh` is set. |
| `sync`, `pull files` | Tried first for catalog metadata and each file download | Used for discovery or individual downloads when token access fails | Downloaded files and tracking rows are local. |
| `pull articles` | Tried first for article bodies and linked public assets | Used if article discovery or an asset request needs browser cookies | Writes local Markdown and localized assets. |
| `pull syllabus` | Tried first for course metadata and uploaded syllabus files | Used for embedded syllabus content and PDF rendering; also backs up file downloads | Writes Markdown, PDF when rendering succeeds, uploaded files, and a manifest. |
| `pull videos` | Tried first for module metadata and uploaded media files | Used to resolve LearningX, LTI, and LCMS player media | Video streaming still needs the browser provider flow; saves media and captions. |
| `upcoming`, `missing`, `submissions`, `grades`, `calendar`, `discussions`, `activity`, `announcements`, `modules`, `feedback`, `dashboard` | Required for live personal Canvas data | No browser-cookie fallback | `--save` writes Markdown and queryable `canvas_snapshots` rows; otherwise results are not stored. |
| `sql`, `query` saved personal scopes | Refreshes scopes previously saved with `--save` | No browser-cookie fallback for personal scopes | SQL rows update on entry; Markdown snapshots update only when `--save` is run again. `--no-refresh` uses cached rows. |
| Discord catalog, refresh, sync, and pull | Uses the corresponding terminal command's token-first path | Same fallback as the corresponding terminal command | Remote jobs use the server's own token and browser profile. |
| Telegram live views and digest; Telegram sync | Live views require the token; sync prefers it | Live views have no cookie fallback; sync uses browser cookies when needed | Telegram does not persist personal snapshots unless a local `--save` command is run. |
| `logout` | Tries Canvas API token revocation first | Uses Account Settings if API revocation fails | Removes local authentication after remote revocation. |

The token is sent only to the configured Canvas origin. A file or asset URL on another
host is fetched without that Authorization header. Browser login still handles SNU MFA
when a cookie fallback needs a new session.


## Discord remote control

Discord remote control is intended for a private, self-hosted server where the owner
wants to start work from a phone without SSH. Complete `snuetl setup` first and choose
to save the SNU credentials. You also need Discord's **Manage Server** permission on the
server where the private bot will be installed.

Run the standalone guide whenever you want instructions without changing anything:

```bash
snuetl discord guide
```

Run the actual wizard when ready:

```bash
snuetl discord
```

The wizard displays these steps as you work:

1. Open the [Discord Developer Portal](https://discord.com/developers/applications),
   choose **New Application**, enter a name such as `snuetl`, and create it.
2. On **General Information**, copy **Application ID** and paste it at the wizard prompt.
   Application ID is a numeric, non-secret value. Do not paste Public Key, Client Secret,
   or your personal Discord user ID.
3. Open **Bot** in the application sidebar. Under **Token**, choose **Reset Token**,
   complete Discord's confirmation, copy the newly issued token, and paste it into
   snuetl's hidden prompt. Discord normally shows a newly reset token only once.
4. On **Installation**, ensure **Guild Install** is enabled. User Install is not needed.
   On **Bot**, leave **Require OAuth2 Code Grant** off. The Presence, Server Members, and
   Message Content privileged intents can all remain off.
5. Open the least-privilege invite printed by snuetl, select **Add to server**, choose the
   target server, and authorize it. The generated link requests only the `bot` and
   `applications.commands` scopes and permission integer `84992`.
6. Create or choose one normal text channel, ideally a private channel named `#snuetl`.
   Enter `/snuetl claim` there and paste the one-time code from the terminal into
   Discord's `code` field. The code expires after ten minutes. The person who claims it
   becomes the first authorized snuetl owner.

The bot needs exactly these four channel permissions:

| Permission | Why |
| --- | --- |
| View Channel | Access the one bound channel |
| Send Messages | Post progress and final results |
| Embed Links | Display readable status and result cards |
| Read Message History | Keep responses usable across reconnects |

Do **not** grant Administrator, Manage Server, Manage Channels, Manage Roles, or Manage
Messages. For stronger Discord-side isolation after installation, open **Server Settings
→ Roles**, select the bot role, and remove its four server-wide permissions. Then open
**Edit Channel → Permissions** on `#snuetl`, add the bot role, and explicitly allow the
four permissions there. Independently of Discord's visibility settings, snuetl checks
every slash command, autocomplete request, button, select, and modal and rejects anything
outside the claimed server, channel, and owner allowlist.

Discord responses are designed for mobile rather than mirroring terminal JSON. Status,
diagnostics, courses, files, articles, assignments, quizzes, and recent jobs use compact cards;
long catalogs have owner-only Previous and Next buttons. Dates use Discord timestamps, so
they appear in each viewer's local time zone. Background operations post one progress card
that updates in place and finishes with a plain-language summary of what was downloaded,
updated, preserved, skipped, or failed. `/snuetl cancel` suggests active job IDs, while
course and semester inputs provide autocomplete from the current catalog.

Expected failures provide a recovery action, while unexpected failures show a short error
reference instead of a Python traceback or secret-bearing diagnostic. The matching redacted
traceback is kept in the daemon journal for troubleshooting.

The token is a password. Never paste it into Discord, a command-line argument, a chat
message, or source control. If it is exposed, immediately use **Developer Portal → Bot →
Reset Token** and rerun `snuetl discord`. snuetl stores it separately at
`~/.local/state/snuetl/discord-token` with mode `0600`; it is never stored in TOML,
SQLite job arguments, logs, command-line arguments, or Discord messages.

Setup installs and starts `~/.config/systemd/user/snuetl-discord.service`. For a server
that must keep the user service alive after logout, `snuetl discord status` reports
whether linger is active and prints the exact remediation when needed:

```bash
sudo loginctl enable-linger "$USER"
```

The bot uses slash commands and Discord's Gateway, so it needs no public web server,
redirect URL, Interactions Endpoint URL, client secret, or Message Content intent.

Available mobile commands are `/snuetl status`, `doctor`, `courses`, `files`, `articles`,
`assignments`, `quizzes`, `refresh`, `sync`, `pull`, `login`, `jobs`, and `cancel`. Administrative
terminal operations such as setup, SQL, directory changes, profile switching, update,
logout, and uninstall are deliberately unavailable in Discord. Downloads remain on the
server; Discord receives counts, warnings, progress, and paths relative to the managed
download root, never file uploads.

Long operations use a persistent serialized queue: one job runs at a time and up to ten
wait. A daemon restart marks unfinished jobs interrupted instead of silently replaying
them. `/snuetl cancel JOB_ID` requests cancellation at the next course, item, or video
download progress checkpoint. Controls and catalog results are ephemeral, while durable
progress and final summaries are posted in the bound channel.

`/snuetl pull kind:videos` replaces the terminal checklist with a ten-minute paginated
multi-select that preserves choices across pages and works on Discord mobile. Force mode
requires an extra overwrite confirmation. `/snuetl login` uses only the credentials
already saved on the server; if SNU requests additional verification, an authorized
owner enters the email/SMS code in a private Discord modal. SNU passwords are never
accepted through Discord.

Useful lifecycle commands are:

```bash
snuetl discord status
snuetl discord disable
snuetl discord enable
snuetl discord owner list
snuetl discord owner add USER_ID
snuetl discord owner remove USER_ID
snuetl discord run                 # foreground diagnostics
```

For provisioning, keep the token in an owner-only file and avoid shell history:

```bash
chmod 600 /secure/path/discord-token
snuetl --no-input discord setup \
  --token-file /secure/path/discord-token \
  --application-id APP_ID --guild-id SERVER_ID --channel-id CHANNEL_ID \
  --owner-id USER_ID --yes
```


For the numeric IDs used by non-interactive setup, enable **Discord Settings → Advanced
→ Developer Mode**. Then right-click the server icon and choose **Copy Server ID**,
right-click the channel and choose **Copy Channel ID**, and right-click your account and
choose **Copy User ID**. On mobile, enable Developer Mode under **Settings → Advanced**,
then long-press the corresponding server, channel, or user and choose the copy-ID action.
The interactive claim flow discovers these three IDs automatically.

Discord's current official walkthrough is available in
[Building your first Discord Bot](https://docs.discord.com/developers/quick-start/getting-started);
its [permissions reference](https://docs.discord.com/developers/topics/permissions)
documents the four permission bits in the generated invite.

The human-readable tables show semester codes separately (`2026-2`, `SNUON`), use
canonical course IDs, and keep file names and folders distinct in storage. The table
view intentionally summarizes timestamps and sizes so it remains usable in narrow
terminals; SQL, JSON, JSONL, and CSV retain the complete values.


## Telegram remote control

Telegram control is opt-in and bound to one private chat and one Telegram user. Complete
`snuetl setup` first. Create a dedicated bot with [@BotFather](https://t.me/BotFather)
using `/newbot`, then run:

```bash
snuetl telegram setup
```

Paste the bot token into the hidden terminal prompt. Open the pairing link printed by
snuetl and press **Start** within ten minutes. The bot accepts commands only from the
paired user in that private chat; group messages and other users are ignored. The token
is stored outside the config file in an owner-only `telegram-token` state file.
The bot uses [Telegram long polling](https://core.telegram.org/bots/api#getupdates), so
neither native nor Docker deployment needs a public inbound port. Use a bot that has no
existing webhook or other polling consumer.

Available bot commands are `/status`, `/upcoming`, `/missing`, `/activity`,
`/announcements`, `/modules`, `/feedback`, `/dashboard`, `/sync`, `/jobs`, and `/cancel JOB_ID`.
The four new Canvas views accept an optional course name or ID after the command.
`/sync` queues a file synchronization job and reports its result in the chat. `/jobs`
shows progress, and `/cancel` requests cancellation. Canvas API access is required for
the live views and digest; run `snuetl api setup` locally if the token is missing or
expired. Interactive SNU login remains a local command.

At 09:00 Asia/Seoul, the bot sends a daily digest of incomplete work due in the next
seven days and missing submissions. It sends a short empty digest when there is no work.
The last delivered date is saved to avoid duplicate messages after a restart. Manage it
with `snuetl telegram alerts on` or `snuetl telegram alerts off`. Use
`snuetl telegram status`, `enable`, or `disable` to manage the daemon. Native Linux uses
`snuetl-telegram.service`; the Docker supervisor starts it after pairing. In Docker,
run setup as `docker compose exec snuetl snuetl telegram setup`.


## SQL catalog

Opening `snuetl sql` or `snuetl query` refreshes the catalog before querying. It also
refreshes personal Canvas scopes previously saved with `--save`. This downloads no files.
To refresh without entering SQL, run:

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

Automatic refresh is best effort: if Canvas is unavailable, SQL uses the existing cache
and shows a warning. Use `--refresh` to require a successful refresh, or `--no-refresh`
to query the cache without network access. Previously saved personal SQL scopes are
refreshed on entry; their Markdown files change only when you rerun the personal command
with `--save`. Query `canvas_snapshot_status` to see each saved scope's last refresh time,
including scopes that currently return no rows. Query output defaults to a terminal table and 200 rows. Use `--limit 0`
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
`SHOW TABLES;` is accepted as an SQL-style alias for `.tables`. Press Ctrl+C or Ctrl+D
to leave query mode immediately.

The canonical read-only tables are `semesters`, `courses`, `files`, `articles`,
`announcements`, `pages`, `assignments`, `quizzes`, `modules`, `module_items`, `videos`,
`syllabi`, `artifacts`, `catalog_status`, `sync_runs`, `canvas_snapshots`, and
`canvas_snapshot_status`. Run
`snuetl schema` for their canonical field names. For example, use `course_id` rather
than the ambiguous `class_id`, `file_id` for the remote file identifier, and
`size_bytes` for exact size.

`files` represents remote catalog files whether or not they have been downloaded.
`download_status`, `local_path`, and `sha256` describe local synchronization state.
`articles` combines announcements and course pages while the `announcements` and
`pages` views allow type-specific queries.

`assignments` and `quizzes` are separate views and commands. Both are discovered from
Canvas's assignment feed, then classified using its quiz metadata. The first refresh after
upgrading automatically retires any old assignment-classified quiz row and stores it as a
quiz; no manual database migration is needed.

SNU's API pagination is consumed completely by following every `rel="next"` link. A
catalog scope is committed only after its pages have been fetched, and freshness can
be inspected with `SELECT * FROM catalog_status`. Running `courses`, `files`,
`articles`, `assignments`, or `quizzes` also refreshes the corresponding cached metadata; the
dedicated `refresh` command updates all of them in one token session when possible,
with browser discovery as a fallback.

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

Video pulling uses token-first module discovery followed by authenticated browser media
resolution and yt-dlp, defaults to 1080p and
downloads available captions. For SNU LCMS lectures it reads the actual player media URL,
rejects UniPlayer's short preloader clip, sends the LCMS referrer required by the CDN, and
can resolve LearningX attendance items through their freshly minted LTI token. Interactive
downloads report resolution status and byte/percentage progress. It does not bypass DRM.
`pull all` deliberately excludes videos. `pull videos` presents an arrow-key/spacebar
checklist (including an All checkbox); non-interactive callers such as Hermes can pass one
or more `--video-id` values, or `--yes` to select all. Use `--best` to remove the height cap,
and inspect `--dry-run` first because provider-reported sizes are often unavailable.

`snuetl directory` prints the default root and every routing rule. Changing the root or
a rule automatically migrates files and generated artifacts that snuetl tracks in its
database. Migration copies and verifies checksums before replacing paths in the database;
if two records target the same path, or a destination already contains different
content, the whole migration stops before moving anything. Preview any change with
`--dry-run`, or use `--no-migrate` to change only future placement:

```bash
snuetl directory
snuetl directory set /data/classes --dry-run
snuetl directory set /data/classes
snuetl directory /data/classes              # shorthand for "set"
snuetl directory videos                      # show the current video root
snuetl directory videos /mnt/large-videos --dry-run
snuetl directory videos /mnt/large-videos    # migrate tracked videos separately
snuetl directory videos --default            # keep videos with regular course files
snuetl directory bind /data/classes/A --name x --kind files --remote-folder x
snuetl directory bind /data/classes/A/B --name y --kind files --remote-folder y
snuetl directory bind /data/classes/A/C --name z --kind files --remote-folder z
snuetl directory unbind y
```

Guided setup asks separately where regular course files and large video downloads should
live. A dedicated video location is a storage root, so it retains the collision-safe
`<semester>/<course name>--<course id>/videos/` hierarchy rather than flattening every
course into one directory. Existing tracked videos migrate automatically when setup is
rerun or `snuetl directory videos PATH` is applied; `--dry-run` previews the move and
`--no-migrate` changes only future placement.

Managed content roots are private by default when newly created, but usable existing modes
are preserved. Guided setup and successful `directory set`, `directory videos`, or
`directory bind` operations require read, write, and traverse access; `0755`, `0750`, and
group-managed modes therefore remain unchanged when they already grant the running user the
required access. If an owner-controlled path lacks access, snuetl adds only the missing
owner bits without removing existing group or other bits. The response reports mode,
ownership, read/write/search access, and whether anything changed. A read-only `snuetl
directory` or `--dry-run` audit never changes permissions; unusable paths produce an
`UNSAFE_DIRECTORY_PERMISSIONS` warning with an exact minimal permission or ownership
remedy. A path owned by someone else is accepted when it is already usable; otherwise setup
stops and asks the operator to repair ownership. `snuetl doctor` audits the default root and
every binding as well. Configuration and state directories are intentionally excluded from
this relaxed content policy and remain owner-only at `0700`.

A binding can match any combination of cached course ID or unique title, canonical
semester, content kind, and eTL folder prefix:

```bash
snuetl directory bind /data/db/week-2 \
  --name db-week-2 --course 306087 --semester 2026-2 \
  --kind files --remote-folder "자료/Week 2"
```

The destination is an exact local mount point: the matched remote prefix is stripped.
For example, binding remote `x` to local `A` places `x/notes.pdf` at
`A/notes.pdf`; unmatched descendants remain, so `x/week-2/notes.pdf` becomes
`A/week-2/notes.pdf`. This also permits nested rules such as `x -> A` and
`x/week-2 -> A/B`.

When rules overlap, snuetl deterministically chooses the most specific one: more
course/semester/kind selectors first, then the longest remote-folder prefix, then the
most recently defined equally specific rule. A remote-folder selector implies
`--kind files`; folder selectors are rejected for articles, syllabi, or videos.

User-created content can coexist inside every managed root. snuetl treats its SQLite
records—not a directory scan—as ownership: an unrelated `A/D/assignment.txt` is never
considered deleted just because `D` does not exist on eTL, and it is not moved or
removed by route migration. If a first-time pull wants the exact path of an existing
untracked file, the remote file receives a deterministic
`__snuetl-<remote-id>` suffix instead of overwriting it. Do not repurpose a file already
tracked by snuetl for local work, because a later remote revision may replace that tracked
file; keep personal work in its own filename or subdirectory.

Use `snuetl profile` to list and interactively switch eTL identities. Move with Up/Down,
check one identity with Space, and press Enter to confirm. Use
`snuetl profile "Graduate"` to select one directly; agents can list profiles with
`snuetl profile --json --no-input`.


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
cancelled via Ctrl+C or Ctrl+D. Commands never prompt in `--no-input` mode.


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

Files tracked before 0.7 stay in their original locations until the next automatic
`snuetl directory` migration. Logs deliberately omit query strings and redact common
secret fields.

The synchronizer first tries the personal Canvas token. If it is unavailable or rejected,
it uses the authenticated Canvas-compatible API with browser cookies, then semantic DOM
selectors. File downloads also try the token before cookie-backed requests. It never
uses fixed screen coordinates.


## Scheduled deployment (experimental)

This section covers a native pipx/systemd deployment. For a containerized installation,
use [Docker Compose deployment](#docker-compose-deployment) instead.

Run each deployment as a dedicated, unprivileged Linux user. Keep the checkout, config,
state, and download roots owned by that user; never run `snuetl` or its browser with
`sudo`. Before enabling automation, complete enrollment and prove that one manual sync
succeeds:

```bash
bash linux-client/setup.sh --install-only
snuetl setup --headless
snuetl doctor
snuetl sync
```

Review every warning from `snuetl doctor` before continuing. In particular, confirm that
the configured directories are private, the selected browser launches, authentication is
valid, and the download volume has enough free space.

### Scheduled synchronization

Setup deliberately does not enable a schedule. To opt in, install the version-controlled
systemd user-unit templates:

```bash
mkdir -p ~/.config/systemd/user
cp linux-client/systemd/snuetl.service linux-client/systemd/snuetl.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now snuetl.timer
systemctl --user list-timers snuetl.timer
```

The supplied service is a `Type=oneshot` user unit with a restrictive umask and systemd
hardening directives. It expects the pipx executable at `~/.local/bin/snuetl`; update
`ExecStart` in the installed copy if pipx uses a different binary directory. To keep the
user manager and timer running after logout, an administrator must enable lingering:

```bash
sudo loginctl enable-linger "$USER"
```

### Operations

Use these checks for routine monitoring and incident diagnosis:

```bash
systemctl --user status snuetl.timer
systemctl --user status snuetl.service
journalctl --user -u snuetl.service --since today
snuetl status
snuetl doctor
```

An exit code of `2` means authentication must be renewed with `snuetl login`. Exit code
`3` indicates invalid configuration or local state; run `snuetl doctor` for the precise
remediation. A partial result exits with code `4` and should be inspected in the service
journal before the next scheduled run.

For a local-checkout installation, deploy a reviewed revision and rebuild the pipx
environment with:

```bash
git pull --ff-only
snuetl update
snuetl doctor
systemctl --user start snuetl.service
```

The timer does not need to be re-enabled after an application update. If the unit files
changed, copy them again and run `systemctl --user daemon-reload` before the final service
start.

### Backup and recovery

Back up the download root and, if catalog history matters, the configuration and state
directories listed above. Treat state backups as secrets: they can contain the saved SNU
password, Discord token, authenticated browser cookies, and local catalog. Encrypt backups,
restrict access, and stop the timer and Discord service before taking a consistent snapshot.

Do not restore a trusted-browser profile onto a different host as a substitute for
enrollment. On a replacement machine, restore only the required data, run `snuetl setup`
and `snuetl login` locally, then validate the deployment with `snuetl doctor` and a manual
sync before enabling the timer.

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
database under the configured default or routed roots; `--purge-config`,
`--purge-state`, or `--purge` remove private app data. `--remove-shared-deps` only
considers Chromium or FFmpeg installations recorded as installed by the setup wizard. It
never runs broad directory deletion or removes unrelated files.


## Development

```bash
python -m pip install -e '.[test]'
ruff check .
mypy
pytest
```

The browser-facing architecture is organized around typed boundaries: an authenticated
LMS session owns locking and browser state, discovery coordinates Canvas API and DOM
backends, and file synchronization is shared by `sync` and `pull files`. Provider-specific
video resolution lives separately from pull orchestration. Keep new LMS parsing behind
these boundaries and cover payload changes with redacted fixtures under `tests/fixtures`.

Live account tests are intentionally not automatic. HTML/JSON fixtures should be redacted
before committing, and traces must never be recorded on the SNU credential or verification
screens.

The desktop build workflow covers package creation on native runners. Run the complete
local quality gate before opening a change:

```bash
ruff check .
mypy
pytest
```
