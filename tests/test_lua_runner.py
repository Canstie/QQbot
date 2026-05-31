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
        raw_message="~hello",
        is_at_bot=False,
        timestamp=1,
    )


def make_event_at(timestamp: float) -> MessageEvent:
    return MessageEvent(
        platform="onebot.v11",
        message_id=1,
        group_id=123,
        user_id=456,
        raw_message="~hello",
        is_at_bot=False,
        timestamp=timestamp,
    )


def configure_lua_dir(tmp_path, monkeypatch):
    lua_dir = tmp_path / "scripts" / "lua"
    lua_dir.mkdir(parents=True)
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "qqbot.sqlite3"))
    monkeypatch.setenv("QQBOT_LUA_DIR", str(lua_dir))
    monkeypatch.setenv("QQBOT_LUA_ENABLED", "true")
    reset_runtime()
    return lua_dir


@pytest.mark.asyncio
async def test_lua_command_routes_to_matching_script(tmp_path, monkeypatch):
    lua_dir = configure_lua_dir(tmp_path, monkeypatch)
    (lua_dir / "hello.lua").write_text(
        """
        function on_command(event, api)
          return "lua reply"
        end
        """,
        encoding="utf-8",
    )

    result = await run_lua_message(
        FakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="hello"),
    )

    assert result.reply == "lua reply"
    assert result.stop is True


@pytest.mark.asyncio
async def test_lua_command_receives_command_and_args(tmp_path, monkeypatch):
    lua_dir = configure_lua_dir(tmp_path, monkeypatch)
    (lua_dir / "天气.lua").write_text(
        """
        function on_command(event, api)
          return event.command .. "|" .. event.args .. "|" .. event.message .. "|" .. event.full_message
        end
        """,
        encoding="utf-8",
    )

    result = await run_lua_message(
        FakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="天气 北京"),
    )

    assert result.reply == "天气|北京|北京|天气 北京"
    assert result.stop is True


@pytest.mark.asyncio
async def test_missing_lua_command_continues_json_reply(tmp_path, monkeypatch):
    configure_lua_dir(tmp_path, monkeypatch)

    result = await run_lua_message(
        FakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="missing"),
    )

    assert result.reply is None
    assert result.stop is False


@pytest.mark.asyncio
async def test_invalid_lua_command_does_not_escape_directory(tmp_path, monkeypatch):
    configure_lua_dir(tmp_path, monkeypatch)

    result = await run_lua_message(
        FakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="../secret"),
    )

    assert result.reply is None
    assert result.stop is False


@pytest.mark.asyncio
async def test_direct_reply_does_not_run_lua(tmp_path, monkeypatch):
    lua_dir = configure_lua_dir(tmp_path, monkeypatch)
    (lua_dir / "keyword.lua").write_text(
        """
        function on_command(event, api)
          return "should not run"
        end
        """,
        encoding="utf-8",
    )

    result = await run_lua_message(
        FakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="direct", normalized_message="keyword"),
    )

    assert result.reply is None
    assert result.stop is False


@pytest.mark.asyncio
async def test_lua_script_can_call_group_member_api(tmp_path, monkeypatch):
    lua_dir = configure_lua_dir(tmp_path, monkeypatch)
    (lua_dir / "群人数.lua").write_text(
        """
        function on_command(event, api)
          local members = api.get_group_member_list(event.group_id)
          return "members=" .. tostring(#members)
        end
        """,
        encoding="utf-8",
    )

    result = await run_lua_message(
        FakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="群人数"),
    )

    assert result.reply == "members=2"
    assert result.stop is True


@pytest.mark.asyncio
async def test_lua_script_can_call_generic_api(tmp_path, monkeypatch):
    lua_dir = configure_lua_dir(tmp_path, monkeypatch)
    (lua_dir / "群信息.lua").write_text(
        """
        function on_command(event, api)
          local group = api.call("get_group_info", {group_id = event.group_id})
          return group.group_name
        end
        """,
        encoding="utf-8",
    )

    result = await run_lua_message(
        FakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="群信息"),
    )

    assert result.reply == "test group"
    assert result.stop is True


@pytest.mark.asyncio
async def test_lua_table_result_can_stop_without_reply(tmp_path, monkeypatch):
    lua_dir = configure_lua_dir(tmp_path, monkeypatch)
    (lua_dir / "stop.lua").write_text(
        """
        function on_command(event, api)
          return {stop = true}
        end
        """,
        encoding="utf-8",
    )

    result = await run_lua_message(
        FakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="stop"),
    )

    assert result.reply is None
    assert result.stop is True


@pytest.mark.asyncio
async def test_lua_table_result_can_request_quote(tmp_path, monkeypatch):
    lua_dir = configure_lua_dir(tmp_path, monkeypatch)
    (lua_dir / "quote.lua").write_text(
        """
        function on_command(event, api)
          return {reply = "quoted", quote = true}
        end
        """,
        encoding="utf-8",
    )

    result = await run_lua_message(
        FakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="quote"),
    )

    assert result.reply == "quoted"
    assert result.stop is True
    assert result.quote is True


@pytest.mark.asyncio
async def test_lua_on_message_entrypoint_is_still_accepted(tmp_path, monkeypatch):
    lua_dir = configure_lua_dir(tmp_path, monkeypatch)
    (lua_dir / "legacy.lua").write_text(
        """
        function on_message(event, api)
          return "legacy ok"
        end
        """,
        encoding="utf-8",
    )

    result = await run_lua_message(
        FakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="legacy"),
    )

    assert result.reply == "legacy ok"
    assert result.stop is True


@pytest.mark.asyncio
async def test_lua_state_persists_between_messages(tmp_path, monkeypatch):
    lua_dir = configure_lua_dir(tmp_path, monkeypatch)
    (lua_dir / "stateful.lua").write_text(
        """
        function on_command(event, api)
          local key = tostring(event.group_id) .. ":" .. tostring(event.user_id)
          local saved = api.get_state(key)
          if saved ~= nil then
            return "saved=" .. saved
          end
          api.set_state(key, "first")
          return "created"
        end
        """,
        encoding="utf-8",
    )

    first = await run_lua_message(
        FakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="stateful"),
    )
    second = await run_lua_message(
        FakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="stateful"),
    )

    assert first.reply == "created"
    assert second.reply == "saved=first"


@pytest.mark.asyncio
async def test_lua_event_exposes_fixed_china_date(tmp_path, monkeypatch):
    lua_dir = configure_lua_dir(tmp_path, monkeypatch)
    (lua_dir / "date.lua").write_text(
        """
        function on_command(event, api)
          return event.date
        end
        """,
        encoding="utf-8",
    )

    result = await run_lua_message(
        FakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="date"),
    )

    assert result.reply == "1970-01-01"


@pytest.mark.asyncio
async def test_lua_state_key_can_reset_by_date(tmp_path, monkeypatch):
    lua_dir = configure_lua_dir(tmp_path, monkeypatch)
    (lua_dir / "daily.lua").write_text(
        """
        function on_command(event, api)
          local key = tostring(event.date) .. ":" .. tostring(event.group_id) .. ":" .. tostring(event.user_id)
          local saved = api.get_state(key, "daily")
          if saved ~= nil then
            return "saved=" .. saved
          end
          api.set_state(key, event.date, "daily")
          return "created=" .. event.date
        end
        """,
        encoding="utf-8",
    )

    first = await run_lua_message(
        FakeBot(),
        make_event_at(1),
        PolicyDecision(True, "ok", handler="default", normalized_message="daily"),
    )
    second = await run_lua_message(
        FakeBot(),
        make_event_at(2),
        PolicyDecision(True, "ok", handler="default", normalized_message="daily"),
    )
    third = await run_lua_message(
        FakeBot(),
        make_event_at(86400),
        PolicyDecision(True, "ok", handler="default", normalized_message="daily"),
    )

    assert first.reply == "created=1970-01-01"
    assert second.reply == "saved=1970-01-01"
    assert third.reply == "created=1970-01-02"


@pytest.mark.asyncio
async def test_lua_http_get_json_and_url_encode(tmp_path, monkeypatch):
    lua_dir = configure_lua_dir(tmp_path, monkeypatch)
    (lua_dir / "weather.lua").write_text(
        """
        function on_command(event, api)
          local encoded = api.url_encode(event.args)
          local data = api.http_get_json("https://example.invalid/weather?q=" .. encoded)
          return encoded .. "|" .. data.current_condition[1].temp_C
        end
        """,
        encoding="utf-8",
    )

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size):
            return b'{"current_condition":[{"temp_C":"21"}]}'

    def fake_urlopen(request, timeout):
        assert request.full_url == "https://example.invalid/weather?q=%E5%8C%97%E4%BA%AC"
        assert timeout > 0
        return FakeResponse()

    monkeypatch.setattr("qq_personal_bot.lua_runner.urlopen", fake_urlopen)

    result = await run_lua_message(
        FakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="weather 北京"),
    )

    assert result.reply == "%E5%8C%97%E4%BA%AC|21"
