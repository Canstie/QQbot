from __future__ import annotations

from types import SimpleNamespace

import pytest

from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.settings import AppSettings
from tools import migrate_download_images_to_minio as migration


class FakeStorage:
    def __init__(self, *, fail_upload: bool = False) -> None:
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.fail_upload = fail_upload

    def ensure_available(self) -> None:
        pass

    def put_image(self, object_key, body, content_type, metadata) -> None:
        if self.fail_upload:
            raise RuntimeError("upload failed")
        self.objects[object_key] = (body, metadata)

    def stat_image(self, object_key):
        body, metadata = self.objects[object_key]
        return SimpleNamespace(
            size=len(body),
            metadata={f"x-amz-meta-{key}": value for key, value in metadata.items()},
        )

    def remove_image(self, object_key, *, missing_ok=False) -> None:
        self.objects.pop(object_key, None)


def _store(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=(1,)))
    return store


def test_migration_is_idempotent_and_deletes_only_after_verification(tmp_path, monkeypatch):
    source = tmp_path / "downloadimage"
    day = source / "20260817"
    day.mkdir(parents=True)
    (day / "one.png").write_bytes(b"\x89PNG\r\n\x1a\nfirst")
    (day / "two.gif").write_bytes(b"GIF89asecond")
    store = _store(tmp_path)
    storage = FakeStorage()
    monkeypatch.setattr(migration, "get_store", lambda: store)
    monkeypatch.setattr(migration, "get_download_storage", lambda: storage)

    first = migration.migrate(source, delete_source_after_verify=False)
    second = migration.migrate(source, delete_source_after_verify=True)

    assert first == {"total": 2, "succeeded": 2, "skipped": 0, "failed": 0, "verified": 2}
    assert second == {"total": 2, "succeeded": 0, "skipped": 2, "failed": 0, "verified": 2}
    assert len(storage.objects) == 2
    assert not source.exists()


def test_failed_migration_keeps_source_files(tmp_path, monkeypatch):
    source = tmp_path / "downloadimage"
    day = source / "20260817"
    day.mkdir(parents=True)
    image = day / "one.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfirst")
    monkeypatch.setattr(migration, "get_store", lambda: _store(tmp_path))
    monkeypatch.setattr(
        migration,
        "get_download_storage",
        lambda: FakeStorage(fail_upload=True),
    )

    with pytest.raises(RuntimeError, match="keeping source files"):
        migration.migrate(source, delete_source_after_verify=True)

    assert image.is_file()
