from __future__ import annotations

from types import SimpleNamespace

import pytest

from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.settings import AppSettings
from tools import migrate_classics_to_minio as migration


class FakeClassicStorage:
    def __init__(self, *, fail_upload: bool = False) -> None:
        self.objects: dict[tuple[int, str], tuple[bytes, dict[str, str]]] = {}
        self.fail_upload = fail_upload

    def put_image(self, group_id, object_key, body, content_type, metadata) -> None:
        if self.fail_upload:
            raise RuntimeError("upload failed")
        self.objects[(int(group_id), object_key)] = (body, metadata)

    def stat_image(self, group_id, object_key):
        body, metadata = self.objects[(int(group_id), object_key)]
        return SimpleNamespace(
            size=len(body),
            metadata={f"x-amz-meta-{key}": value for key, value in metadata.items()},
        )

    def remove_image(self, group_id, object_key, *, missing_ok=False) -> None:
        self.objects.pop((int(group_id), object_key), None)


def _store(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=(1,)))
    return store


def test_classic_migration_deduplicates_per_group_and_deletes_after_verify(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "classics"
    first_group = source / "123"
    second_group = source / "456"
    first_group.mkdir(parents=True)
    second_group.mkdir(parents=True)
    body = b"GIF89aclassic"
    (first_group / "one.gif").write_bytes(body)
    (first_group / "duplicate.gif").write_bytes(body)
    (second_group / "same-in-another-group.gif").write_bytes(body)
    store = _store(tmp_path)
    storage = FakeClassicStorage()
    monkeypatch.setattr(migration, "get_store", lambda: store)
    monkeypatch.setattr(migration, "get_classic_storage", lambda: storage)

    first = migration.migrate(source, delete_source_after_verify=False)
    second = migration.migrate(source, delete_source_after_verify=True)

    assert first == {
        "total": 3,
        "succeeded": 2,
        "skipped": 1,
        "failed": 0,
        "verified": 3,
        "groups": 2,
    }
    assert second == {
        "total": 3,
        "succeeded": 0,
        "skipped": 3,
        "failed": 0,
        "verified": 3,
        "groups": 2,
    }
    assert len(storage.objects) == 2
    assert not source.exists()


def test_failed_classic_migration_keeps_source(tmp_path, monkeypatch):
    source = tmp_path / "classics"
    group = source / "123"
    group.mkdir(parents=True)
    image = group / "one.gif"
    image.write_bytes(b"GIF89aclassic")
    store = _store(tmp_path)
    monkeypatch.setattr(migration, "get_store", lambda: store)
    monkeypatch.setattr(
        migration,
        "get_classic_storage",
        lambda: FakeClassicStorage(fail_upload=True),
    )

    with pytest.raises(RuntimeError, match="keeping source files"):
        migration.migrate(source, delete_source_after_verify=True)

    assert image.is_file()
