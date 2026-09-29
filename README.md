# SNUETL

**Unofficial SNU eTL clients — all components are experimental and in testing.**

SNUETL helps users access course content that their own Seoul National University eTL account is authorized to access. This is an independent personal project. **It is not an official SNU tool and is not affiliated with, endorsed by, or supported by Seoul National University.**

## Choose a client

| Directory | Purpose | Usage |
| --- | --- | --- |
| [`windows-client/`](windows-client/) | Windows 10/11 x64 tray/dashboard client, on-demand Explorer files, Canvas updates | [Download the latest release](https://github.com/euro-kim/snuetl/releases/latest) |
| [`linux-client/`](linux-client/) | Linux CLI, synchronization, optional bots and systemd scheduling | [Linux instructions](linux-client/README.md) |
| [`docker/`](docker/) | Container deployment on Linux x86-64 / ARM64 | [Docker instructions](docker/README.md) |
| [`macos-client/`](macos-client/) | Read-only Codex desktop helper for Apple Silicon / Intel; no Finder sync client | [macOS instructions](macos-client/README.md) |

The optional [Windows Codex helper](windows-client/codex-helper/README.md) is separate from the Windows desktop sync client. Feature sets differ across platforms. All clients, installers, integrations, and deployment methods are test-stage software, not production-ready services.

## Download only the client you need

For Linux, Docker, or macOS source installs, use **Git 2.39 or newer** and copy the
matching command block below. Windows users should download the release installer instead.
Partial cloning delays downloading file contents until Git needs them; sparse checkout
limits the working files to the selected directories. Root files such as `pyproject.toml`,
`README.md`, and `LICENSE` remain available automatically in cone mode. Shared `src/` is
required by every source client. Git still fetches repository metadata; this is not a
separate per-platform repository. See the official [partial clone](https://git-scm.com/docs/git-clone#Documentation/git-clone.txt---filterltfilter-specgt) and [sparse checkout](https://git-scm.com/docs/git-sparse-checkout) documentation.

**Private repository:** you must have access and authenticate Git using your credential manager. If you use SSH, replace `https://github.com/euro-kim/snuetl.git` below with `git@github.com:euro-kim/snuetl.git`. Do not put access tokens in clone URLs.

### Linux CLI

This checks out the Linux setup and shared Python package, without the Windows, macOS, or Docker directories. Review the [Linux requirements](linux-client/README.md#requirements) first.

```bash
git clone --filter=blob:none --sparse --single-branch https://github.com/euro-kim/snuetl.git snuetl-linux
cd snuetl-linux
git sparse-checkout set --cone src linux-client
bash linux-client/setup.sh
```

### Docker on Linux

This checks out the local image build and deployment files plus shared Python code. It builds the image on your machine; no published SNUETL container image is required. See the [Docker prerequisites and storage guide](docker/README.md).

```bash
git clone --filter=blob:none --sparse --single-branch https://github.com/euro-kim/snuetl.git snuetl-docker
cd snuetl-docker
git sparse-checkout set --cone src docker
bash docker/docker-setup.sh
```

### Windows client

Download the Windows installer directly from the
[latest GitHub release](https://github.com/euro-kim/snuetl/releases/latest). Do not clone the
repository just to install the Windows app. Because this repository is private, GitHub may
ask you to sign in before it shows the release assets. The
[Windows README](windows-client/README.md) remains the reference for usage and source
development.

### macOS Codex helper (build from source)

Run on a Mac with the [macOS build prerequisites](macos-client/README.md#build-outputs-and-private-testing). This selects the Mac installer, shared code, and required skill; it does not include the Windows client.

```bash
git clone --filter=blob:none --sparse --single-branch https://github.com/euro-kim/snuetl.git snuetl-macos
cd snuetl-macos
git sparse-checkout set --cone src macos-client .agents/skills/snuetl
bash macos-client/build.sh
```

### Update or expand your checkout

From inside the checkout, pull updates normally; Git retains your selected directories:

```bash
git pull --ff-only
```

Pulling updates the source; rerun the relevant setup/build command to update an installed client. For Docker, use `bash docker/docker-update.sh` to review, pull and rebuild together as described in its README.

To add shared tests or another platform later, use `git sparse-checkout add tests` or, for example, `git sparse-checkout add docker`. `git sparse-checkout list` shows the current selection. Use `git sparse-checkout disable` to materialize the full repository when needed; this may download more files.

For an existing full clone, first commit or stash tracked changes, then run the matching `git sparse-checkout set --cone ...` line above. This trims the checked-out files but does not reclaim objects already downloaded into `.git`. Other Git operations that inspect omitted files or history may download additional content.

Linux packages and published Docker images may be considered later. The Linux and Docker instructions currently use source checkouts and local builds.

## Repository layout

```text
windows-client/  Windows application, installer, tests and optional Codex helper
linux-client/    Linux setup, example configuration, systemd files and usage guide
docker/          Dockerfile, Compose configuration, setup/update scripts and guide
macos-client/    macOS Codex helper installer, build scripts and guide
src/snuetl/      Shared Python API, authentication, CLI and synchronization code
tests/           Shared Python tests and redacted fixtures
docs/            Maintainer notes and workflow guidance
```

`pyproject.toml` stays at the root because the clients share one Python package. Local `.venv/`, `.tools/`, build outputs and caches are ignored development files, not additional clients.

Existing Linux Docker deployments keep their root `.env`, configured bind mounts and `docker-data/`. Scripts now live in `docker/`; follow its README before updating. Downloaded course files are not moved by this source reorganization.

## Development and GitHub Actions

Start with the README for your platform. Both packaging workflows are **manual only** during testing: use GitHub → Actions → select the build → Run workflow. They upload build artifacts; they do not publish releases. The automatic Codex release job and automatic tag/PR build triggers have been removed. See [workflow notes](docs/github-actions.md).

Private releases require authenticated GitHub access. The Windows client's private-release token setting is described in its README. Git SSH access alone does not authenticate installer downloads.

## License and disclaimer

Copyright © 2026 Euro Kim. Original SNUETL source code and documentation across all client directories are provided under the **[MIT License](LICENSE)**. Keep the copyright and license notices when redistributing. Third-party dependencies, bundled components, names, logos and course materials remain subject to their own licenses or owners' rights; this project does not relicense them or claim SNU endorsement.

**Provided “as is,” without warranty.** There is no promise of accuracy, availability, security, fitness for a particular purpose, or continued compatibility with eTL. Testing software may contain bugs, fail to sync, show incomplete academic information, or lose data. Verify deadlines, grades and submissions directly on the official eTL site and keep your own backups.

**To the maximum extent permitted by applicable law, the authors, maintainers, contributors and copyright holders disclaim liability for claims, damages or other liability arising from using, modifying, distributing or relying on this software**, including data loss, missed deadlines, account restrictions, service interruptions and third-party claims. The full warranty and liability terms are in [LICENSE](LICENSE), following the [MIT license text](https://opensource.org/license/mit). Nothing here excludes liability that applicable law does not permit to be excluded.

You are responsible for using only authorized accounts and materials, protecting your credentials, and complying with applicable law, SNU/eTL policies and third-party terms. No warranty, support commitment or legal indemnity is offered. A README or software license cannot guarantee that an author will never face legal liability.
