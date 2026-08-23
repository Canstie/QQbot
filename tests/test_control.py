from __future__ import annotations

import importlib
from types import SimpleNamespace

import nonebot
import pytest

nonebot.init()
control = importlib.import_module("qq_personal_bot.plugins.control")


class CommandFinished(Exception):
    pass


class FakeMatcher:
    def __init__(self) -> None:
        self.messages: list[str | None] = []

    async def finish(self, message: str | None = None) -> None:
        self.messages.append(message)
        raise CommandFinished


class FakeStore:
    def __init__(self) -> None:
        self.removed: list[int] = []
        self.ai_enabled: list[tuple[int, int]] = []
        self.model = "deepseek-v4-flash"
        self.model_changes: list[tuple[str, int]] = []
        self.knowledge_bases = [
            {"id": 11, "name": "果果", "active": True},
            {"id": 22, "name": "判官果果", "active": False},
        ]
        self.knowledge_changes: list[tuple[int, bool, int]] = []

    def is_admin(self, user_id: int) -> bool:
        return True

    def remove_admin(self, user_id: int, actor_id: int) -> None:
        self.removed.append(user_id)

    def enable_dsapi_group(self, group_id: int, *, actor_id: int) -> None:
        self.ai_enabled.append((group_id, actor_id))

    def get_dsapi_config(self):
        return {"active_knowledge": {"model": self.model}}

    def set_active_dsapi_model(self, model: str, *, actor_id: int):
        self.model = model
        self.model_changes.append((model, actor_id))
        return {"model": model}

    def list_dsapi_knowledge_bases(self):
        return self.knowledge_bases

    def activate_dsapi_knowledge_base(
        self,
        knowledge_id: int,
        *,
        clear_history: bool,
        actor_id: int,
    ) -> None:
        self.knowledge_changes.append((knowledge_id, clear_history, actor_id))


@pytest.mark.asyncio
async def test_admin_remove_command_is_web_only(monkeypatch):
    store = FakeStore()
    matcher = FakeMatcher()
    monkeypatch.setattr(control, "get_store", lambda: store)

    with pytest.raises(CommandFinished):
        await control._handle_bot_command(
            matcher,
            SimpleNamespace(user_id=10000),
            ["admin", "remove", "20000"],
        )

    assert matcher.messages == ["Admin removal is Web-only. Use the QQBot admin page."]
    assert store.removed == []


@pytest.mark.asyncio
async def test_aion_enables_explicit_group(monkeypatch):
    store = FakeStore()
    matcher = FakeMatcher()
    monkeypatch.setattr(control, "get_store", lambda: store)

    with pytest.raises(CommandFinished):
        await control._handle_bot_command(
            matcher,
            SimpleNamespace(user_id=10000),
            ["aion", "12345"],
        )

    assert matcher.messages == ["AI enabled for group 12345."]
    assert store.ai_enabled == [(12345, 10000)]


@pytest.mark.asyncio
async def test_aion_uses_current_group_when_group_id_is_omitted(monkeypatch):
    store = FakeStore()
    matcher = FakeMatcher()
    monkeypatch.setattr(control, "get_store", lambda: store)

    with pytest.raises(CommandFinished):
        await control._handle_bot_command(
            matcher,
            SimpleNamespace(user_id=10000, group_id=67890),
            ["aion"],
        )

    assert matcher.messages == ["AI enabled for group 67890."]
    assert store.ai_enabled == [(67890, 10000)]


@pytest.mark.asyncio
async def test_aim_lists_models_and_marks_current(monkeypatch):
    store = FakeStore()
    matcher = FakeMatcher()
    monkeypatch.setattr(control, "get_store", lambda: store)

    with pytest.raises(CommandFinished):
        await control._handle_bot_command(
            matcher,
            SimpleNamespace(user_id=10000),
            ["aim", "list"],
        )

    assert "1. flash — deepseek-v4-flash（当前）" in matcher.messages[0]
    assert "2. pro — deepseek-v4-pro" in matcher.messages[0]
    assert "3. vision — deepseek-v4-flash-vision-exp，支持引用图片识别" in matcher.messages[0]


@pytest.mark.asyncio
async def test_aim_switches_to_vision_model(monkeypatch):
    store = FakeStore()
    matcher = FakeMatcher()
    monkeypatch.setattr(control, "get_store", lambda: store)

    with pytest.raises(CommandFinished):
        await control._handle_bot_command(
            matcher,
            SimpleNamespace(user_id=10000),
            ["aim", "vision"],
        )

    assert store.model_changes == [("deepseek-v4-flash-vision-exp", 10000)]
    assert matcher.messages == [
        "AI 模型已切换为 vision（deepseek-v4-flash-vision-exp）。\n🖼 已开启引用图片识别。"
    ]


@pytest.mark.asyncio
async def test_aik_lists_and_switches_by_displayed_position(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(control, "get_store", lambda: store)

    list_matcher = FakeMatcher()
    with pytest.raises(CommandFinished):
        await control._handle_bot_command(
            list_matcher,
            SimpleNamespace(user_id=10000),
            ["aik", "list"],
        )
    assert list_matcher.messages == [
        "AI 知识库列表\n1. 果果（当前）\n2. 判官果果\n使用：/bot aik <序号>"
    ]

    switch_matcher = FakeMatcher()
    with pytest.raises(CommandFinished):
        await control._handle_bot_command(
            switch_matcher,
            SimpleNamespace(user_id=10000),
            ["aik", "2"],
        )
    assert store.knowledge_changes == [(22, True, 10000)]
    assert switch_matcher.messages == ["AI 知识库已切换为 2. 判官果果。"]
