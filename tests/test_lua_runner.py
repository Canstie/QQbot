from __future__ import annotations

import json
from pathlib import Path

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


class RichFakeBot:
    self_id = "99999"

    async def call_api(self, action: str, **params):
        if action == "get_group_member_list":
            assert params["group_id"] == 123
            return [
                {"user_id": 1, "nickname": "Alpha", "card": ""},
                {"user_id": 2, "nickname": "Beta", "card": "BetaCard"},
                {"user_id": 3, "nickname": "Gamma", "card": ""},
                {"user_id": 99999, "nickname": "Bot", "card": ""},
            ]
        if action == "get_login_info":
            return {"user_id": 99999, "nickname": "bot"}
        raise AssertionError(f"Unexpected action: {action}")


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


def configure_builtin_lua_dir(tmp_path, monkeypatch):
    lua_dir = PROJECT_ROOT / "scripts" / "lua"
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
    (lua_dir / "澶╂皵.lua").write_text(
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
        PolicyDecision(True, "ok", handler="default", normalized_message="澶╂皵 鍖椾含"),
    )

    assert result.reply == "澶╂皵|鍖椾含|鍖椾含|澶╂皵 鍖椾含"
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


@pytest.mark.asyncio
async def test_lua_json_encode_decode_helpers(tmp_path, monkeypatch):
    lua_dir = configure_lua_dir(tmp_path, monkeypatch)
    (lua_dir / "json.lua").write_text(
        """
        function on_command(event, api)
          local encoded = api.json_encode({
            name = "鐣寗鐐掕泲",
            items = {
              {id = "1", title = "A"}
            }
          })
          local decoded = api.json_decode(encoded)
          return decoded.name .. "|" .. decoded.items[1].id .. "|" .. decoded.items[1].title
        end
        """,
        encoding="utf-8",
    )

    result = await run_lua_message(
        FakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="json"),
    )

    assert result.reply == "鐣寗鐐掕泲|1|A"


@pytest.mark.asyncio
async def test_builtin_today_personality_replaces_luck_command(tmp_path, monkeypatch):
    lua_dir = configure_builtin_lua_dir(tmp_path, monkeypatch)
    assert (lua_dir / "今日人品.lua").is_file()
    assert not (lua_dir / "今日运气.lua").exists()

    old_result = await run_lua_message(
        RichFakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="今日运气"),
    )
    first = await run_lua_message(
        RichFakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="今日人品"),
    )
    second = await run_lua_message(
        RichFakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="今日人品"),
    )

    assert old_result.reply is None
    assert first.reply == second.reply
    assert first.quote is True
    assert first.reply is not None
    assert any(char.isdigit() for char in first.reply)


@pytest.mark.asyncio
async def test_builtin_daily_yiji_is_fixed(tmp_path, monkeypatch):
    configure_builtin_lua_dir(tmp_path, monkeypatch)

    first = await run_lua_message(
        RichFakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="今日宜忌"),
    )
    second = await run_lua_message(
        RichFakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="今日宜忌"),
    )

    assert first.reply == second.reply
    assert first.quote is True
    assert first.reply is not None
    assert "今日宜：" in first.reply
    assert "今日忌：" in first.reply
    assert "今日签语：" in first.reply


@pytest.mark.asyncio
async def test_builtin_menu_uses_local_recipe_database_without_network(tmp_path, monkeypatch):
    configure_builtin_lua_dir(tmp_path, monkeypatch)

    def fail_urlopen(*args, **kwargs):
        raise AssertionError("今日菜单 should not call external HTTP APIs")

    monkeypatch.setattr("qq_personal_bot.lua_runner.urlopen", fail_urlopen)

    default_first = await run_lua_message(
        RichFakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="今日菜单"),
    )
    default_second = await run_lua_message(
        RichFakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="今日菜单"),
    )
    guangzhou = await run_lua_message(
        RichFakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="今日菜单 广州"),
    )
    hotpot = await run_lua_message(
        RichFakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="今日菜单 火锅"),
    )
    unknown = await run_lua_message(
        RichFakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="今日菜单 不存在"),
    )

    assert default_first.quote is True
    assert guangzhou.quote is True
    assert hotpot.quote is True
    assert unknown.quote is True
    assert default_first.reply == default_second.reply
    assert default_first.reply is not None
    assert guangzhou.reply is not None
    assert hotpot.reply is not None
    assert unknown.reply is not None
    assert default_first.reply.startswith("今日菜单\n推荐：")
    assert "理由：" in default_first.reply
    assert "菜系：" not in default_first.reply
    assert "地区：" not in default_first.reply
    assert "分类：" not in default_first.reply
    assert "标签：" not in default_first.reply
    assert "食材：" not in default_first.reply
    assert guangzhou.reply.startswith("今日菜单｜广州\n推荐：")
    assert "理由：" in guangzhou.reply
    assert hotpot.reply.startswith("今日菜单｜火锅\n推荐：")
    assert "TheMealDB" not in hotpot.reply
    assert "外部菜单接口" not in hotpot.reply
    assert unknown.reply.startswith("今日菜单｜不存在\n推荐：")


@pytest.mark.asyncio
async def test_builtin_menu_uses_jisu_recipe_api_image_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("QQBOT_MENU_PROVIDER", "jisu")
    monkeypatch.setenv("QQBOT_JISU_RECIPE_APPKEY", "test-key")
    configure_builtin_lua_dir(tmp_path, monkeypatch)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size):
            return json.dumps(
                {
                    "status": 0,
                    "msg": "ok",
                    "result": {
                        "num": "1",
                        "list": [
                            {
                                "id": "8",
                                "name": "醋溜白菜",
                                "classid": "2",
                                "pic": "http://api.jisuapi.com/recipe/upload/test.jpg",
                                "tag": "家常菜,下饭",
                                "material": [{"mname": "白菜", "amount": "380g"}],
                                "process": [{"pcontent": "快速翻炒至入味。"}],
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            ).encode("utf-8")

    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        return FakeResponse()

    monkeypatch.setattr("qq_personal_bot.menu_recipes.urlopen", fake_urlopen)

    result = await run_lua_message(
        RichFakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="今日菜单"),
    )

    assert result.quote is True
    assert result.reply is not None
    assert "推荐：醋溜白菜" in result.reply
    assert "[CQ:image,file=http://api.jisuapi.com/recipe/upload/test.jpg]" in result.reply
    assert calls
    assert "api.jisuapi.com/recipe/search" in calls[0]
    assert "appkey=test-key" in calls[0]


@pytest.mark.asyncio
async def test_lua_api_local_image_returns_nil_for_missing_file(tmp_path, monkeypatch):
    image_dir = tmp_path / "menu_images"
    image_dir.mkdir(parents=True)
    existing_image = image_dir / "local.gif"
    existing_image.write_bytes(
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00"
        b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
        b"\x00\x02\x02D\x01\x00;"
    )
    invalid_image = image_dir / "invalid.jpg"
    invalid_image.write_text("not an image", encoding="utf-8")
    seed_path = tmp_path / "recipes_seed.jsonl"
    seed_path.write_text(
        (
            '{"id":"local-image","title":"本地带图菜","aliases":["带图"],'
            '"cuisine":"测试菜","region":"本地","category":"样例","tags":["图片"],'
            '"ingredients":["米饭","青菜"],"steps":["装盘"],'
            f'"image_url":"{existing_image.as_posix()}"}}\n'
            '{"id":"missing-image","title":"本地无图菜","aliases":["无图"],'
            '"cuisine":"测试菜","region":"本地","category":"样例","tags":["降级"],'
            '"ingredients":["面条","酱油"],"steps":["拌匀"],"image_url":""}'
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("QQBOT_MENU_SEED_PATH", str(seed_path))
    monkeypatch.setenv("QQBOT_MENU_IMAGE_DIR", str(image_dir))
    lua_dir = configure_lua_dir(tmp_path, monkeypatch)
    (lua_dir / "menu_api.lua").write_text(
        """
        function on_command(event, api)
          local with_image = api.pick_menu_recipe("带图", 0)
          local missing = api.pick_menu_recipe("无图", 0)
          local with_image_cq = api.local_image(with_image.image_relpath) or "missing"
          local missing_cq = api.local_image("does-not-exist.gif") or "nil"
          local invalid_cq = api.local_image("invalid.jpg") or "invalid"
          return with_image.title .. "|" .. with_image_cq .. "|" .. missing.title .. "|" .. missing_cq .. "|" .. invalid_cq
        end
        """,
        encoding="utf-8",
    )

    result = await run_lua_message(
        FakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="menu_api"),
    )

    assert result.reply is not None
    assert "本地带图菜|[CQ:image,file=" in result.reply
    assert "|本地无图菜|nil|invalid" in result.reply


@pytest.mark.asyncio
async def test_builtin_change_wife_reply_uses_avatar_and_name_only(tmp_path, monkeypatch):
    configure_builtin_lua_dir(tmp_path, monkeypatch)

    result = await run_lua_message(
        RichFakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="换个老婆"),
    )

    assert result.quote is True
    assert result.reply is not None
    assert result.reply.startswith("你今天亲爱的群老婆是\n[CQ:image,file=https://q1.qlogo.cn/")
    assert any(name in result.reply for name in {"Alpha", "BetaCard", "Gamma"})
    assert "（" not in result.reply


@pytest.mark.asyncio
async def test_builtin_group_rank_is_fixed_and_excludes_bot(tmp_path, monkeypatch):
    configure_builtin_lua_dir(tmp_path, monkeypatch)

    first = await run_lua_message(
        RichFakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="群排行 摸鱼王"),
    )
    second = await run_lua_message(
        RichFakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="群排行 摸鱼王"),
    )

    assert first.reply == second.reply
    assert first.quote is True
    assert first.reply is not None
    assert "今日「摸鱼王」排行榜" in first.reply
    assert "1. " in first.reply
    assert "2. " in first.reply
    assert "3. " in first.reply
    assert "Bot" not in first.reply
