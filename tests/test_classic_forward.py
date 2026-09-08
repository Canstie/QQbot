from __future__ import annotations

import asyncio
import hashlib
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import pytest

from qq_personal_bot import classic_forward as forward
from qq_personal_bot.classic_storage import ClassicStorageError
from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.settings import AppSettings


def node_path(node):
    return Path(url2pathname(unquote(urlparse(node["data"]["content"][0]["data"]["file"]).path)))


@pytest.fixture
def archive(monkeypatch):
    bodies = {f"{i}.png": f"image-{i}".encode() for i in range(7)}
    records = [{"id": i, "object_key": key, "content_type": "image/png",
                "size_bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
               for i, (key, body) in enumerate(bodies.items())]
    store = SimpleNamespace(list_classic_images=Mock(return_value=records))
    storage = SimpleNamespace(read_image=Mock(side_effect=lambda group, key: bodies[key]))
    monkeypatch.setattr(forward, "get_store", lambda: store)
    monkeypatch.setattr(forward, "get_classic_storage", Mock(return_value=storage))
    return store, storage, records


@pytest.mark.asyncio
@pytest.mark.parametrize("count_limit,expected", [(100, [7]), (3, [3, 3, 1])])
async def test_all_nodes_batches_and_cleanup(archive, monkeypatch, count_limit, expected):
    store, storage, records = archive
    monkeypatch.setattr(forward, "MAX_BATCH_IMAGES", count_limit)
    notices = AsyncMock()
    seen = []
    sizes = []
    calls = 0

    async def send(nodes):
        nonlocal calls
        calls += 1
        notices.assert_awaited_once()
        sizes.append(len(nodes))
        assert all(not path.exists() for path in seen)
        for node in nodes:
            assert node["type"] == "node"
            assert node["data"]["user_id"] == "456"
            assert node["data"]["nickname"] == "本群典藏"
            assert [segment["type"] for segment in node["data"]["content"]] == ["image"]
            path = node_path(node)
            assert path.read_bytes() == f"image-{len(seen)}".encode()
            seen.append(path)

    await forward.send_all_classics(123, "456", send, notices)
    assert sizes == expected
    assert calls == len(expected)
    assert len(seen) == len(records)
    assert all(not path.exists() and not path.parent.exists() for path in seen)
    store.list_classic_images.assert_called_once_with(123, limit=None)
    assert all(call.args[0] == 123 for call in storage.read_image.call_args_list)
    assert 123 not in forward._active_groups


@pytest.mark.asyncio
@pytest.mark.parametrize("group_id", [None, 123])
async def test_private_or_empty_never_reads_minio(archive, group_id):
    store, storage, _ = archive
    store.list_classic_images.return_value = []
    send, notice = AsyncMock(), AsyncMock()
    await forward.send_all_classics(group_id, "456", send, notice)
    send.assert_not_awaited()
    storage.read_image.assert_not_called()
    forward.get_classic_storage.assert_not_called()
    notice.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("count,expected", [(1, [1]), (62, [62]), (99, [99]),
                                          (100, [100]), (101, [100, 1]),
                                          (200, [100, 100]), (201, [100, 100, 1])])
async def test_default_batch_limit_is_hundred_with_independent_remainder(archive, count, expected):
    store, _, records = archive
    store.list_classic_images.return_value = [
        dict(records[i % len(records)], id=i) for i in range(count)
    ]
    batches = []
    paths = []

    async def send(nodes):
        batches.append(len(nodes))
        assert all(not path.exists() for path in paths)
        for node in nodes:
            assert [segment["type"] for segment in node["data"]["content"]] == ["image"]
            path = node_path(node)
            assert path.is_file()
            paths.append(path)

    notice = AsyncMock()
    await forward.send_all_classics(123, "456", send, notice)
    assert forward.MAX_BATCH_IMAGES == 100
    assert batches == expected
    assert len({path.name for path in paths}) == count
    assert ("一份聊天记录" if count <= 100 else "每批最多 100 张") in notice.call_args.args[0]
    assert all(not path.parent.exists() for path in paths)


@pytest.mark.asyncio
async def test_total_bytes_do_not_split_record(archive, monkeypatch):
    _, _, records = archive
    for record in records:
        record["size_bytes"] = 30 * 1024 * 1024

    async def cache(storage, group_id, record, directory):
        path = directory / f'{record["id"]}.png'
        path.touch()
        return path

    # Stub file materialization only; large aggregate metadata must not trigger splitting.
    monkeypatch.setattr(forward, "_cache_image_async", cache)
    send = AsyncMock()
    await forward.send_all_classics(123, "456", send, AsyncMock())
    send.assert_awaited_once()
    assert len(send.call_args.args[0]) == 7


@pytest.mark.asyncio
async def test_corrupt_and_missing_images_are_skipped(archive):
    _, storage, records = archive
    records[0]["sha256"] = "0" * 64
    records[1]["size_bytes"] += 1
    read = storage.read_image.side_effect
    def broken(group, key):
        if key == "2.png":
            raise ClassicStorageError("missing")
        return read(group, key)
    storage.read_image.side_effect = broken
    send, notice = AsyncMock(), AsyncMock()
    await forward.send_all_classics(123, "456", send, notice)
    send.assert_awaited_once()
    assert len(send.call_args.args[0]) == 4
    assert notice.call_args.args[0] == "本群典图已发送 4 张，另有 3 张读取失败。"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_batch", [1, 2])
async def test_send_failure_stops_and_cleans_files(archive, monkeypatch, failure_batch):
    monkeypatch.setattr(forward, "MAX_BATCH_IMAGES", 2)
    sent_paths = []
    calls = 0
    async def send(nodes):
        nonlocal calls
        calls += 1
        current_paths = [node_path(node) for node in nodes]
        assert all(path.is_file() for path in current_paths)
        assert all(not path.exists() for path in sent_paths)
        sent_paths.extend(current_paths)
        if calls == failure_batch:
            raise RuntimeError("API timeout")
    send_mock = AsyncMock(side_effect=send)
    notice = AsyncMock()
    await forward.send_all_classics(123, "456", send_mock, notice)
    assert send_mock.await_count == failure_batch
    assert f"第 {failure_batch} 批聊天记录发送失败或超时" in notice.call_args.args[0]
    assert f"前 {failure_batch - 1} 批已确认发送 {(failure_batch - 1) * 2} 张，共 7 张" in notice.call_args.args[0]
    assert "避免重复发送" in notice.call_args.args[0]
    assert archive[1].read_image.call_count == failure_batch * 2
    assert all(not path.parent.exists() for path in sent_paths)
    assert 123 not in forward._active_groups


@pytest.mark.asyncio
async def test_storage_unavailable_releases_group(archive, monkeypatch):
    monkeypatch.setattr(forward, "get_classic_storage", Mock(side_effect=ClassicStorageError("offline")))
    send, notice = AsyncMock(), AsyncMock()
    await forward.send_all_classics(123, "456", send, notice)
    send.assert_not_awaited()
    assert "已确认发送 0 张，共 7 张" in notice.call_args.args[0]
    assert 123 not in forward._active_groups


@pytest.mark.asyncio
async def test_all_images_unreadable_does_not_send_empty_forward(archive):
    archive[1].read_image.side_effect = ClassicStorageError("missing")
    send, notice = AsyncMock(), AsyncMock()
    await forward.send_all_classics(123, "456", send, notice)
    send.assert_not_awaited()
    assert notice.call_args.args[0] == "本群典图已发送 0 张，另有 7 张读取失败。"


@pytest.mark.asyncio
async def test_cancellation_during_batch_send_cleans_files(archive, monkeypatch):
    monkeypatch.setattr(forward, "MAX_BATCH_IMAGES", 2)
    paths = []
    async def send(nodes):
        paths.extend(node_path(node) for node in nodes)
        assert len(paths) == 2 and all(path.exists() for path in paths)
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await forward.send_all_classics(123, "456", send, AsyncMock())
    assert all(not path.parent.exists() for path in paths)
    assert 123 not in forward._active_groups


@pytest.mark.asyncio
async def test_same_group_overlap_is_rejected(archive):
    started, release = asyncio.Event(), asyncio.Event()
    async def send(nodes):
        started.set()
        await release.wait()
    task = asyncio.create_task(forward.send_all_classics(123, "456", send, AsyncMock()))
    await started.wait()
    duplicate, notice = AsyncMock(), AsyncMock()
    try:
        await forward.send_all_classics(123, "456", duplicate, notice)
        duplicate.assert_not_awaited()
        assert "正在整理发送中" in notice.call_args.args[0]
    finally:
        release.set()
        await task
    assert 123 not in forward._active_groups


@pytest.mark.asyncio
async def test_cancel_waits_for_writer_and_cleans_directory(archive, monkeypatch):
    started, release = threading.Event(), threading.Event()
    paths = []
    original = forward._cache_image
    def blocked(storage, group, record, directory):
        paths.append(directory)
        started.set()
        assert release.wait(5)
        return original(storage, group, record, directory)
    monkeypatch.setattr(forward, "_cache_image", blocked)
    task = asyncio.create_task(forward.send_all_classics(123, "456", AsyncMock(), AsyncMock()))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert paths[0].exists()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not paths[0].exists()
    assert 123 not in forward._active_groups


def test_store_all_is_group_scoped_unlimited_and_does_not_change_counts(tmp_path):
    store = PolicyStore(tmp_path / "test.sqlite3")
    store.initialize(AppSettings(db_path=tmp_path / "test.sqlite3", admins=(10000,)))
    with store._connect() as conn:
        conn.executemany(
            "INSERT INTO classic_images(group_id,sha256,object_key,content_type,size_bytes,created_at) VALUES(?,?,?,?,?,?)",
            [(123, str(i), f"{i}.png", "image/png", 7, i) for i in range(1001)] +
            [(456, "other", "other.png", "image/png", 7, 2000)],
        )
    assert len(store.list_classic_images(123)) == 1000
    records = store.list_classic_images(123, limit=None)
    assert len(records) == 1001
    assert records[0]["object_key"] == "1000.png"
    assert all(record["group_id"] == 123 and record["blast_count"] == 0 for record in records)
