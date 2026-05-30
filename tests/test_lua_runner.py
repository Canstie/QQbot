from __future__ import annotations

import pytest

from qq_personal_bot.core.models import MessageEvent, PolicyDecision
from qq_personal_bot.lua_runner import run_lua_message
from qq_personal_bot.runtime import reset_runtime


class FakeBot:
    self_id = "99999"

    async def call_api(self, action: str, **params):
        if action == "get_group_member_list":
            assert params["group_id"] == 123
            return [{"user_id": 1}, {"user_id": 2}]
        if action == "get_login_info":
            return {"user_id": 99999, "nickname": "bot"}
        if action == "get_group_info":
            assert params["group_id"] == 123
            return {"group_id": 123, "group_name": "test group"}
        raise AssertionError(f"Unexpected action: {action}")


def make_event() -> MessageEvent:
    return MessageEvent(
        platform="onebot.v11",
        message_id=1,
        group_id=123,
        user_id=456,
        raw_message="~群人数",
        is_at_bot=False,
        timestamp=1,
    )


@pytest.mark.asyncio
async def test_lua_script_can_return_reply(tmp_path, monkeypatch):
    script = tmp_path / "main.lua"
    script.write_text(
        """
        function on_message(event, api)
          if event.message == "hello" then
            return "lua reply"
          end
          return nil
        end
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("QQBOT_LUA_SCRIPT", str(script))
    monkeypatch.setenv("QQBOT_LUA_ENABLED", "true")
    reset_runtime()

    result = await run_lua_message(
        FakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="hello"),
    )

    assert result.reply == "lua reply"
    assert result.stop is True


@pytest.mark.asyncio
async def test_lua_script_can_call_group_member_api(tmp_path, monkeypatch):
    script = tmp_path / "main.lua"
    script.write_text(
        """
        function on_message(event, api)
          local members = api.get_group_member_list(event.group_id)
          return "members=" .. tostring(#members)
        end
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("QQBOT_LUA_SCRIPT", str(script))
    monkeypatch.setenv("QQBOT_LUA_ENABLED", "true")
    reset_runtime()

    result = await run_lua_message(
        FakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="群人数"),
    )

    assert result.reply == "members=2"
    assert result.stop is True


@pytest.mark.asyncio
async def test_lua_script_can_call_generic_api(tmp_path, monkeypatch):
    script = tmp_path / "main.lua"
    script.write_text(
        """
        function on_message(event, api)
          local group = api.call("get_group_info", {group_id = event.group_id})
          return group.group_name
        end
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("QQBOT_LUA_SCRIPT", str(script))
    monkeypatch.setenv("QQBOT_LUA_ENABLED", "true")
    reset_runtime()

    result = await run_lua_message(
        FakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="群信息"),
    )

    assert result.reply == "test group"
    assert result.stop is True


@pytest.mark.asyncio
async def test_lua_table_result_can_stop_without_reply(tmp_path, monkeypatch):
    script = tmp_path / "main.lua"
    script.write_text(
        """
        function on_message(event, api)
          return {stop = true}
        end
        """,
        encoding="utf-8",
    )
    monkeypatch.setenv("QQBOT_LUA_SCRIPT", str(script))
    monkeypatch.setenv("QQBOT_LUA_ENABLED", "true")
    reset_runtime()

    result = await run_lua_message(
        FakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="stop"),
    )

    assert result.reply is None
    assert result.stop is True
