from concierge.identity import IdentityStore, Identity


def test_resolve_creates_with_reserved_atl_none():
    store = IdentityStore(":memory:")
    ident = store.resolve("123", now_iso="2026-06-28T00:00:00+00:00")
    assert ident.discord_user_id == "123"
    assert ident.finsearch_user_id == "discord_123"      # deterministic
    assert ident.atl_account_id is None                   # reserved
    store.close()


def test_resolve_is_idempotent():
    store = IdentityStore(":memory:")
    a = store.resolve("123", now_iso="2026-06-28T00:00:00+00:00")
    b = store.resolve("123", now_iso="2099-01-01T00:00:00+00:00")  # later — must not overwrite
    assert a == b
    assert b.created_at == "2026-06-28T00:00:00+00:00"
    store.close()


def test_persists_across_reopen(tmp_path):
    db = str(tmp_path / "id.sqlite")
    s1 = IdentityStore(db); s1.resolve("123", now_iso="2026-06-28T00:00:00+00:00"); s1.close()
    s2 = IdentityStore(db)
    assert s2.get("123") is not None
    assert s2.get("999") is None
    s2.close()
