"""Static guards for the non-root container hardening (P0 Root A.3).

Runs in the standard backend harness (SimpleTestCase, no DB):
    uv run python manage.py test tests.test_dockerfile_nonroot -v 2

These lock the Dockerfile / deploy invariants so a later edit cannot
silently re-root the container or widen the chown back to the whole tree.
"""
import os

from django.test import SimpleTestCase

_HERE = os.path.dirname(os.path.abspath(__file__))
DOCKERFILE = os.path.join(_HERE, "..", "Dockerfile")
ENTRYPOINT_SH = os.path.join(_HERE, "..", "entrypoint.sh")
DEPLOY_WORKFLOW = os.path.join(
    _HERE, "..", "..", "..", ".github", "workflows", "backend-deploy.yml"
)
COMPOSE = os.path.join(_HERE, "..", "..", "..", "docker-compose.yml")

RUNTIME_DIRS = ["/app/staticfiles", "/app/media", "/app/logs", "/tmp/fingpt_cache", "/app/runtime"]


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


class DockerfileNonRootTests(SimpleTestCase):
    def setUp(self):
        self.text = _read(DOCKERFILE)
        self.lines = self.text.splitlines()

    def _index_of(self, needle):
        for i, line in enumerate(self.lines):
            if needle in line:
                return i
        self.fail(f"expected a line containing {needle!r} in the Dockerfile")

    def test_creates_nonroot_user_uid_1001(self):
        self.assertIn("groupadd --system --gid 1001 fingpt", self.text)
        self.assertIn(
            "useradd --system --uid 1001 --gid fingpt --no-create-home fingpt",
            self.text,
        )

    def test_app_drops_to_nonroot_via_entrypoint_root_init_drop(self):
        # Root-init-drop: PID1 must be root-in-userns to load the SSRF egress firewall
        # (nft needs CAP_NET_ADMIN), so the Dockerfile deliberately has NO `USER` line;
        # entrypoint.sh drops to the non-root uid1001 (fingpt) via setpriv, removing
        # NET_ADMIN from the bounding set + setting no_new_privs, BEFORE running the app.
        # The no-write-under-/app posture holds: the long-running app is uid1001.
        self.assertNotIn("\nUSER ", self.text, "Dockerfile must NOT set a USER (root-init-drop)")
        entry = _read(ENTRYPOINT_SH)
        self.assertIn("setpriv", entry)
        self.assertIn("--reuid=1001", entry)
        self.assertIn("--regid=1001", entry)
        self.assertIn("--bounding-set=-net_admin", entry)
        self.assertIn("--no-new-privs", entry)

    def test_privilege_drop_is_last_statement_of_root_init_block(self):
        # Structural: the root-init `if [ id -u == 0 ]` block must END with the setpriv
        # exec, and NO app-phase work (gunicorn/collectstatic/truthlayer/OPENAI gate) may
        # run inside it -- else the app would run as root. Substring presence is not enough
        # (dropping `exec` or hoisting an app line above setpriv keeps all substrings).
        lines = _read(ENTRYPOINT_SH).splitlines()
        start = next(i for i, l in enumerate(lines) if "id -u" in l and "then" in l)
        end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "fi")
        logical, buf = [], ""
        for l in lines[start + 1:end]:
            s = l.strip()
            if not s or s.startswith("#"):
                continue
            buf += ((" " if buf else "") + s.rstrip("\\").strip())
            if not l.rstrip().endswith("\\"):
                logical.append(buf)
                buf = ""
        self.assertTrue(logical[-1].startswith("exec setpriv"), logical[-1])
        self.assertIn('-- "$0" "$@"', logical[-1])
        self.assertIn("--bounding-set=-net_admin", logical[-1])
        self.assertIn("--no-new-privs", logical[-1])
        joined = "\n".join(logical)
        for marker in ("gunicorn", "collectstatic", "truthlayer", "REQUIRE_OPENAI_API_KEY"):
            self.assertNotIn(marker, joined, f"app-phase marker {marker!r} inside root block")

    def test_chown_only_runtime_dirs_not_whole_tree(self):
        # Count only actual chown COMMANDS, not prose in comments (the root-init-drop
        # comment block legitimately mentions chowning the runtime mount).
        chown_lines = [
            l for l in self.lines if "chown" in l and not l.strip().startswith("#")
        ]
        self.assertEqual(
            len(chown_lines), 1, f"expected exactly one chown line, got {chown_lines}"
        )
        chown = chown_lines[0]
        self.assertIn("chown -R fingpt:fingpt", chown)
        for d in RUNTIME_DIRS:
            self.assertIn(d, chown)
        # Source tree must stay root-owned: never chown the whole /app or .venv.
        self.assertNotIn("chown -R fingpt:fingpt /app ", chown + " ")
        self.assertNotIn("/app/api", chown)
        self.assertNotIn("/app/.venv", chown)
        # The store no longer builds under /app: /app/truthlayer/data (vendored
        # snapshots) stays root-owned and read-only, restoring no-write-under-/app.
        self.assertNotIn("/app/truthlayer/data", chown)

    def test_chown_after_last_root_run_before_entrypoint(self):
        ln_idx = self._index_of("ln -sf /ms-playwright")
        verify_idx = self._index_of("Chromium browser found")
        chown_idx = self._index_of("chown -R fingpt:fingpt")
        entry_idx = self._index_of('ENTRYPOINT ["/app/entrypoint.sh"]')
        # The chown comes after the last root RUN (the /usr/bin symlink + browser verify)
        # and before ENTRYPOINT. There is no Dockerfile USER line (root-init-drop): the
        # privilege drop to uid1001 happens at runtime in entrypoint.sh via setpriv.
        self.assertGreater(chown_idx, ln_idx)
        self.assertGreater(chown_idx, verify_idx)
        self.assertLess(chown_idx, entry_idx)

    def test_store_persisted_on_runtime_volume(self):
        # The regenerable DuckDB store must build onto the /app/runtime volume
        # (persisted across restarts) — not the ephemeral image layer — and nothing
        # under /app/truthlayer stays writable.
        self.assertIn("TRUTHLAYER_DB_PATH=/app/runtime/truthlayer.duckdb", self.text)
        mkdir_lines = [l for l in self.lines if "mkdir -p" in l and "/app/runtime" in l]
        self.assertEqual(
            len(mkdir_lines), 1, f"expected /app/runtime in one mkdir line, got {mkdir_lines}"
        )
        # /app/truthlayer/data is no longer referenced in the Dockerfile at all
        # (the snapshots arrive via COPY and stay root-owned / read-only).
        self.assertNotIn("/app/truthlayer/data", self.text)


class DeployUserNamespaceTests(SimpleTestCase):
    def test_runtime_bind_mount_uses_U_relabel_for_nonroot(self):
        text = _read(DEPLOY_WORKFLOW)
        # Rootless podman must chown the runtime bind mount to the in-container
        # uid (1001) so the non-root process can write it.
        self.assertIn("/home/deploy/fingpt/runtime:/app/runtime:U", text)
        self.assertNotIn("/home/deploy/fingpt/runtime:/app/runtime ", text)

    def test_execstart_podman_run_has_net_admin_cap(self):
        # --cap-add=NET_ADMIN is load-bearing (root-in-userns needs it to load nft) and
        # must be on the ExecStart podman run line SPECIFICALLY -- a whole-file assertIn
        # would pass if only the pre-cutover validation kept it while ExecStart dropped it.
        text = _read(DEPLOY_WORKFLOW)
        execstart = [l for l in text.splitlines() if "--name ${SYSTEMD_UNIT}" in l and "podman run" in l]
        self.assertTrue(execstart, "ExecStart podman run line not found")
        for l in execstart:
            self.assertIn("--cap-add=NET_ADMIN", l)

    def test_compose_api_service_has_net_admin_cap(self):
        import yaml
        with open(COMPOSE, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        caps = data["services"]["api"].get("cap_add", [])
        self.assertIn("NET_ADMIN", caps)

    def test_runtime_bind_mount_selinux_relabel_for_nonroot(self):
        text = _read(DEPLOY_WORKFLOW)
        # The droplet is SELinux-enforcing Fedora. :U relabels OWNERSHIP (DAC) but leaves
        # the host dir's SELinux label untouched, so the non-root container's container_t
        # domain is denied write access (MAC) — surfacing as the same generic EACCES. The
        # first deploy that actually WROTE the /app/runtime mount (the truth-layer store
        # build, #322) crash-looped on "Permission denied" for exactly this reason. :Z
        # relabels the (private, this-container-only) mount to container_file_t so the
        # container can write it. Ownership AND SELinux must both be handled: ship :U,Z.
        self.assertIn("/home/deploy/fingpt/runtime:/app/runtime:U,Z", text)
