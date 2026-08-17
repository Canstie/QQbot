from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from types import SimpleNamespace

import nonebot
import pytest

nonebot.init()
download = importlib.import_module("qq_personal_bot.plugins.download")


class CommandFinished(Exception):
    pass


class FakeMatcher:
    def __init__(self) -> None:
        self.messages: list[str | None] = []

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


@pytest.mark.asyncio
async def test_download_is_admin_only(monkeypatch):
    matcher = FakeMatcher()
    bot = FakeBot()
    store = SimpleNamespace(is_admin=lambda user_id: False)
    monkeypatch.setattr(download, "get_store", lambda: store)

    with pytest.raises(CommandFinished):
        await download._handle_download(
            matcher,
            bot,
            SimpleNamespace(user_id=123, message=[]),
        )

    assert matcher.messages == ["权限不足：仅管理员可使用 /download。"]
    assert bot.calls == []


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
    previous_directory = tmp_path / "20260816"
    previous_directory.mkdir()
    (previous_directory / f"{existing_digest}.jpg").write_bytes(existing_body)

    bodies = {
        "existing": existing_body,
        "new": new_body,
        "invalid": b"not-an-image",
    }
    monkeypatch.setattr(download, "_read_image_source", lambda source: bodies[source])

    stats = await download._download_image_sources(
        ["existing", "new", "invalid"],
        root=tmp_path,
        day="20260817",
    )

    new_digest = hashlib.sha256(new_body).hexdigest()
    assert stats.total == 3
    assert stats.succeeded == 1
    assert stats.skipped == 1
    assert stats.failed == 1
    assert (tmp_path / "20260817" / f"{new_digest}.png").read_bytes() == new_body


def test_formats_requested_download_summary():
    result = download._format_download_result(
        download.DownloadStats(
            total=8,
            succeeded=5,
            skipped=2,
            failed=1,
            directory=Path("downloadimage/20260817"),
        )
    )

    assert result == (
        "转发图片下载完成：共 8 张\n"
        "✅ 成功 5，⏭️ 跳过 2（已存在），❌ 失败 1\n"
        "📁 已保存至当天的文件夹中：downloadimage/20260817"
    )
