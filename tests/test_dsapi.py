from __future__ import annotations

import json

import pytest

from qq_personal_bot.core.models import MessageEvent
from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.dsapi import (
    _brief_reply,
    _chat_completions_url,
    _pick_random_sticker,
    _random_reply_selected,
    _request_chat_completion,
    build_mention_prompt,
    generate_mention_reply,
    generate_random_group_reply,
)
from qq_personal_bot.settings import AppSettings


class FakeBot:
    def __init__(self, payload=None):
        self.payload = payload
        self.calls = []

    async def call_api(self, action, **params):
        self.calls.append((action, params))
        return self.payload


def make_event(*, text="帮我回复", segments=(), is_at_bot=True, message_id=1):
    return MessageEvent(
        platform="onebot.v11",
        message_id=message_id,
        group_id=123,
        user_id=456,
        raw_message=text,
        segments=segments,
        is_at_bot=is_at_bot,
    )


def make_settings(tmp_path, **overrides):
    values = {
        "db_path": tmp_path / "qqbot.sqlite3",
        "admins": (),
        "dsapi_api_key": "secret",
        "sticker_dir": tmp_path / "stickers",
    }
    values.update(overrides)
    return AppSettings(**values)


def make_store(
    tmp_path,
    *,
    enabled_groups=(123,),
    knowledge_prompt="",
    random_reply_percent=2,
    random_sticker_percent=20,
):
    settings = make_settings(tmp_path)
    store = PolicyStore(settings.db_path)
    store.initialize(settings)
    store.set_dsapi_config(
        knowledge_enabled=bool(knowledge_prompt),
        knowledge_prompt=knowledge_prompt,
        history_turns=2,
        enabled_groups=list(enabled_groups),
        clear_history=False,
        actor_id=0,
        random_reply_percent=random_reply_percent,
        random_sticker_percent=random_sticker_percent,
    )
    return store


@pytest.mark.asyncio
async def test_plain_text_mention_builds_prompt_without_onebot_lookup():
    bot = FakeBot()

    prompt = await build_mention_prompt(
        bot,
        make_event(text="你好", segments=({"type": "at", "data": {"qq": "999"}},)),
    )

    assert prompt == "你好"
    assert bot.calls == []


@pytest.mark.asyncio
async def test_quoted_text_is_included_in_prompt():
    bot = FakeBot(
        {"message": [{"type": "text", "data": {"text": "昨天是谁值班？"}}]}
    )
    event = make_event(
        text="回答一下",
        segments=({"type": "reply", "data": {"id": "42"}},),
    )

    prompt = await build_mention_prompt(bot, event)

    assert prompt == "被引用的消息：\n昨天是谁值班？\n\n用户的问题或补充：\n回答一下"
    assert bot.calls == [("get_msg", {"message_id": "42"})]


@pytest.mark.asyncio
@pytest.mark.parametrize("segment_type", ["image", "record", "video", "file", "json", "forward"])
async def test_current_multimodal_message_is_discarded(segment_type):
    bot = FakeBot()
    event = make_event(
        segments=(
            {"type": "at", "data": {"qq": "999"}},
            {"type": segment_type, "data": {"url": "https://example.test/content"}},
            {"type": "text", "data": {"text": "解释一下"}},
        )
    )

    assert await build_mention_prompt(bot, event) is None
    assert bot.calls == []


@pytest.mark.asyncio
async def test_quoted_multimodal_message_is_discarded_before_dsapi_call(tmp_path, monkeypatch):
    bot = FakeBot({"message": [{"type": "image", "data": {"file": "a.jpg"}}]})
    event = make_event(segments=({"type": "reply", "data": {"id": 42}},))
    requested = False

    def fake_request(*args, **kwargs):
        nonlocal requested
        requested = True

    monkeypatch.setattr("qq_personal_bot.dsapi._request_chat_completion", fake_request)

    assert await generate_mention_reply(
        bot,
        event,
        make_settings(tmp_path),
        make_store(tmp_path),
    ) is None
    assert requested is False


@pytest.mark.asyncio
async def test_missing_api_key_does_not_resolve_quote_or_call_dsapi(tmp_path, monkeypatch):
    bot = FakeBot()
    requested = False

    def fake_request(*args, **kwargs):
        nonlocal requested
        requested = True

    monkeypatch.setattr("qq_personal_bot.dsapi._request_chat_completion", fake_request)

    response = await generate_mention_reply(
        bot,
        make_event(segments=({"type": "reply", "data": {"id": 42}},)),
        make_settings(tmp_path, dsapi_api_key=""),
        make_store(tmp_path),
    )

    assert response is None
    assert requested is False
    assert bot.calls == []


@pytest.mark.asyncio
async def test_group_without_ai_enabled_does_not_resolve_quote_or_call_dsapi(tmp_path, monkeypatch):
    bot = FakeBot()
    requested = False

    def fake_request(*args, **kwargs):
        nonlocal requested
        requested = True

    monkeypatch.setattr("qq_personal_bot.dsapi._request_chat_completion", fake_request)
    response = await generate_mention_reply(
        bot,
        make_event(segments=({"type": "reply", "data": {"id": 42}},)),
        make_settings(tmp_path),
        make_store(tmp_path, enabled_groups=()),
    )

    assert response is None
    assert requested is False
    assert bot.calls == []


@pytest.mark.asyncio
async def test_knowledge_and_group_history_are_sent_and_response_is_recorded(tmp_path, monkeypatch):
    store = make_store(tmp_path, knowledge_prompt="你是档案管理员，只依据档案回答。")
    store.record_dsapi_exchange(
        group_id=123,
        user_content="上一问",
        assistant_content="上一答",
        history_turns=2,
    )
    captured = {}

    def fake_request(settings, messages):
        captured["messages"] = messages
        return "本轮回答"

    monkeypatch.setattr("qq_personal_bot.dsapi._request_chat_completion", fake_request)

    response = await generate_mention_reply(
        FakeBot(),
        make_event(text="本轮问题"),
        make_settings(tmp_path),
        store,
    )

    assert response == "本轮回答"
    assert "你是档案管理员" in captured["messages"][0]["content"]
    assert "只回复一句话" in captured["messages"][0]["content"]
    assert captured["messages"][1:] == [
        {"role": "user", "content": "上一问"},
        {"role": "assistant", "content": "上一答"},
        {"role": "user", "content": "本轮问题"},
    ]
    assert store.get_dsapi_chat_history(123, 2) == [
        {"role": "user", "content": "上一问"},
        {"role": "assistant", "content": "上一答"},
        {"role": "user", "content": "本轮问题"},
        {"role": "assistant", "content": "本轮回答"},
    ]


@pytest.mark.asyncio
async def test_idle_group_history_is_not_sent_to_dsapi(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    store.record_dsapi_exchange(
        group_id=123,
        user_content="过期问题",
        assistant_content="过期回答",
        history_turns=2,
    )
    with store._connect() as conn:
        conn.execute(
            "UPDATE dsapi_chat_history SET created_at = ? WHERE group_id = ?",
            (1.0, 123),
        )
    captured = {}

    def fake_request(settings, messages):
        captured["messages"] = messages
        return "新回答"

    monkeypatch.setattr("qq_personal_bot.dsapi._request_chat_completion", fake_request)

    response = await generate_mention_reply(
        FakeBot(),
        make_event(text="新问题"),
        make_settings(tmp_path, dsapi_history_idle_seconds=1200),
        store,
    )

    assert response == "新回答"
    assert captured["messages"][1:] == [{"role": "user", "content": "新问题"}]


@pytest.mark.asyncio
async def test_random_group_reply_uses_plain_message_and_random_instruction(tmp_path, monkeypatch):
    captured = {}

    def fake_request(settings, messages):
        captured["messages"] = messages
        return "确实有点离谱。"

    monkeypatch.setattr("qq_personal_bot.dsapi._request_chat_completion", fake_request)
    event = make_event(text="今天也太热了", segments=(), is_at_bot=False)

    response = await generate_random_group_reply(
        event,
        make_settings(tmp_path),
        make_store(tmp_path, random_reply_percent=100),
    )

    assert response == "确实有点离谱。"
    assert "群聊随机插话" in captured["messages"][0]["content"]
    assert captured["messages"][-1] == {"role": "user", "content": "今天也太热了"}


@pytest.mark.asyncio
async def test_random_group_reply_respects_zero_percent_and_multimodal(tmp_path, monkeypatch):
    requested = False

    def fake_request(*args, **kwargs):
        nonlocal requested
        requested = True

    monkeypatch.setattr("qq_personal_bot.dsapi._request_chat_completion", fake_request)
    settings = make_settings(tmp_path)

    assert await generate_random_group_reply(
        make_event(text="普通消息", is_at_bot=False),
        settings,
        make_store(tmp_path, random_reply_percent=0),
    ) is None
    assert await generate_random_group_reply(
        make_event(
            text="看看",
            segments=({"type": "image", "data": {"file": "a.jpg"}},),
            is_at_bot=False,
        ),
        settings,
        make_store(tmp_path, random_reply_percent=100),
    ) is None
    assert requested is False


def test_random_reply_probability_boundaries():
    event = make_event(text="普通消息", is_at_bot=False)

    assert _random_reply_selected(event, 0) is False
    assert _random_reply_selected(event, 100) is True


def test_sticker_ratio_varies_across_messages():
    outcomes = {
        _random_reply_selected(
            make_event(text="普通消息", is_at_bot=False, message_id=message_id),
            50,
            salt="sticker",
        )
        for message_id in range(100)
    }

    assert outcomes == {False, True}


@pytest.mark.asyncio
async def test_random_group_reply_can_send_sticker_without_dsapi_call(tmp_path, monkeypatch):
    sticker_dir = tmp_path / "stickers"
    sticker_dir.mkdir()
    sticker = sticker_dir / "happy.gif"
    sticker.write_bytes(b"GIF89a" + b"\x00" * 20)
    requested = False

    def fake_request(*args, **kwargs):
        nonlocal requested
        requested = True

    monkeypatch.setattr("qq_personal_bot.dsapi._request_chat_completion", fake_request)
    event = make_event(text="好耶", is_at_bot=False)
    settings = make_settings(tmp_path, sticker_dir=sticker_dir)

    response = await generate_random_group_reply(
        event,
        settings,
        make_store(
            tmp_path,
            random_reply_percent=100,
            random_sticker_percent=100,
        ),
    )

    assert response == sticker
    assert _pick_random_sticker(event, settings, 100) == response
    assert requested is False


def test_chat_completion_request_uses_compatible_endpoint(tmp_path, monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps(
                {"choices": [{"message": {"content": "  模型回复  "}}]},
                ensure_ascii=False,
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.headers["Authorization"]
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("qq_personal_bot.dsapi.urlopen", fake_urlopen)
    settings = make_settings(
        tmp_path,
        dsapi_base_url="https://dsapi.example/v1/",
        dsapi_model="deepseek-test",
    )

    response = _request_chat_completion(settings, [{"role": "user", "content": "你好"}])

    assert response == "模型回复"
    assert captured["url"] == "https://dsapi.example/v1/chat/completions"
    assert captured["authorization"] == "Bearer secret"
    assert captured["payload"]["model"] == "deepseek-test"
    assert captured["payload"]["max_tokens"] == 80
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert captured["payload"]["stream"] is False
    assert captured["timeout"] == 30.0


def test_full_chat_completion_url_is_not_duplicated():
    assert (
        _chat_completions_url("https://dsapi.example/v1/chat/completions")
        == "https://dsapi.example/v1/chat/completions"
    )


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("第一句话。第二句话。", "第一句话。"),
        ("第一行\n第二行", "第一行 第二行"),
        ("很长" * 40, "很长" * 29 + "很。"),
        ("", None),
    ],
)
def test_brief_reply_keeps_one_short_line(content, expected):
    assert _brief_reply(content) == expected
