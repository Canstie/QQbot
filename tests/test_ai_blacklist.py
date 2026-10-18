from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from qq_personal_bot.core.models import MessageEvent
from qq_personal_bot.core.policy import PolicyEngine
from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.settings import AppSettings

nonebot.init()
from qq_personal_bot import dsapi
from qq_personal_bot.plugins import chat, control


class CommandFinished(Exception):
    pass


class FakeMatcher:
    def __init__(self):
        self.messages = []

    async def finish(self, message=None):
        self.messages.append(message)
        raise CommandFinished


@pytest.fixture
def setup(tmp_path):
    settings = AppSettings(
        db_path=tmp_path / "blacklist.sqlite3",
        admins=(10000,),
        dsapi_api_key="test",
        per_group_seconds=0,
        per_user_per_minute=1,
    )
    store = PolicyStore(settings.db_path)
    store.initialize(settings)
    store.set_group_enabled(123, True, actor_id=10000)
    store.enable_dsapi_group(123, actor_id=10000)
    return settings, store


@pytest.fixture(autouse=True)
def isolate_game_service(monkeypatch):
    monkeypatch.setattr(chat, 'get_turtle_soup_service', lambda: SimpleNamespace(
        handle=AsyncMock(return_value=False), active=lambda group_id: None,
    ))


def test_blacklist_persists_and_cache_updates_immediately(setup):
    settings, store = setup
    assert store.get_dsapi_blocked_users() == []
    assert not store.is_dsapi_user_blocked(456)
    assert store.set_dsapi_user_blocked(456, True, actor_id=10000)
    assert store.is_dsapi_user_blocked(456)
    assert not store.set_dsapi_user_blocked(456, True, actor_id=10000)
    store.set_dsapi_user_blocked(123, True, actor_id=10000)
    users = store.get_dsapi_blocked_users()
    assert users == [123, 456]
    users.clear()
    assert store.get_dsapi_blocked_users() == [123, 456]

    restarted = PolicyStore(settings.db_path)
    restarted.initialize(settings)
    assert restarted.is_dsapi_user_blocked(456)
    assert restarted.set_dsapi_user_blocked(456, False, actor_id=10000)
    assert not restarted.is_dsapi_user_blocked(456)
    assert not restarted.set_dsapi_user_blocked(456, False, actor_id=10000)
    assert restarted.get_dsapi_blocked_users() == [123]
    with restarted._connect() as conn:
        rows = conn.execute(
            "SELECT actor_id, target, detail FROM audit_log "
            "WHERE action = 'set_dsapi_user_blocked' ORDER BY id"
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        (10000, "456", '{"blocked": true}'),
        (10000, "123", '{"blocked": true}'),
        (10000, "456", '{"blocked": false}'),
    ]


def test_upgrade_adds_blacklist_without_changing_existing_settings(setup):
    settings, store = setup
    with store._connect() as conn:
        conn.execute("DROP TABLE dsapi_blocked_users")
    upgraded = PolicyStore(settings.db_path)
    upgraded.initialize(settings)
    assert upgraded.get_dsapi_blocked_users() == []
    assert upgraded.is_group_enabled(123)
    assert upgraded.is_admin(10000)
    assert upgraded.get_dsapi_config()["enabled_groups"] == [123]


@pytest.mark.parametrize("user_id", [0, -1, 2**63])
def test_store_rejects_invalid_qq_ids(setup, user_id):
    _, store = setup
    with pytest.raises(ValueError, match="QQ 号"):
        store.set_dsapi_user_blocked(user_id, True, actor_id=10000)
    assert store.get_dsapi_blocked_users() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["456", str(MessageSegment.at(456))])
@pytest.mark.parametrize("command", ["拉黑", "解黑"])
async def test_admin_can_add_remove_by_qq_or_mention(setup, monkeypatch, command, target):
    _, store = setup
    store.set_dsapi_user_blocked(456, command == "解黑", actor_id=10000)
    monkeypatch.setattr(control, "get_store", lambda: store)
    matcher = FakeMatcher()
    args = Message(f"{command} {target}")
    with pytest.raises(CommandFinished):
        await control.handle_bot_command(matcher, SimpleNamespace(user_id=10000), args)
    assert store.is_dsapi_user_blocked(456) is (command == "拉黑")
    assert "456" in matcher.messages[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["拉黑", "解黑", "黑名单"])
async def test_non_admin_cannot_manage_blacklist(setup, monkeypatch, command):
    _, store = setup
    store.set_dsapi_user_blocked(789, True, actor_id=10000)
    monkeypatch.setattr(control, "get_store", lambda: store)
    matcher = FakeMatcher()
    parts = [command] if command == "黑名单" else [command, "789"]
    with pytest.raises(CommandFinished):
        await control._handle_bot_command(matcher, SimpleNamespace(user_id=456), parts)
    assert "Permission denied" in matcher.messages[0]
    assert store.get_dsapi_blocked_users() == [789]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parts",
    [
        ["拉黑"],
        ["解黑", "456", "789"],
        ["拉黑", "0"],
        ["解黑", "-1"],
        ["拉黑", "abc"],
        ["拉黑", "9223372036854775808"],
        ["拉黑", "[CQ:at,qq=all]"],
        ["拉黑", "４５６"],
        ["黑名单", "456"],
    ],
)
async def test_invalid_blacklist_commands_do_not_mutate(setup, monkeypatch, parts):
    _, store = setup
    monkeypatch.setattr(control, "get_store", lambda: store)
    matcher = FakeMatcher()
    with pytest.raises(CommandFinished):
        await control._handle_bot_command(matcher, SimpleNamespace(user_id=10000), parts)
    assert "用法" in matcher.messages[0] or "有效的 QQ 号" in matcher.messages[0]
    assert store.get_dsapi_blocked_users() == []


@pytest.mark.asyncio
async def test_list_and_repeated_commands(setup, monkeypatch):
    _, store = setup
    monkeypatch.setattr(control, "get_store", lambda: store)
    event = SimpleNamespace(user_id=10000)
    for parts, expected in [
        (["黑名单"], "暂无"),
        (["拉黑", "456"], "加入"),
        (["拉黑", "456"], "已在"),
        (["黑名单"], "所有群通用）\n456"),
        (["解黑", "456"], "移出"),
        (["解黑", "456"], "不在"),
    ]:
        matcher = FakeMatcher()
        with pytest.raises(CommandFinished):
            await control._handle_bot_command(matcher, event, parts)
        assert expected in matcher.messages[0]


@pytest.mark.asyncio
async def test_self_sent_blacklist_command_uses_explicit_group_send(setup, monkeypatch):
    _, store = setup
    monkeypatch.setattr(control, "get_store", lambda: store)
    matcher = FakeMatcher()
    bot = SimpleNamespace(send_group_msg=AsyncMock())
    event = SimpleNamespace(
        user_id=10000, group_id=123, raw_message=f"/bot 拉黑 {MessageSegment.at(456)}"
    )
    with pytest.raises(CommandFinished):
        await control.handle_self_sent_bot_command(matcher, bot, event)
    assert store.is_dsapi_user_blocked(456)
    bot.send_group_msg.assert_awaited_once()
    assert bot.send_group_msg.await_args.kwargs["group_id"] == 123
    assert matcher.messages == [None]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,mentions",
    [
        ("你好", [999]),
        ("普通群聊", []),
        ("评价一下", [999, 789]),
        ("评价一下", [789]),
        ("~锐评", [789]),
        ("~锐评", []),
        ("~评价一下", [789, 790]),
    ],
)
@pytest.mark.parametrize("explicit_send", [False, True])
async def test_blocked_ai_requests_are_silent_even_when_rate_limited(
    setup,
    monkeypatch,
    text,
    mentions,
    explicit_send,
):
    settings, store = setup
    engine = PolicyEngine(store)
    # Exhaust this user's allowance before blocking them; no rate-limit notice should escape.
    engine.evaluate(
        MessageEvent(
            platform="onebot.v11",
            message_id=0,
            group_id=123,
            user_id=456,
            raw_message="你好",
            is_at_bot=True,
        ),
        self_id=999,
    )
    store.set_dsapi_user_blocked(456, True, actor_id=10000)
    monkeypatch.setattr(chat, "get_store", lambda: store)
    monkeypatch.setattr(chat, "get_settings", lambda: settings)
    monkeypatch.setattr(chat, "get_policy_engine", lambda: engine)
    monkeypatch.setattr(chat, "_record_group_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat, "pending_lua_command", lambda event: None)
    monkeypatch.setattr(chat, "handle_custom_flow", lambda event: None)
    generated = [AsyncMock() for _ in range(3)]
    for name, mock in zip(
        ("generate_mention_reply", "generate_random_group_reply", "generate_roast_reply"),
        generated,
    ):
        monkeypatch.setattr(chat, name, mock)
    lua = AsyncMock()
    monkeypatch.setattr(chat, "run_lua_message", lua)
    matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock())
    bot = SimpleNamespace(self_id=999, call_api=AsyncMock(), send_group_msg=AsyncMock())
    event = SimpleNamespace(
        group_id=123,
        user_id=456,
        message_id=1,
        time=1,
        message=[{"type": "text", "data": {"text": text}}]
        + [{"type": "at", "data": {"qq": str(qq)}} for qq in mentions],
    )
    await chat._dispatch_onebot_message(matcher, bot, event, explicit_group_send=explicit_send)
    for mock in generated + [lua, matcher.send, matcher.finish, bot.call_api, bot.send_group_msg]:
        mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_unblock_restores_mentions_and_other_users_remain_allowed(setup, monkeypatch):
    settings, store = setup
    store.set_dsapi_user_blocked(456, True, actor_id=10000)
    engine = PolicyEngine(store)
    monkeypatch.setattr(chat, "get_store", lambda: store)
    monkeypatch.setattr(chat, "get_settings", lambda: settings)
    monkeypatch.setattr(chat, "get_policy_engine", lambda: engine)
    monkeypatch.setattr(chat, "_record_group_activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(chat, "pending_lua_command", lambda event: None)
    monkeypatch.setattr(chat, "handle_custom_flow", lambda event: None)
    monkeypatch.setattr(
        chat, "run_lua_message", AsyncMock(return_value=SimpleNamespace(reply=None, stop=False))
    )
    generate = AsyncMock(return_value="你好呀")
    monkeypatch.setattr(chat, "generate_mention_reply", generate)
    bot = SimpleNamespace(self_id=999)
    for user_id in [789, 456]:
        if user_id == 456:
            store.set_dsapi_user_blocked(456, False, actor_id=10000)
        event = SimpleNamespace(
            group_id=123,
            user_id=user_id,
            message_id=user_id,
            time=1,
            message=[
                {"type": "at", "data": {"qq": "999"}},
                {"type": "text", "data": {"text": "你好"}},
            ],
        )
        matcher = SimpleNamespace(finish=AsyncMock())
        await chat._dispatch_onebot_message(matcher, bot, event)
        matcher.finish.assert_awaited_once_with("你好呀")
    assert generate.await_count == 2


def test_blacklist_leaves_non_ai_commands_usable(setup):
    _, store = setup
    store.set_dsapi_user_blocked(456, True, actor_id=10000)
    engine = PolicyEngine(store)
    event = MessageEvent(
        platform="onebot.v11", message_id=1, group_id=123, user_id=456, raw_message="~今日天气 上海"
    )
    decision = engine.evaluate(event, self_id=999)
    assert decision.allowed
    assert decision.handler == "default"


@pytest.mark.asyncio
async def test_ai_generators_ignore_blocked_users_before_lookup_context_or_model(
    setup, monkeypatch
):
    settings, store = setup
    store.set_dsapi_user_blocked(456, True, actor_id=10000)
    prompt = AsyncMock()
    request = Mock()
    sticker = Mock()
    monkeypatch.setattr(dsapi, "build_mention_prompt", prompt)
    monkeypatch.setattr(dsapi, "_request_chat_completion_with_fallback", request)
    monkeypatch.setattr(dsapi, "_pick_random_sticker", sticker)
    for mention in [True, False]:
        event = MessageEvent(
            platform="onebot.v11",
            message_id=1,
            group_id=123,
            user_id=456,
            raw_message="你好",
            is_at_bot=mention,
        )
        response = (
            await dsapi.generate_mention_reply(SimpleNamespace(), event, settings, store)
            if mention
            else await dsapi.generate_random_group_reply(event, settings, store)
        )
        assert response is None
    assert store.get_dsapi_group_context(123) == []
    assert store.get_dsapi_chat_history(123, history_turns=2) == []
    prompt.assert_not_awaited()
    request.assert_not_called()
    sticker.assert_not_called()
