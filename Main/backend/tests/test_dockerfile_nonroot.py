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
DEPLOY_WORKFLOW = os.path.join(
    _HERE, "..", "..", "..", ".github", "workflows", "backend-deploy.yml"
)

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

    def test_switches_to_nonroot_user(self):
        self.assertIn("\nUSER fingpt\n", self.text)

    def test_chown_only_runtime_dirs_not_whole_tree(self):
        chown_lines = [l for l in self.lines if "chown" in l]
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

    def test_user_switch_after_last_root_run_before_entrypoint(self):
        ln_idx = self._index_of("ln -sf /ms-playwright")
        verify_idx = self._index_of("Chromium browser found")
        chown_idx = self._index_of("chown -R fingpt:fingpt")
        user_idx = self._index_of("USER fingpt")
        entry_idx = self._index_of('ENTRYPOINT ["/app/entrypoint.sh"]')
        # USER comes after the last root RUN (the /usr/bin symlink + browser verify)
        # and after the chown, then immediately before ENTRYPOINT.
        self.assertGreater(user_idx, ln_idx)
        self.assertGreater(user_idx, verify_idx)
        self.assertGreater(user_idx, chown_idx)
        self.assertLess(user_idx, entry_idx)

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
