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

    def is_admin(self, user_id: int) -> bool:
        return True

    def remove_admin(self, user_id: int, actor_id: int) -> None:
        self.removed.append(user_id)


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
