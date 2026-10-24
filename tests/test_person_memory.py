from __future__ import annotations

import json
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qq_personal_bot import ai_client, dsapi
from qq_personal_bot.core.models import MessageEvent
from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.identity import person_context_ids
from qq_personal_bot.memory_graph import parse_claims
from qq_personal_bot.person_memory import build_person_memory_guidance
from qq_personal_bot.settings import AppSettings


@pytest.fixture
def context(tmp_path):
    settings = AppSettings(db_path=tmp_path / "people.sqlite3", admins=(), dsapi_api_key="test")
    store = PolicyStore(settings.db_path)
    store.initialize(settings)
    store.set_group_enabled(123, True, actor_id=0)
    store.set_memory_groups([123], actor_id=0)
    store.set_dsapi_config(
        enabled=True, enabled_groups=[123], knowledge_enabled=True, knowledge_prompt="像群友一样说话",
        history_turns=2, clear_history=False, actor_id=0,
    )
    knowledge_id = store.get_dsapi_config()["active_knowledge_id"]
    knowledge = store.update_dsapi_knowledge_base(
        knowledge_id, name="测试角色", prompt="像群友一样说话", actor_id=0,
        relationship_memory_enabled=True,
        similar_examples=1, style_examples=[{"topic": "舞萌", "response": "今天也出勤吗"}],
    )
    for user, summary in [(789, "和小红聊过做饭"), (456, "和小明互称队友"), (111, "旧话题的互动摘要")]:
        store.upsert_dsapi_relationship_memory(
            knowledge_id=knowledge_id, user_id=user, group_id=321, summary=summary,
        )
    store.add_person_alias(789, "小红", actor_id=0)
    store.add_person_alias(456, "小明", actor_id=0)
    store.add_person_alias(111, "旧话题人物", actor_id=0)
    store.record_memory_messages([
        {"group_id": 123, "user_id": user, "message_id": index, "timestamp": time.time() - 1000,
         "raw_message": text, "display_name": f"成员{user}"}
        for index, (user, text) in enumerate(
            [(789, "我喜欢做饭"), (456, "我喜欢舞萌"), (111, "我喜欢打球")], start=1,
        )
    ])
    batch = store.claim_memory_batch(123, force=True)
    claims = [
        {"user_id": message["user_id"], "predicate": "preference", "object_type": "topic",
         "object_key": message["content"][3:], "object_label": message["content"][3:],
         "statement": message["content"], "certainty": "stated", "source_ids": [message["id"]]}
        for message in batch["messages"]
    ]
    assert store.save_memory_batch(batch, parse_claims(json.dumps({"claims": claims}), batch))
    return store, settings, knowledge


def profiles(guidance):
    return json.loads(guidance.splitlines()[1])["people"]


def guidance(store, knowledge, **kwargs):
    options = {"group_id": 123, "current_user_id": 789, "reference_text": "小明喜欢什么"}
    options.update(kwargs)
    return build_person_memory_guidance(store, knowledge, **options)


def test_identity_summary_and_sourced_graph_are_joined_without_changing_storage(context):
    store, _, knowledge = context
    before = store.get_dsapi_relationship_memory(knowledge["id"], 456)
    people = profiles(guidance(store, knowledge))
    assert [person["user_id"] for person in people] == [789, 456]
    assert [person["role"] for person in people] == ["current_speaker", "discussed_person"]
    assert people[1]["aliases"] == ["小明"]
    assert people[1]["interaction_memory"]["summary"] == "和小明互称队友"
    assert people[1]["interaction_memory"]["last_group_id"] == 321
    graph = people[1]["group_graph"]
    assert graph["group_id"] == 123 and graph["user_id"] == 456
    assert graph["edges"][0]["statement"] == "我喜欢舞萌"
    assert graph["edges"][0]["evidence"][0]["user_id"] == 456
    assert store.get_dsapi_relationship_memory(knowledge["id"], 456) == before
    assert store.get_dsapi_config()["active_knowledge"]["relationship_count"] == 3
    assert all("摘要" not in edge["statement"] for edge in graph["edges"])


def test_real_mention_fetches_people_without_aliases(context):
    store, _, knowledge = context
    store.remove_person_alias(456, actor_id=0)
    people = profiles(guidance(
        store, knowledge, reference_text="她喜欢什么", mentioned_user_ids=(456,),
    ))
    assert people[1]["user_id"] == 456 and people[1]["aliases"] == []
    assert people[1]["interaction_memory"]["summary"] == "和小明互称队友"
    assert people[1]["group_graph"]["edges"]


def test_ambiguous_name_does_not_fetch_either_candidate_but_real_at_does(context):
    store, _, knowledge = context
    store.add_person_alias(111, "小明", actor_id=0)
    assert [p["user_id"] for p in profiles(guidance(store, knowledge))] == [789]
    assert [p["user_id"] for p in profiles(guidance(
        store, knowledge, mentioned_user_ids=(456,),
    ))] == [789, 456]


def test_real_mentions_have_priority_and_people_are_deduplicated_and_bounded(context):
    store, _, _ = context
    assert person_context_ids(
        store, current_user_id=789, text="小明和旧话题人物", mentioned_user_ids=(222, 222, 333),
    ) == (789, 222, 333)
    assert person_context_ids(store, current_user_id=456, text="小明 小明") == (456,)


@pytest.mark.parametrize("group_id", [321, None])
def test_graph_never_crosses_groups_and_summary_scope_is_explicit(context, group_id):
    store, _, knowledge = context
    result = guidance(store, knowledge, group_id=group_id)
    people = profiles(result)
    assert people and all("group_graph" not in person for person in people)
    assert all("interaction_memory" in person for person in people)
    assert "跨群" in result and "最后更新" in result


@pytest.mark.parametrize("knowledge_mode", ["off", "empty", "another_role"])
def test_disabled_or_other_role_summary_is_not_loaded_but_graph_still_works(context, knowledge_mode):
    store, _, knowledge = context
    if knowledge_mode == "off":
        knowledge = {**knowledge, "relationship_memory_enabled": False}
    elif knowledge_mode == "empty":
        knowledge = {}
    else:
        knowledge = store.create_dsapi_knowledge_base(
            name="另一个角色", prompt="其他设定", actor_id=0, relationship_memory_enabled=True,
        )
    people = profiles(guidance(store, knowledge))
    assert all("interaction_memory" not in person for person in people)
    assert all(person["group_graph"]["group_id"] == 123 for person in people)


def test_graph_switch_is_independent_of_relationship_switch(context):
    store, _, knowledge = context
    store.set_feature_enabled("ai.roast", False)
    assert all("group_graph" not in p for p in profiles(guidance(store, knowledge)))
    assert guidance(store, {**knowledge, "relationship_memory_enabled": False}) == ""
    store.set_feature_enabled("ai.roast", True)
    store.set_memory_groups([], actor_id=0)
    assert all("group_graph" not in p for p in profiles(guidance(store, knowledge)))


def test_clearing_summary_or_graph_takes_effect_without_rebuilding_a_person_profile(context):
    store, _, knowledge = context
    store.clear_dsapi_relationship_memory(knowledge["id"], actor_id=0)
    people = profiles(guidance(store, knowledge))
    assert all("interaction_memory" not in person for person in people)
    assert all(person["group_graph"]["edges"] for person in people)
    store.clear_memory(123, actor_id=0)
    assert guidance(store, knowledge) == ""


@pytest.mark.parametrize("mode", ["text", "web", "vision", "random"])
@pytest.mark.asyncio
async def test_reply_paths_share_person_memory_and_ignore_old_discussion_targets(
    context, monkeypatch, mode,
):
    store, settings, knowledge = context
    store.remove_person_alias(456, actor_id=0)
    if mode == "web":
        store.update_dsapi_knowledge_base(
            knowledge["id"], name=knowledge["name"], prompt=knowledge["prompt"],
            actor_id=0, web_search_enabled=True,
        )
    store.add_person_alias(999, "机器人别名", actor_id=0)
    captured = []

    def complete(settings, messages, **kwargs):
        captured.append(messages[0]["content"])
        return "她说过喜欢舞萌。"

    async def web(complete, system, history, prompt, **kwargs):
        captured.append(system)
        return "她说过喜欢舞萌。"

    prompt = "她喜欢舞萌吗"
    if mode == "vision":
        prompt = [{"type": "text", "text": prompt},
                  {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]
    monkeypatch.setattr(dsapi, "build_mention_prompt", AsyncMock(return_value=prompt))
    monkeypatch.setattr(dsapi, "_refresh_relationship_memory", AsyncMock())
    monkeypatch.setattr(ai_client, "complete_text", complete)
    monkeypatch.setattr(dsapi, "generate_web_reply", web)
    monkeypatch.setattr(dsapi, "_recent_group_context", lambda *args, **kwargs: [
        {"user_id": 111, "content": "旧话题人物喜欢什么"},
    ])
    event = MessageEvent(
        platform="onebot.v11", message_id=20, group_id=123, user_id=789,
        raw_message="[CQ:at,qq=999][CQ:at,qq=456]她喜欢舞萌吗", is_at_bot=mode != "random",
        segments=({"type": "at", "data": {"qq": "999"}},
                  {"type": "at", "data": {"qq": "456"}}) if mode != "random" else (),
    )
    if mode == "random":
        # A random reply has no bot mention; a configured alias resolves the discussion target.
        store.add_person_alias(456, "小明", actor_id=0)
        event = replace(event, raw_message="小明喜欢舞萌吗")
        monkeypatch.setattr(dsapi, "_random_reply_selected", lambda *args, **kwargs: True)
        monkeypatch.setattr(dsapi, "_pick_random_sticker", lambda *args, **kwargs: None)
        response = await dsapi.generate_random_group_reply(event, settings, store)
    else:
        response = await dsapi.generate_mention_reply(SimpleNamespace(self_id="999"), event, settings, store)
    assert response == "她说过喜欢舞萌。" and len(captured) == 1
    memory_block = captured[0].split("按 QQ 关联的人物记忆", 1)[1]
    people = profiles(memory_block)
    assert [person["user_id"] for person in people] == [789, 456]
    assert people[1]["interaction_memory"]["summary"] == "和小明互称队友"
    assert people[1]["group_graph"]["edges"][0]["statement"] == "我喜欢舞萌"
    assert captured[0].count("和小明互称队友") == 1
    assert "旧话题的互动摘要" not in captured[0]
    assert "我喜欢打球" not in captured[0]
    if mode == "vision":
        assert "本轮包含图片" in captured[0]


@pytest.mark.asyncio
async def test_relationship_update_keeps_discussion_subjects_and_ai_assertions_separate(context, monkeypatch):
    store, settings, knowledge = context
    captured = []

    def complete(settings, messages, **kwargs):
        captured.append(messages)
        return "和小红聊过做饭。"

    monkeypatch.setattr(ai_client, "complete_text", complete)
    await dsapi._refresh_relationship_memory(
        settings, store, knowledge_id=knowledge["id"], group_id=123, user_id=789,
        user_message="小明喜欢舞萌吗", assistant_message="小明喜欢舞萌。",
    )
    assert "他人的事实" in captured[0][0]["content"]
    assert "AI 回复" in captured[0][0]["content"] and "不能作为" in captured[0][0]["content"]
    assert store.get_dsapi_relationship_memory(knowledge["id"], 789)["summary"] == "和小红聊过做饭。"
    assert store.get_dsapi_relationship_memory(knowledge["id"], 456)["summary"] == "和小明互称队友"
