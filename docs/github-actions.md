# GitHub Actions during testing

Both retained workflows run only through `workflow_dispatch`:

- `windows-client.yml`: builds/tests the Windows sync client and uploads the installer and checksums.
- `codex-desktop.yml`: builds the optional macOS/Windows Codex helpers and uploads platform artifacts.

The Codex workflow's automatic release job was removed. It previously attempted `gh release create` on each version tag, which could fail when a maintainer had already created that release. It also required repository write access unnecessarily for artifact builds. Both workflows now use `contents: read`; automatic tag and PR triggers are disabled during testing to avoid redundant cross-platform packaging runs. Builds and their source are retained because these are still supported experimental deliverables.

Historical private Actions run logs were not accessible in this environment. These changes are based on the workflow definitions, not a verified diagnosis of each past failed run. Existing run history is untouched. Commit and push these workflow changes to apply them on GitHub.

To build: open Actions, select the workflow, choose Run workflow, and download its artifacts. Publishing an installer remains a separate maintainer action. Optional browser ZIPs need not be rebuilt for every Windows core release; see the Windows README.

## Local validation of the layout change

Seven deployment configuration checks and 30 shared/backend Python tests passed. Bash syntax was checked for all five moved shell scripts; PowerShell parsing passed for the Windows build/install scripts. CLI help, workflow triggers/permissions, local README links, and Python wheel packaging were checked. Linux Docker deployment and macOS installer execution were not run on this Windows host.
