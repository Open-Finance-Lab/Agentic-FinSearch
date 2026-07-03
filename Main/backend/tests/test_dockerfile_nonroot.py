"""Static guards for the non-root container hardening (P0 Root A.3).

Runs in the standard backend harness (SimpleTestCase, no DB):
    uv run python manage.py test tests.test_dockerfile_nonroot -v 2

These lock the Dockerfile / deploy invariants so a later edit cannot
silently re-root the container or widen the chown back to the whole tree.
"""
import os
import re

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
        # entrypoint.sh drops to the non-root uid1001 (fingpt) via setpriv BEFORE running
        # the app. The setpriv statement itself (full flag set + last-statement position)
        # is pinned by test_privilege_drop_is_last_statement_of_root_init_block.
        self.assertNotIn("\nUSER ", self.text, "Dockerfile must NOT set a USER (root-init-drop)")
        self.assertIn("setpriv", _read(ENTRYPOINT_SH))

    def test_privilege_drop_is_last_statement_of_root_init_block(self):
        # Structural: the root-init `if [ id -u == 0 ]` block must END with the setpriv
        # exec, and NO app-phase work (gunicorn/collectstatic/truthlayer/OPENAI gate) may
        # run inside it -- else the app would run as root. Substring presence is not enough
        # (dropping `exec` or hoisting an app line above setpriv keeps all substrings).
        lines = _read(ENTRYPOINT_SH).splitlines()
        start = next(i for i, l in enumerate(lines) if "id -u" in l and "then" in l)
        end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "fi")
        block = re.sub(r"\\\n\s*", " ", "\n".join(lines[start + 1:end]))
        logical = [s.strip() for s in block.splitlines()
                   if s.strip() and not s.strip().startswith("#")]
        drop = logical[-1]
        self.assertTrue(drop.startswith("exec setpriv"), drop)
        self.assertIn('-- "$0" "$@"', drop)
        # The full flag set is asserted HERE, on the actual exec statement -- a whole-file
        # substring check would pass with the flags on any line of the script.
        for flag in ("--reuid=1001", "--regid=1001", "--init-groups",
                     "--bounding-set=-net_admin", "--no-new-privs"):
            self.assertIn(flag, drop)
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

    def test_runtime_user_has_writable_home(self):
        # setpriv drops the uid but NOT the environment: without the ENV pin the
        # app and every MCP stdio child inherit PID1's HOME=/root, which uid1001
        # cannot write. edgartools mkdirs ~/.edgar AT IMPORT, so the sec-edgar
        # MCP child died with EACCES on every boot from the root-init redesign
        # until this pin existed. HOME must point at a dir that is created and
        # fingpt-owned in the image.
        self.assertIn("HOME=/home/fingpt", self.text)
        mkdir_lines = [l for l in self.lines if "mkdir -p /home/fingpt" in l]
        self.assertEqual(
            len(mkdir_lines), 1,
            f"expected /home/fingpt created in exactly one line, got {mkdir_lines}",
        )
        chown = next(l for l in self.lines if "chown -R fingpt:fingpt" in l)
        self.assertIn("/home/fingpt", chown)

    def test_playwright_dependencies_marker_prebaked_for_read_only_rootfs(self):
        # Playwright writes a 0-byte DEPENDENCIES_VALIDATED marker next to the
        # browser executable at FIRST launch (host-requirements validation cache).
        # Under the deploy's --read-only rootfs that write fails -- fail-SOFT in
        # the playwright driver, but then EVERY fresh process re-validates on its
        # first launch. The image must bake the marker organically: a REAL one-time
        # chromium launch (never a bare touch, which would skip the validation the
        # marker attests) as the runtime uid via setpriv (no USER line exists,
        # root-init-drop), with a loud `test -f` so the build fails if Playwright
        # ever moves the marker.
        folded = re.sub(r"\\\n\s*", " ", self.text)
        bake = [l for l in folded.splitlines()
                if l.startswith("RUN ") and "DEPENDENCIES_VALIDATED" in l]
        self.assertEqual(len(bake), 1, f"expected one marker-bake RUN, got {bake}")
        self.assertIn("setpriv --reuid=1001 --regid=1001", bake[0])
        self.assertIn("chromium.launch", bake[0])
        self.assertIn(
            "test -f /ms-playwright/chromium_headless_shell-*/DEPENDENCIES_VALIDATED",
            bake[0],
        )

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

    def test_precutover_gate_runs_real_entrypoint_full_boot(self):
        # The deploy's pre-cutover gate must exercise the image's REAL entrypoint via
        # BOOT_CHECK_ONLY=1, not an inline re-copy of the boot sequence: a hand-copied
        # gate can silently drift from what PID1 actually runs at cutover. No
        # --entrypoint override may bypass it anywhere in the deploy, and the gate
        # passes NO positional args after the image ref -- the Dockerfile CMD flows
        # through byte-identical (zero-drift property; the entrypoint backgrounds
        # "$@" rather than hardcoding gunicorn).
        wf = _read(DEPLOY_WORKFLOW)
        self.assertNotIn("--entrypoint", wf)
        gate = self._gate_line()
        self.assertIn("--env BOOT_CHECK_ONLY=1", gate)
        tail = gate.split('"$REMOTE_IMAGE"', 1)[1].strip()
        self.assertTrue(
            tail == "" or tail.startswith("||"),
            f"gate must end at the image ref (no positional args), got tail {tail!r}",
        )
        # Placement in entrypoint.sh is load-bearing. The retained manual/local
        # --egress-check-only branch: AFTER the setpriv drop (above the root-init
        # block it would exit 0 with NO firewall loaded -- a vacuous gate) and
        # BEFORE the app phase's heavy store build (fast and dependency-free).
        entry_lines = _read(ENTRYPOINT_SH).splitlines()
        flag_idx = next(i for i, l in enumerate(entry_lines)
                        if "--egress-check-only" in l and l.lstrip().startswith("if "))
        setpriv_idx = next(i for i, l in enumerate(entry_lines)
                           if l.lstrip().startswith("exec setpriv"))
        store_idx = next(i for i, l in enumerate(entry_lines) if "_ensure_built" in l)
        self.assertGreater(flag_idx, setpriv_idx)
        self.assertLess(flag_idx, store_idx)
        # The boot-check branch: AFTER the Playwright verify block (so the FULL boot
        # has run under the gate's flags) and wrapping the final exec -- the
        # fall-through path must still end in a bare exec "$@".
        boot_idx = next(i for i, l in enumerate(entry_lines)
                        if "BOOT_CHECK_ONLY" in l and l.lstrip().startswith("if "))
        playwright_idx = next(i for i, l in enumerate(entry_lines)
                              if "Verifying Playwright runtime" in l)
        self.assertGreater(boot_idx, playwright_idx)
        logical = [l.strip() for l in entry_lines
                   if l.strip() and not l.strip().startswith("#")]
        self.assertEqual(logical[-1], 'exec "$@"')

    def test_image_retirement_keeps_new_and_rollback_target_only(self):
        # Deploys pull by DIGEST, so superseded images keep a repo@sha256 name, are
        # never "dangling", and plain `podman image prune -f` skips them forever
        # (~3.3GB piled up per deploy; 10 deep when first cleaned by hand 2026-07-03).
        # The deploy must retire them explicitly, keeping exactly the just-deployed
        # image and PREV_IMAGE (the documented manual-rollback target).
        wf = _read(DEPLOY_WORKFLOW)
        script = wf[wf.index("Deploy to Fedora droplet"):wf.index("Verify deployment health")]
        # Both keeps spelled on the case guard itself, not merely defined somewhere.
        self.assertIn('"$KEEP_NEW"|"$KEEP_PREV")', script)
        # Retirement is scoped to the backend repo -- it must never be able to touch
        # redis or other infra images.
        self.assertIn('REPO="${REMOTE_IMAGE%%@*}"', script)
        self.assertIn('--filter "reference=${REPO}"', script)
        # Degenerate keep sets must SKIP retirement, not delete "everything else":
        # a same-digest job re-run (KEEP_NEW == KEEP_PREV), a deploy while the
        # container is absent (PREV_IMAGE=unknown: first deploy, or a crash-looped
        # unit whose --rm removed the container), or a failed keep resolution would
        # otherwise collapse the keep set and delete the last-known-good image
        # precisely when a rollback is most likely needed.
        self.assertIn('if [ "$PREV_IMAGE" = "unknown" ]', script)
        self.assertIn('elif [ "$KEEP_NEW" = "$KEEP_PREV" ]', script)
        self.assertEqual(script.count("skipping image retirement"), 4)
        # Keeps are compared in FULL-Id space: `podman images` emits SHORT 12-char
        # ids, so each must be normalized via inspect before the case guard --
        # casing on "$short" directly would match nothing and retire everything
        # (the rollback target included) while the deploy stayed green.
        self.assertIn(
            """img=$(podman image inspect --format '{{.Id}}' "$short" 2>/dev/null) || continue""",
            script,
        )
        self.assertIn('case "$img" in', script)
        # Post-cutover fail-open: a refused removal must WARN and continue -- under
        # the script's set -euo pipefail an unguarded refusal reds a deploy that has
        # already cut over AND skips the Verify-deployment-health step.
        self.assertIn('podman rmi "$short" || echo "WARN: could not remove', script)
        # Fail-safe placement: keep-set derivation precedes any removal, PREV_IMAGE
        # is recorded from the still-running container BEFORE cutover, and the whole
        # block sits after the pull (hoisted above it, the inspect of a not-yet-
        # present digest would abort every deploy) and after cutover.
        self.assertLess(script.index("KEEP_NEW="), script.index("podman rmi"))
        self.assertLess(script.index("KEEP_PREV="), script.index("podman rmi"))
        self.assertLess(script.index("PREV_IMAGE="), script.index("systemctl --user restart"))
        self.assertLess(script.index('podman pull "$REMOTE_IMAGE"'), script.index("KEEP_NEW="))
        self.assertLess(script.index("systemctl --user restart"), script.index("podman rmi"))

    def test_image_retirement_never_forces_and_never_prunes_all(self):
        # Forced removal can take out RUNNING containers; pruning all unused images
        # deletes the rollback target outright, and a time-window filter ages by
        # BUILD time, so any deploy gap longer than the window deletes it too.
        # Regexes over non-comment lines, not literal substrings: literals missed
        # `--force`, `-f -a`, the double-quoted `--filter "until=...` (the script's
        # own quoting style), and `--filter=until=...`.
        wf = _read(DEPLOY_WORKFLOW)
        code = "\n".join(
            l for l in wf.splitlines() if not l.lstrip().startswith("#")
        )
        self.assertIsNone(re.search(r"\brmi\b[^\n]*(\s-\w*f\b|\s--force\b)", code))
        self.assertIsNone(re.search(r"\bprune\b[^\n]*(\s-\w*a\b|\s--all\b)", code))
        self.assertIsNone(re.search(r"""--filter[= ]["']?until""", code))

    def test_deploy_job_bound_to_production_environment(self):
        # DEPLOY_SSH_KEY (the prod SSH key) and GHCR_READ_TOKEN are ENVIRONMENT
        # secrets on Production, whose deployment branch policy is main-only. The
        # job must reference that environment or the secrets simply don't resolve
        # -- and, critically, a workflow edited/dispatched on a non-main ref can
        # never release them, independently of the ref-gate `if:`. Dropping this
        # key would silently fall back to... nothing (the repo-scoped copies are
        # deleted), so the deploy-gate step would skip deploys forever.
        wf = _read(DEPLOY_WORKFLOW)
        deploy_job = wf[wf.index("\n  deploy:"):wf.index("Deploy to Fedora droplet")]
        self.assertIn("environment:", deploy_job)
        self.assertIn("name: Production", deploy_job)

    def test_deploy_job_serialized_by_concurrency_group(self):
        # Without serialization, two quick-succession merges run overlapping deploy
        # scripts against the same droplet: they can land out of order, and the
        # retirement loop of one can rmi the other's freshly pulled (still
        # container-less) image mid-cutover, forcing a multi-GB re-pull inside
        # ExecStart against the health window. Same stanza the concierge and
        # heartbeat deploy jobs already carry.
        wf = _read(DEPLOY_WORKFLOW)
        deploy_job = wf[wf.index("\n  deploy:"):wf.index("Deploy to Fedora droplet")]
        self.assertIn("concurrency:", deploy_job)
        self.assertIn("group: backend-deploy", deploy_job)
        self.assertIn("cancel-in-progress: false", deploy_job)

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

    # Frozen root-init capability set. Each cap maps 1:1 to a root-init step in
    # entrypoint.sh -- nft firewall load -> NET_ADMIN, /app/runtime re-chown ->
    # CHOWN, setpriv uid/gid drop -> SETUID/SETGID, --bounding-set=-net_admin ->
    # SETPCAP -- so widening it means a root-init step was ADDED and must be
    # justified in the deploy workflow's cap-map comment, never slipped in here.
    ROOT_INIT_CAPS = sorted(["NET_ADMIN", "CHOWN", "SETUID", "SETGID", "SETPCAP"])

    @staticmethod
    def _joined_deploy_lines():
        # The gate's podman run spans backslash continuations; fold them so flag
        # extraction sees one logical line per command (same fold the entrypoint
        # structural test uses).
        return re.sub(r"\\\n\s*", " ", _read(DEPLOY_WORKFLOW)).splitlines()

    def _execstart_line(self):
        lines = [l for l in self._joined_deploy_lines()
                 if "podman run" in l and "--name ${SYSTEMD_UNIT}" in l]
        self.assertEqual(len(lines), 1, f"expected one ExecStart podman run, got {lines}")
        return lines[0]

    def _gate_line(self):
        lines = [l for l in self._joined_deploy_lines()
                 if "podman run" in l and "BOOT_CHECK_ONLY=1" in l]
        self.assertEqual(len(lines), 1, f"expected one gate podman run, got {lines}")
        return lines[0]

    @staticmethod
    def _cap_pids_flags(line):
        # The frozen-flag extractor: valued flags are pinned in their equals-form
        # spelling (--tmpfs=..., --memory=...) on BOTH podman run lines, so the
        # space-form (--tmpfs /tmp) would extract nothing and red the parity test
        # rather than silently passing; --read-only is valueless.
        return sorted(re.findall(
            r"--(?:cap-add|cap-drop|pids-limit|tmpfs|memory|memory-swap)=\S+"
            r"|--read-only\b",
            line,
        ))

    def test_execstart_cap_set_is_frozen(self):
        # assertEqual on the FULL extracted cap list, not assertIn per cap: a sixth
        # --cap-add quietly appended to ExecStart must red this test -- preventing
        # silent widening is the whole point. --cap-drop=ALL is part of the
        # property: without it the five adds stack ON TOP of podman's default cap
        # set instead of replacing it, and the "frozen set" is fiction.
        line = self._execstart_line()
        self.assertIn("--cap-drop=ALL", line)
        self.assertEqual(
            sorted(re.findall(r"--cap-add=([A-Z_]+)", line)), self.ROOT_INIT_CAPS
        )

    def test_execstart_pids_limit_pinned(self):
        # Fork-bomb ceiling. 1024 covers today's worst case of ~400-600 tasks
        # (gunicorn workers x threads + agent fan-out + Playwright/Chromium).
        # Raise it DELIBERATELY (both podman run lines + this pin) if
        # AGENT_MAX_CONCURRENCY or GUNICORN_WORKERS grows -- never by deleting
        # the flag, which removes the ceiling entirely.
        self.assertIn("--pids-limit=1024", self._execstart_line())

    def test_gate_and_execstart_flag_parity(self):
        # The safety property of the frozen set: the pre-cutover gate must run the
        # IDENTICAL cap/pids/read-only/tmpfs/memory flags as the ExecStart it
        # fronts, so everything -- all five caps (nft + chown + setpriv +
        # bounding-set drop), the read-only rootfs, and every tmpfs the boot needs
        # to write -- is exercised end-to-end BEFORE cutover, and a too-narrow set
        # aborts the deploy with the old container still serving, instead of
        # crash-looping the unit. Either line edited alone -- gate widened for a
        # quick fix, or ExecStart narrowed without re-proving the gate -- must red
        # here. Exactly ONE deliberate asymmetry is carved out: the gate's
        # throwaway --tmpfs=/app/runtime (the ExecStart uses the real bind mount;
        # see test_gate_and_execstart_deliberate_asymmetries).
        gate = self._cap_pids_flags(self._gate_line())
        runtime_tmpfs = [f for f in gate if f.startswith("--tmpfs=/app/runtime")]
        self.assertEqual(
            len(runtime_tmpfs), 1,
            f"expected exactly one gate-only /app/runtime tmpfs, got {runtime_tmpfs}",
        )
        shared = [f for f in gate if f not in runtime_tmpfs]
        self.assertEqual(shared, self._cap_pids_flags(self._execstart_line()))
        self.assertTrue(shared, "no frozen flags found on the gate podman run")

    def test_gate_and_execstart_deliberate_asymmetries(self):
        # The complete, closed list of ways the gate may differ from ExecStart.
        gate = self._gate_line()
        execstart = self._execstart_line()
        # 1. Runtime store: ExecStart mounts the REAL persistent dir (:U chown +
        #    :Z SELinux relabel); the gate must use a THROWAWAY tmpfs and never
        #    reference the real dir -- pre-cutover, :U would re-chown it under the
        #    serving container and a new-recipe store build would os.replace() the
        #    shared DuckDB while the old container serves it (truthlayer/retrieve.py).
        self.assertIn("-v /home/deploy/fingpt/runtime:/app/runtime:U,Z", execstart)
        self.assertIn("--tmpfs=/app/runtime", gate)
        self.assertNotIn("/home/deploy/fingpt/runtime", gate)
        # 2. Redis keyspace: the gate boots the full app, whose boot-time writes
        #    must land in the throwaway DB15 -- never the serving container's
        #    rate-limit/budget counters in DB0.
        self.assertIn("--env REDIS_URL=redis://fingpt-redis:6379/15", gate)
        self.assertNotIn("6379/0", gate)
        self.assertIn("--env REDIS_URL=redis://fingpt-redis:6379/0", execstart)
        # 3. No --publish on the gate: its :8000 stays netns-private (the health
        #    poll runs INSIDE the container); publishing would collide with the
        #    still-serving container's loopback bind and race the cutover.
        self.assertNotIn("--publish", gate)
        self.assertIn("--publish 127.0.0.1:8000:8000", execstart)
        # Both lines carry the read-only rootfs and identical memory caps (also
        # frozen by the parity test; asserted directly here to document intent).
        for line in (gate, execstart):
            self.assertIn("--read-only", line)
            self.assertIn("--memory=1.7g", line)
            self.assertIn("--memory-swap=2g", line)

    # Frozen tmpfs target set shared by the gate and ExecStart. /tmp (Chromium temp
    # profiles + shm redirect + nft mktemp + fingpt_cache), /app/staticfiles
    # (collectstatic at boot), /home/fingpt (fontconfig + ~/.edgar + yfinance; must
    # be uid1001-owned or the MCP-child EACCES bug returns). /app/logs and
    # /app/media are vestigial with ZERO writers and are deliberately ABSENT: under
    # --read-only a future stray write must fail LOUDLY, not vanish into RAM.
    SHARED_TMPFS_TARGETS = sorted(["/tmp", "/app/staticfiles", "/home/fingpt"])

    @staticmethod
    def _tmpfs_targets(line):
        return sorted(m.split(":", 1)[0] for m in re.findall(r"--tmpfs=(\S+)", line))

    def test_tmpfs_target_sets_frozen(self):
        # assertEqual on the FULL target set per line, not assertIn per target: a
        # new tmpfs quietly appended to either line (e.g. papering over a stray
        # write instead of fixing it) must red this test.
        self.assertEqual(
            self._tmpfs_targets(self._execstart_line()), self.SHARED_TMPFS_TARGETS
        )
        self.assertEqual(
            self._tmpfs_targets(self._gate_line()),
            sorted(self.SHARED_TMPFS_TARGETS + ["/app/runtime"]),
        )

    def test_tmpfs_options_pinned(self):
        # /tmp must be sized (unbounded tmpfs is a memory-DoS surface) and
        # world-writable-sticky (1777: Chromium/nft/cache all write it as
        # uid1001); the uid1001 dirs must carry uid=1001,gid=1001 -- a bare tmpfs
        # mounts root-owned and resurrects the EACCES class documented at the
        # Dockerfile HOME comment.
        for line in (self._execstart_line(), self._gate_line()):
            self.assertIn("--tmpfs=/tmp:rw,size=512m,mode=1777", line)
            self.assertIn("--tmpfs=/app/staticfiles:rw,uid=1001,gid=1001", line)
            self.assertIn("--tmpfs=/home/fingpt:rw,uid=1001,gid=1001", line)
        self.assertIn("--tmpfs=/app/runtime:rw,size=512m", self._gate_line())

    def test_execstart_read_only_rootfs(self):
        # The rootfs hardening itself, asserted directly on the serving line (the
        # parity test would also catch it, but this documents the intent): a
        # compromised app process cannot write outside the tmpfs set, /dev/shm,
        # and the /app/runtime mount.
        self.assertIn("--read-only", self._execstart_line())

    def test_entrypoint_boot_check_branch_structure(self):
        # Structural sentinel for the BOOT_CHECK_ONLY branch in entrypoint.sh: it
        # must probe EVERY writable surface (the fail-SOFT MCP-child $HOME writes
        # can never fail a /health/ poll -- mcp_client/apps.py logger.errors and
        # serves on), background the image's own CMD (never a hardcoded gunicorn),
        # poll the same /health/ endpoint as the Dockerfile HEALTHCHECK, and kill
        # the backgrounded app under a hard deadline so the gate can never wedge
        # the deploy.
        lines = _read(ENTRYPOINT_SH).splitlines()
        start = next(i for i, l in enumerate(lines)
                     if "BOOT_CHECK_ONLY" in l and l.lstrip().startswith("if "))
        end = next(i for i in range(start + 1, len(lines)) if lines[i] == "fi")
        block = "\n".join(lines[start:end + 1])
        # (a) writable-surface probes, one per surface, plus $HOME.
        for surface in ("/app/staticfiles", "/app/runtime",
                        '${CACHE_FILE_PATH:-/tmp/fingpt_cache}', '"$HOME"'):
            self.assertIn(surface, block, f"boot check must probe {surface}")
        self.assertIn("touch", block)
        # (b) backgrounds the real CMD -- "$@", not a re-spelled command line.
        self.assertIn('"$@" &', block)
        self.assertNotIn("gunicorn", block)
        # (c) polls /health/ with the same python-urllib probe as the HEALTHCHECK.
        self.assertIn("urllib.request.urlopen", block)
        self.assertIn("/health/", block)
        # (d) bounded kill: SIGTERM plus a watchdog hard-SIGKILL, then wait.
        self.assertIn('kill "$APP_PID"', block)
        self.assertIn('kill -9 "$APP_PID"', block)
        self.assertIn('wait "$APP_PID"', block)
