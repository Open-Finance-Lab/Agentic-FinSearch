# Deployment Automation

The `Backend CI and Deploy` workflow (`.github/workflows/backend-deploy.yml`) runs on pull requests and on pushes to `main`, both filtered to the same paths (`Main/backend/**` and the workflow file itself). A pull request run **validates only**; a push to `main` validates, publishes the image, and optionally restarts the Fedora droplet (`fedora-agentic-finsearch-beta-1`). The flow:

1. Checks out this repo and installs Python `3.12` with `uv`.
2. Runs `uv sync --locked`, `uv run python manage.py check`, and `uv run python manage.py test` inside `Main/backend`. `--locked` rather than `--frozen`: it fails if `uv.lock` has drifted from `pyproject.toml`, instead of quietly building from stale resolutions. `Main/backend/Dockerfile` still uses `--frozen`, correctly — by the time it runs, this step has already validated the very lock it copies in.
3. Builds the backend container with the Dockerfile in `Main/backend/`. On pushes to `main` and on `workflow_dispatch` it then pushes three tags to GHCR — **pull request runs stop after the build and publish nothing**:
   - `ghcr.io/<owner>/<repo>-backend:${GITHUB_SHA}`
   - `ghcr.io/<owner>/<repo>-backend:main`
   - `ghcr.io/<owner>/<repo>-backend:latest`
4. Uses SSH to reach `deploy@agenticfinsearch.org`, pulls the `:main` tag with `podman`, and restarts the user-level systemd unit `fingpt-api`. This job is ref-gated to `main`, so it never runs for a pull request.

## Required GitHub secrets

| Secret | Description |
| --- | --- |
| `DEPLOY_SSH_KEY` | Private key that matches `/home/deploy/.ssh/authorized_keys`. The workflow uses it to SSH into the droplet. |
| `GHCR_READ_TOKEN` | GitHub Personal Access Token with the `read:packages` scope. Needed so the droplet can `podman login ghcr.io …` during deploy. |

`GITHUB_TOKEN` is injected automatically by Actions for the GHCR push and does **not** need to be created manually.

## Droplet prerequisites

- Systemd user services must be enabled (`loginctl enable-linger deploy`) so `systemctl --user restart fingpt-api` works from non-interactive SSH sessions.
- The `fingpt-api` unit should reference the GHCR image (`ghcr.io/<owner>/<repo>-backend:main`) instead of a locally loaded tarball. A simple `podman run --rm ghcr.io/...` wrapper script is sufficient.
- Ensure `/home/deploy/.config/containers` allows logins to GHCR (the workflow already executes `podman login`, so no persistent config is strictly required).
- Confirm that `.env` / `.env.production` and the Caddy config are already on the droplet; the workflow only handles code + container restarts.
- Gunicorn now defaults to two workers for memory safety. Override with `GUNICORN_WORKERS=<n>` in your environment if the droplet has headroom.

After creating the secrets above, push to `main` (or use the manual `workflow_dispatch`) to trigger the pipeline. Watch the workflow logs for the deploy step; it will be skipped automatically if either secret is missing.
