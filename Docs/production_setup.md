# FinSearch Backend Production Setup (Podman)

This guide walks through preparing the backend container for production while keeping day‑to‑day development workflows unchanged (`docker compose up --build` still uses the development settings).

## 1. Prepare Production Environment Variables

1. Copy the sample file:  
   `cp Main/backend/.env.production.example /opt/fingpt/.env.production`
2. Edit the copy and provide secure values:
   - `DJANGO_SECRET_KEY`: generate with `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`.
   - `DJANGO_ALLOWED_HOSTS`: comma-separated list of hostnames served by this instance (e.g. `api.example.com`).
   - `CORS_ALLOWED_ORIGINS`: every frontend origin or extension ID that should reach the API.
   - Provider credentials (`OPENAI_API_KEY`, etc.) for whichever LLM backends you will call.
3. Leave `DJANGO_SETTINGS_MODULE=django_config.settings_prod` so the container boots in hardened mode.
4. Keep `RUN_COLLECTSTATIC=1` to let the entrypoint run `collectstatic` automatically when the production settings are active.

Store the final `.env.production` somewhere outside the repository (e.g. `/opt/fingpt` on the server) and restrict file permissions (`chmod 600`).

## 2. Build and Test the Image Locally

1. Build the standard development image (default behaviour remains unchanged):  
   `podman build -t fingpt-api:dev Main/backend`
2. Smoke test with dev settings if desired:  
   `podman run --rm --env-file Main/backend/.env -p 8000:8000 fingpt-api:dev`
3. For a production-ready image that still defaults to dev until runtime, re-tag the same image:  
   `podman tag fingpt-api:dev ghcr.io/your-org/fingpt-api:latest`
4. Push to your registry once satisfied (example using GHCR):  
   ```
   podman login ghcr.io
   podman push ghcr.io/your-org/fingpt-api:latest
   ```

> **Note:** You do not need a separate build for production. Switching to `settings_prod` happens entirely through environment variables at runtime.

## 3. Run with Production Settings (Podman Desktop or CLI)

### Podman CLI

```
podman run -d \
  --name fingpt-api \
  --env-file /opt/fingpt/.env.production \
  -p 127.0.0.1:8000:8000 \
  ghcr.io/your-org/fingpt-api:latest
```

- The new entrypoint automatically executes `python manage.py collectstatic --noinput` whenever `DJANGO_SETTINGS_MODULE=django_config.settings_prod`.  
- Logs stream to stdout/stderr. View them with `podman logs -f fingpt-api`.
- The container health check still probes `/health/` on port 8000.

### Podman Desktop

1. Containers tab → **Create Container**.
2. Choose the uploaded image (`fingpt-api:latest`).
3. Under **Environment**, click *Load from file* and select `/opt/fingpt/.env.production`.
4. Publish port `8000`.
5. Create & start. The UI mirrors the CLI behaviour.

## 4. Preparing a Cloud Host

1. Provision a Linux VM with Podman (Fedora, CentOS Stream, Ubuntu, or RHEL).
2. Create a deploy user and enable rootless Podman (`sudo loginctl enable-linger username`).
3. Copy `/opt/fingpt/.env.production` to the host (same path recommended) and set owner/perms.
4. Pull the image from your registry:  
   `podman pull ghcr.io/your-org/fingpt-api:latest`
5. Launch the container with the same command as in section 3.
6. Optional: convert it into a managed service.
   ```
   podman generate systemd --name fingpt-api --files --new
   sudo mv fingpt-api.service /etc/systemd/system/
   sudo systemctl enable --now fingpt-api.service
   ```

## 5. Networking & TLS

- Keep Gunicorn bound to `0.0.0.0:8000` inside the container.
- Publish the host port to loopback only (`-p 127.0.0.1:8000:8000`) so the
  container is reachable exclusively through the front reverse proxy, never
  directly from the network.
- Terminate TLS using either:
  - A reverse-proxy container in the same pod (Caddy, Nginx, Traefik), or
  - Your cloud provider’s load balancer pointing at the host’s port 8000.
- When TLS is in place, redirect HTTP → HTTPS at the proxy layer.

### Client IP / rate-limiting (P0 Root C.1)

The API derives the client IP for rate limiting from `X-Real-IP` /
`X-Forwarded-For`, but ONLY when the TCP peer is listed in `TRUSTED_PROXIES`
(env, default `127.0.0.1,::1`). The front proxy MUST set those headers itself
and override client-supplied copies:

1. Use the provided `Deploy/podman/Caddyfile.example`, which sets
   `header_up X-Real-IP {remote_host}` and overrides `X-Forwarded-For`.
2. Set `TRUSTED_PROXIES` in `.env.production` to the address the proxy
   connects from (the pod/network peer IP; `127.0.0.1,::1` for a same-pod
   Caddy).
3. APPLY THE CONFIG TO THE LIVE PROXY — editing the example file is not
   enough. Copy it onto the running Caddy and reload:
   ```
   podman cp Deploy/podman/Caddyfile.example fingpt-caddy:/etc/caddy/Caddyfile
   podman exec fingpt-caddy caddy reload --config /etc/caddy/Caddyfile
   ```
   Then `curl -s https://api.your-domain.com/health/` and confirm the backend
   logs show the real client IP, not the proxy's.

## 6. Verifying the Deployment

1. `podman ps` → ensure the container is `running` and health check passes.
2. `curl -f https://api.your-domain.com/health/` → expect `{"status":"ok", ...}`.
3. Inspect static assets: static files should now live under `/app/staticfiles` inside the container (entrypoint handles this).
4. Confirm logs and provider API calls behave as expected.

## 7. Next Steps

- Automate builds with CI (GitHub Actions runner using Podman) and push tagged releases to your registry.
- Add monitoring (Prometheus + exporters, log shipping, or cloud-native tooling).
- Package MCP servers as separate containers and join them to the same Podman pod so the backend talks to them over localhost.
- Document rollback: keep previous tags in the registry and note the `podman run` command for quick revert.
