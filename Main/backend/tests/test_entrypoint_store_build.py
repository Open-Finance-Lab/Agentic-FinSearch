"""Static guard: entrypoint.sh builds the store through the atomic path.

Once the store persists on the /app/runtime volume, a build killed mid-write must
never leave a corrupt file. retrieve._ensure_built() builds into a temp file and
atomically renames it into place; the old inline build_from_vendored() wrote directly
into DB_PATH and is unsafe on a persistent volume. This locks that choice.
"""
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
ENTRYPOINT = os.path.join(_HERE, "..", "entrypoint.sh")


def _read():
    with open(ENTRYPOINT, "r", encoding="utf-8") as fh:
        return fh.read()


def test_entrypoint_builds_store_via_ensure_built():
    text = _read()
    assert "retrieve._ensure_built()" in text


def test_entrypoint_does_not_build_store_directly():
    text = _read()
    # The old direct build wrote straight into DB_PATH — unsafe on the persistent volume.
    assert "build_from_vendored()" not in text
