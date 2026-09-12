from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from qq_personal_bot.core.models import PolicyDecision
from qq_personal_bot.miniapp import CachedMiniAppImages, MiniAppImageSource
from qq_personal_bot.plugins import chat


def make_event(*, group_id: int = 123, raw_message: str = "~抽群老婆"):
    return SimpleNamespace(group_id=group_id, raw_message=raw_message, message=raw_message)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["ok", "group_not_enabled", "group_rate_limited"])
@pytest.mark.parametrize("explicit_send", [False, True])
async def test_teacher_command_policy_and_plain_text(monkeypatch, reason, explicit_send):
    from unittest.mock import AsyncMock
    event = SimpleNamespace(segments=(), group_id=123, is_at_bot=False)
    decision = PolicyDecision(reason == "ok", reason, handler="default",
                              normalized_message="查老师 龙 模拟电子技术")
    monkeypatch.setattr(chat, "onebot_to_internal", lambda event, self_id: event)
    monkeypatch.setattr(chat, "_record_group_activity", lambda *a, **kw: None)
    monkeypatch.setattr(chat, "pending_lua_command", lambda event: None)
    monkeypatch.setattr(chat, "handle_custom_flow", lambda event: None)
    monkeypatch.setattr(chat, "get_policy_engine", lambda: SimpleNamespace(evaluate=lambda *a, **kw: decision))
    query = AsyncMock(return_value=["老师一 [CQ:at,qq=all]", "老师二 [CQ:image,file=x]"])
    lua = AsyncMock(side_effect=AssertionError("must stop before Lua or default reply"))
    monkeypatch.setattr(chat, "query_teachers", query)
    monkeypatch.setattr(chat, "run_lua_message", lua)
    matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock())
    bot = SimpleNamespace(self_id=456, send_group_msg=AsyncMock())
    await chat._handle_onebot_message(matcher, bot, event, explicit_group_send=explicit_send)
    if reason == "ok":
        query.assert_awaited_once_with("龙", "模拟电子技术")
        responses = ([call.kwargs["message"] for call in bot.send_group_msg.call_args_list]
                     if explicit_send else [call.args[0] for call in matcher.send.call_args_list])
        assert [response.type for response in responses] == ["text", "text"]
        assert [response.data["text"] for response in responses] == query.return_value
    else:
        query.assert_not_awaited()
        matcher.send.assert_not_awaited()
        bot.send_group_msg.assert_not_awaited()
    lua.assert_not_awaited()


def test_recent_bot_output_event_is_ignored():
    chat._recent_bot_outputs.clear()
    original = make_event(raw_message="~抽群老婆")
    echoed = make_event(raw_message="~抽群老婆")

    chat._remember_recent_bot_output(original, "~抽群老婆", now=100.0)

    assert chat._is_recent_bot_output_event(echoed, now=101.0) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["ok", "group_not_enabled", "group_rate_limited"])
@pytest.mark.parametrize("explicit_send", [False, True])
async def test_gallery_command_policy_and_image_send(monkeypatch, tmp_path, reason, explicit_send):
    from unittest.mock import AsyncMock
    event = SimpleNamespace(segments=(), group_id=123, is_at_bot=False)
    decision = PolicyDecision(reason == "ok", reason, handler="default", normalized_message="涩图")
    monkeypatch.setattr(chat, "onebot_to_internal", lambda event, self_id: event)
    monkeypatch.setattr(chat, "_record_group_activity", lambda *a, **kw: None)
    monkeypatch.setattr(chat, "pending_lua_command", lambda event: None)
    monkeypatch.setattr(chat, "handle_custom_flow", lambda event: None)
    monkeypatch.setattr(chat, "get_policy_engine", lambda: SimpleNamespace(evaluate=lambda *a, **kw: decision))
    async def gallery(send):
        await send(tmp_path / "first.png")
        await send(tmp_path / "second.gif")
    query = AsyncMock(side_effect=gallery)
    monkeypatch.setattr(chat, "send_random_gallery", query)
    lua = AsyncMock(side_effect=AssertionError("gallery must finish before Lua"))
    monkeypatch.setattr(chat, "run_lua_message", lua)
    matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock())
    bot = SimpleNamespace(self_id=456, send_group_msg=AsyncMock())
    await chat._handle_onebot_message(matcher, bot, event, explicit_group_send=explicit_send)
    if reason == "ok":
        query.assert_awaited_once()
        responses = ([call.kwargs["message"] for call in bot.send_group_msg.call_args_list]
                     if explicit_send else [call.args[0] for call in matcher.send.call_args_list])
        assert [response.type for response in responses] == ["image", "image"]
        assert responses[0].data["file"] == (tmp_path / "first.png").resolve().as_uri()
    else:
        query.assert_not_awaited()
        matcher.send.assert_not_awaited()
        bot.send_group_msg.assert_not_awaited()
    lua.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["ok", "group_not_enabled", "group_rate_limited", "user_rate_limited"])
@pytest.mark.parametrize("explicit_send", [False, True])
async def test_all_classics_policy_and_current_group_forward(monkeypatch, reason, explicit_send):
    from unittest.mock import AsyncMock
    event = SimpleNamespace(segments=(), group_id=123, is_at_bot=False)
    decision = PolicyDecision(reason == "ok", reason, handler="default", normalized_message="爆典all")
    monkeypatch.setattr(chat, "onebot_to_internal", lambda event, self_id: event)
    monkeypatch.setattr(chat, "_record_group_activity", lambda *a, **kw: None)
    monkeypatch.setattr(chat, "pending_lua_command", lambda event: None)
    monkeypatch.setattr(chat, "handle_custom_flow", lambda event: None)
    monkeypatch.setattr(chat, "get_policy_engine", lambda: SimpleNamespace(evaluate=lambda *a, **kw: decision))
    nodes = [{"type": "node", "data": {"content": []}}]
    async def export(group_id, self_id, send, notice):
        assert group_id == 123 and self_id == "456"
        await notice("正在整理")
        await send(nodes)
    export_mock = AsyncMock(side_effect=export)
    monkeypatch.setattr(chat, "send_all_classics", export_mock)
    lua = AsyncMock(side_effect=AssertionError("export must finish before Lua"))
    monkeypatch.setattr(chat, "run_lua_message", lua)
    matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock())
    bot = SimpleNamespace(self_id=456, send_group_msg=AsyncMock(), call_api=AsyncMock())
    await chat._handle_onebot_message(matcher, bot, event, explicit_group_send=explicit_send)
    if reason == "ok":
        export_mock.assert_awaited_once()
        bot.call_api.assert_awaited_once_with("send_group_forward_msg", group_id=123, messages=nodes, _timeout=600)
        response = (bot.send_group_msg.call_args.kwargs["message"] if explicit_send else matcher.send.call_args.args[0])
        assert response.type == "text"
    else:
        export_mock.assert_not_awaited()
        bot.call_api.assert_not_awaited()
        matcher.send.assert_not_awaited()
        bot.send_group_msg.assert_not_awaited()
    lua.assert_not_awaited()


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


def test_unquoted_lua_cq_response_is_parsed_as_image_message(tmp_path):
    image_path = (tmp_path / "help.png").resolve()
    event = SimpleNamespace(message_id=42)

    response = chat._build_lua_response(
        f"[CQ:image,file={image_path.as_uri()}]",
        event,
        quote=False,
    )

    assert not isinstance(response, str)
    assert len(response) == 1
    assert response[0].type == "image"
    assert response[0].data["file"] == image_path.as_uri()


def test_unquoted_lua_plain_text_remains_plain_text():
    response = chat._build_lua_response(
        "普通文字回复",
        SimpleNamespace(message_id=42),
        quote=False,
    )

    assert response == "普通文字回复"


def test_random_sticker_response_uses_onebot_image_segment(tmp_path):
    sticker = tmp_path / "sticker.png"
    response = chat._build_random_group_response(sticker)

    assert response.type == "image"
    assert response.data["file"] == Path(sticker).resolve().as_uri()


def test_miniapp_image_collection_response(tmp_path):
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    image_response = chat._build_miniapp_image_response(
        CachedMiniAppImages(directory=tmp_path, paths=(first, second))
    )

    assert [segment.type for segment in image_response] == ["image", "image"]
    assert image_response[0].data["file"] == first.resolve().as_uri()
    assert image_response[1].data["file"] == second.resolve().as_uri()


def test_bilibili_miniapp_is_silently_blocked_for_configured_group(monkeypatch):
    store = SimpleNamespace(is_bilibili_group_blocked=lambda group_id: group_id == 123)
    monkeypatch.setattr(chat, "get_store", lambda: store)
    bilibili = MiniAppImageSource(
        source_url="https://b23.tv/wrXwLXN",
        platform="bilibili",
    )
    xiaohongshu = MiniAppImageSource(
        source_url="https://www.xiaohongshu.com/explore/example",
        platform="xiaohongshu",
    )

    assert chat._miniapp_image_source_allowed(bilibili, SimpleNamespace(group_id=123)) is False
    assert chat._miniapp_image_source_allowed(bilibili, SimpleNamespace(group_id=456)) is True
    assert chat._miniapp_image_source_allowed(xiaohongshu, SimpleNamespace(group_id=123)) is True


@pytest.mark.asyncio
async def test_miniapp_sends_only_image_collection(monkeypatch, tmp_path):
    directory = tmp_path / "cached"
    directory.mkdir()
    first = directory / "first.jpg"
    second = directory / "second.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    cached = CachedMiniAppImages(directory=directory, paths=(first, second))
    source = MiniAppImageSource(
        source_url="https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?link_id=c0687248f6da",
    )
    internal_event = SimpleNamespace(segments=(), group_id=123)

    monkeypatch.setattr(chat, "onebot_to_internal", lambda event, self_id: internal_event)
    monkeypatch.setattr(chat, "_record_group_activity", lambda event, self_id: None)
    monkeypatch.setattr(chat, "extract_miniapp_image_source", lambda segments: source)
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

    assert len(matcher.calls) == 1
    assert matcher.calls[0][0] == "finish"
    assert [segment.type for segment in matcher.calls[0][1]] == ["image", "image"]
    assert not directory.exists()
