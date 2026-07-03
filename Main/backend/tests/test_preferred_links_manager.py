"""PreferredLinksManager storage-path + seeding guards (read-only rootfs).

The sync_preferred_urls endpoint (used by the shipped extension) writes through
this manager. Its historical default path, backend/data/preferred_links.json,
lives under root-owned /app/data in the production image, so the write EACCESed
into a 500 long before the rootfs went --read-only. The fix: the storage path is
env-configurable (PREFERRED_LINKS_PATH), the prod image pins it onto the writable
/app/runtime volume (Dockerfile ENV), and a missing runtime copy is seeded from
the read-only in-image default file so curated defaults survive the move. Local
dev keeps the old in-repo path (env unset).
"""
import json
import os

from datascraper import preferred_links_manager as plm
from datascraper.preferred_links_manager import (
    DEFAULT_STORAGE_PATH,
    PreferredLinksManager,
)

DOCKERFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Dockerfile")


def test_default_path_used_when_env_unset(monkeypatch):
    monkeypatch.delenv("PREFERRED_LINKS_PATH", raising=False)
    manager = PreferredLinksManager()
    assert manager.storage_path == DEFAULT_STORAGE_PATH


def test_env_var_overrides_storage_path(monkeypatch, tmp_path):
    target = tmp_path / "runtime" / "preferred_links.json"
    monkeypatch.setenv("PREFERRED_LINKS_PATH", str(target))
    manager = PreferredLinksManager()
    assert manager.storage_path == target
    assert target.exists()


def test_explicit_arg_beats_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PREFERRED_LINKS_PATH", str(tmp_path / "from-env.json"))
    explicit = tmp_path / "explicit.json"
    manager = PreferredLinksManager(storage_path=str(explicit))
    assert manager.storage_path == explicit


def test_missing_runtime_copy_seeded_from_in_image_default(monkeypatch, tmp_path):
    # The prod-shaped move: env points at an (empty) runtime volume; the first
    # manager construction must seed the runtime copy from the in-image default
    # file VERBATIM -- the curated defaults (data/preferred_links.json) are richer
    # than the hardcoded fallback list.
    target = tmp_path / "preferred_links.json"
    monkeypatch.setenv("PREFERRED_LINKS_PATH", str(target))
    manager = PreferredLinksManager()
    seeded = json.loads(target.read_text())
    assert seeded == json.loads(DEFAULT_STORAGE_PATH.read_text())
    assert manager.get_links() == seeded["preferred_links"]


def test_existing_runtime_copy_never_overwritten_by_seed(monkeypatch, tmp_path):
    target = tmp_path / "preferred_links.json"
    existing = {
        "version": "1.0",
        "preferred_links": ["https://example.com/user-curated"],
        "metadata": {"last_updated": None, "total_links": 1},
    }
    target.write_text(json.dumps(existing))
    monkeypatch.setenv("PREFERRED_LINKS_PATH", str(target))
    manager = PreferredLinksManager()
    assert manager.get_links() == ["https://example.com/user-curated"]


def test_seed_falls_back_to_hardcoded_defaults_when_default_file_absent(
    monkeypatch, tmp_path
):
    # Defense in depth: a checkout/image without data/preferred_links.json must
    # still initialize (hardcoded defaults), not crash the first request.
    monkeypatch.setattr(plm, "DEFAULT_STORAGE_PATH", tmp_path / "absent.json")
    target = tmp_path / "preferred_links.json"
    manager = PreferredLinksManager(storage_path=str(target))
    links = manager.get_links()
    assert links, "hardcoded defaults expected"
    assert "https://finance.yahoo.com" in links


def test_writes_land_on_the_configured_path(monkeypatch, tmp_path):
    # The actual prod failure mode: sync writes must hit the configured (writable)
    # path -- and only it.
    target = tmp_path / "preferred_links.json"
    monkeypatch.setenv("PREFERRED_LINKS_PATH", str(target))
    manager = PreferredLinksManager()
    manager.set_links(["https://reuters.com", "https://reuters.com", "https://ft.com"])
    stored = json.loads(target.read_text())
    assert stored["preferred_links"] == ["https://reuters.com", "https://ft.com"]  # deduped
    assert stored["metadata"]["total_links"] == 2


def test_dockerfile_pins_preferred_links_onto_runtime_volume():
    # The image must point the manager at the writable persistent volume; without
    # this pin the default path lands under root-owned /app/data on a --read-only
    # rootfs and every extension sync 500s again.
    with open(DOCKERFILE, "r", encoding="utf-8") as fh:
        text = fh.read()
    assert "PREFERRED_LINKS_PATH=/app/runtime/preferred_links.json" in text
