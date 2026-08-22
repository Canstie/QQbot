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

    def is_admin(self, user_id: int) -> bool:
        return True

    def remove_admin(self, user_id: int, actor_id: int) -> None:
        self.removed.append(user_id)

    def enable_dsapi_group(self, group_id: int, *, actor_id: int) -> None:
        self.ai_enabled.append((group_id, actor_id))


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
