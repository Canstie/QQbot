from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from qq_personal_bot import classic_sharing, lua_runner
from qq_personal_bot.classic_storage import ClassicStorageError
from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.settings import AppSettings


class MemoryClassicStorage:
    def __init__(self) -> None:
        self.objects: dict[tuple[int, str], bytes] = {}
        self.fail_target: int | None = None

    def put_image(self, group_id, object_key, body, content_type, metadata):
        if group_id == self.fail_target:
            raise ClassicStorageError("copy failed")
        self.objects[(group_id, object_key)] = body

    def read_image(self, group_id, object_key):
        return self.objects[(group_id, object_key)]

    def stat_image(self, group_id, object_key):
        return SimpleNamespace(size=len(self.objects[(group_id, object_key)]))

    def remove_image(self, group_id, object_key, *, missing_ok=False):
        self.objects.pop((group_id, object_key), None)

    def remove_group_bucket(self, group_id, object_keys):
        for key in object_keys:
            self.objects.pop((group_id, key), None)


def _add(store, storage, group_id, body):
    digest = hashlib.sha256(body).hexdigest()
    key = f"{digest}.gif"
    storage.objects[(group_id, key)] = body
    store.record_classic_image(
        group_id=group_id, sha256=digest, object_key=key,
        content_type="image/gif", size_bytes=len(body),
    )
    return digest


@pytest.fixture
def archive(tmp_path, monkeypatch):
    db_path = tmp_path / "policy.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=(10000,)))
    storage = MemoryClassicStorage()
    monkeypatch.setattr(classic_sharing, "get_store", lambda: store)
    monkeypatch.setattr(classic_sharing, "get_classic_storage", lambda: storage)
    monkeypatch.setattr(lua_runner, "get_store", lambda: store)
    monkeypatch.setattr(lua_runner, "get_classic_storage", lambda: storage)
    return store, storage, tmp_path


def test_bind_shares_store_blast_and_all_images_then_dissolve_clones(archive):
    store, storage, tmp_path = archive
    common = b"GIF89a-common"
    unique = b"GIF89a-unique"
    _add(store, storage, 123, common)
    _add(store, storage, 456, common)
    _add(store, storage, 456, unique)
    api = lua_runner.LuaApi(None, None, None, None, "存典", 5)
    picked_before_bind = api.pick_classic_image(456, 1)

    result = classic_sharing.bind_classic_group(123, 456)
    assert result["merged_count"] == 1
    assert result["duplicate_count"] == 1
    assert store.resolve_classic_group(456) == 123
    assert len(store.list_classic_images(123)) == 2
    assert store.list_classic_images(456) == []
    assert all(group_id != 456 for group_id, _ in storage.objects)
    assert api.classic_image(picked_before_bind).startswith("[CQ:image,file=base64://")

    path = tmp_path / "new.gif"
    path.write_bytes(b"GIF89a-after-binding")
    assert api.save_classic_image(456, str(path)) == "stored"
    assert len(store.list_classic_images(123)) == 3
    assert api.save_classic_image(123, str(path)) == "exists"
    picked = api.pick_classic_image(456, 42)
    assert picked.startswith("123/")
    assert api.classic_image(picked).startswith("[CQ:image,file=base64://")

    dissolved = classic_sharing.dissolve_classic_group(123)
    assert dissolved["images_per_group"] == 3
    assert dissolved["copied_count"] == 3
    assert store.resolve_classic_group(456) == 456
    assert {item["sha256"] for item in store.list_classic_images(456)} == {
        item["sha256"] for item in store.list_classic_images(123)
    }
    assert all(
        storage.read_image(456, item["object_key"])
        == storage.read_image(123, item["object_key"])
        for item in store.list_classic_images(456)
    )
    another = tmp_path / "after.gif"
    another.write_bytes(b"GIF89a-independent")
    assert api.save_classic_image(456, str(another)) == "stored"
    assert len(store.list_classic_images(456)) == 4
    assert len(store.list_classic_images(123)) == 3


def test_copy_failure_keeps_original_binding_and_records(archive):
    store, storage, _ = archive
    _add(store, storage, 456, b"GIF89a-member")
    storage.fail_target = 123
    with pytest.raises(ClassicStorageError):
        classic_sharing.bind_classic_group(123, 456)
    assert store.resolve_classic_group(456) == 456
    assert len(store.list_classic_images(456)) == 1

    storage.fail_target = None
    classic_sharing.bind_classic_group(123, 456)
    storage.fail_target = 456
    with pytest.raises(ClassicStorageError):
        classic_sharing.dissolve_classic_group(123)
    assert store.resolve_classic_group(456) == 123
    assert store.list_classic_images(456) == []


def test_binding_rejects_cycles_and_double_membership(archive):
    store, _, _ = archive
    classic_sharing.bind_classic_group(123, 456)
    with pytest.raises(ValueError):
        classic_sharing.bind_classic_group(456, 789)
    with pytest.raises(ValueError):
        classic_sharing.bind_classic_group(789, 456)
    with pytest.raises(ValueError):
        classic_sharing.bind_classic_group(789, 123)
    assert store.list_classic_bindings() == [{"master_group_id": 123, "group_ids": [456]}]
