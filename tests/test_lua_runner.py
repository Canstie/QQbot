from __future__ import annotations

import base64
import io
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

from qq_personal_bot import lua_runner
from qq_personal_bot.core.models import MessageEvent, PolicyDecision
from qq_personal_bot.lua_runner import pending_lua_command, run_lua_message
from qq_personal_bot.runtime import get_store, reset_runtime


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


class ReplyImageFakeBot(RichFakeBot):
    def __init__(self, image_path: Path):
        self.image_path = image_path

    async def call_api(self, action: str, **params):
        if action == "get_msg":
            assert params["message_id"] == "quoted-image"
            return {
                "message": [
                    {
                        "type": "image",
                        "data": {"file": str(self.image_path)},
                    }
                ]
            }
        return await super().call_api(action, **params)


class RawReplyImageFakeBot(RichFakeBot):
    def __init__(self, image_path: Path):
        self.image_path = image_path

    async def call_api(self, action: str, **params):
        if action == "get_msg":
            assert params["message_id"] == "quoted-image"
            return {"raw_message": f"[CQ:image,file={self.image_path}]"}
        return await super().call_api(action, **params)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_event(
    *,
    user_id: int = 456,
    message_id: int | str = 1,
    raw_message: str = "~hello",
    segments=(),
    timestamp: float = 1,
) -> MessageEvent:
    return MessageEvent(
        platform="onebot.v11",
        message_id=message_id,
        group_id=123,
        user_id=user_id,
        raw_message=raw_message,
        segments=segments,
        is_at_bot=False,
        timestamp=timestamp,
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


def china_timestamp(year: int, month: int, day: int, hour: int, minute: int = 0) -> float:
    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        tzinfo=timezone(timedelta(hours=8)),
    ).timestamp()


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


def write_test_image(path: Path) -> dict[tuple[int, int], tuple[int, int, int, int]]:
    image = Image.new("RGBA", (4, 4))
    pixels = image.load()
    colors = {}
    for y in range(4):
        for x in range(4):
            color = (x * 50 + 10, y * 50 + 20, (x + y) * 30 + 30, 255)
            pixels[x, y] = color
            colors[(x, y)] = color
    image.save(path)
    return colors


def decode_cq_base64_image(message: str) -> Image.Image:
    prefix = "[CQ:image,file=base64://"
    assert message.startswith(prefix)
    assert message.endswith("]")
    body = message[len(prefix) : -1]
    return Image.open(io.BytesIO(base64.b64decode(body))).convert("RGBA")


def decode_cq_base64_image_bytes(message: str) -> bytes:
    prefix = "[CQ:image,file=base64://"
    assert message.startswith(prefix)
    assert message.endswith("]")
    body = message[len(prefix) : -1]
    return base64.b64decode(body)


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
async def test_lua_today_lunar_helper_returns_expected_chinese_date(tmp_path, monkeypatch):
    lua_dir = configure_lua_dir(tmp_path, monkeypatch)
    (lua_dir / "lunar.lua").write_text(
        """
        function on_command(event, api)
          local lunar = api.today_lunar()
          return lunar.year .. "|" .. lunar.month .. "|" .. lunar.day .. "|" ..
            lunar.month_label .. "|" .. lunar.day_label .. "|" .. tostring(lunar.is_leap_month)
        end
        """,
        encoding="utf-8",
    )

    result = await run_lua_message(
        FakeBot(),
        make_event(
            raw_message="~lunar",
            timestamp=china_timestamp(2024, 2, 10, 12),
        ),
        PolicyDecision(True, "ok", handler="default", normalized_message="lunar"),
    )

    assert result.reply == "2024|1|1|正月|初一|false"


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
        make_event(timestamp=china_timestamp(2024, 2, 10, 12)),
        PolicyDecision(True, "ok", handler="default", normalized_message="今日宜忌"),
    )
    second = await run_lua_message(
        RichFakeBot(),
        make_event(timestamp=china_timestamp(2024, 2, 10, 12)),
        PolicyDecision(True, "ok", handler="default", normalized_message="今日宜忌"),
    )
    third = await run_lua_message(
        RichFakeBot(),
        make_event(user_id=789, timestamp=china_timestamp(2024, 2, 10, 12)),
        PolicyDecision(True, "ok", handler="default", normalized_message="今日宜忌"),
    )
    next_day = await run_lua_message(
        RichFakeBot(),
        make_event(timestamp=china_timestamp(2024, 2, 11, 12)),
        PolicyDecision(True, "ok", handler="default", normalized_message="今日宜忌"),
    )

    assert first.reply == second.reply
    assert second.reply == third.reply
    assert first.reply != next_day.reply
    assert first.quote is True
    assert first.reply is not None
    assert "今日宜：" in first.reply
    assert "今日忌：" in first.reply
    assert "今日签语：" in first.reply
    assert "农历正月初一" in first.reply


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
    assert guangzhou.reply.startswith("今日菜单\n推荐：")
    assert "理由：" in guangzhou.reply
    assert hotpot.reply.startswith("今日菜单\n推荐：")
    assert "TheMealDB" not in hotpot.reply
    assert "外部菜单接口" not in hotpot.reply
    assert unknown.reply.startswith("今日菜单\n推荐：")


@pytest.mark.asyncio
async def test_builtin_menu_uses_jisu_recipe_api_image_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("QQBOT_MENU_PROVIDER", "jisu")
    monkeypatch.setenv("QQBOT_JISU_RECIPE_APPKEY", "test-key")
    configure_builtin_lua_dir(tmp_path, monkeypatch)

    class FakeResponse:
        def __init__(self, body: bytes):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size):
            return self.body

    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        if "api.jisuapi.com/recipe/search" in request.full_url:
            return FakeResponse(
                json.dumps(
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
            )
        return FakeResponse(
            b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00"
            b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
            b"\x00\x02\x02D\x01\x00;"
        )

    monkeypatch.setattr("qq_personal_bot.menu_recipes.urlopen", fake_urlopen)

    result = await run_lua_message(
        RichFakeBot(),
        make_event(),
        PolicyDecision(True, "ok", handler="default", normalized_message="今日菜单"),
    )

    assert result.quote is True
    assert result.reply is not None
    assert "推荐：醋溜白菜" in result.reply
    assert "[CQ:image,file=file:///" in result.reply
    assert calls
    assert "api.jisuapi.com/recipe/search" in calls[0]
    assert "appkey=test-key" in calls[0]
    assert "api.jisuapi.com/recipe/upload/test.jpg" in calls[1]


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


@pytest.mark.parametrize(
    ("command", "expected_pixels"),
    [
        (
            "左对称",
            {
                (0, 0): (0, 0),
                (1, 0): (1, 0),
                (2, 0): (1, 0),
                (3, 0): (0, 0),
            },
        ),
        (
            "右对称",
            {
                (0, 0): (3, 0),
                (1, 0): (2, 0),
                (2, 0): (2, 0),
                (3, 0): (3, 0),
            },
        ),
        (
            "上对称",
            {
                (0, 0): (0, 0),
                (0, 1): (0, 1),
                (0, 2): (0, 1),
                (0, 3): (0, 0),
            },
        ),
        (
            "下对称",
            {
                (0, 0): (0, 3),
                (0, 1): (0, 2),
                (0, 2): (0, 2),
                (0, 3): (0, 3),
            },
        ),
    ],
)
@pytest.mark.asyncio
async def test_builtin_image_symmetry_commands_use_quoted_image(
    tmp_path,
    monkeypatch,
    command,
    expected_pixels,
):
    configure_builtin_lua_dir(tmp_path, monkeypatch)
    image_path = tmp_path / "quoted.png"
    source_pixels = write_test_image(image_path)

    result = await run_lua_message(
        ReplyImageFakeBot(image_path),
        make_event(
            raw_message=f"~{command}",
            segments=({"type": "reply", "data": {"id": "quoted-image"}},),
        ),
        PolicyDecision(True, "ok", handler="default", normalized_message=command),
    )

    assert result.quote is True
    assert result.reply is not None
    mirrored = decode_cq_base64_image(result.reply)
    for target, source in expected_pixels.items():
        assert mirrored.getpixel(target) == source_pixels[source]


@pytest.mark.asyncio
async def test_builtin_image_symmetry_command_uses_event_reply_message(tmp_path, monkeypatch):
    configure_builtin_lua_dir(tmp_path, monkeypatch)
    image_path = tmp_path / "quoted.png"
    source_pixels = write_test_image(image_path)

    result = await run_lua_message(
        RichFakeBot(),
        make_event(
            raw_message="~左对称",
            segments=(
                {
                    "type": "reply",
                    "data": {
                        "id": "quoted-image",
                        "message": [{"type": "image", "data": {"file": str(image_path)}}],
                    },
                },
            ),
        ),
        PolicyDecision(True, "ok", handler="default", normalized_message="左对称"),
    )

    assert result.quote is True
    assert result.reply is not None
    mirrored = decode_cq_base64_image(result.reply)
    assert mirrored.getpixel((3, 0)) == source_pixels[(0, 0)]


@pytest.mark.asyncio
async def test_builtin_image_symmetry_command_reads_raw_message_from_get_msg(tmp_path, monkeypatch):
    configure_builtin_lua_dir(tmp_path, monkeypatch)
    image_path = tmp_path / "quoted.png"
    source_pixels = write_test_image(image_path)

    result = await run_lua_message(
        RawReplyImageFakeBot(image_path),
        make_event(
            raw_message="~左对称",
            segments=({"type": "reply", "data": {"id": "quoted-image"}},),
        ),
        PolicyDecision(True, "ok", handler="default", normalized_message="左对称"),
    )

    assert result.quote is True
    assert result.reply is not None
    mirrored = decode_cq_base64_image(result.reply)
    assert mirrored.getpixel((3, 0)) == source_pixels[(0, 0)]


@pytest.mark.asyncio
async def test_builtin_image_symmetry_command_preserves_animated_gif(tmp_path, monkeypatch):
    configure_builtin_lua_dir(tmp_path, monkeypatch)
    image_path = tmp_path / "animated.gif"
    first = Image.new("RGBA", (4, 2), (0, 0, 0, 0))
    first.putpixel((0, 0), (255, 0, 0, 255))
    first.putpixel((1, 0), (0, 255, 0, 255))
    second = Image.new("RGBA", (4, 2), (0, 0, 0, 0))
    second.putpixel((0, 0), (0, 0, 255, 255))
    second.putpixel((1, 0), (255, 255, 0, 255))
    first.save(
        image_path,
        format="GIF",
        save_all=True,
        append_images=[second],
        duration=[80, 120],
        loop=0,
        disposal=2,
    )

    result = await run_lua_message(
        ReplyImageFakeBot(image_path),
        make_event(
            raw_message="~左对称",
            segments=({"type": "reply", "data": {"id": "quoted-image"}},),
        ),
        PolicyDecision(True, "ok", handler="default", normalized_message="左对称"),
    )

    assert result.quote is True
    assert result.reply is not None
    with Image.open(io.BytesIO(decode_cq_base64_image_bytes(result.reply))) as mirrored:
        assert mirrored.format == "GIF"
        assert mirrored.n_frames == 2
        mirrored.seek(0)
        frame0 = mirrored.convert("RGBA")
        assert frame0.getpixel((3, 0)) == (255, 0, 0, 255)
        assert frame0.getpixel((2, 0)) == (0, 255, 0, 255)
        mirrored.seek(1)
        frame1 = mirrored.convert("RGBA")
        assert frame1.getpixel((3, 0)) == (0, 0, 255, 255)
        assert frame1.getpixel((2, 0)) == (255, 255, 0, 255)


@pytest.mark.asyncio
async def test_image_symmetry_timeout_stops_without_json_fallback(tmp_path, monkeypatch):
    lua_dir = configure_lua_dir(tmp_path, monkeypatch)
    (lua_dir / "左对称.lua").write_text(
        'function on_command(event, api)\n  return "should not run"\nend\n',
        encoding="utf-8",
    )

    def slow_lua(*args, **kwargs):
        time.sleep(0.7)
        return lua_runner.LuaMessageResult(reply="late")

    monkeypatch.setattr(lua_runner, "_command_timeout_seconds", lambda command, default: 0.01)
    monkeypatch.setattr(lua_runner, "_run_lua_message_sync", slow_lua)

    result = await run_lua_message(
        RichFakeBot(),
        make_event(raw_message="~左对称"),
        PolicyDecision(True, "ok", handler="default", normalized_message="左对称"),
    )

    assert result.stop is True
    assert result.quote is True
    assert result.reply is not None
    assert "处理超时" in result.reply


@pytest.mark.asyncio
async def test_builtin_image_symmetry_command_requires_quoted_image(tmp_path, monkeypatch):
    configure_builtin_lua_dir(tmp_path, monkeypatch)

    result = await run_lua_message(
        RichFakeBot(),
        make_event(raw_message="~左对称"),
        PolicyDecision(True, "ok", handler="default", normalized_message="左对称"),
    )

    assert result.quote is True
    assert result.reply == "请引用一张图片再发送 ~左对称。"


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
async def test_builtin_store_and_blast_classic_lua(tmp_path, monkeypatch):
    monkeypatch.setenv("QQBOT_CLASSICS_IMAGE_DIR", str(tmp_path / "classics"))
    configure_builtin_lua_dir(tmp_path, monkeypatch)
    image_path = tmp_path / "classic.gif"
    image_path.write_bytes(
        b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00"
        b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
        b"\x00\x02\x02D\x01\x00;"
    )

    start = await run_lua_message(
        RichFakeBot(),
        make_event(raw_message="~存典"),
        PolicyDecision(True, "ok", handler="default", normalized_message="存典"),
    )
    image_event = make_event(
        raw_message="[CQ:image,file=classic.gif]",
        segments=({"type": "image", "data": {"file": str(image_path)}},),
    )
    assert start.reply == "请发出你要存的典或发送'取消'以取消"
    assert pending_lua_command(image_event) == "存典"

    saved = await run_lua_message(
        RichFakeBot(),
        image_event,
        PolicyDecision(True, "ok", handler="lua", normalized_message="存典"),
    )
    blasted = await run_lua_message(
        RichFakeBot(),
        make_event(raw_message="~爆典", message_id=2),
        PolicyDecision(True, "ok", handler="default", normalized_message="爆典"),
    )

    saved_images = list((tmp_path / "classics" / "123").iterdir())
    assert saved.reply == "存典成功"
    assert pending_lua_command(image_event) is None
    assert len(saved_images) == 1
    assert blasted.reply is not None
    assert blasted.reply.startswith("[CQ:image,file=file:///")
    assert saved_images[0].name in blasted.reply


@pytest.mark.asyncio
async def test_builtin_force_marry_rejects_already_claimed_wife(tmp_path, monkeypatch):
    configure_builtin_lua_dir(tmp_path, monkeypatch)
    target_at = ({"type": "at", "data": {"qq": "1"}},)

    first = await run_lua_message(
        RichFakeBot(),
        make_event(raw_message="~强娶 [CQ:at,qq=1]", segments=target_at),
        PolicyDecision(True, "ok", handler="default", normalized_message="强娶"),
    )
    second = await run_lua_message(
        RichFakeBot(),
        make_event(user_id=789, raw_message="~强娶 [CQ:at,qq=1]", segments=target_at),
        PolicyDecision(True, "ok", handler="default", normalized_message="强娶"),
    )

    assert first.quote is True
    assert first.reply is not None
    assert "Alpha" in first.reply
    assert "强娶成功!" in first.reply
    assert second.quote is True
    assert second.reply == "ta已经是别人的群老婆"


@pytest.mark.asyncio
async def test_builtin_force_marry_bot_succeeds_with_warning(tmp_path, monkeypatch):
    configure_builtin_lua_dir(tmp_path, monkeypatch)
    target_at = ({"type": "at", "data": {"qq": "99999"}},)

    result = await run_lua_message(
        RichFakeBot(),
        make_event(raw_message="~强娶 [CQ:at,qq=99999]", segments=target_at),
        PolicyDecision(True, "ok", handler="default", normalized_message="强娶"),
    )

    assert result.quote is True
    assert result.reply is not None
    assert "Bot" in result.reply
    assert "nk=99999" in result.reply
    assert "强娶成功!" in result.reply
    assert "和我是没有好结果的" in result.reply
    assert "不能强娶 bot 自己" not in result.reply


@pytest.mark.asyncio
async def test_builtin_pick_and_change_wife_skip_claimed_members(tmp_path, monkeypatch):
    configure_builtin_lua_dir(tmp_path, monkeypatch)
    target_at = ({"type": "at", "data": {"qq": "1"}},)

    claimed = await run_lua_message(
        RichFakeBot(),
        make_event(raw_message="~强娶 [CQ:at,qq=1]", segments=target_at),
        PolicyDecision(True, "ok", handler="default", normalized_message="强娶"),
    )
    picked = await run_lua_message(
        RichFakeBot(),
        make_event(user_id=789, message_id=2, raw_message="~抽群老婆"),
        PolicyDecision(True, "ok", handler="default", normalized_message="抽群老婆"),
    )
    changed = await run_lua_message(
        RichFakeBot(),
        make_event(user_id=789, message_id=3, raw_message="~换个老婆"),
        PolicyDecision(True, "ok", handler="default", normalized_message="换个老婆"),
    )

    assert claimed.reply is not None
    assert "Alpha" in claimed.reply
    assert picked.reply is not None
    assert "Alpha" not in picked.reply
    assert changed.reply is not None
    assert "Alpha" not in changed.reply


@pytest.mark.asyncio
async def test_builtin_group_summary_reports_daily_activity(tmp_path, monkeypatch):
    configure_builtin_lua_dir(tmp_path, monkeypatch)
    store = get_store()
    event_time = china_timestamp(2026, 6, 20, 22, 0)
    store.record_group_message_activity(
        group_id=123,
        user_id=1,
        timestamp=china_timestamp(2026, 6, 19, 8, 5),
        raw_message="早上好",
        segments=(),
    )
    store.record_group_message_activity(
        group_id=123,
        user_id=2,
        timestamp=china_timestamp(2026, 6, 19, 22, 0),
        raw_message="hello world",
        segments=(
            {"type": "text", "data": {"text": "hello world"}},
            {"type": "image", "data": {"file": "a.jpg"}},
            {"type": "at", "data": {"qq": "1"}},
        ),
    )
    store.record_group_message_activity(
        group_id=123,
        user_id=2,
        timestamp=china_timestamp(2026, 6, 19, 23, 30),
        raw_message="晚安",
        segments=(
            {"type": "text", "data": {"text": "晚安"}},
            {"type": "image", "data": {"file": "b.jpg"}},
        ),
    )

    result = await run_lua_message(
        RichFakeBot(),
        make_event(raw_message="~群总结", timestamp=event_time),
        PolicyDecision(True, "ok", handler="default", normalized_message="群总结"),
    )

    assert result.quote is True
    assert result.reply is not None
    assert "昨日群总结" in result.reply
    assert "总消息：3 条" in result.reply
    assert "参与人数：2 人" in result.reply
    assert "最活跃时段：08:00-09:00（1 条）" in result.reply
    assert "早鸟：Alpha（08:05）" in result.reply
    assert "夜猫子：BetaCard（23:30）" in result.reply
    assert "水群榜\n1. BetaCard：2 条" in result.reply
    assert "字数榜\n1. BetaCard：13 字" in result.reply
    assert "发图榜\n1. BetaCard：2 张" in result.reply
    assert "@人榜\n1. BetaCard：1 次" in result.reply


@pytest.mark.asyncio
async def test_builtin_group_summary_handles_empty_day(tmp_path, monkeypatch):
    configure_builtin_lua_dir(tmp_path, monkeypatch)

    result = await run_lua_message(
        RichFakeBot(),
        make_event(raw_message="~群总结", timestamp=china_timestamp(2026, 6, 19, 12, 0)),
        PolicyDecision(True, "ok", handler="default", normalized_message="群总结"),
    )

    assert result.quote is True
    assert result.reply == "昨天还没有统计到群消息。"


@pytest.mark.asyncio
async def test_builtin_help_lists_available_features(tmp_path, monkeypatch):
    configure_builtin_lua_dir(tmp_path, monkeypatch)

    result = await run_lua_message(
        RichFakeBot(),
        make_event(raw_message="~help", timestamp=china_timestamp(2026, 6, 20, 12, 0)),
        PolicyDecision(True, "ok", handler="default", normalized_message="help"),
    )

    assert result.quote is True
    assert result.reply is not None
    assert "~help 查看这份帮助" in result.reply
    assert "~今日菜单 随机推荐今日吃什么" in result.reply
    assert "~抽群老婆 抽今日群老婆" in result.reply
    assert "~群总结 查看昨天的群消息总结" in result.reply
    assert "~今日饭店 随机抽一家已添加的饭店" in result.reply
    assert "吃什么 / csm / 今天吃什么 等会直接触发今日菜单" in result.reply
    assert "/bot status" in result.reply
    assert "群排行" not in result.reply
