"""Sentinel for the repo-wide dotenv ignore block (Root G hygiene).

Concierge and Heartbeat keep their REAL dotenvs (.env.concierge /
.env.heartbeat, written by hand on the droplet) right next to tracked
.example templates, and the root .gitignore used to be a path-specific list
(Main/backend/.env, chat_interface/...) that silently rotted each time a
service was added -- a `git add -A` from the repo root would have staged
live Discord/OpenAI credentials. The repo-wide `.env` / `.env.*` block
closes that; these tests pin BOTH directions of it:

  * every real dotenv shape is ignored (`git check-ignore` exits 0), and
  * the four committed .example templates stay tracked -- the `!` negations
    are load-bearing, since `.env.*` alone would ignore the templates on a
    fresh clone and `git add` of an updated template would start failing.

Lives here, NOT in Concierge/tests: the concierge droplet refresh ships the
tree without .git, where these git invocations can only error. The whole
module skips in that case (and wherever else .git is absent, e.g. a bare
source export).

Runs in the standard backend harness:
    uv run pytest tests/test_gitignore_env.py -q
"""
import shutil
import subprocess
from pathlib import Path

import pytest

# tests/ -> backend/ -> Main/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]

# .git is a FILE (gitdir pointer) in linked worktrees; exists() covers both.
pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (REPO_ROOT / ".git").exists(),
    reason="not a git checkout (droplet refresh ships the tree without .git)",
)

# Real-secret paths that must never be stageable. The bare .env variants are
# asserted alongside the suffixed ones: check-ignore evaluates patterns for
# paths that need not exist, so this pins the rule for whichever shape a
# future service (or a hand edit on the droplet) actually writes.
REAL_DOTENVS = [
    "Concierge/.env.concierge",
    "Concierge/.env",
    "Heartbeat/.env.heartbeat",
    "Heartbeat/.env",
]

# The committed onboarding templates the two `!` negations re-include.
TRACKED_TEMPLATES = [
    "Concierge/.env.concierge.example",
    "Heartbeat/.env.heartbeat.example",
    "Main/backend/.env.example",
    "Main/backend/.env.production.example",
]


def _git(*args: str) -> int:
    # Exit code is the whole contract here; swallow output so a git warning
    # can't pollute the pytest report.
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode


@pytest.mark.parametrize("path", REAL_DOTENVS)
def test_real_dotenvs_are_ignored(path):
    # check-ignore -q: 0 = ignored, 1 = plain `git add` would stage it.
    assert _git("check-ignore", "-q", "--", path) == 0, (
        f"{path} is not gitignored -- `git add -A` would stage live secrets"
    )


@pytest.mark.parametrize("path", TRACKED_TEMPLATES)
def test_example_templates_stay_tracked(path):
    # ls-files --error-unmatch: 0 = tracked in the index. Trips if the
    # negations are dropped and a later `git rm --cached` style cleanup (or a
    # rewrite of the dotenv block) untracks the onboarding templates.
    assert _git("ls-files", "--error-unmatch", "--", path) == 0, (
        f"{path} is no longer tracked -- the .example negations were broken"
    )


@pytest.mark.parametrize("path", TRACKED_TEMPLATES)
def test_example_templates_not_ignored_for_fresh_clones(path):
    # Tracked files sail past ignore rules, so ls-files alone can't see a
    # broken negation until someone re-adds the file. check-ignore must exit
    # 1 (NOT ignored) or a fresh clone could never stage a template update.
    assert _git("check-ignore", "-q", "--", path) == 1, (
        f"{path} matches an ignore rule -- the `!` negation no longer wins"
    )
