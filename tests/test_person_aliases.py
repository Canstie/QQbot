from __future__ import annotations

import importlib
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qq_personal_bot import ai_client, dsapi
from qq_personal_bot.core.models import MessageEvent
from qq_personal_bot.core.person_aliases import resolve_person_references
from qq_personal_bot.core.person_evaluation import evaluation_person_references
from qq_personal_bot.core.policy import PolicyEngine
from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.identity import build_identity_guidance
from qq_personal_bot.memory_graph import build_graph_guidance
from qq_personal_bot.settings import AppSettings


@pytest.fixture
def context(tmp_path):
    settings = AppSettings(
        db_path=tmp_path / "aliases.sqlite3",
        admins=(10000,),
        dsapi_api_key="test",
        per_group_seconds=0,
        per_user_per_minute=0,
    )
    store = PolicyStore(settings.db_path)
    store.initialize(settings)
    store.set_group_enabled(123, True, actor_id=10000)
    store.set_dsapi_config(
        enabled=True,
        enabled_groups=[123],
        knowledge_enabled=False,
        knowledge_prompt="",
        history_turns=2,
        clear_history=False,
        actor_id=10000,
    )
    return store, settings


def event(text="小明是谁，对应哪个QQ？", *, user_id=789, segments=()):
    return MessageEvent(
        platform="onebot.v11",
        message_id=90,
        group_id=123,
        user_id=user_id,
        raw_message=text,
        is_at_bot=True,
        segments=segments,
    )


def test_aliases_persist_and_invalidate_cached_reads(context):
    store, settings = context
    assert store.list_person_aliases() == []
    assert store.add_person_alias(456, "小明", actor_id=10000)
    assert store.add_person_alias(456, "会长", actor_id=10000)
    assert not store.add_person_alias(456, "小明", actor_id=10000)
    returned = store.list_person_aliases()
    returned[0]["alias"] = "伪造"
    assert {r["alias"] for r in store.list_person_aliases(456)} == {"小明", "会长"}
    reopened = PolicyStore(settings.db_path)
    reopened.initialize(settings)
    assert len(reopened.list_person_aliases(456)) == 2
    assert store.remove_person_alias(456, "小明", actor_id=10000) == 1
    assert store.list_person_aliases(456) == [{"user_id": 456, "alias": "会长"}]
    assert store.remove_person_alias(456, actor_id=10000) == 1
    assert store.list_person_aliases() == []


@pytest.mark.parametrize("alias", ["", "x" * 41, "a\nb", "a\x00b", "[CQ:at,qq=all]"])
def test_invalid_alias_does_not_write(context, alias):
    store, _ = context
    with pytest.raises(ValueError):
        store.add_person_alias(456, alias, actor_id=10000)
    assert store.list_person_aliases() == []


@pytest.mark.parametrize("qq", [0, -1, True, 2**63])
def test_invalid_qq_does_not_write(context, qq):
    with pytest.raises(ValueError):
        context[0].add_person_alias(qq, "小明", actor_id=10000)
    assert context[0].list_person_aliases() == []


def test_normalized_names_limit_and_ambiguous_references(context):
    store, _ = context
    store.add_person_alias(456, "Ａｌｉｃｅ", actor_id=10000)
    assert resolve_person_references(store, "ALICE今天怎么样").user_ids == (456,)
    assert not resolve_person_references(store, "malice").user_ids
    store.add_person_alias(456, "会长", actor_id=10000)
    store.add_person_alias(789, "网安会长", actor_id=10000)
    assert resolve_person_references(store, "评价网安会长").user_ids == (789,)
    store.add_person_alias(789, "Alice", actor_id=10000)
    refs = resolve_person_references(store, "评价Alice")
    assert refs.user_ids == () and refs.ambiguous_aliases
    assert not resolve_person_references(store, "QQ 14560").user_ids
    for i in range(6):
        store.add_person_alias(456, f"名字{i}", actor_id=10000)
    with pytest.raises(ValueError, match="8"):
        store.add_person_alias(456, "第九个", actor_id=10000)


def test_identity_context_resolves_both_directions_and_separates_speaker(context):
    store, _ = context
    store.add_person_alias(789, "提问者", actor_id=10000)
    store.add_person_alias(456, "小明", actor_id=10000)
    for text in ("小明对应哪个QQ", "456是谁"):
        guidance = build_identity_guidance(store, text=text, current_user_id=789)
        data = json.loads(guidance.splitlines()[1])
        assert data["current_speaker_qq"] == 789
        assert {p["user_id"] for p in data["people"]} == {456, 789}
    store.add_person_alias(111, "小明", actor_id=10000)
    data = json.loads(build_identity_guidance(store, text="小明是谁").splitlines()[1])
    assert data["ambiguous_aliases"][0]["total_matches"] == 2
    store.remove_person_alias(456, actor_id=10000)
    assert "456" not in build_identity_guidance(store, text="小明是谁")


class Finished(Exception):
    pass


class Matcher:
    def __init__(self):
        self.messages = []

    async def finish(self, message=None):
        self.messages.append(message)
        raise Finished


@pytest.fixture
def control(context, monkeypatch):
    import nonebot

    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init()
    module = importlib.import_module("qq_personal_bot.plugins.control")
    monkeypatch.setattr(module, "get_store", lambda: context[0])
    return module


@pytest.mark.asyncio
async def test_real_at_assignment_query_delete_and_self_sent_command(context, control):
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    store, _ = context
    args = MessageSegment.at(456) + MessageSegment.text("=小明 [备用]&朋友")
    matcher = Matcher()
    with pytest.raises(Finished):
        await control.handle_bot_command(matcher, SimpleNamespace(user_id=10000), Message(args))
    assert store.list_person_aliases(456) == [{"user_id": 456, "alias": "小明 [备用]&朋友"}]
    matcher = Matcher()
    with pytest.raises(Finished):
        await control._handle_bot_command(
            matcher, SimpleNamespace(user_id=10000), ["别名", "小明 [备用]&朋友"]
        )
    assert "456" in matcher.messages[0]
    bot = SimpleNamespace(send_group_msg=AsyncMock())
    with pytest.raises(Finished):
        await control.handle_self_sent_bot_command(
            Matcher(),
            bot,
            SimpleNamespace(user_id=10000, group_id=123, raw_message="/bot [CQ:at,qq=456]=小明"),
        )
    bot.send_group_msg.assert_awaited_once()
    with pytest.raises(Finished):
        await control._handle_bot_command(
            Matcher(), SimpleNamespace(user_id=10000), ["别名", "删除", "456", "小明"]
        )
    assert len(store.list_person_aliases(456)) == 1
    with pytest.raises(Finished):
        await control._handle_bot_command(Matcher(), SimpleNamespace(user_id=10000), ["456="])
    assert store.list_person_aliases(456) == []


@pytest.mark.asyncio
async def test_alias_command_requires_admin(context, control):
    matcher = Matcher()
    with pytest.raises(Finished):
        await control._handle_bot_command(matcher, SimpleNamespace(user_id=789), ["456=小明"])
    assert "Permission denied" in matcher.messages[0]
    assert context[0].list_person_aliases() == []


def test_alias_evaluations_follow_blacklist_and_explicit_at_wins(context):
    store, _ = context
    store.add_person_alias(456, "小明", actor_id=10000)
    request = replace(event("评价小明"), is_at_bot=False)
    assert PolicyEngine(store).evaluate(request, self_id=999).handler == "person_evaluation"
    store.set_dsapi_user_blocked(789, True, actor_id=10000)
    assert PolicyEngine(store).evaluate(request, self_id=999).reason == "ai_user_blocked"
    store.add_person_alias(111, "小明", actor_id=10000)
    refs = evaluation_person_references(
        replace(request, segments=({"type": "at", "data": {"qq": "456"}},)),
        store,
        self_id=999,
    )
    assert refs.user_ids == (456,) and not refs.ambiguous_aliases


@pytest.mark.asyncio
@pytest.mark.parametrize("search", [False, True])
async def test_ordinary_reply_and_search_receive_aliases_without_knowledge(
    context, monkeypatch, search
):
    store, settings = context
    store.add_person_alias(456, "小明", actor_id=10000)
    store.add_person_alias(789, "小红", actor_id=10000)
    captured = []

    def complete(settings, messages, **kwargs):
        captured.append(messages[0]["content"])
        return "小明的QQ是456。"

    async def web(complete, system, history, prompt, **kwargs):
        captured.append(system)
        return "小明的QQ是456。"

    monkeypatch.setattr(ai_client, "complete_text", complete)
    monkeypatch.setattr(dsapi, "generate_web_reply", web)
    if search:
        knowledge = store.create_dsapi_knowledge_base(
            name="搜索配置",
            prompt="",
            web_search_enabled=True,
            actor_id=10000,
        )
        store.activate_dsapi_knowledge_base(knowledge["id"], clear_history=False, actor_id=10000)
        assert store.get_dsapi_config()["knowledge_enabled"] is False
    assert await dsapi.generate_mention_reply(None, event(), settings, store)
    assert len(captured) == 1
    assert '"current_speaker_qq": 789' in captured[0]
    assert '"user_id": 456' in captured[0] and '"aliases": ["小明"]' in captured[0]


def test_graph_guidance_fetches_discussed_alias_instead_of_only_sender(context, monkeypatch):
    store, _ = context
    store.set_memory_groups([123], actor_id=10000)
    store.add_person_alias(456, "小明", actor_id=10000)
    queried = []

    def graph(group, user, **kwargs):
        queried.append(user)
        return {"user_id": user, "edges": [{"statement": "喜欢舞萌"}] if user == 456 else []}

    monkeypatch.setattr(store, "get_person_graph", graph)
    guidance = build_graph_guidance(store, 123, 789, "小明喜欢什么")
    assert queried == [789, 456] and '"user_id": 456' in guidance


@pytest.mark.asyncio
async def test_ambiguous_alias_asks_for_at_without_calling_model(context, control, monkeypatch):
    chat = importlib.import_module("qq_personal_bot.plugins.chat")
    store, _ = context
    store.add_person_alias(456, "小明", actor_id=10000)
    store.add_person_alias(111, "小明", actor_id=10000)
    finish = AsyncMock()
    monkeypatch.setattr(chat, "get_store", lambda: store)
    monkeypatch.setattr(chat, "_finish_with_response", finish)
    generate = AsyncMock()
    monkeypatch.setattr(chat, "generate_roast_reply", generate)
    internal = event("评价小明")
    ctx = chat.DispatchContext(
        None,
        SimpleNamespace(self_id=999),
        None,
        internal,
        False,
        SimpleNamespace(active=lambda group: False),
        SimpleNamespace(handler="mention", normalized_message="评价小明"),
    )
    assert await chat._handle_person_evaluation(ctx)
    assert "对应多个 QQ" in finish.call_args.args[3]
    generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_background_extraction_receives_explicit_alias_mapping(context, monkeypatch):
    from qq_personal_bot.memory_graph import refresh_memory_graph

    store, settings = context
    store.set_memory_groups([123], actor_id=10000)
    store.add_person_alias(456, "小明", actor_id=10000)
    store.record_memory_messages(
        [
            {"group_id": 123, "user_id": 456, "message_id": 12, "raw_message": "我喜欢舞萌"},
        ]
    )
    captured = []

    def complete(settings, messages, **kwargs):
        captured.append(messages[0]["content"])
        return '{"claims": []}'

    monkeypatch.setattr(ai_client, "complete_text", complete)
    assert await refresh_memory_graph(store, settings, 123, force=True)
    assert len(captured) == 1
    assert '"user_id": 456' in captured[0] and '"aliases": ["小明"]' in captured[0]
    assert "别名映射不作为发言证据" in captured[0]
