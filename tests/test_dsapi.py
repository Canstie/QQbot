from __future__ import annotations

import base64
import json
from io import BytesIO

import pytest

import qq_personal_bot.dsapi as dsapi_module
from qq_personal_bot.core.models import MessageEvent
from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.dsapi import (
    DSAPIImageError,
    DSAPILengthError,
    _brief_reply,
    _chat_completions_url,
    _format_reply,
    _models_url,
    _pick_random_sticker,
    _random_reply_selected,
    _refresh_relationship_memory,
    _request_chat_completion,
    build_mention_prompt,
    fetch_dsapi_models,
    generate_mention_reply,
    generate_random_group_reply,
)
from qq_personal_bot.settings import AppSettings
from qq_personal_bot.web_search import WebSearchResult, WebSearchSession

_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j"
    "l1sAAAAASUVORK5CYII="
)
_PNG_SOURCE = "data:image/png;base64," + base64.b64encode(_PNG_BYTES).decode()


@pytest.fixture(autouse=True)
def reset_tool_compatibility_cache():
    dsapi_module._web_tools_disabled_until.clear()
    dsapi_module._json_output_disabled_until.clear()
    yield
    dsapi_module._web_tools_disabled_until.clear()
    dsapi_module._json_output_disabled_until.clear()


@pytest.fixture
def quoted_image_download(monkeypatch):
    requests = []

    def download(request, timeout):
        requests.append(request)
        return BytesIO(_PNG_BYTES)

    monkeypatch.setattr("qq_personal_bot.classic_storage.urlopen", download)
    return requests


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
    enabled=True,
    enabled_groups=(123,),
    knowledge_prompt="",
    random_reply_percent=2,
    random_sticker_percent=20,
):
    settings = make_settings(tmp_path)
    store = PolicyStore(settings.db_path)
    store.initialize(settings)
    store.set_dsapi_config(
        enabled=enabled,
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
async def test_ai_master_switch_disables_mentions_and_random_context(tmp_path, monkeypatch):
    requested = False

    def fake_request(*args, **kwargs):
        nonlocal requested
        requested = True

    monkeypatch.setattr("qq_personal_bot.dsapi._request_chat_completion", fake_request)
    settings = make_settings(tmp_path)
    store = make_store(
        tmp_path,
        enabled=False,
        random_reply_percent=100,
        random_sticker_percent=100,
    )

    assert await generate_mention_reply(FakeBot(), make_event(), settings, store) is None
    assert await generate_random_group_reply(
        make_event(text="普通群聊", is_at_bot=False),
        settings,
        store,
    ) is None
    assert store.get_dsapi_group_context(123) == []
    assert requested is False


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
async def test_vision_prompt_sends_actual_image_bytes_in_content_blocks(quoted_image_download):
    image_url = "https://multimedia.nt.qq.com.cn/download?fileid=abc"
    bot = FakeBot(
        {
            "message": [
                {"type": "text", "data": {"text": "看这个"}},
                {"type": "image", "data": {"url": image_url, "file": "a.jpg"}},
            ]
        }
    )
    event = make_event(
        text="这是什么？",
        segments=({"type": "reply", "data": {"id": "42"}},),
    )

    prompt = await build_mention_prompt(bot, event, vision_enabled=True)

    assert prompt == [
        {
            "type": "text",
            "text": "被引用的消息：\n看这个\n\n用户的问题或补充：\n这是什么？",
        },
        {
            "type": "image_url",
            "image_url": {"url": _PNG_SOURCE, "detail": "auto"},
        },
    ]
    assert bot.calls == [("get_msg", {"message_id": "42"})]
    assert quoted_image_download[0].full_url == image_url
    assert quoted_image_download[0].get_header("User-agent")


@pytest.mark.asyncio
async def test_large_quoted_image_is_prepared_before_sending_to_the_model(tmp_path, monkeypatch):
    from PIL import Image

    from qq_personal_bot.vision_images import MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS

    output = BytesIO()
    Image.new("RGB", (4000, 2800), "blue").save(output, format="PNG")
    source = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
    bot = FakeBot({"message":[{"type":"image", "data":{"url":source}}]})
    store = make_store(tmp_path)
    store.set_active_dsapi_model("deepseek-flash", actor_id=0)

    def complete(settings, messages, **kwargs):
        image_url = messages[-1]["content"][1]["image_url"]["url"]
        assert image_url.startswith("data:image/jpeg;base64,")
        prepared = base64.b64decode(image_url.partition(",")[2])
        assert len(prepared) <= MAX_IMAGE_BYTES
        with Image.open(BytesIO(prepared)) as image:
            assert image.width * image.height <= MAX_IMAGE_PIXELS
        return "图中是蓝色区域。"

    monkeypatch.setattr(dsapi_module, "_request_chat_completion", complete)
    event = make_event(text="图里是什么", segments=({"type":"reply", "data":{"id":42}},))
    assert await generate_mention_reply(bot, event, make_settings(tmp_path), store) == "图中是蓝色区域。"


@pytest.mark.asyncio
async def test_vision_model_sends_quoted_image_and_records_text_history(
    tmp_path, monkeypatch, quoted_image_download,
):
    image_url = "https://multimedia.nt.qq.com.cn/download?fileid=abc"
    bot = FakeBot({"message": [{"type": "image", "data": {"url": image_url}}]})
    store = make_store(tmp_path)
    store.set_active_dsapi_model("deepseek-flash", actor_id=0)
    captured = {}

    def fake_request(settings, messages, **kwargs):
        captured["messages"] = messages
        captured["options"] = kwargs
        return "图中是一只猫。"

    monkeypatch.setattr("qq_personal_bot.dsapi._request_chat_completion", fake_request)

    response = await generate_mention_reply(
        bot,
        make_event(
            text="图里是什么？",
            segments=({"type": "reply", "data": {"id": 42}},),
        ),
        make_settings(tmp_path),
        store,
    )

    assert response == "图中是一只猫。"
    assert captured["options"]["model"] == "deepseek-flash"
    assert captured["messages"][-1]["content"][1] == {
        "type": "image_url",
        "image_url": {"url": _PNG_SOURCE, "detail": "auto"},
    }
    knowledge_id = store.get_dsapi_config()["active_knowledge_id"]
    assert store.get_dsapi_chat_history(
        123, 2, knowledge_id=knowledge_id
    )[0]["content"].endswith(
        "[引用图片 1 张]"
    )
    assert "base64" not in store.get_dsapi_chat_history(123, 2, knowledge_id=knowledge_id)[0]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["这两位是谁？", "请搜索一下这张图中的角色是谁"])
@pytest.mark.parametrize("managed", [False, True])
async def test_quoted_images_bypass_enabled_web_search(
    tmp_path, monkeypatch, question, managed,
):
    store = make_store(tmp_path)
    enable_web_search(store)
    store.set_active_dsapi_model("deepseek-flash", actor_id=0)
    settings = make_settings(
        tmp_path, dsapi_provider_managed=managed, dsapi_provider_id=77 if managed else 0,
        dsapi_model="gemini-test" if managed else "deepseek-flash", dsapi_vision_enabled=managed,
    )
    calls = []

    def post(settings, body):
        calls.append(body)
        assert "tools" not in body and "tool_choice" not in body
        assert "response_format" not in body
        assert "本轮包含图片" in body["messages"][0]["content"]
        assert dsapi_module.web_search_guidance() not in body["messages"][0]["content"]
        assert body["messages"][-1]["content"][1]["image_url"]["url"] == _PNG_SOURCE
        return completion("图中是两位穿制服的角色。")

    monkeypatch.setattr(dsapi_module, "_post_chat_body", post)
    monkeypatch.setattr(dsapi_module, "generate_web_reply",
                        lambda *args, **kwargs: pytest.fail("images must bypass web decisions"))
    monkeypatch.setattr("qq_personal_bot.web_search.search_web",
                        lambda *args, **kwargs: pytest.fail("images must not search"))
    bot = FakeBot({"message": [{"type": "image", "data": {"url": _PNG_SOURCE}}]})
    event = make_event(text=question, segments=({"type": "reply", "data": {"id": "42"}},))
    assert await generate_mention_reply(bot, event, settings, store) == "图中是两位穿制服的角色。"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_quoted_text_still_uses_web_decisions(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    enable_web_search(store)
    calls = []

    async def web(complete, system, history, prompt, **kwargs):
        calls.append(prompt)
        assert isinstance(prompt, str) and "被引用的文字" in prompt
        assert dsapi_module.web_search_guidance() in system
        return "已按文字问题处理。"

    monkeypatch.setattr(dsapi_module, "generate_web_reply", web)
    monkeypatch.setattr(dsapi_module, "_request_chat_completion",
                        lambda *args, **kwargs: pytest.fail("text must use the web entry point"))
    bot = FakeBot({"message": [{"type": "text", "data": {"text": "被引用的文字"}}]})
    event = make_event(text="核实这句话", segments=({"type": "reply", "data": {"id": "42"}},))
    assert await generate_mention_reply(bot, event, make_settings(tmp_path), store) == "已按文字问题处理。"
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("recovers", [False, True])
async def test_direct_vision_repairs_one_bad_tool_call_with_the_same_image(
    tmp_path, monkeypatch, recovers,
):
    store = make_store(tmp_path)
    enable_web_search(store)
    store.set_active_dsapi_model("deepseek-flash", actor_id=0)
    calls = []

    def post(settings, body):
        calls.append(body)
        image_messages = [m for m in body["messages"] if isinstance(m["content"], list)]
        assert len(image_messages) == 1
        assert image_messages[0]["content"][1]["image_url"]["url"] == _PNG_SOURCE
        assert "tools" not in body
        if len(calls) == 1 or not recovers:
            return {"choices": [{"finish_reason": "malformed_function_call"}]}
        assert "直接用自然语言" in body["messages"][-1]["content"]
        return completion("图中是一只猫。")

    monkeypatch.setattr(dsapi_module, "_post_chat_body", post)
    bot = FakeBot({"message": [{"type": "image", "data": {"url": _PNG_SOURCE}}]})
    event = make_event(text="图片是什么", segments=({"type": "reply", "data": {"id": "42"}},))
    if recovers:
        assert await generate_mention_reply(bot, event, make_settings(tmp_path), store) == "图中是一只猫。"
    else:
        with pytest.raises(dsapi_module.DSAPIProtocolError):
            await generate_mention_reply(bot, event, make_settings(tmp_path), store)
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    dsapi_module.DSAPIContentBlockedError("provider blocked this request by content policy"),
    dsapi_module.DSAPIError("connection failed during start_tls"),
])
async def test_direct_vision_does_not_retry_content_blocks_or_network_errors(tmp_path, monkeypatch, error):
    store = make_store(tmp_path)
    enable_web_search(store)
    store.set_active_dsapi_model("deepseek-flash", actor_id=0)
    calls = []

    def post(settings, body):
        calls.append(body)
        raise error

    monkeypatch.setattr(dsapi_module, "_post_chat_body", post)
    bot = FakeBot({"message": [{"type": "image", "data": {"url": _PNG_SOURCE}}]})
    event = make_event(text="图片是什么", segments=({"type": "reply", "data": {"id": "42"}},))
    with pytest.raises(type(error)):
        await generate_mention_reply(bot, event, make_settings(tmp_path), store)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_expired_qq_link_falls_back_to_onebot_local_image(tmp_path, monkeypatch):
    image_path = tmp_path / "cached.png"
    image_path.write_bytes(_PNG_BYTES)

    def unavailable(*args, **kwargs):
        raise OSError("expired link")

    monkeypatch.setattr("qq_personal_bot.classic_storage.urlopen", unavailable)
    bot = FakeBot({"data": {"file": str(image_path)}})
    event = make_event(segments=({"type": "reply", "data": {"message": [
        {"type": "image", "data": {"url": "https://qq.example/expired", "file": "cache.png"}},
    ]}},))

    prompt = await build_mention_prompt(bot, event, vision_enabled=True)

    assert prompt[1]["image_url"]["url"] == _PNG_SOURCE
    assert bot.calls == [("get_image", {"file": "cache.png"})]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"base64": base64.b64encode(_PNG_BYTES).decode()},
    {"file": "base64://" + base64.b64encode(_PNG_BYTES).decode()},
])
async def test_onebot_inline_images_preserve_actual_mime_type(payload):
    bot = FakeBot(payload)
    event = make_event(segments=({"type": "reply", "data": {"message": [
        {"type": "image", "data": {"file": "a.png"}},
    ]}},))

    prompt = await build_mention_prompt(bot, event, vision_enabled=True)

    assert prompt[1]["image_url"]["url"] == _PNG_SOURCE


@pytest.mark.asyncio
async def test_cq_image_url_is_unescaped_downloaded_and_deduplicated(quoted_image_download):
    url = "https://qq.example/download?a=1&amp;b=2"
    message = f"[CQ:image,url={url}][CQ:image,url={url}]"
    event = make_event(segments=({"type": "reply", "data": {"message": message}},))

    prompt = await build_mention_prompt(FakeBot(), event, vision_enabled=True)

    assert len(prompt) == 2
    assert quoted_image_download[0].full_url == "https://qq.example/download?a=1&b=2"
    assert len(quoted_image_download) == 1


@pytest.mark.asyncio
async def test_unreadable_quoted_image_never_reaches_model(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "qq_personal_bot.classic_storage.urlopen", lambda *args, **kwargs: BytesIO(b"<html>expired</html>"),
    )
    requested = False

    def fake_request(*args, **kwargs):
        nonlocal requested
        requested = True

    monkeypatch.setattr("qq_personal_bot.dsapi._request_chat_completion", fake_request)
    store = make_store(tmp_path)
    store.set_active_dsapi_model("deepseek-flash", actor_id=0)
    event = make_event(segments=({"type": "reply", "data": {"message": [
        {"type": "image", "data": {"url": "https://qq.example/missing"}},
    ]}},))

    with pytest.raises(DSAPIImageError, match="failed to read quoted image bytes"):
        await generate_mention_reply(FakeBot(), event, make_settings(tmp_path), store)
    assert requested is False


@pytest.mark.asyncio
async def test_partially_unreadable_images_do_not_send_partial_question():
    event = make_event(segments=({"type": "reply", "data": {"message": [
        {"type": "image", "data": {"url": _PNG_SOURCE}},
        {"type": "image", "data": {"file": "missing.png"}},
    ]}},))
    with pytest.raises(DSAPIImageError):
        await build_mention_prompt(FakeBot(), event, vision_enabled=True)


@pytest.mark.asyncio
async def test_raw_quoted_local_path_is_not_read_without_onebot_lookup(tmp_path):
    image_path = tmp_path / "private.png"
    image_path.write_bytes(_PNG_BYTES)
    bot = FakeBot()
    event = make_event(segments=({"type": "reply", "data": {"message": [
        {"type": "image", "data": {"file": str(image_path)}},
    ]}},))
    with pytest.raises(DSAPIImageError):
        await build_mention_prompt(bot, event, vision_enabled=True)
    assert bot.calls == [("get_image", {"file": str(image_path)})]


@pytest.mark.asyncio
async def test_inline_image_size_and_encoding_are_checked(monkeypatch):
    event = make_event(segments=({"type": "reply", "data": {"message": [
        {"type": "image", "data": {"url": _PNG_SOURCE}},
    ]}},))
    monkeypatch.setattr("qq_personal_bot.dsapi._MAX_VISION_IMAGE_BYTES", len(_PNG_BYTES) - 1)
    with pytest.raises(DSAPIImageError):
        await build_mention_prompt(FakeBot(), event, vision_enabled=True)
    monkeypatch.setattr("qq_personal_bot.dsapi._MAX_VISION_IMAGE_BYTES", len(_PNG_BYTES))
    monkeypatch.setattr("qq_personal_bot.dsapi._MAX_VISION_TOTAL_BYTES", len(_PNG_BYTES) - 1)
    with pytest.raises(DSAPIImageError, match="in total"):
        await build_mention_prompt(FakeBot(), event, vision_enabled=True)


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
    store = make_store(tmp_path)
    store.set_active_dsapi_model("deepseek-v4-pro", actor_id=0)
    requested = False

    def fake_request(*args, **kwargs):
        nonlocal requested
        requested = True

    monkeypatch.setattr("qq_personal_bot.dsapi._request_chat_completion", fake_request)

    assert await generate_mention_reply(
        bot,
        event,
        make_settings(tmp_path),
        store,
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
    knowledge_id = store.get_dsapi_config()["active_knowledge_id"]
    store.record_dsapi_exchange(
        group_id=123,
        user_content="上一问",
        assistant_content="上一答",
        history_turns=2,
        knowledge_id=knowledge_id,
    )
    captured = {}

    def fake_request(settings, messages, **kwargs):
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
    assert store.get_dsapi_chat_history(123, 2, knowledge_id=knowledge_id) == [
        {"role": "user", "content": "上一问"},
        {"role": "assistant", "content": "上一答"},
        {"role": "user", "content": "本轮问题"},
        {"role": "assistant", "content": "本轮回答"},
    ]


@pytest.mark.asyncio
async def test_active_knowledge_base_is_used_for_dsapi_prompt(tmp_path, monkeypatch):
    store = make_store(tmp_path, knowledge_prompt="果果角色")
    archive = store.create_dsapi_knowledge_base(
        name="档案员",
        prompt="档案员角色，只依据资料回答。",
        actor_id=0,
        model="deepseek-reasoner",
        thinking_enabled=True,
        max_tokens=512,
        response_mode="normal",
        temperature=0.25,
    )
    store.activate_dsapi_knowledge_base(archive["id"], clear_history=False, actor_id=0)
    captured = {}

    def fake_request(settings, messages, **kwargs):
        captured["messages"] = messages
        captured["options"] = kwargs
        return "档案回答第一段。\n\n档案回答第二段。"

    monkeypatch.setattr("qq_personal_bot.dsapi._request_chat_completion", fake_request)

    response = await generate_mention_reply(
        FakeBot(),
        make_event(text="当前角色是谁"),
        make_settings(tmp_path),
        store,
    )

    assert response == "档案回答第一段。\n\n档案回答第二段。"
    assert "档案员角色" in captured["messages"][0]["content"]
    assert "果果角色" not in captured["messages"][0]["content"]
    assert "1至3个短段落" in captured["messages"][0]["content"]
    assert captured["options"] == {
        "model": "deepseek-reasoner",
        "max_tokens": None,
        "thinking_enabled": True,
        "temperature": 0.25,
    }


@pytest.mark.asyncio
async def test_ai_relationship_and_prompt_style_examples_are_injected(tmp_path, monkeypatch):
    store = make_store(tmp_path, knowledge_prompt="你是春雨。")
    knowledge_id = store.get_dsapi_config()["active_knowledge_id"]
    store.update_dsapi_knowledge_base(
        knowledge_id,
        name="春雨",
        prompt="你是春雨。",
        actor_id=0,
        relationship_memory_enabled=True,
        similar_examples=3,
        style_examples=[
            {"topic": "原神抽卡又歪了", "response": "下次还赌"},
        ],
    )
    store.upsert_dsapi_relationship_memory(
        knowledge_id=knowledge_id,
        user_id=456,
        group_id=777,
        summary="关系亲近，习惯轻微互怼",
    )
    captured = {}

    async def fake_refresh(*args, **kwargs):
        return None

    def fake_request(settings, messages, **kwargs):
        captured["messages"] = messages
        return "骂它也不出货的"

    monkeypatch.setattr("qq_personal_bot.dsapi._request_chat_completion", fake_request)
    monkeypatch.setattr("qq_personal_bot.dsapi._refresh_relationship_memory", fake_refresh)

    response = await generate_mention_reply(
        FakeBot(),
        make_event(text="原神这个池子又歪了"),
        make_settings(tmp_path),
        store,
    )

    assert response == "骂它也不出货的"
    system_prompt = captured["messages"][0]["content"]
    assert "关系亲近，习惯轻微互怼" in system_prompt
    assert "原神抽卡又歪了" in system_prompt
    assert "下次还赌" in system_prompt


def completion(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}


@pytest.mark.parametrize("response", [
    {"choices": [{"finish_reason": "prohibited_content", "message": {"role": "assistant"}}]},
    {"choices": [{"finish_reason": "content_filter"}]},
    {"choices": [{"message": {"content": None, "refusal": "private refusal text"}}]},
    {"choices": [], "promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}},
    {"prompt_feedback": {"block_reason": "SAFETY"}},
    {"candidates": [{"finishReason": "BLOCKLIST"}]},
    {"error": {"code": "content_policy_violation", "message": "private upstream details"}},
])
def test_provider_content_blocks_are_recognized_before_missing_text(
    tmp_path, monkeypatch, caplog, response,
):
    response["echoed_input"] = "private user question"
    monkeypatch.setattr(dsapi_module, "_post_chat_body", lambda *args: response)
    with pytest.raises(dsapi_module.DSAPIContentBlockedError, match="content policy"):
        _request_chat_completion(make_settings(tmp_path), [{"role": "user", "content": "你好"}])
    assert "AI response blocked" in caplog.text
    for secret in ("private user question", "private refusal text", "private upstream details"):
        assert secret not in caplog.text


@pytest.mark.parametrize("response", [None, [], {}, {"choices": []},
    {"choices": [None]}, {"choices": [{"message": None}]},
    {"choices": [{"message": {}, "finish_reason": "stop"}]},
    {"choices": [{"message": {"content": None}, "finish_reason": "stop"}]},
    {"choices": [], "promptFeedback": {"blockReason": "不应写入日志的用户原话"}},
])
def test_unusable_responses_are_not_assumed_to_be_content_blocks(
    tmp_path, monkeypatch, caplog, response,
):
    monkeypatch.setattr(dsapi_module, "_post_chat_body", lambda *args: response)
    with pytest.raises(dsapi_module.DSAPIResponseError):
        _request_chat_completion(make_settings(tmp_path), [{"role": "user", "content": "你好"}])
    assert "AI response unusable" in caplog.text
    assert "AI response blocked" not in caplog.text
    assert "不应写入日志的用户原话" not in caplog.text


@pytest.mark.parametrize("thinking", [False, True])
def test_explicit_content_block_does_not_retry_or_search(tmp_path, monkeypatch, thinking):
    calls = []
    session = WebSearchSession()

    def post(settings, body):
        calls.append(body)
        return {"choices": [{"finish_reason": "PROHIBITED_CONTENT"}]}

    monkeypatch.setattr(dsapi_module, "_post_chat_body", post)
    monkeypatch.setattr("qq_personal_bot.web_search.search_web",
                        lambda *args, **kwargs: pytest.fail("blocked requests must not search"))
    with pytest.raises(dsapi_module.DSAPIContentBlockedError):
        dsapi_module._request_chat_completion_with_fallback(
            make_settings(tmp_path), [{"role": "user", "content": "测试文本"}],
            model="test-model", max_tokens=None, thinking_enabled=thinking,
            temperature=None, web_session=session,
        )
    assert len(calls) == 1 and session.searches == 0


def test_unspecified_prompt_feedback_does_not_block_normal_answers(tmp_path, monkeypatch):
    response = completion("你好，在呢。")
    response["promptFeedback"] = {"blockReason": "BLOCK_REASON_UNSPECIFIED"}
    response["choices"][0]["message"]["refusal"] = None
    monkeypatch.setattr(dsapi_module, "_post_chat_body", lambda *args: response)
    assert _request_chat_completion(
        make_settings(tmp_path), [{"role": "user", "content": "你好"}],
    ) == "你好，在呢。"


@pytest.mark.parametrize("choice", [
    {"finish_reason": "malformed_function_call", "message": {"content": None}},
    {"finish_reason": "MALFORMED_FUNCTION_CALL"},
    {"finish_reason": "tool_calls", "message": {"tool_calls": []}},
    {"finish_reason": "function_call", "message": {"content": "partial text"}},
])
def test_invalid_or_unrequested_tools_are_classified_as_protocol_errors(
    tmp_path, monkeypatch, choice,
):
    monkeypatch.setattr(dsapi_module, "_post_chat_body", lambda *args: {"choices": [choice]})
    with pytest.raises(dsapi_module.DSAPIProtocolError):
        _request_chat_completion(make_settings(tmp_path), [{"role": "user", "content": "看这张图"}])


def test_thinking_fallback_does_not_consume_a_second_protocol_retry(tmp_path, monkeypatch):
    calls = []

    def post(settings, body):
        calls.append(body)
        return {"choices": [{"finish_reason": "malformed_function_call"}]}

    monkeypatch.setattr(dsapi_module, "_post_chat_body", post)
    with pytest.raises(dsapi_module.DSAPIProtocolError):
        dsapi_module._request_chat_completion_with_fallback(
            make_settings(tmp_path), [{"role": "user", "content": "看图"}], model="test-model",
            max_tokens=None, thinking_enabled=True, temperature=None,
        )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_quoted_content_block_is_not_recorded_as_an_assistant_exchange(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    enable_web_search(store)
    knowledge_id = store.get_dsapi_config()["active_knowledge_id"]
    calls = []

    def post(settings, body):
        calls.append(body)
        current = json.loads(body["messages"][-1]["content"])["current_request"]
        assert "被引用的样本文本" in current
        return {"choices": [], "promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}

    monkeypatch.setattr(dsapi_module, "_post_chat_body", post)
    bot = FakeBot({"message": [{"type": "text", "data": {"text": "被引用的样本文本"}}]})
    event = make_event(text="", segments=({"type": "reply", "data": {"id": "42"}},))
    with pytest.raises(dsapi_module.DSAPIContentBlockedError):
        await generate_mention_reply(bot, event, make_settings(tmp_path), store)
    assert len(calls) == 1
    assert store.get_dsapi_chat_history(123, 2, knowledge_id=knowledge_id) == []


def enable_web_search(store):
    knowledge = store.get_dsapi_config()["active_knowledge"]
    if knowledge is None:
        store.create_dsapi_knowledge_base(name="联网测试", prompt="", actor_id=0, web_search_enabled=True)
    else:
        store.update_dsapi_knowledge_base(
            knowledge["id"], name=knowledge["name"], prompt=knowledge["prompt"], actor_id=0, web_search_enabled=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["明日方舟终末地现在的卡池是谁的", "你搜索一下"])
async def test_web_reply_resolves_question_and_uses_checked_evidence(tmp_path, monkeypatch, question):
    store = make_store(tmp_path)
    enable_web_search(store)
    knowledge_id = store.get_dsapi_config()["active_knowledge_id"]
    topic = "明日方舟终末地现在的卡池是谁的"
    store.record_dsapi_exchange(group_id=123, user_content=topic,
        assistant_content="旧助手说游戏尚未上线。", history_turns=2, knowledge_id=knowledge_id)
    quote = "公开公告说明，本期开放对象为甲，活动已于本周开始。"
    calls = []
    def post(settings, body):
        calls.append(body)
        assert "tools" not in body
        assert body["response_format"] == {"type": "json_object"}
        if len(calls) == 1:
            assert "旧助手说游戏尚未上线" not in str(body["messages"])
            assert topic in str(body["messages"])
            return completion(json.dumps({"action":"search", "question":topic,
                "time_scope":"current", "queries":["测试主题 当期公告"]}))
        if len(calls) == 2:
            return completion(json.dumps({"status":"insufficient", "read_sources":[1]}))
        data = json.loads(body["messages"][-1]["content"])
        assert data["question"] == topic and data["sources"][0]["text"] == quote
        assert not any(item["role"] == "assistant" for item in body["messages"])
        return completion(json.dumps({"status":"supported", "time_valid":True,
            "answer":"本期开放对象为甲[1]。", "evidence":[{"source":1,"quote":quote}]}))
    monkeypatch.setattr(dsapi_module, "_post_chat_body", post)
    monkeypatch.setattr("qq_personal_bot.web_search.search_web", lambda *args, **kwargs: [
        WebSearchResult("公开公告", "https://example.test/notice", "摘要不足以确认全部信息。")])
    monkeypatch.setattr("qq_personal_bot.web_agent.read_web_pages", lambda items: [quote for _ in items])
    reply = await generate_mention_reply(FakeBot(), make_event(text=question), make_settings(tmp_path), store)
    assert reply == "本期开放对象为甲。" and len(calls) == 3
    assert store.get_dsapi_chat_history(123, 2, knowledge_id=knowledge_id)[-1]["content"] == reply


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["有男的说是拉拉怎么办", "朋友说话让我困惑怎么办", "晚上好"])
async def test_web_conversation_uses_native_json_without_keyword_routing(tmp_path, monkeypatch, question):
    store = make_store(tmp_path)
    enable_web_search(store)
    calls = []

    def post(settings, body):
        calls.append(body)
        assert body["response_format"] == {"type": "json_object"}
        assert "只适用于answer字符串" in body["messages"][0]["content"]
        assert json.loads(body["messages"][-1]["content"])["current_request"] == question
        return completion(json.dumps({"action": "answer", "basis": "conversation", "answer": "先听听对方的意思。"}))

    monkeypatch.setattr(dsapi_module, "_post_chat_body", post)
    monkeypatch.setattr("qq_personal_bot.web_search.search_web",
                        lambda *args, **kwargs: pytest.fail("conversation does not need search"))
    assert await generate_mention_reply(FakeBot(), make_event(text=question), make_settings(tmp_path), store) == "先听听对方的意思。"
    assert len(calls) == 1


@pytest.mark.parametrize("status", [400, 422])
def test_json_mode_compatibility_fallback_is_cached_per_model_and_expires(tmp_path, monkeypatch, status):
    now = [0.0]
    calls = []
    monkeypatch.setattr(dsapi_module.time, "monotonic", lambda: now[0])

    def post(settings, body):
        calls.append(json.loads(json.dumps(body)))
        if len(calls) == 1:
            raise dsapi_module.DSAPIError(f"HTTP {status}")
        return completion('{"action":"clarify","answer":"你指什么？"}')

    monkeypatch.setattr(dsapi_module, "_post_chat_body", post)
    settings = make_settings(tmp_path)
    messages = [{"role": "user", "content": "输出JSON"}]
    for model in ("first", "first", "second"):
        _request_chat_completion(settings, messages, model=model, json_output=True)
    now[0] = 601
    _request_chat_completion(settings, messages, model="first", json_output=True)
    assert ["response_format" in body for body in calls] == [True, False, False, True, True]
    assert all(body["messages"] == messages and "tools" not in body for body in calls)


@pytest.mark.parametrize("error", [
    dsapi_module.DSAPIError("HTTP 401"), dsapi_module.DSAPIError("HTTP 500"),
    dsapi_module.DSAPIError("connection failed during start_tls"),
    dsapi_module.DSAPIContentBlockedError("HTTP 400"), dsapi_module.DSAPIProtocolError("HTTP 400"),
])
def test_json_mode_does_not_retry_network_policy_or_other_http_failures(tmp_path, monkeypatch, error):
    calls = []

    def post(settings, body):
        calls.append(body)
        raise error

    monkeypatch.setattr(dsapi_module, "_post_chat_body", post)
    with pytest.raises(type(error)):
        _request_chat_completion(make_settings(tmp_path), [{"role": "user", "content": "JSON"}], json_output=True)
    assert len(calls) == 1
    assert not dsapi_module._json_output_disabled_until


def test_thinking_fallback_preserves_native_json_mode(tmp_path, monkeypatch):
    calls = []

    def post(settings, body):
        calls.append(body)
        return completion(None if len(calls) == 1 else '{"action":"clarify","answer":"什么？"}')

    monkeypatch.setattr(dsapi_module, "_post_chat_body", post)
    reply = dsapi_module._request_chat_completion_with_fallback(
        make_settings(tmp_path), [{"role": "user", "content": "JSON"}], model="test-model",
        max_tokens=None, thinking_enabled=True, temperature=None, json_output=True,
    )
    assert json.loads(reply)["action"] == "clarify"
    assert [body["thinking"]["type"] for body in calls] == ["enabled", "disabled"]
    assert all(body["response_format"] == {"type": "json_object"} for body in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("random_reply", [False, True])
async def test_web_entry_points_separate_current_message_from_group_background(
    tmp_path, monkeypatch, random_reply,
):
    store = make_store(tmp_path, random_reply_percent=100, random_sticker_percent=0)
    enable_web_search(store)
    knowledge_id = store.get_dsapi_config()["active_knowledge_id"]
    store.record_dsapi_exchange(group_id=123, user_content="上一个问题是什么",
        assistant_content="已经答完了", history_turns=2, knowledge_id=knowledge_id)
    store.record_dsapi_group_message(group_id=123, user_id=789, content="群友的背景消息",
        knowledge_id=knowledge_id)
    current = "现在换个话题，硬核玩法才是王道"

    def post(settings, body):
        messages = body["messages"]
        assert [item["role"] for item in messages] == ["system", "user"]
        data = json.loads(messages[-1]["content"])
        assert data["current_request"] == current
        assert data["current_user_id"] == 456
        assert data["previous_user_questions"] == ["上一个问题是什么"]
        assert data["recent_group_messages"] == [{"user_id": 789, "content": "群友的背景消息"}]
        return completion(json.dumps({"action": "answer", "basis": "conversation", "answer": "有挑战才过瘾。"}))

    monkeypatch.setattr(dsapi_module, "_post_chat_body", post)
    event = make_event(text=current, is_at_bot=not random_reply)
    settings = make_settings(tmp_path)
    reply = (await generate_random_group_reply(event, settings, store) if random_reply else
             await generate_mention_reply(FakeBot(), event, settings, store))
    assert reply == "有挑战才过瘾。"


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "[CQ:at,qq=999]"])
async def test_bare_mention_is_an_attention_request_not_a_repeat_of_old_question(
    tmp_path, monkeypatch, text,
):
    store = make_store(tmp_path)
    enable_web_search(store)
    knowledge_id = store.get_dsapi_config()["active_knowledge_id"]
    store.record_dsapi_exchange(group_id=123, user_content="泡水灵梦是什么梗",
        assistant_content="这个问题已经回答过了。", history_turns=2, knowledge_id=knowledge_id)
    store.record_dsapi_group_message(group_id=123, user_id=789, content="刚才在聊旧话题",
        knowledge_id=knowledge_id)

    def post(settings, body):
        data = json.loads(body["messages"][-1]["content"])
        assert data["current_request"] == "（用户仅 @ 了你，没有附带问题或其他内容。）"
        assert data["previous_user_questions"] == ["泡水灵梦是什么梗"]
        return completion(json.dumps({"action": "answer", "basis": "conversation", "answer": "在呢，什么事？"}))

    monkeypatch.setattr(dsapi_module, "_post_chat_body", post)
    monkeypatch.setattr("qq_personal_bot.web_search.search_web", lambda *a, **kw: pytest.fail("unneeded search"))
    bot = FakeBot()
    event = make_event(text=text, segments=({"type": "at", "data": {"qq": "999"}},))
    assert await generate_mention_reply(bot, event, make_settings(tmp_path), store) == "在呢，什么事？"
    assert bot.calls == []


@pytest.mark.asyncio
async def test_bare_mention_with_quote_still_responds_to_the_quoted_message():
    bot = FakeBot({"message": [{"type": "text", "data": {"text": "被引用的新话题"}}]})
    event = make_event(text="[CQ:reply,id=42][CQ:at,qq=999]", segments=(
        {"type": "reply", "data": {"id": "42"}}, {"type": "at", "data": {"qq": "999"}},
    ))
    prompt = await build_mention_prompt(bot, event)
    assert "被引用的新话题" in prompt
    assert "请回复这条被引用的消息。" in prompt
    assert "[CQ:" not in prompt
    assert "没有附带问题" not in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("response_mode", ["short", "normal", "detailed"])
async def test_web_source_appendices_are_hidden_in_each_reply(tmp_path, monkeypatch, response_mode):
    store = make_store(tmp_path)
    enable_web_search(store)
    knowledge = store.get_dsapi_config()["active_knowledge"]
    store.update_dsapi_knowledge_base(knowledge["id"], name=knowledge["name"], prompt=knowledge["prompt"],
        actor_id=0, web_search_enabled=True, response_mode=response_mode, max_reply_messages=3)
    quote = "可供本轮核验的一段完整公开资料。"
    responses = iter([
        {"action":"search", "question":"所问事件", "time_scope":"timeless", "queries":["事件 原始资料"]},
        {"status":"supported", "time_valid":True, "answer":
            "项目已发布[1]。\n\n检索来源：\n[1] 官方消息\nhttps://example.test/source"
            "<|消息分隔|>我的看法是继续观察[1]。\n\n参考资料：\n[1] 官方消息",
         "evidence":[{"source":1,"quote":quote}]},
    ])
    monkeypatch.setattr(dsapi_module, "_post_chat_body", lambda *args: completion(json.dumps(next(responses))))
    monkeypatch.setattr("qq_personal_bot.web_search.search_web", lambda *args, **kwargs: [
        WebSearchResult("原始资料", "https://example.test/source", quote)])
    monkeypatch.setattr("qq_personal_bot.web_agent.read_web_pages", lambda items: ["" for _ in items])
    reply = await generate_mention_reply(FakeBot(), make_event(text="核实一下这个事件"), make_settings(tmp_path), store)
    assert reply == ["项目已发布。", "我的看法是继续观察。"]


def test_unsupported_tool_api_falls_back_and_does_not_repeat_bad_requests(tmp_path, monkeypatch):
    requests = []

    def post(settings, body):
        requests.append(json.loads(json.dumps(body)))
        if "tools" in body:
            raise dsapi_module.DSAPIError("HTTP 400")
        return completion("你好")

    monkeypatch.setattr(dsapi_module, "_post_chat_body", post)
    messages = [{"role": "user", "content": "你好"}]
    for _ in range(2):
        assert _request_chat_completion(
            make_settings(tmp_path), messages, web_session=WebSearchSession(),
        ) == "你好"
    assert len(requests) == 3
    assert "tools" in requests[0]
    assert "tools" not in requests[1] and "tools" not in requests[2]
    assert messages == [{"role": "user", "content": "你好"}]


@pytest.mark.parametrize("tool_call", [
    {"id": "x", "function": "invalid"}, {"function": {"name": "web_search"}}, "invalid",
])
def test_invalid_tool_protocol_is_rejected(tmp_path, monkeypatch, tool_call):
    monkeypatch.setattr(dsapi_module, "_post_chat_body", lambda *args: {"choices": [{
        "finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [tool_call]},
    }]})
    with pytest.raises(dsapi_module.DSAPIError, match="invalid web tool"):
        _request_chat_completion(
            make_settings(tmp_path), [{"role": "user", "content": "核实资料"}],
            web_session=WebSearchSession(),
        )


@pytest.mark.asyncio
async def test_thinking_empty_reply_retries_in_nonthinking_mode(tmp_path, monkeypatch):
    store = make_store(tmp_path, knowledge_prompt="果果角色")
    config = store.get_dsapi_config()
    store.update_dsapi_knowledge_base(
        config["active_knowledge_id"],
        name="果果",
        prompt="果果角色",
        actor_id=0,
        thinking_enabled=True,
    )
    calls = []

    def fake_request(settings, messages, **kwargs):
        calls.append(kwargs)
        return None if kwargs["thinking_enabled"] else "降级后回复"

    monkeypatch.setattr("qq_personal_bot.dsapi._request_chat_completion", fake_request)

    response = await generate_mention_reply(
        FakeBot(),
        make_event(text="在吗"),
        make_settings(tmp_path),
        store,
    )

    assert response == "降级后回复"
    assert [item["thinking_enabled"] for item in calls] == [True, False]


@pytest.mark.asyncio
async def test_thinking_length_reply_retries_without_sending_partial_text(tmp_path, monkeypatch):
    store = make_store(tmp_path, knowledge_prompt="测试角色")
    knowledge = store.get_dsapi_config()["active_knowledge"]
    knowledge_id = knowledge["id"]
    store.update_dsapi_knowledge_base(
        knowledge_id,
        name=knowledge["name"],
        prompt=knowledge["prompt"],
        actor_id=0,
        thinking_enabled=True,
        max_tokens=512,
        response_mode="detailed",
    )
    requests = []

    class FakeResponse:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            thinking = self.body["thinking"]["type"] == "enabled"
            return json.dumps(
                {
                    "choices": [
                        {
                            "finish_reason": "length" if thinking else "stop",
                            "message": {
                                "content": "这是一段未完成的回复，" if thinking else "这是完整的回答。"
                            },
                        }
                    ],
                    "usage": {
                        "completion_tokens": 512 if thinking else 20,
                        "completion_tokens_details": {"reasoning_tokens": 340 if thinking else 0},
                    },
                },
                ensure_ascii=False,
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        requests.append(body)
        return FakeResponse(body)

    monkeypatch.setattr("qq_personal_bot.dsapi.urlopen", fake_urlopen)

    response = await generate_mention_reply(
        FakeBot(), make_event(text="请解释这个问题"), make_settings(tmp_path), store
    )

    assert response == "这是完整的回答。"
    assert [body["thinking"]["type"] for body in requests] == ["enabled", "disabled"]
    assert all("max_tokens" not in body for body in requests)
    history = store.get_dsapi_chat_history(123, 2, knowledge_id=knowledge_id)
    assert history[-1] == {"role": "assistant", "content": "这是完整的回答。"}


def test_nonthinking_length_response_is_rejected(tmp_path, monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps(
                {"choices": [{"finish_reason": "length", "message": {"content": "半句话，"}}]}
            ).encode("utf-8")

    monkeypatch.setattr("qq_personal_bot.dsapi.urlopen", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(DSAPILengthError, match="length limit"):
        _request_chat_completion(make_settings(tmp_path), [{"role": "user", "content": "问题"}])


@pytest.mark.parametrize("message", [{}, {"content": None}, {"content": "只写了一半"}])
def test_length_response_remains_a_length_error_when_content_is_missing(tmp_path, monkeypatch, message):
    monkeypatch.setattr(dsapi_module, "_post_chat_body", lambda *args: {
        "choices": [{"finish_reason": "length", "message": message}],
    })
    with pytest.raises(DSAPILengthError):
        _request_chat_completion(make_settings(tmp_path), [{"role": "user", "content": "测试请求"}])


def test_completion_logs_stop_reason_and_lengths_without_answer_or_credentials(tmp_path, monkeypatch, caplog):
    result = completion("不能出现在日志中的模型回答")
    result["choices"][0]["finish_reason"] = "stop"
    result["usage"] = {"completion_tokens": 22, "completion_tokens_details": {"reasoning_tokens": 7}}
    monkeypatch.setattr(dsapi_module, "_open_ai_request", lambda *args: BytesIO(json.dumps(result).encode()))
    with caplog.at_level("INFO", logger=dsapi_module.__name__):
        assert _request_chat_completion(
            make_settings(tmp_path), [{"role": "user", "content": "不能出现在日志中的用户问题"}],
        ) == "不能出现在日志中的模型回答"
    assert "finish_reason=stop" in caplog.text
    assert "completion_tokens=22 reasoning_tokens=7" in caplog.text
    assert "output_chars=13" in caplog.text
    assert "不能出现在日志中" not in caplog.text
    assert "secret" not in caplog.text


@pytest.mark.parametrize("result", [None, []])
def test_completion_metadata_logging_preserves_invalid_response_handling(tmp_path, monkeypatch, result):
    monkeypatch.setattr(dsapi_module, "_open_ai_request", lambda *args: BytesIO(json.dumps(result).encode()))
    with pytest.raises(dsapi_module.DSAPIResponseError):
        _request_chat_completion(make_settings(tmp_path), [{"role": "user", "content": "你好"}])


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

    def fake_request(settings, messages, **kwargs):
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

    def fake_request(settings, messages, **kwargs):
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
async def test_random_group_reply_includes_previous_ten_group_messages(tmp_path, monkeypatch):
    captured = {}

    def fake_request(settings, messages, **kwargs):
        captured["messages"] = messages
        return "接上了。"

    monkeypatch.setattr("qq_personal_bot.dsapi._request_chat_completion", fake_request)
    settings = make_settings(tmp_path)
    store = make_store(
        tmp_path,
        random_reply_percent=0,
        random_sticker_percent=0,
    )

    for index in range(11):
        response = await generate_random_group_reply(
            make_event(text=f"消息{index}", is_at_bot=False, message_id=index),
            settings,
            store,
        )
        assert response is None

    assert [item["content"] for item in store.get_dsapi_group_context(123)] == [
        f"消息{index}" for index in range(1, 11)
    ]
    store.set_dsapi_config(
        knowledge_enabled=False,
        knowledge_prompt="",
        history_turns=2,
        enabled_groups=[123],
        clear_history=False,
        actor_id=0,
        random_reply_percent=100,
        random_sticker_percent=0,
    )

    response = await generate_random_group_reply(
        make_event(text="最新消息", is_at_bot=False, message_id=99),
        settings,
        store,
    )

    assert response == "接上了。"
    prompt = captured["messages"][-1]["content"]
    assert "[QQ 456] 消息0\n" not in prompt
    for index in range(1, 11):
        assert f"[QQ 456] 消息{index}" in prompt
    assert prompt.endswith("[QQ 456] 最新消息")
    assert store.get_dsapi_chat_history(123, 2) == [
        {"role": "user", "content": "最新消息"},
        {"role": "assistant", "content": "接上了。"},
    ]


@pytest.mark.asyncio
async def test_random_group_reply_uses_configured_long_group_context(tmp_path, monkeypatch):
    captured = {}

    def fake_request(settings, messages, **kwargs):
        captured["messages"] = messages
        return "接上了"

    monkeypatch.setattr("qq_personal_bot.dsapi._request_chat_completion", fake_request)
    settings = make_settings(tmp_path)
    store = make_store(
        tmp_path,
        random_reply_percent=0,
        random_sticker_percent=0,
    )
    store.create_dsapi_knowledge_base(
        name="长上下文",
        prompt="",
        actor_id=0,
        context_messages=25,
    )

    for index in range(26):
        await generate_random_group_reply(
            make_event(text=f"群聊{index}", is_at_bot=False, message_id=index),
            settings,
            store,
        )
    store.set_dsapi_config(
        knowledge_enabled=False,
        knowledge_prompt="",
        history_turns=2,
        enabled_groups=[123],
        clear_history=False,
        actor_id=0,
        random_reply_percent=100,
        random_sticker_percent=0,
    )

    response = await generate_random_group_reply(
        make_event(text="最新", is_at_bot=False, message_id=99),
        settings,
        store,
    )

    assert response == "接上了"
    prompt = captured["messages"][-1]["content"]
    assert "群聊0" not in prompt
    assert "群聊1" in prompt
    assert "群聊25" in prompt


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
    assert "max_tokens" not in captured["payload"]
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert captured["payload"]["stream"] is False
    assert captured["timeout"] == 30.0

    response = _request_chat_completion(
        settings,
        [{"role": "user", "content": "再想想"}],
        model="deepseek-reasoner",
        max_tokens=512,
        thinking_enabled=True,
        temperature=0.3,
    )

    assert response == "模型回复"
    assert captured["payload"]["model"] == "deepseek-reasoner"
    assert captured["payload"]["max_tokens"] == 512
    assert captured["payload"]["thinking"] == {"type": "enabled"}
    assert captured["payload"]["temperature"] == 0.3


def test_fetch_models_uses_configured_dsapi_endpoint(tmp_path, monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps(
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "deepseek-flash",
                            "object": "model",
                            "owned_by": "deepseek",
                        },
                        {
                            "id": "deepseek-v4-pro",
                            "object": "model",
                            "owned_by": "deepseek",
                        },
                        {"id": "deepseek-v4-flash", "owned_by": "duplicate"},
                    ],
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.headers["Authorization"]
        captured["method"] = request.method
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("qq_personal_bot.dsapi.urlopen", fake_urlopen)
    settings = make_settings(tmp_path, dsapi_base_url="https://dsapi.example/v1/")

    models = fetch_dsapi_models(settings)

    assert captured == {
        "url": "https://dsapi.example/v1/models",
        "authorization": "Bearer secret",
        "method": "GET",
        "timeout": 30.0,
    }
    assert [item["id"] for item in models] == [
        "deepseek-flash",
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    ]
    assert models[0]["key"] == "flash"
    assert models[0]["vision"] is True
    assert models[1]["vision"] is False
    assert all(item["source"] == "live" for item in models)


def test_full_chat_completion_url_is_not_duplicated():
    assert (
        _chat_completions_url("https://dsapi.example/v1/chat/completions")
        == "https://dsapi.example/v1/chat/completions"
    )


def test_models_url_accepts_base_and_chat_completion_urls():
    assert _models_url("https://dsapi.example/v1/") == "https://dsapi.example/v1/models"
    assert (
        _models_url("https://dsapi.example/v1/chat/completions")
        == "https://dsapi.example/v1/models"
    )
    assert _models_url("https://dsapi.example/v1/models") == "https://dsapi.example/v1/models"


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


def test_response_mode_only_truncates_short_replies():
    content = "第一句话。\n\n第二段包含需要保留的识图细节。"

    assert _format_reply(content, "short") == "第一句话。"
    assert _format_reply(content, "normal") == content
    assert _format_reply(content, "detailed") == content


def test_multi_reply_separator_produces_bounded_message_list():
    content = "先等等<|消息分隔|>我看看<|消息分隔|>找到了<|消息分隔|>不该出现"

    assert _format_reply(content, "short", max_reply_messages=3) == [
        "先等等",
        "我看看",
        "找到了",
    ]


@pytest.mark.parametrize("mode", ["normal", "detailed"])
@pytest.mark.parametrize("limit", [1, 2, 3])
def test_complete_reply_merges_excess_parts_without_losing_the_ending(mode, limit):
    parts = ["故事的开头。", "发生了冲突。", "误会终于澄清。", "最后两人重归于好。"]
    reply = _format_reply(dsapi_module._MULTI_REPLY_SEPARATOR.join(parts), mode, max_reply_messages=limit)
    messages = [reply] if isinstance(reply, str) else reply
    assert len(messages) == limit
    assert messages == [*parts[:limit - 1], "\n\n".join(parts[limit - 1:])]
    assert messages[-1].endswith(parts[-1])


@pytest.mark.asyncio
async def test_relationship_memory_is_maintained_by_ai(tmp_path, monkeypatch):
    store = make_store(tmp_path, knowledge_prompt="你是春雨。")
    knowledge_id = store.get_dsapi_config()["active_knowledge_id"]
    captured = {}

    def fake_request(settings, messages, **kwargs):
        captured["messages"] = messages
        captured["options"] = kwargs
        return "关系逐渐熟悉，常聊抽卡，习惯轻微互怼。"

    monkeypatch.setattr("qq_personal_bot.dsapi._request_chat_completion", fake_request)

    await _refresh_relationship_memory(
        make_settings(tmp_path),
        store,
        knowledge_id=knowledge_id,
        group_id=123,
        user_id=456,
        user_message="我抽卡又歪了",
        assistant_message="骂它也不出货的",
        model="deepseek-flash",
    )

    memory = store.get_dsapi_relationship_memory(knowledge_id, 456)
    assert memory is not None
    assert "常聊抽卡" in memory["summary"]
    assert memory["interaction_count"] == 1
    assert "不可信数据" in captured["messages"][0]["content"]
    assert captured["options"]["temperature"] == 0.2
