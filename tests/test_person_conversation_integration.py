from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from qq_personal_bot import ai_client
from qq_personal_bot.core.models import MessageEvent
from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.dsapi import generate_mention_reply, generate_roast_reply
from qq_personal_bot.memory_graph import parse_claims
from qq_personal_bot.person_conversation import PersonConversationSession
from qq_personal_bot.settings import AppSettings
from qq_personal_bot.turtle_soup import GAME_CONTEXT


@pytest.fixture
def memory(tmp_path):
    settings = AppSettings(db_path=tmp_path / "memory.sqlite3", admins=(), dsapi_api_key="test")
    store = PolicyStore(settings.db_path)
    store.initialize(settings)
    store.set_group_enabled(123, True, actor_id=0)
    store.set_memory_groups([123], actor_id=0)
    store.set_dsapi_config(enabled=True, enabled_groups=[123], knowledge_enabled=False,
                           knowledge_prompt="", history_turns=2, clear_history=False, actor_id=0)
    store.record_memory_messages([
        {"group_id": 123, "user_id": 789, "message_id": i, "raw_message": text}
        for i, text in enumerate(("我喜欢打篮球", "我周末去打篮球", "我打算明天继续练球"), 1)
    ])
    ai_client._web_tools_disabled_until.clear()
    ai_client._json_output_disabled_until.clear()
    return store, settings


def completion(content, calls=None):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
    return {"choices": [{"finish_reason": "tool_calls" if calls else "stop", "message": message}]}


def test_game_speech_keeps_fiction_context_for_person_evaluation(memory):
    store, settings = memory
    assert store.record_memory_messages([
        {"group_id": 123, "user_id": 789, "message_id": 10,
         "raw_message": "我就是故事里的凶手吗", "memory_context": GAME_CONTEXT},
    ]) == 1
    session = PersonConversationSession(store, settings, group_id=123, requester_user_id=456)
    page = session.run_tool("get_person_conversation", '{"qq":"789"}')
    message = next(m for m in page["messages"] if m["message_id"] == "10")
    assert message["memory_context"] == GAME_CONTEXT
    assert "虚构剧情不能当作本人真实经历" in session.guidance


@pytest.mark.asyncio
@pytest.mark.parametrize("web_enabled", [False, True])
async def test_mention_model_reads_person_speech_before_answering(memory, monkeypatch, web_enabled):
    store, settings = memory
    if web_enabled:
        knowledge = store.create_dsapi_knowledge_base(
            name="对话", prompt="自然聊天", actor_id=0, web_search_enabled=True,
        )
        store.activate_dsapi_knowledge_base(knowledge["id"], clear_history=False, actor_id=0)
    requests = []

    def post(_settings, body):
        requests.append(json.loads(json.dumps(body)))
        if len(requests) == 1:
            assert body["tools"][0]["function"]["name"] == "get_person_conversation"
            return completion(None, [{"type": "function", "id": "speech-1", "function": {
                "name": "get_person_conversation", "arguments": '{"qq":"789","query":"篮球"}',
            }}])
        result = json.loads(next(item["content"] for item in body["messages"] if item["role"] == "tool"))
        assert result["target_qq"] == 789 and result["group_id"] == 123
        assert [m["content"] for m in result["messages"]] == ["我喜欢打篮球", "我周末去打篮球"]
        assert not store.get_person_graph(123, 789)["edges"]  # Raw tool does not need extracted claims.
        answer = "他提过喜欢篮球。"
        return completion(json.dumps({"action": "answer", "basis": "provided_data", "answer": answer},
                                     ensure_ascii=False) if web_enabled else answer)

    monkeypatch.setattr(ai_client, "_post_chat_body", post)
    event = MessageEvent(platform="onebot.v11", group_id=123, user_id=456, message_id=20,
                         raw_message="QQ789有什么兴趣", is_at_bot=True)
    assert await generate_mention_reply(SimpleNamespace(self_id=999), event, settings, store) == "他提过喜欢篮球。"
    assert len(requests) == 2
    history = store.get_dsapi_chat_history(123, 2,
        knowledge_id=int(store.get_dsapi_config()["active_knowledge_id"] or 0))
    assert history[-1] == {"role": "assistant", "content": "他提过喜欢篮球。"}
    assert all(item["role"] in {"user", "assistant"} for item in history)


@pytest.mark.asyncio
async def test_roast_model_can_inspect_raw_speech_in_addition_to_graph(memory, monkeypatch):
    store, settings = memory
    batch = store.claim_memory_batch(123, force=True)
    ids = batch["new_ids"]
    claims = [
        {"user_id": 789, "predicate": "preference", "object_type": "topic", "object_key": "篮球",
         "object_label": "篮球", "statement": "说喜欢篮球", "certainty": "stated", "source_ids": ids[:2]},
        {"user_id": 789, "predicate": "plan", "object_type": "event", "object_key": "练球计划",
         "object_label": "练球计划", "statement": "计划继续练球", "certainty": "tentative", "source_ids": ids[2:]},
    ]
    assert store.save_memory_batch(batch, parse_claims(json.dumps({"claims": claims}), batch))
    requests = []

    def post(_settings, body):
        requests.append(body)
        if len(requests) == 1:
            assert body["tools"][0]["function"]["name"] == "get_person_conversation"
            return completion(None, [{"type": "function", "id": "raw", "function": {
                "name": "get_person_conversation", "arguments": '{"qq":"789"}',
            }}])
        result = json.loads(next(m["content"] for m in body["messages"] if m["role"] == "tool"))
        assert result["returned_count"] == 3
        return completion("他喜欢篮球，也提过继续练球的计划。")

    monkeypatch.setattr(ai_client, "_post_chat_body", post)
    assert await generate_roast_reply(group_id=123, target_user_id=789, settings=settings,
        store=store) == "他喜欢篮球，也提过继续练球的计划。"
    assert len(requests) == 2
