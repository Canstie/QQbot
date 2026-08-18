from __future__ import annotations

import hashlib
import importlib
from types import SimpleNamespace

import nonebot
import pytest

from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.settings import AppSettings

nonebot.init()
download = importlib.import_module("qq_personal_bot.plugins.download")


class CommandFinished(Exception):
    pass


class FakeMatcher:
    def __init__(self) -> None:
        self.messages: list[str | None] = []

    async def send(self, message: str) -> None:
        self.messages.append(message)

    async def finish(self, message: str | None = None) -> None:
        self.messages.append(message)
        raise CommandFinished


class FakeSegment:
    def __init__(self, segment_type: str, data: dict):
        self.type = segment_type
        self.data = data


class FakeBot:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call_api(self, action: str, **params):
        self.calls.append((action, params))
        if action == "get_msg" and params["message_id"] == "quoted":
            return {"message": [{"type": "forward", "data": {"id": "outer"}}]}
        if action == "get_forward_msg" and params["id"] == "outer":
            return {
                "message": [
                    {
                        "type": "node",
                        "data": {
                            "content": [
                                {
                                    "type": "image",
                                    "data": {"url": "https://example.test/a.jpg"},
                                },
                                {"type": "forward", "data": {"id": "inner"}},
                            ]
                        },
                    },
                    {
                        "type": "node",
                        "data": {
                            "content": [
                                {"type": "image", "data": {"file": "opaque.jpg"}}
                            ]
                        },
                    },
                ]
            }
        if action == "get_forward_msg" and params["id"] == "inner":
            return {
                "messages": [
                    {
                        "message": [
                            {
                                "type": "image",
                                "data": {"url": "https://example.test/b.png"},
                            }
                        ]
                    }
                ]
            }
        if action == "get_image" and params["file"] == "opaque.jpg":
            return {"url": "https://example.test/opaque.jpg"}
        raise AssertionError(f"Unexpected API call: {action} {params}")


class FakeStorage:
    bucket = "qqbot-downloads"

    def __init__(self, events: list[str] | None = None) -> None:
        self.objects: dict[str, bytes] = {}
        self.events = events

    def ensure_available(self) -> None:
        if self.events is not None:
            self.events.append("storage-ready")

    def put_image(self, object_key, body, content_type, metadata) -> None:
        self.objects[object_key] = body

    def stat_image(self, object_key):
        return SimpleNamespace(size=len(self.objects[object_key]))

    def remove_image(self, object_key, *, missing_ok=False) -> None:
        self.objects.pop(object_key, None)


@pytest.mark.asyncio
async def test_download_silently_ignores_non_admin(monkeypatch):
    matcher = FakeMatcher()
    bot = FakeBot()
    store = SimpleNamespace(is_admin=lambda user_id: False)
    monkeypatch.setattr(download, "get_store", lambda: store)

    await download._handle_download(
        matcher,
        bot,
        SimpleNamespace(user_id=123, message=[]),
    )

    assert matcher.messages == []
    assert bot.calls == []


@pytest.mark.asyncio
async def test_download_overview_silently_ignores_non_admin(monkeypatch):
    matcher = FakeMatcher()
    store = SimpleNamespace(is_admin=lambda user_id: False)
    monkeypatch.setattr(download, "get_store", lambda: store)

    await download._handle_download_overview(matcher, SimpleNamespace(user_id=123))

    assert matcher.messages == []


@pytest.mark.asyncio
async def test_collects_images_from_nested_forward_record():
    bot = FakeBot()
    event = SimpleNamespace(
        message=[FakeSegment("reply", {"id": "quoted"})],
        reply=SimpleNamespace(
            message_id="quoted",
            message=[FakeSegment("forward", {"id": "outer"})],
        ),
    )

    collected = await download._collect_referenced_images(bot, event)
    sources = [await download._resolve_image_source(bot, image) for image in collected.images]

    assert sources == [
        "https://example.test/a.jpg",
        "https://example.test/opaque.jpg",
        "https://example.test/b.png",
    ]
    assert collected.errors == []
    assert ("get_forward_msg", {"id": "outer"}) in bot.calls
    assert ("get_forward_msg", {"id": "inner"}) in bot.calls


@pytest.mark.asyncio
async def test_fetches_quoted_message_when_reply_has_no_embedded_content():
    bot = FakeBot()
    event = SimpleNamespace(
        message=[FakeSegment("reply", {"id": "quoted"})],
        reply=SimpleNamespace(message_id="quoted", message=None),
    )

    collected = await download._collect_referenced_images(bot, event)

    assert len(collected.images) == 3
    assert bot.calls[0] == ("get_msg", {"message_id": "quoted"})
    assert ("get_forward_msg", {"id": "outer"}) in bot.calls


@pytest.mark.asyncio
async def test_downloads_and_deduplicates_images_by_content(tmp_path, monkeypatch):
    existing_body = b"\xff\xd8\xffexisting"
    new_body = b"\x89PNG\r\n\x1a\nnew-image"
    existing_digest = hashlib.sha256(existing_body).hexdigest()
    store = PolicyStore(tmp_path / "policy.sqlite3")
    store.initialize(AppSettings(db_path=tmp_path / "policy.sqlite3", admins=(1,)))
    store.record_download_image(
        sha256=existing_digest,
        object_key=f"20260816/{existing_digest}.jpg",
        content_type="image/jpeg",
        size_bytes=len(existing_body),
        downloaded_date="20260816",
    )
    storage = FakeStorage()

    bodies = {
        "existing": existing_body,
        "new": new_body,
        "invalid": b"not-an-image",
    }
    monkeypatch.setattr(download, "_read_image_source", lambda source: bodies[source])

    stats = await download._download_image_sources(
        ["existing", "new", "invalid"],
        storage=storage,
        store=store,
        day="20260817",
    )

    new_digest = hashlib.sha256(new_body).hexdigest()
    assert stats.total == 3
    assert stats.succeeded == 1
    assert stats.skipped == 1
    assert stats.failed == 1
    assert storage.objects[f"20260817/{new_digest}.png"] == new_body


@pytest.mark.asyncio
async def test_concurrent_duplicate_sources_create_one_object(tmp_path, monkeypatch):
    body = b"\x89PNG\r\n\x1a\nsame-image"
    store = PolicyStore(tmp_path / "policy.sqlite3")
    store.initialize(AppSettings(db_path=tmp_path / "policy.sqlite3", admins=(1,)))
    storage = FakeStorage()
    monkeypatch.setattr(download, "_read_image_source", lambda source: body)

    stats = await download._download_image_sources(
        ["first", "second"],
        storage=storage,
        store=store,
        day="20260817",
    )

    assert stats.succeeded == 1
    assert stats.skipped == 1
    assert len(storage.objects) == 1


@pytest.mark.asyncio
async def test_sends_progress_before_storage_and_download(monkeypatch):
    events: list[str] = []
    matcher = FakeMatcher()
    original_send = matcher.send

    async def tracked_send(message: str) -> None:
        events.append("progress")
        await original_send(message)

    matcher.send = tracked_send
    storage = FakeStorage(events)
    store = SimpleNamespace(
        is_admin=lambda user_id: True,
        get_download_image_by_hash=lambda digest: None,
        record_download_image=lambda **kwargs: ({"object_key": kwargs["object_key"]}, True),
    )

    async def collected(bot, event):
        return download._CollectedImages(images=[{"url": "image"}], errors=[])

    async def resolved(bot, image):
        return "image"

    def read(source):
        events.append("download")
        return b"\x89PNG\r\n\x1a\nbody"

    monkeypatch.setattr(download, "get_store", lambda: store)
    monkeypatch.setattr(download, "get_download_storage", lambda: storage)
    monkeypatch.setattr(download, "_collect_referenced_images", collected)
    monkeypatch.setattr(download, "_resolve_image_source", resolved)
    monkeypatch.setattr(download, "_read_image_source", read)

    with pytest.raises(CommandFinished):
        await download._handle_download(
            matcher,
            FakeBot(),
            SimpleNamespace(
                user_id=1,
                message=[],
                reply=SimpleNamespace(message_id="quoted", message=[]),
            ),
        )

    assert events[:3] == ["progress", "storage-ready", "download"]
    assert matcher.messages[0] == "⏳ 正在下载聊天记录中的图片，请稍候……"


def test_formats_requested_download_summary():
    result = download._format_download_result(
        download.DownloadStats(
            total=8,
            succeeded=5,
            skipped=2,
            failed=1,
            bucket="qqbot-downloads",
            downloaded_date="20260817",
        )
    )

    assert result == (
        "转发图片下载完成：共 8 张\n"
        "✅ 成功 5，⏭️ 跳过 2（已存在），❌ 失败 1\n"
        "☁️ 已保存至 MinIO：qqbot-downloads/20260817/"
    )


def test_formats_download_overview():
    assert download._format_download_overview(
        {"total": 34, "total_bytes": 66 * 1024 * 1024, "today_count": 4}
    ) == (
        "下载图片总览\n"
        "🖼 总图片：34 张\n"
        "📦 总大小：66 MB\n"
        "📅 今日新增：4 张"
    )


@pytest.mark.asyncio
async def test_download_overview_reports_unavailable_storage(monkeypatch):
    matcher = FakeMatcher()
    store = SimpleNamespace(is_admin=lambda user_id: True)
    monkeypatch.setattr(download, "get_store", lambda: store)
    monkeypatch.setattr(
        download,
        "get_download_storage",
        lambda: (_ for _ in ()).throw(download.DownloadStorageError("offline")),
    )

    with pytest.raises(CommandFinished):
        await download._handle_download_overview(matcher, SimpleNamespace(user_id=1))

    assert matcher.messages == ["查询失败：MinIO 对象存储暂不可用，请稍后重试。"]
