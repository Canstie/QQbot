import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from qq_personal_bot import random_gallery as gallery
from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.download_storage import DownloadStorageError
from qq_personal_bot.settings import AppSettings


def make_store(tmp_path):
    store = PolicyStore(tmp_path / "gallery.sqlite3")
    store.initialize(AppSettings(db_path=store.path, admins=()))
    return store


def add_image(store, number):
    body = f"image {number}".encode()
    digest = hashlib.sha256(body).hexdigest()
    image, _ = store.record_download_image(
        sha256=digest, object_key=f"20260907/{digest}.png", content_type="image/png",
        size_bytes=len(body), downloaded_date="20260907")
    return image, body


def test_lru_no_repeat_until_pool_exhausted_and_survives_restart(tmp_path):
    store = make_store(tmp_path)
    for i in range(10):
        add_image(store, i)
    first = store.reserve_download_images(5)
    reopened = PolicyStore(store.path)
    reopened.initialize(AppSettings(db_path=store.path, admins=()))
    second = reopened.reserve_download_images(5)
    assert len({row["id"] for row in first + second}) == 10
    third = reopened.reserve_download_images(5)
    assert [row["id"] for row in third] == [row["id"] for row in first]
    new, _ = add_image(store, 11)
    assert store.reserve_download_images(1)[0]["id"] == new["id"]


def test_concurrent_reservations_and_safe_rollback(tmp_path):
    store = make_store(tmp_path)
    for i in range(10):
        add_image(store, i)
    with ThreadPoolExecutor(2) as pool:
        batches = list(pool.map(store.reserve_download_images, [5, 5]))
    assert len({row["id"] for batch in batches for row in batch}) == 10
    store.release_download_images(batches[0])
    retry = store.reserve_download_images(5)
    assert {row["id"] for row in retry} == {row["id"] for row in batches[0]}
    store.release_download_images(batches[0])
    with store._connect() as conn:
        for row in retry:
            assert conn.execute("SELECT last_used_seq FROM download_images WHERE id = ?",
                                (row["id"],)).fetchone()[0] == row["reserved_seq"]


def test_old_database_migration_empty_small_pool_and_delete(tmp_path):
    path = tmp_path / "gallery.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE download_images (
            id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, object_key TEXT NOT NULL UNIQUE,
            content_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, downloaded_date TEXT NOT NULL,
            created_at REAL NOT NULL)""")
        conn.execute("INSERT INTO download_images VALUES (1, ?, 'old.png', 'image/png', 1, '20260817', 1)",
                     ("a" * 64,))
    store = make_store(tmp_path)
    picked = store.reserve_download_images(5)
    assert len(picked) == 1 and picked[0]["object_key"] == "old.png"
    store.initialize(AppSettings(db_path=path, admins=()))
    assert store.reserve_download_images(1)[0]["previous_seq"] == picked[0]["reserved_seq"]
    store.delete_download_image(1)
    store.release_download_images(picked)
    assert store.reserve_download_images(5) == []


class FakeResponse:
    def __init__(self, body):
        self.body = body
        self.closed = self.released = False

    def stream(self, size):
        yield self.body

    def close(self):
        self.closed = True

    def release_conn(self):
        self.released = True


class FakeStorage:
    def __init__(self, objects):
        self.objects = objects
        self.responses = []

    def get_image(self, key):
        if key not in self.objects:
            raise DownloadStorageError("missing")
        response = FakeResponse(self.objects[key])
        self.responses.append(response)
        return response


@pytest.mark.asyncio
@pytest.mark.parametrize("random_value,expected", [(0, 1), (4, 5)])
async def test_random_count_reads_minio_and_cleans_files(monkeypatch, tmp_path, random_value, expected):
    store = make_store(tmp_path)
    objects = {}
    for i in range(5):
        row, body = add_image(store, i)
        objects[row["object_key"]] = body
    storage = FakeStorage(objects)
    monkeypatch.setattr(gallery, "get_store", lambda: store)
    monkeypatch.setattr(gallery, "get_download_storage", lambda: storage)
    monkeypatch.setattr(gallery.secrets, "randbelow", lambda limit: random_value)
    paths = []

    async def send(path):
        assert path.read_bytes() in objects.values()
        paths.append(path)

    assert await gallery.send_random_gallery(send) is None
    assert len(paths) == expected and len(set(paths)) == expected
    assert all(not path.parent.exists() for path in paths)
    assert all(response.closed and response.released for response in storage.responses)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "corrupt", "send", "unavailable"])
async def test_failures_release_lru_and_cleanup(monkeypatch, tmp_path, failure):
    store = make_store(tmp_path)
    row, body = add_image(store, 1)
    objects = {} if failure == "missing" else {row["object_key"]: b"bad" if failure == "corrupt" else body}
    storage = FakeStorage(objects)
    def factory():
        if failure == "unavailable":
            raise DownloadStorageError("offline")
        return storage
    monkeypatch.setattr(gallery, "get_store", lambda: store)
    monkeypatch.setattr(gallery, "get_download_storage", factory)
    paths = []
    async def send(path):
        paths.append(path)
        raise RuntimeError("OneBot send failed")
    assert "暂时无法" in await gallery.send_random_gallery(send)
    assert store.reserve_download_images(1)[0]["previous_seq"] == 0
    assert all(not path.parent.exists() for path in paths)
    assert all(response.closed and response.released for response in storage.responses)


@pytest.mark.asyncio
async def test_empty_gallery_does_not_access_minio(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock, Mock
    monkeypatch.setattr(gallery, "get_store", lambda: make_store(tmp_path))
    storage = Mock(side_effect=AssertionError("must not fetch"))
    monkeypatch.setattr(gallery, "get_download_storage", storage)
    sender = AsyncMock()
    assert "图库暂无图片" in await gallery.send_random_gallery(sender)
    sender.assert_not_awaited()
    storage.assert_not_called()


@pytest.mark.asyncio
async def test_partial_read_failure_keeps_successful_use(monkeypatch, tmp_path):
    store = make_store(tmp_path)
    good, body = add_image(store, 1)
    bad, _ = add_image(store, 2)
    monkeypatch.setattr(gallery, "get_store", lambda: store)
    monkeypatch.setattr(gallery, "get_download_storage", lambda: FakeStorage({good["object_key"]: body}))
    monkeypatch.setattr(gallery.secrets, "randbelow", lambda limit: 4)
    from unittest.mock import AsyncMock
    sender = AsyncMock()
    assert "已发送 1 张" in await gallery.send_random_gallery(sender)
    sender.assert_awaited_once()
    assert store.reserve_download_images(1)[0]["id"] == bad["id"]


def test_corrupt_or_oversize_object_is_not_sent(monkeypatch, tmp_path):
    store = make_store(tmp_path)
    row, body = add_image(store, 1)
    storage = FakeStorage({row["object_key"]: body})
    monkeypatch.setattr(gallery, "MAX_IMAGE_BYTES", 2)
    with pytest.raises(DownloadStorageError):
        gallery._cache_image(storage, row, tmp_path)
    assert not storage.responses


@pytest.mark.asyncio
async def test_cancelled_send_cleans_and_releases_reservations(monkeypatch, tmp_path):
    import asyncio
    store = make_store(tmp_path)
    row, body = add_image(store, 1)
    monkeypatch.setattr(gallery, "get_store", lambda: store)
    monkeypatch.setattr(gallery, "get_download_storage", lambda: FakeStorage({row["object_key"]: body}))
    paths = []
    async def send(path):
        paths.append(path)
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await gallery.send_random_gallery(send)
    assert not paths[0].parent.exists()
    assert store.reserve_download_images(1)[0]["previous_seq"] == 0
