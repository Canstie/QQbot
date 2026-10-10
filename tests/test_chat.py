from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qq_personal_bot.activity import close_group_activity
from qq_personal_bot.core.models import MessageEvent, PolicyDecision
from qq_personal_bot.core.policy import PolicyEngine
from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.miniapp import (
    CachedMiniAppImages,
    MiniAppImageSource,
    XiaoheiheCaptchaRequired,
)
from qq_personal_bot.plugins import chat
from qq_personal_bot.settings import AppSettings
from qq_personal_bot.xiaoheihe_captcha import get_xiaoheihe_captcha_store


def make_event(*, group_id: int = 123, raw_message: str = "~抽群老婆"):
    return SimpleNamespace(group_id=group_id, raw_message=raw_message, message=raw_message)


@pytest.mark.asyncio
async def test_quote_capture_still_runs_when_daily_activity_is_off(tmp_path, monkeypatch):
    db_path = tmp_path / "qqbot.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=()))
    store.set_group_enabled(123, True, actor_id=0)
    store.set_feature_enabled("activity.record", False)
    store.set_memory_groups([123], actor_id=0)
    monkeypatch.setattr(chat, "get_store", lambda: store)

    for message_id in range(1, 4):
        content = f"我说的第 {message_id} 句话"
        chat._record_group_activity(
            MessageEvent(
                platform="onebot.v11",
                group_id=123,
                user_id=456,
                message_id=message_id,
                raw_message=content,
                segments=({"type": "text", "data": {"text": content}},),
            ),
            self_id=999,
        )
    await close_group_activity()
    assert len(store.get_memory_messages(123, 456)) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["prefix", "mention", "evaluation", "lua_pending", "flow_pending"])
async def test_bot_requests_do_not_enter_memory_but_remain_in_daily_archive(tmp_path, monkeypatch, kind):
    db_path = tmp_path / "memory.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=()))
    store.set_group_enabled(123, True, actor_id=0)
    store.set_memory_groups([123], actor_id=0)
    store.set_feature_enabled("activity.record", True)
    monkeypatch.setattr(chat, "get_store", lambda: store)
    raw = "~总结" if kind == "prefix" else "帮我看一下"
    segments = ()
    if kind == "evaluation":
        raw = "评价一下"
        segments = ({"type": "text", "data": {"text": raw}},
                    {"type": "at", "data": {"qq": 789}})
    if kind == "lua_pending":
        store.set_lua_state("lua_pending_command", "123:456", "存典")
    if kind == "flow_pending":
        store.set_lua_state("custom_input_flow", "123:456",
                            json.dumps({"type": "menu", "updated_at": time.time()}))
    chat._record_group_activity(
        MessageEvent(platform="onebot.v11", group_id=123, user_id=456, message_id=1,
                     raw_message=raw, segments=segments, is_at_bot=kind == "mention",
                     timestamp=time.time()), self_id=999,
    )
    # Ordinary conversations remain eligible, including words that trigger automatic replies.
    chat._record_group_activity(
        MessageEvent(platform="onebot.v11", group_id=123, user_id=789, message_id=2,
                     raw_message="我今天去机厅出勤了", timestamp=time.time()), self_id=999,
    )
    await close_group_activity()
    assert store.get_memory_messages(123, 456) == []
    assert len(store.get_memory_messages(123, 789)) == 1
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM group_daily_messages WHERE group_id=123").fetchone()[0] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("text,handler", [
    ("锐评", "mention"), ("锐评一下", "default"),
    ("评价一下他的游戏习惯", "person_evaluation"),
    ("批判一下", "default"), ("你怎么看", "mention"),
])
@pytest.mark.parametrize("gate", [None, "ai.master", "ai.roast", "api", "chat_group", "memory_group"])
async def test_roast_command_uses_mentioned_target_in_same_group(monkeypatch, text, handler, gate):
    from unittest.mock import AsyncMock

    internal = SimpleNamespace(
        group_id=123,
        user_id=456,
        message_id=99,
        raw_message=text,
        is_at_bot=handler == "mention",
        segments=(
            {"type": "at", "data": {"qq": "999"}},
            {"type": "text", "data": {"text": text}},
            {"type": "at", "data": {"qq": "789"}},
            {"type": "at", "data": {"qq": "789"}},
            {"type": "at", "data": {"qq": "all"}},
        ),
    )
    store = SimpleNamespace(
        is_feature_enabled=lambda feature_id: feature_id != gate,
        get_dsapi_config=lambda: {
            "enabled": gate != "api", "enabled_groups": [] if gate == "chat_group" else [123],
        },
        is_memory_group_enabled=lambda group_id: gate != "memory_group" and group_id == 123,
    )
    monkeypatch.setattr(chat, "onebot_to_internal", lambda event, self_id: internal)
    monkeypatch.setattr(chat, "_record_group_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat, "extract_miniapp_image_source", lambda segments: None)
    monkeypatch.setattr(chat, "pending_lua_command", lambda event: None)
    monkeypatch.setattr(chat, "handle_custom_flow", lambda event: None)
    monkeypatch.setattr(chat, "get_store", lambda: store)
    monkeypatch.setattr(chat, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(
        chat,
        "get_policy_engine",
        lambda: SimpleNamespace(
            evaluate=lambda event, self_id: PolicyDecision(
                True, "ok", handler=handler, normalized_message=text
            )
        ),
    )
    flush = AsyncMock()
    generate = AsyncMock(return_value="从现有聊天看，他对游戏有持续兴趣；生活情况资料不足。")
    finish = AsyncMock()
    monkeypatch.setattr(chat, "flush_group_activity", flush)
    monkeypatch.setattr(chat, "generate_roast_reply", generate)
    monkeypatch.setattr(chat, "_finish_with_response", finish)

    await chat._dispatch_onebot_message(
        SimpleNamespace(), SimpleNamespace(self_id=999), SimpleNamespace()
    )

    if gate is not None:
        flush.assert_not_awaited()
        generate.assert_not_awaited()
        assert finish.call_args.args[3] == "本群尚未开启人物图谱评价。"
        return
    flush.assert_awaited_once()
    generate.assert_awaited_once()
    assert generate.call_args.kwargs["group_id"] == 123
    assert generate.call_args.kwargs["target_user_id"] == 789
    assert generate.call_args.kwargs["request_text"] == text
    assert finish.call_args.args[3] == generate.return_value


@pytest.mark.asyncio
@pytest.mark.parametrize("parts,target,request_text", [
    (["评价一下他的游戏习惯", 789], 789, "评价一下他的游戏习惯"),
    (["你对", 789, "有什么看法"], 789, "你对有什么看法"),
    ([789, "~批判一下"], 789, "批判一下"),
    (["评价一下", 789, 790], None, None),
    (["~锐评"], None, None),
])
async def test_person_evaluation_routes_real_onebot_mentions(
    tmp_path, monkeypatch, parts, target, request_text
):
    settings = AppSettings(db_path=tmp_path / "evaluation.sqlite3", admins=(), dsapi_api_key="test")
    store = PolicyStore(settings.db_path)
    store.initialize(settings)
    store.set_group_enabled(123, True, actor_id=0)
    store.set_memory_groups([123], actor_id=0)
    store.set_dsapi_config(enabled=True, enabled_groups=[123], knowledge_enabled=False,
                           knowledge_prompt="", history_turns=2, clear_history=False, actor_id=0)
    event = SimpleNamespace(
        group_id=123, user_id=456, message_id=99, time=1,
        message=[
            {"type": "at", "data": {"qq": str(part)}} if isinstance(part, int)
            else {"type": "text", "data": {"text": part}}
            for part in parts
        ],
    )
    monkeypatch.setattr(chat, "get_store", lambda: store)
    monkeypatch.setattr(chat, "get_settings", lambda: settings)
    monkeypatch.setattr(chat, "get_policy_engine", lambda: PolicyEngine(store))
    monkeypatch.setattr(chat, "_record_group_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat, "pending_lua_command", lambda event: None)
    monkeypatch.setattr(chat, "handle_custom_flow", lambda event: None)
    monkeypatch.setattr(chat, "flush_group_activity", AsyncMock())
    generate = AsyncMock(return_value="有依据的评价")
    finish = AsyncMock()
    monkeypatch.setattr(chat, "generate_roast_reply", generate)
    monkeypatch.setattr(chat, "_finish_with_response", finish)

    await chat._dispatch_onebot_message(SimpleNamespace(), SimpleNamespace(self_id=999), event)

    if target is None:
        generate.assert_not_awaited()
        assert "请一次 @一位群友" in finish.call_args.args[3]
    else:
        generate.assert_awaited_once_with(group_id=123, target_user_id=target, settings=settings,
                                          store=store, request_text=request_text)
        assert finish.call_args.args[3] == "有依据的评价"


@pytest.mark.asyncio
@pytest.mark.parametrize("reason,handler", [("ok", "mention"), ("no_trigger", None)])
async def test_quoting_bot_feature_image_does_not_call_ai(monkeypatch, reason, handler):
    internal = MessageEvent(
        platform="onebot.v11",
        group_id=123,
        user_id=456,
        message_id=1,
        raw_message="哈哈",
        is_at_bot=True,
        segments=(
            {"type": "reply", "data": {
                "id": 9,
                "user_id": 999,
                "message": [{"type": "image", "data": {"file": "classic.jpg"}}],
            }},
            {"type": "text", "data": {"text": "哈哈"}},
        ),
    )
    monkeypatch.setattr(chat, "onebot_to_internal", lambda event, self_id: internal)
    monkeypatch.setattr(chat, "_record_group_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat, "extract_miniapp_image_source", lambda segments: None)
    monkeypatch.setattr(chat, "pending_lua_command", lambda event: None)
    monkeypatch.setattr(chat, "handle_custom_flow", lambda event: None)
    monkeypatch.setattr(
        chat,
        "get_policy_engine",
        lambda: SimpleNamespace(
            evaluate=lambda event, self_id: PolicyDecision(
                reason == "ok", reason, handler=handler, normalized_message="哈哈"
            )
        ),
    )
    lua = AsyncMock(return_value=SimpleNamespace(reply=None, stop=False))
    mention = AsyncMock()
    random = AsyncMock()
    monkeypatch.setattr(chat, "run_lua_message", lua)
    monkeypatch.setattr(chat, "generate_mention_reply", mention)
    monkeypatch.setattr(chat, "generate_random_group_reply", random)

    await chat._dispatch_onebot_message(
        SimpleNamespace(), SimpleNamespace(self_id=999), SimpleNamespace()
    )

    mention.assert_not_awaited()
    random.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_at_can_ask_about_quoted_bot_image():
    bot = SimpleNamespace(self_id=999)
    quoted_image = {"type": "reply", "data": {
        "user_id": 999,
        "message": [{"type": "image", "data": {"file": "classic.jpg"}}],
    }}
    event = SimpleNamespace(segments=(quoted_image, {"type": "at", "data": {"qq": "999"}}))
    assert await chat._is_passive_bot_image_reply(bot, event) is False
    other_user_image = {"type": "reply", "data": {
        "user_id": 456,
        "message": [{"type": "image", "data": {"file": "user.jpg"}}],
    }}
    assert await chat._is_passive_bot_image_reply(
        bot, SimpleNamespace(segments=(other_user_image,))
    ) is False


@pytest.mark.asyncio
async def test_quoted_bot_image_can_be_identified_via_get_msg():
    bot = SimpleNamespace(
        self_id=999,
        call_api=AsyncMock(return_value={
            "user_id": 999,
            "message": [{"type": "image", "data": {"file": "classic.jpg"}}],
        }),
    )
    event = SimpleNamespace(segments=({"type": "reply", "data": {"id": 9}},))
    assert await chat._is_passive_bot_image_reply(bot, event) is True
    bot.call_api.assert_awaited_once_with("get_msg", message_id=9)


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
@pytest.mark.parametrize("command,count", [("涩图", None), ("涩图 2", 2), ("涩图 40", 40)])
async def test_gallery_command_policy_and_delivery(monkeypatch, tmp_path, reason, explicit_send, command, count):
    from unittest.mock import AsyncMock
    event = SimpleNamespace(segments=(), group_id=123, is_at_bot=False)
    decision = PolicyDecision(reason == "ok", reason, handler="default", normalized_message=command)
    monkeypatch.setattr(chat, "onebot_to_internal", lambda event, self_id: event)
    monkeypatch.setattr(chat, "_record_group_activity", lambda *a, **kw: None)
    monkeypatch.setattr(chat, "pending_lua_command", lambda event: None)
    monkeypatch.setattr(chat, "handle_custom_flow", lambda event: None)
    monkeypatch.setattr(chat, "get_policy_engine", lambda: SimpleNamespace(evaluate=lambda *a, **kw: decision))
    nodes = [{"type": "node", "data": {"content": []}}]

    async def gallery_images(send):
        await send(tmp_path / "first.png")
        await send(tmp_path / "second.gif")

    async def gallery_forward(send, self_id, requested):
        assert self_id == "456" and requested == count
        await send(nodes)

    image_query = AsyncMock(side_effect=gallery_images)
    forward_query = AsyncMock(side_effect=gallery_forward)
    monkeypatch.setattr(chat, "send_random_gallery", image_query)
    monkeypatch.setattr(chat, "send_random_gallery_forward", forward_query)
    lua = AsyncMock(side_effect=AssertionError("gallery must finish before Lua"))
    monkeypatch.setattr(chat, "run_lua_message", lua)
    matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock())
    bot = SimpleNamespace(self_id=456, send_group_msg=AsyncMock(), call_api=AsyncMock())
    await chat._handle_onebot_message(matcher, bot, event, explicit_group_send=explicit_send)
    if reason == "ok" and count is None:
        image_query.assert_awaited_once()
        forward_query.assert_not_awaited()
        responses = ([call.kwargs["message"] for call in bot.send_group_msg.call_args_list]
                     if explicit_send else [call.args[0] for call in matcher.send.call_args_list])
        assert [response.type for response in responses] == ["image", "image"]
        assert responses[0].data["file"] == (tmp_path / "first.png").resolve().as_uri()
        bot.call_api.assert_not_awaited()
    elif reason == "ok":
        forward_query.assert_awaited_once()
        image_query.assert_not_awaited()
        bot.call_api.assert_awaited_once_with(
            "send_group_forward_msg", group_id=123, messages=nodes, _timeout=600
        )
        matcher.send.assert_not_awaited()
        bot.send_group_msg.assert_not_awaited()
    else:
        image_query.assert_not_awaited()
        forward_query.assert_not_awaited()
        bot.call_api.assert_not_awaited()
        matcher.send.assert_not_awaited()
        bot.send_group_msg.assert_not_awaited()
    lua.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["涩图 0", "涩图 41", "涩图 100", "涩图 abc", "涩图 1 2"])
async def test_gallery_command_rejects_invalid_count(monkeypatch, command):
    from unittest.mock import AsyncMock

    event = SimpleNamespace(segments=(), group_id=123, is_at_bot=False)
    decision = PolicyDecision(True, "ok", handler="default", normalized_message=command)
    monkeypatch.setattr(chat, "onebot_to_internal", lambda event, self_id: event)
    monkeypatch.setattr(chat, "_record_group_activity", lambda *a, **kw: None)
    monkeypatch.setattr(chat, "pending_lua_command", lambda event: None)
    monkeypatch.setattr(chat, "handle_custom_flow", lambda event: None)
    monkeypatch.setattr(chat, "get_policy_engine", lambda: SimpleNamespace(evaluate=lambda *a, **kw: decision))
    monkeypatch.setattr(chat, "get_store", lambda: SimpleNamespace(is_feature_enabled=lambda feature: True))
    query = AsyncMock()
    monkeypatch.setattr(chat, "send_random_gallery", query)
    monkeypatch.setattr(chat, "run_lua_message", AsyncMock(side_effect=AssertionError("must stop")))
    matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock())
    bot = SimpleNamespace(self_id=456, send_group_msg=AsyncMock(), call_api=AsyncMock())

    await chat._handle_onebot_message(matcher, bot, event, explicit_group_send=False)

    query.assert_not_awaited()
    bot.call_api.assert_not_awaited()
    assert "用法：~涩图 [数量]，数量须在 1—40 之间。" in matcher.send.call_args.args[0].data["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_send", [False, True])
@pytest.mark.parametrize(
    "command,day_label,yesterday",
    [("总结", "今天", False), ("总结 昨天", "昨天", True)],
)
async def test_digest_command_sends_progress_and_generated_image(
    monkeypatch, tmp_path, explicit_send, command, day_label, yesterday
):
    from unittest.mock import AsyncMock

    event = SimpleNamespace(segments=(), group_id=123, user_id=456, is_at_bot=False)
    decision = PolicyDecision(True, "ok", handler="default", normalized_message=command)
    store = SimpleNamespace(
        is_admin=lambda user_id: user_id == 456,
        is_feature_enabled=lambda feature_id: True,
    )
    image_path = tmp_path / "digest.png"
    report = SimpleNamespace(image_path=image_path)
    monkeypatch.setattr(chat, "onebot_to_internal", lambda event, self_id: event)
    monkeypatch.setattr(chat, "_record_group_activity", lambda *a, **kw: None)
    monkeypatch.setattr(chat, "pending_lua_command", lambda event: None)
    monkeypatch.setattr(chat, "handle_custom_flow", lambda event: None)
    monkeypatch.setattr(
        chat,
        "get_policy_engine",
        lambda: SimpleNamespace(evaluate=lambda *a, **kw: decision),
    )
    monkeypatch.setattr(chat, "get_store", lambda: store)
    generate = AsyncMock(return_value=report)
    monkeypatch.setattr(chat, "generate_group_digest_report", generate)
    lua = AsyncMock(side_effect=AssertionError("digest must finish before Lua"))
    monkeypatch.setattr(chat, "run_lua_message", lua)
    matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock())
    bot = SimpleNamespace(self_id=456, send_group_msg=AsyncMock())

    await chat._handle_onebot_message(matcher, bot, event, explicit_group_send=explicit_send)

    generate.assert_awaited_once()
    assert generate.await_args.kwargs["yesterday"] is yesterday
    responses = (
        [call.kwargs["message"] for call in bot.send_group_msg.call_args_list]
        if explicit_send
        else [call.args[0] for call in matcher.send.call_args_list]
    )
    assert [response.type for response in responses] == ["text", "image"]
    assert f"正在整理{day_label}" in responses[0].data["text"]
    assert responses[1].data["file"] == image_path.as_uri()
    lua.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_send", [False, True])
async def test_digest_command_silently_ignores_non_admin_without_generation(
    monkeypatch, explicit_send
):
    from unittest.mock import AsyncMock

    event = SimpleNamespace(segments=(), group_id=123, user_id=789, is_at_bot=False)
    decision = PolicyDecision(True, "ok", handler="default", normalized_message="总结")
    store = SimpleNamespace(
        is_admin=lambda user_id: False,
        is_feature_enabled=lambda feature_id: True,
    )
    monkeypatch.setattr(chat, "onebot_to_internal", lambda event, self_id: event)
    monkeypatch.setattr(chat, "_record_group_activity", lambda *a, **kw: None)
    monkeypatch.setattr(chat, "pending_lua_command", lambda event: None)
    monkeypatch.setattr(chat, "handle_custom_flow", lambda event: None)
    monkeypatch.setattr(
        chat,
        "get_policy_engine",
        lambda: SimpleNamespace(evaluate=lambda *a, **kw: decision),
    )
    monkeypatch.setattr(chat, "get_store", lambda: store)
    generate = AsyncMock()
    monkeypatch.setattr(chat, "generate_group_digest_report", generate)
    lua = AsyncMock(side_effect=AssertionError("digest must finish before Lua"))
    monkeypatch.setattr(chat, "run_lua_message", lua)
    matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock())
    bot = SimpleNamespace(self_id=456, send_group_msg=AsyncMock())

    await chat._handle_onebot_message(matcher, bot, event, explicit_group_send=explicit_send)

    generate.assert_not_awaited()
    matcher.send.assert_not_awaited()
    matcher.finish.assert_not_awaited()
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


@pytest.mark.asyncio
async def test_multi_message_ai_reply_sends_each_part_in_order():
    from unittest.mock import AsyncMock

    matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock())
    bot = SimpleNamespace(send_group_msg=AsyncMock())
    event = SimpleNamespace(group_id=123)

    await chat._finish_ai_response(
        matcher,
        bot,
        event,
        ["第一条", "第二条", "第三条"],
        explicit_group_send=False,
    )

    assert [call.args[0] for call in matcher.send.call_args_list] == ["第一条", "第二条"]
    matcher.finish.assert_awaited_once_with("第三条")


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


@pytest.mark.asyncio
async def test_xiaoheihe_captcha_sends_verification_to_source_group_once(monkeypatch):
    from unittest.mock import AsyncMock

    captcha_store = get_xiaoheihe_captcha_store()
    captcha_store.clear()
    chat._recent_bot_outputs.clear()
    source = MiniAppImageSource(
        source_url="https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?link_id=c0687248f6da",
        platform="xiaoheihe",
    )
    internal_event = SimpleNamespace(segments=(), group_id=123, is_at_bot=False)
    store = SimpleNamespace(is_feature_enabled=lambda feature_id: True)
    monkeypatch.setattr(chat, "onebot_to_internal", lambda event, self_id: internal_event)
    monkeypatch.setattr(chat, "_record_group_activity", lambda event, self_id: None)
    monkeypatch.setattr(chat, "extract_miniapp_image_source", lambda segments: source)
    monkeypatch.setattr(chat, "_automatic_reply_allowed", lambda event: True)
    monkeypatch.setattr(chat, "get_store", lambda: store)
    monkeypatch.setattr(
        chat,
        "get_settings",
        lambda: SimpleNamespace(public_base_url="https://bot.example.com/qqbot"),
    )
    monkeypatch.setattr(chat, "pending_lua_command", lambda event: None)
    monkeypatch.setattr(chat, "handle_custom_flow", lambda event: None)
    monkeypatch.setattr(
        chat,
        "get_policy_engine",
        lambda: SimpleNamespace(
            evaluate=lambda event, self_id: PolicyDecision(False, "group_not_enabled")
        ),
    )

    async def captcha_required(_source):
        raise XiaoheiheCaptchaRequired()

    monkeypatch.setattr(chat, "cache_miniapp_images", captcha_required)
    matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock())
    bot = SimpleNamespace(self_id=456, send_group_msg=AsyncMock())
    event = SimpleNamespace(group_id=123)

    await chat._handle_onebot_message(matcher, bot, event)
    await chat._handle_onebot_message(matcher, bot, event)

    bot.send_group_msg.assert_awaited_once()
    assert bot.send_group_msg.call_args.kwargs["group_id"] == 123
    notification = bot.send_group_msg.call_args.kwargs["message"]
    assert "手机系统浏览器" in notification
    assert "https://bot.example.com/qqbot/xiaoheihe-captcha/" in notification
    captcha_store.clear()
    chat._recent_bot_outputs.clear()
