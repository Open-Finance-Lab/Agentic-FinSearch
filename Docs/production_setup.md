# FinSearch Backend Production Setup (Podman)

This guide walks through preparing the backend container for production while keeping day‑to‑day development workflows unchanged (`docker compose up --build` still uses the development settings).

## 1. Prepare Production Environment Variables

1. Copy the sample file:  
   `cp Main/backend/.env.production.example /opt/fingpt/.env.production`
2. Edit the copy and provide secure values:
   - `DJANGO_SECRET_KEY`: generate with `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`.
   - `DJANGO_ALLOWED_HOSTS`: comma-separated list of hostnames served by this instance (e.g. `api.example.com`).
   - `CORS_ALLOWED_ORIGINS`: every frontend origin or extension ID that should reach the API.
   - `FINGPT_API_KEY`: a strong random value (`python -c "import secrets; print(secrets.token_urlsafe(48))"`). `settings_prod` REFUSES to start if this is empty.
   - `REDIS_URL`: where the cache/counter store lives (see §3). For the podman deploy below this is `redis://fingpt-redis:6379/0`.
   - `TRUSTED_PROXIES`: the network the front proxy reaches the backend from (see §6); leave the default `127.0.0.1,::1` only if the proxy is genuinely loopback-local with no SNAT.
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

## 3. Provision Redis and the Podman Network (REQUIRED)

`settings_prod` uses Redis as Django's **default cache**, so the agent-budget
concurrency / daily-run counters and django-ratelimit get atomic, cross-worker
`incr`/`decr` — these are HARD limits, not best-effort. The backend touches the
cache on startup and on every request, so Redis must be running and reachable
**before** you start the API container. (This step is not optional; the older
revisions of this runbook omitted it.)

1. Create a user-defined Podman network so the API and Redis containers get
   stable DNS names and a known subnet (rootless Podman):
   ```
   podman network create fingpt-net
   podman network inspect fingpt-net \
     --format '{{range .Subnets}}{{.Subnet}}{{end}}'
   # Note the subnet (e.g. 10.89.0.0/24) — you'll set TRUSTED_PROXIES to it in §6.
   ```
2. Start Redis on that network, named `fingpt-redis` so the API reaches it by
   name. Mount a named volume and use a **non-evicting** policy — the agent
   budget and django-ratelimit counters share this store, and `settings_prod`
   uses Redis precisely so those hard-limit counters are never culled. An
   eviction policy like `allkeys-lru` would silently drop them under memory
   pressure and turn the hard limits soft; `noeviction` makes writes fail
   loudly instead:
   ```
   podman run -d --name fingpt-redis --network fingpt-net --restart=always \
     -v fingpt-redis-data:/data \
     docker.io/library/redis:7-alpine \
     redis-server --save 60 1 --maxmemory 256mb --maxmemory-policy noeviction
   ```
   (This is a cache/counter store, not a system of record — losing the volume
   only resets rate-limit windows and budget counters.)
3. Point the backend at it in `/opt/fingpt/.env.production`:
   ```
   REDIS_URL=redis://fingpt-redis:6379/0
   ```
   (A `docker compose` dev stack uses the service name `redis`, so the default
   `redis://redis:6379/0` applies there instead.)
4. Verify Redis answers before going further:
   ```
   podman exec fingpt-redis redis-cli ping        # -> PONG
   ```

## 4. Run with Production Settings (Podman Desktop or CLI)

### Podman CLI

Join the API container to `fingpt-net` (so it can resolve `fingpt-redis`) and
publish its port to loopback only:

```
podman run -d \
  --name fingpt-api \
  --network fingpt-net \
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
4. Attach it to the `fingpt-net` network and publish port `8000` to `127.0.0.1`.
5. Create & start. The UI mirrors the CLI behaviour.

## 5. Preparing a Cloud Host

1. Provision a Linux VM with Podman (Fedora, CentOS Stream, Ubuntu, or RHEL).
2. Create a deploy user and enable rootless Podman (`sudo loginctl enable-linger username`).
3. Copy `/opt/fingpt/.env.production` to the host (same path recommended) and set owner/perms.
4. Provision the network + Redis as in §3.
5. Pull the image from your registry:  
   `podman pull ghcr.io/your-org/fingpt-api:latest`
6. Launch the container with the same command as in §4 (including `--network fingpt-net`).
7. Open the firewall for the **public proxy** and confirm DNS. Caddy auto-issues
   Let's Encrypt certs, which needs inbound TCP 80 + 443 reachable from the
   internet — firewalld (default on Fedora/CentOS Stream/RHEL) blocks these.
   Keep the API itself published to `127.0.0.1:8000` only.
   ```
   sudo firewall-cmd --add-service=http --add-service=https --permanent
   sudo firewall-cmd --reload
   # (ufw on Debian/Ubuntu: sudo ufw allow 80,443/tcp)
   ```
   Ensure the domain's A/AAAA records resolve to this host *before* starting
   Caddy, or the ACME challenge fails.
8. Optional: convert it into a managed service.
   ```
   podman generate systemd --name fingpt-api --files --new
   sudo mv fingpt-api.service /etc/systemd/system/
   sudo systemctl enable --now fingpt-api.service
   ```

## 6. Networking & TLS

- Keep Gunicorn bound to `0.0.0.0:8000` inside the container.
- Publish the host port to loopback only (`-p 127.0.0.1:8000:8000`) so the
  container is reachable exclusively through the front reverse proxy, never
  directly from the network.
- Terminate TLS using either:
  - A reverse proxy (Caddy, Nginx, Traefik) — either a **host system service**
    or a **container in the same Podman network**, or
  - Your cloud provider’s load balancer pointing at the host’s loopback port.
- When TLS is in place, redirect HTTP → HTTPS at the proxy layer. Caddy does
  this automatically once a site has a cert — but note the bundled
  `Deploy/podman/Caddyfile.example` sets `auto_https disable_redirects`, which
  turns that off; remove that line to let the proxy issue the 80→443 redirect
  (otherwise only Django's `SECURE_SSL_REDIRECT` bounces it, an extra app hop).

### Client IP / rate-limiting (P0 Root C.1)

The API derives the client IP for rate limiting from `X-Real-IP` /
`X-Forwarded-For`, but ONLY when the TCP peer (`REMOTE_ADDR`) falls inside
`TRUSTED_PROXIES` (env, default `127.0.0.1,::1`). **Entries are matched as IP
networks**, so a bare IP is a `/32` and a CIDR trusts a whole range
(`api/identity.py`). The front proxy MUST set those headers itself and override
any client-supplied copies.

> **Rootless-Podman SNAT — read this before setting `TRUSTED_PROXIES`.** When
> the backend container is reached through a published port (or from another
> container), rootless Podman rewrites the source address to a *podman-network*
> address — **not** `127.0.0.1`, and **not** the proxy's LAN IP. That address is
> dynamic, so an exact-match `TRUSTED_PROXIES` value silently fails: headers get
> ignored and every caller collapses into one shared rate-limit bucket. Pin
> `TRUSTED_PROXIES` to the network **subnet**, not a single IP:
>
> ```
> TRUSTED_PROXIES=10.89.0.0/24      # the fingpt-net subnet from §3
> ```
>
> Confirm the real value empirically: make one request, then check `podman logs
> fingpt-api` for the logged client/remote address, and set `TRUSTED_PROXIES` to
> the covering CIDR (`podman network inspect fingpt-net`). CIDR matching is what
> makes this robust to the dynamic SNAT address.

Pick the variant that matches how Caddy runs:

**A) Caddy as a host system service** (root-managed — this droplet's topology)

Caddy is *not* a container here; its config lives at `/etc/caddy/Caddyfile` and
it reloads through systemd, not `podman exec`. Reverse-proxy to the
loopback-published backend port and set the forwarding headers:

```
# /etc/caddy/Caddyfile  (excerpt)
api.your-domain.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000 {
        # Derive client IP from the real TCP peer and OVERRIDE any
        # client-supplied forwarding headers so a caller cannot spoof them.
        header_up X-Real-IP {remote_host}
        header_up X-Forwarded-For {remote_host}
        header_up X-Forwarded-Proto {scheme}
    }
    header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
}
```

Apply and reload as root (NOT `podman exec`):

```
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Because the host→container hop is SNAT'd by rootless Podman, set
`TRUSTED_PROXIES` to the **podman subnet** (see the SNAT note above), not
`127.0.0.1`.

**B) Caddy as a container in the same Podman network**

> **Caveat — the front Caddy's OWN published port is also SNAT'd.** When the
> *front* proxy is itself a rootless container publishing 80/443, the external
> client's source IP is rewritten by the rootless port-forwarder. Only `pasta`
> (the Podman 5+ default) preserves the real source address; under `slirp4netns`
> Caddy's `{remote_host}` becomes an internal forwarder address, so it forwards
> *that* as `X-Real-IP` and per-client rate limiting collapses again. For
> reliable per-client limiting prefer **variant A** (host-service Caddy), or
> verify the port handler (`podman info --format '{{.Host.NetworkBackend}}'`)
> and test with a genuine EXTERNAL client, not a localhost curl.

1. Launch Caddy on `fingpt-net`, publishing 80/443 and mounting the config
   (this step was missing before — `fingpt-caddy` must exist before you can
   `cp`/`exec` into it):
   ```
   cp Deploy/podman/Caddyfile.example /opt/fingpt/Caddyfile   # then edit domain + email
   podman run -d --name fingpt-caddy --network fingpt-net --restart=always \
     -p 80:80 -p 443:443 \
     -v /opt/fingpt/Caddyfile:/etc/caddy/Caddyfile:Z \
     -v fingpt-caddy-data:/data \
     docker.io/library/caddy:2
   ```
   The backend is reached by name as `fingpt-api:8000` over `fingpt-net`, so its
   in-container `REMOTE_ADDR` is Caddy's `fingpt-net` address.
2. After editing the mounted Caddyfile, reload IN PLACE — editing the repo's
   example file is not enough:
   ```
   podman cp /opt/fingpt/Caddyfile fingpt-caddy:/etc/caddy/Caddyfile
   podman exec fingpt-caddy caddy reload --config /etc/caddy/Caddyfile
   ```

Set `TRUSTED_PROXIES` to the subnet of the network Caddy connects from
(`fingpt-net`).

After either variant, `curl -s https://api.your-domain.com/health/` and confirm
the backend logs show the **real client IP**, not the proxy/SNAT address.

## 7. Verifying the Deployment

1. `podman ps` → ensure `fingpt-api` and `fingpt-redis` are `running` and the
   API health check passes.
2. `podman exec fingpt-redis redis-cli ping` → expect `PONG` (the cache/counter
   store the rate limiter and agent budget depend on).
3. `curl -f https://api.your-domain.com/health/` → expect `{"status":"ok", ...}`.
4. Make a request from a real external client, then confirm the resolved client
   IP is the caller's real address, not the SNAT/proxy address — proof that
   `TRUSTED_PROXIES` covers the proxy and `X-Real-IP` is being honored. If your
   request logging doesn't surface the client IP, enable gunicorn access logging
   (include `%(h)s`) or temporarily inspect `get_request_identity` for one
   request; a value of `ip:<the-SNAT-address>` for every caller means
   `TRUSTED_PROXIES` is still wrong.
5. Inspect static assets: static files should now live under `/app/staticfiles` inside the container (entrypoint handles this).
6. Confirm logs and provider API calls behave as expected.

## 8. Next Steps

- Automate builds with CI (GitHub Actions runner using Podman) and push tagged releases to your registry.
- Add monitoring (Prometheus + exporters, log shipping, or cloud-native tooling).
- Package MCP servers as separate containers and join them to `fingpt-net` so the backend talks to them over the network.
- Document rollback: keep previous tags in the registry and note the `podman run` command for quick revert.
