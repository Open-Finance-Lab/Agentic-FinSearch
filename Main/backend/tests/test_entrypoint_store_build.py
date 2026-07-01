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
    # The entrypoint must not call build_from_vendored in ANY form: it writes straight
    # into DB_PATH (no temp-file + atomic rename) and is unsafe on the persistent volume.
    # Match the bare name so the arg-passing form build_from_vendored(store.DB_PATH) is
    # caught too — not just the zero-arg build_from_vendored() the old entrypoint used.
    assert "build_from_vendored" not in text
