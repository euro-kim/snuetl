# Next Docker image plan

Keep the existing SNUETL features and runtime dependencies while making the image smaller and routine builds faster.

## 1. Use the slim Python base

- Change the runtime base from `python:3.12-bookworm` to `python:3.12-slim-bookworm`. Use the same Python and Debian release in any build stage.
- Keep Chromium, its required system libraries, FFmpeg, and the Python packages needed by the current features.
- Build the image and verify the CLI, headless browser launch, and FFmpeg in the resulting container. Check both supported CPU architectures if publishing a multi-architecture image.
- Record compressed pull size and local image size before and after the change; do not assume the base-image size difference equals the final saving.

## 3. Separate build and runtime stages, and cache dependencies

- Add a build stage for Python package wheels. Install only those wheels and required runtime packages in the final slim image; leave build tools and caches out of it.
- Put a production dependency manifest or lockfile in a layer before copying `src/`. Build the SNUETL package wheel after copying source, so an app code change does not reinstall unchanged third-party dependencies.
- Keep the Playwright package and installed Chromium version aligned. Preserve the current browser and FFmpeg functionality in the runtime stage.
- Move source revision and package version labels near the end of the Dockerfile, after costly dependency layers, so each new commit does not invalidate them.
- Change normal builds in `docker-setup.sh` to use Docker's layer cache while retaining `--pull` for base-image updates. Provide an explicit cache-free rebuild option for troubleshooting.
- Verify that `docker-update.sh` still compares the deployed image revision, rebuilds and redeploys when required, and reports a healthy container. Compare a repeated build and a source-only rebuild to confirm dependency layers are reused.

Do not change ownership or permissions of mounted host directories as part of this work.
