from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from qq_personal_bot.plugins import chat
from qq_personal_bot.miniapp import CachedMiniAppImages, MiniAppLink


def make_event(*, group_id: int = 123, raw_message: str = "~抽群老婆"):
    return SimpleNamespace(group_id=group_id, raw_message=raw_message, message=raw_message)


def test_recent_bot_output_event_is_ignored():
    chat._recent_bot_outputs.clear()
    original = make_event(raw_message="~抽群老婆")
    echoed = make_event(raw_message="~抽群老婆")

    chat._remember_recent_bot_output(original, "~抽群老婆", now=100.0)

    assert chat._is_recent_bot_output_event(echoed, now=101.0) is True


def test_recent_bot_output_event_expires():
    chat._recent_bot_outputs.clear()
    original = make_event(raw_message="~抽群老婆")
    echoed = make_event(raw_message="~抽群老婆")

    chat._remember_recent_bot_output(original, "~抽群老婆", now=100.0)

    assert chat._is_recent_bot_output_event(echoed, now=106.0) is False


def test_recent_bot_output_event_is_group_scoped():
    chat._recent_bot_outputs.clear()
    original = make_event(group_id=123, raw_message="~抽群老婆")
    echoed = make_event(group_id=456, raw_message="~抽群老婆")

    chat._remember_recent_bot_output(original, "~抽群老婆", now=100.0)

    assert chat._is_recent_bot_output_event(echoed, now=101.0) is False


def test_quoted_response_replies_to_original_message():
    event = SimpleNamespace(message_id=42)

    response = chat._build_quoted_response("确实。", event)

    assert response[0].type == "reply"
    assert response[0].data["id"] == "42"
    assert response.extract_plain_text() == "确实。"


def test_random_sticker_response_uses_onebot_image_segment(tmp_path):
    sticker = tmp_path / "sticker.png"
    response = chat._build_random_group_response(sticker)

    assert response.type == "image"
    assert response.data["file"] == Path(sticker).resolve().as_uri()


def test_miniapp_link_and_image_collection_are_separate_responses(tmp_path):
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    link = MiniAppLink(
        title="帖子标题",
        url="https://www.xiaohongshu.com/discovery/item/note123",
        source_url="https://www.xiaohongshu.com/discovery/item/note123?token=test",
    )

    link_response = chat.format_miniapp_link(link)
    image_response = chat._build_miniapp_image_response(
        CachedMiniAppImages(directory=tmp_path, paths=(first, second))
    )

    assert link_response == (
        "标题：帖子标题\n链接：https://www.xiaohongshu.com/discovery/item/note123"
    )
    assert [segment.type for segment in image_response] == ["image", "image"]
    assert image_response[0].data["file"] == first.resolve().as_uri()
    assert image_response[1].data["file"] == second.resolve().as_uri()


@pytest.mark.asyncio
async def test_miniapp_sends_link_before_image_collection(monkeypatch, tmp_path):
    directory = tmp_path / "cached"
    directory.mkdir()
    first = directory / "first.jpg"
    second = directory / "second.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    cached = CachedMiniAppImages(directory=directory, paths=(first, second))
    link = MiniAppLink(
        title="里昂的变化",
        url="https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?link_id=c0687248f6da",
        source_url="https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?link_id=c0687248f6da",
    )
    internal_event = SimpleNamespace(segments=(), group_id=123)

    monkeypatch.setattr(chat, "onebot_to_internal", lambda event, self_id: internal_event)
    monkeypatch.setattr(chat, "_record_group_activity", lambda event, self_id: None)
    monkeypatch.setattr(chat, "extract_miniapp_link", lambda segments: link)
    monkeypatch.setattr(chat, "_automatic_reply_allowed", lambda event: True)

    async def fake_cache(_link):
        return cached

    monkeypatch.setattr(chat, "cache_miniapp_images", fake_cache)

    class Finished(Exception):
        pass

    class FakeMatcher:
        def __init__(self):
            self.calls = []

        async def send(self, response):
            self.calls.append(("send", response))

        async def finish(self, response=None):
            self.calls.append(("finish", response))
            raise Finished

    matcher = FakeMatcher()
    bot = SimpleNamespace(self_id=456)

    with pytest.raises(Finished):
        await chat._handle_onebot_message(matcher, bot, SimpleNamespace(group_id=123))

    assert matcher.calls[0] == (
        "send",
        "标题：里昂的变化\n"
        "链接：https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?link_id=c0687248f6da",
    )
    assert matcher.calls[1][0] == "finish"
    assert [segment.type for segment in matcher.calls[1][1]] == ["image", "image"]
    assert not directory.exists()
