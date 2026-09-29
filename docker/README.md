# SNUETL Docker deployment

> **Experimental / testing only.** This is an unofficial project, not affiliated with, endorsed by, or supported by Seoul National University. Project code is MIT-licensed; see the [license and disclaimer](../README.md#license-and-disclaimer).

Run the commands below from the **repository root**. The scripts and Compose file moved here; your existing root `.env` and `docker-data/` stay in place. Always use `--project-directory . -f docker/compose.yaml` from the root so existing relative mounts and the Compose project name remain unchanged. Do not move or delete your downloaded data.


Docker Compose packages Python, Playwright Firefox, its browser libraries, and FFmpeg into
one image. Firefox replaces Chromium as the default managed browser because
[Playwright documents a smaller managed download](https://playwright.dev/python/docs/browsers#managing-browser-binaries),
while preserving SNU SSO and LearningX automation. The application prefers
its Canvas API token after enrollment, so routine supported commands avoid browser startup.
The deployment runs as a non-root user, exposes no network ports, and supports native
Linux x86-64 and ARM64 Docker hosts. Docker Desktop on macOS and Windows is not currently
an advertised target because its bind-mount ownership model differs from Linux.

The named container remains running before and after setup. Its supervisor starts the
Discord daemon automatically when Discord becomes configured, but it never schedules
polling. Synchronization happens only when an owner invokes `/snuetl sync` in Discord or
someone runs `snuetl sync` in the container.

## Updating a checkout that used the old layout

After updating the source checkout, run `bash docker/docker-update.sh --force-rebuild` from the repository root. Keep the existing root `.env` and storage directories unchanged. If an already-running copy of the old root update script stops because `docker-setup.sh` moved, rerun the new command; the layout migration itself does not stop or delete the running container. Preserve any explicit `--env-file` and `--project-name` options used for your deployment.

## Partial source checkout

For a new checkout, select only the files this client needs:

```bash
git clone --filter=blob:none --sparse --single-branch https://github.com/euro-kim/snuetl.git snuetl-docker
cd snuetl-docker
git sparse-checkout set --cone src docker
```

Run the setup/build commands below from this checkout root. See the [main README](../README.md#download-only-the-client-you-need) for private Git authentication, updating, and changing an existing checkout.

## First deployment

Run the guided Docker bootstrap from the repository root:

```bash
bash docker/docker-setup.sh
```

The script provides the complete host-side deployment workflow:

- copies `docker/.env.example` to `.env` only when `.env` does not already exist;
- pauses while you review the container name, timezone, and persistent host paths;
- corrects `SNUETL_UID` and `SNUETL_GID` to match the invoking login user;
- creates missing bind mount directories at `0700` and checks access to existing ones;
- leaves ownership and modes of existing mounted directories and their contents unchanged;
- rebuilds with the latest base image and no stale build cache before stopping the current
  container;
- replaces a previous Compose attempt without deleting bind-mounted data;
- recreates the named container in detached mode and waits for a healthy supervisor.

The repository ignores `.env` and the default `docker-data/` tree. Run the script as your
normal login user, not with `sudo`; it requests elevated access only to create a missing
directory beneath a protected parent. Existing configuration and downloaded
content are preserved. The script intentionally does not run a global Docker cache prune,
remove named volumes, or delete any configured host directory.

The deployment requires standard rootful Docker on Linux. Rootless Docker and daemon-level
`userns-remap` translate bind-mount ownership differently and are rejected before any
directory is changed. Docker Desktop on macOS and Windows remains unsupported.

After the script reports that the deployment is healthy, complete headless enrollment and
configure either remote gateway:

```bash
docker compose --project-directory . -f docker/compose.yaml exec snuetl snuetl setup --headless
docker compose --project-directory . -f docker/compose.yaml exec snuetl snuetl telegram setup
docker compose --project-directory . -f docker/compose.yaml exec snuetl snuetl doctor
```

For Discord, use `docker compose --project-directory . -f docker/compose.yaml exec snuetl snuetl discord` instead of the Telegram
setup command.

The setup wizard already defaults to `/data/downloads` and `/data/videos`; keep those
container paths even when the matching host paths differ. Credentials and verification
codes are entered through the attached terminal. No browser window or VNC port is exposed.
After gateway setup completes, the supervisor starts its bot without requiring a
container restart. Discord and Telegram can run together.

## Browser and API-key responsibilities

The **Canvas API token** is the API key used for bearer-authenticated Canvas requests. It is
created inside the guided browser session and saved under the mounted state directory; do
not add it to `.env` or `compose.yaml`.

| Access path | What it runs in the container |
| --- | --- |
| Firefox browser driver | SNU ID/password, MFA, and trusted-device enrollment; creating, rotating, and fallback-revoking the API token in Canvas Account Settings; DOM/cookie fallback for catalog or file access; syllabus page rendering; LearningX/LTI/LCMS video resolution. |
| Canvas API token (API key) | Preferred course/file/article/assignment/quiz/module catalogs and ordinary downloads; required live personal views including upcoming work, submissions, grades, calendar, discussions, activity, feedback, and dashboard. |
| Local SQLite only | Status/history and cached `sql`/`query --no-refresh` reads. |

`courses`, `files`, `articles`, `assignments`, `quizzes`, `refresh`, `sync`, and most pull
operations try the API token first and launch Firefox only for a supported fallback. Live
personal-data commands have no browser fallback. Video pulls still use the browser provider
flow even when their module metadata came from the API. Discord and Telegram invoke these
same paths; they do not introduce a separate authentication method. See the Linux guide's
[per-command access matrix](../linux-client/README.md#personal-token-and-browser-driver-access)
for the complete breakdown.

The container remains headless and exposes no browser or VNC port. A Chromium compatibility
mode remains available for debugging an eTL regression, but it requires rebuilding a custom
image that installs Chromium and setting `engine = "chromium"` in the mounted `config.toml`;
the maintained Dockerfile installs Firefox only.

## Persistent path model

Values in `.env` are paths on the Docker host. Values saved in `config.toml` are paths
inside the container:

| `.env` variable | Container path | Contents |
| --- | --- | --- |
| `SNUETL_CONFIG_DIR` | `/home/snuetl/.config/snuetl` | `config.toml` |
| `SNUETL_STATE_DIR` | `/home/snuetl/.local/state/snuetl` | Browser profile, credentials, Discord token, SQLite catalog |
| `SNUETL_DOWNLOAD_DIR` | `/data/downloads` | Files, articles, and syllabi |
| `SNUETL_VIDEO_DIR` | `/data/videos` | Video downloads and captions |

Do not place host paths such as `/srv/snuetl/downloads` in the container's TOML file.
Instead, set that path as `SNUETL_DOWNLOAD_DIR` in `.env`; it will still appear inside
the container as `/data/downloads`. The stable internal paths allow the image to be
rebuilt or moved without rewriting catalog records.

The default repository-local paths are convenient for a first run. For a server, replace
them with absolute host paths before enrollment. The setup script rejects a literal `~`,
environment-variable expansion, overlapping directories, repository ancestors, and unsafe
top-level system paths before using a mount. After changing `.env`, rerun the script;
it rebuilds for possible UID/GID changes and recreates the container while preserving data:

```bash
bash docker/docker-setup.sh
```

Configuration and state contain reusable credentials, so choose suitable host permissions
yourself. Docker setup and container startup preserve modes on existing bind mounts. If the
container UID/GID cannot read, write, and traverse one, setup reports the path and stops.
New directories start at `0700`.

## Terminal and operations

Open an interactive shell at any time; leaving the shell does not stop the supervisor:

```bash
docker compose --project-directory . -f docker/compose.yaml exec snuetl bash
docker compose --project-directory . -f docker/compose.yaml exec snuetl snuetl status
docker compose --project-directory . -f docker/compose.yaml exec snuetl snuetl sync
```

Inspect or restart the deployment with:

```bash
docker compose --project-directory . -f docker/compose.yaml logs -f snuetl
docker compose --project-directory . -f docker/compose.yaml restart snuetl
docker compose --project-directory . -f docker/compose.yaml stop snuetl
docker compose --project-directory . -f docker/compose.yaml start snuetl
```

An `unhealthy` container means the supervisor heartbeat stopped. A healthy container can
still be waiting for initial setup or gateway pairing; use `snuetl doctor` and
`snuetl telegram status` or `snuetl discord status` to inspect application readiness.

## Updating a Docker deployment

The package is baked into the read-only image, so do not run `snuetl update` through
`docker compose --project-directory . -f docker/compose.yaml exec`. That command now reports the host-side update instruction instead
of attempting an in-container package change. Update an existing container in place from
the host checkout with the dedicated script:

```bash
bash docker/docker-update.sh
```

The updater verifies that the checkout is clean and on `main`, fetches `origin/main`, and
compares that commit with both the checkout and the running container image's revision
label and image ID. A current checkout with an old image is rebuilt. It only permits a
fast-forward source update and never resets local history. The new image is built before
the current container is replaced. If health verification fails, the updater restores and
checks the previous image. All four bind-mounted data directories are preserved. Running
`snuetl update` inside the container cannot persist across recreation because the package
is installed in the image.

After the replacement is healthy, refresh SNU authentication with the corrected login
handling if the old container failed on the NSSO redirect:

```bash
docker compose --project-directory . -f docker/compose.yaml exec snuetl snuetl login --headless
```

For unattended operation after reviewing the remote and for rebuilding an instance that
already has the current source revision:

```bash
bash docker/docker-update.sh --yes
bash docker/docker-update.sh --force-rebuild
```

`--yes` skips the update confirmation. `--force-rebuild` recreates a healthy deployment
even when its image already contains the fetched commit. Each Compose project uses its own
image tag so updating one project does not replace another project's image.

For a consistent backup, stop the container and copy the four host directories from
`.env`, encrypting the config/state backup because it contains reusable credentials,
cookies, and the Discord token. `docker compose --project-directory . -f docker/compose.yaml down` removes the container and network
but does not delete bind-mounted data. Because the image is immutable, remove a Docker
deployment with Compose rather than running `snuetl uninstall`; delete bind directories
separately only after verifying the backup and exact host paths.

## Multiple accounts

Use a separate env file, explicit container name, and four distinct host paths for every
SNU account. For example:

```bash
cp .env.example .env.alice
# Edit SNUETL_CONTAINER_NAME and every SNUETL_*_DIR value in .env.alice.
bash docker/docker-setup.sh --env-file .env.alice --project-name snuetl-alice
docker compose --project-directory . -f docker/compose.yaml --env-file .env.alice -p snuetl-alice exec snuetl \
  snuetl setup --headless
```

Update that specific instance with the same environment and project identity:

```bash
bash docker/docker-update.sh --env-file .env.alice --project-name snuetl-alice
```

Never share a config directory, state directory, or writable download root between
instances. Changing only the Compose project name is insufficient because
`container_name` and the bind-mount paths are explicit.

The Compose service uses Docker's default seccomp policy, `no-new-privileges`, a read-only
root filesystem, Docker init handling, and a non-root application user. Firefox does not
need the Chromium-specific host IPC and user-namespace seccomp exceptions that the previous
deployment requested.


## Build context

The image uses shared `src/`, `pyproject.toml`, and the root README/license. `Dockerfile.dockerignore` allows only those package inputs into the build context. No local credentials, Windows builds, or downloaded course data are sent.
