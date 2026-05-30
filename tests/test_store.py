from __future__ import annotations

from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.settings import AppSettings


def test_store_persists_policy_state(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    settings = AppSettings(
        db_path=db_path,
        admins=(10000,),
        policy_mode="allowlist",
        trigger_prefixes=("~",),
    )
    store = PolicyStore(db_path)
    store.initialize(settings)

    store.set_group_enabled(123, True, actor_id=10000)
    store.set_mode("blocklist", actor_id=10000)
    store.add_prefix("!", actor_id=10000)

    reopened = PolicyStore(db_path)
    reopened.initialize(settings)

    assert reopened.get_mode() == "blocklist"
    assert reopened.is_group_enabled(123)
    assert reopened.is_admin(10000)
    assert "!" in reopened.prefixes()


def test_snapshot_shape(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=(10000,)))

    snapshot = store.snapshot()

    assert snapshot["mode"] == "allowlist"
    assert snapshot["admins"] == [10000]
    assert snapshot["trigger"]["mention"] is True
    assert "~" in snapshot["trigger"]["prefixes"]


def test_empty_prefixes_default_to_tilde(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=(10000,)))

    store.set_prefixes([], actor_id=10000)

    assert store.prefixes() == ["~"]
