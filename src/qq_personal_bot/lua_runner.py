from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nonebot.adapters.onebot.v11 import Bot

from qq_personal_bot.core.models import MessageEvent, PolicyDecision
from qq_personal_bot.runtime import get_settings


@dataclass(frozen=True)
class LuaMessageResult:
    reply: str | None = None
    stop: bool = False


class LuaApi:
    def __init__(
        self,
        lua: Any,
        bot: Bot,
        loop: asyncio.AbstractEventLoop,
        event: MessageEvent,
        timeout_seconds: float,
    ):
        self._lua = lua
        self._bot = bot
        self._loop = loop
        self._event = event
        self._timeout_seconds = timeout_seconds

    def reply(self, message: str) -> bool:
        if self._event.group_id is not None:
            self.send_group_message(self._event.group_id, message)
            return True
        self.send_private_message(self._event.user_id, message)
        return True

    def send_group_message(self, group_id: int, message: str) -> Any:
        return self._call_api("send_group_msg", group_id=int(group_id), message=str(message))

    def send_private_message(self, user_id: int, message: str) -> Any:
        return self._call_api("send_private_msg", user_id=int(user_id), message=str(message))

    def get_group_list(self) -> Any:
        return self._call_api("get_group_list")

    def get_group_info(self, group_id: int, no_cache: bool = False) -> Any:
        return self._call_api("get_group_info", group_id=int(group_id), no_cache=bool(no_cache))

    def get_group_member_list(self, group_id: int, no_cache: bool = False) -> Any:
        return self._call_api(
            "get_group_member_list",
            group_id=int(group_id),
            no_cache=bool(no_cache),
        )

    def get_group_member_info(
        self,
        group_id: int,
        user_id: int,
        no_cache: bool = False,
    ) -> Any:
        return self._call_api(
            "get_group_member_info",
            group_id=int(group_id),
            user_id=int(user_id),
            no_cache=bool(no_cache),
        )

    def get_login_info(self) -> Any:
        return self._call_api("get_login_info")

    def get_stranger_info(self, user_id: int, no_cache: bool = False) -> Any:
        return self._call_api("get_stranger_info", user_id=int(user_id), no_cache=bool(no_cache))

    def call(self, action: str, params: Any = None) -> Any:
        converted = _from_lua(params) if params is not None else {}
        if not isinstance(converted, dict):
            raise TypeError("api.call params must be a table/object")
        return self._call_api(str(action), **converted)

    def _call_api(self, action: str, **params: Any) -> Any:
        future = asyncio.run_coroutine_threadsafe(
            self._bot.call_api(action, **params),
            self._loop,
        )
        return _to_lua(self._lua, future.result(timeout=self._timeout_seconds))


async def run_lua_message(
    bot: Bot,
    event: MessageEvent,
    decision: PolicyDecision,
) -> LuaMessageResult:
    settings = get_settings()
    if not settings.lua_enabled:
        return LuaMessageResult()

    script_path = settings.lua_script
    if not script_path.exists():
        return LuaMessageResult()

    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                _run_lua_message_sync,
                script_path,
                bot,
                event,
                decision,
                loop,
                settings.lua_timeout_seconds,
            ),
            timeout=settings.lua_timeout_seconds + 0.5,
        )
    except Exception:
        return LuaMessageResult()


def _run_lua_message_sync(
    script_path: Path,
    bot: Bot,
    event: MessageEvent,
    decision: PolicyDecision,
    loop: asyncio.AbstractEventLoop,
    timeout_seconds: float,
) -> LuaMessageResult:
    from lupa import LuaRuntime

    lua = LuaRuntime(unpack_returned_tuples=True)
    _sandbox(lua)
    lua.execute(script_path.read_text(encoding="utf-8"))

    handler = lua.globals()["on_message"]
    if handler is None:
        return LuaMessageResult()

    lua_event = _to_lua(
        lua,
        {
            "platform": event.platform,
            "message_id": event.message_id,
            "group_id": event.group_id,
            "user_id": event.user_id,
            "raw_message": event.raw_message,
            "message": decision.normalized_message,
            "handler": decision.handler,
            "is_direct": decision.handler == "direct",
            "is_at_bot": event.is_at_bot,
            "timestamp": event.timestamp,
        },
    )
    api = LuaApi(lua, bot, loop, event, timeout_seconds)
    return _normalize_lua_result(handler(lua_event, api))


def _sandbox(lua: Any) -> None:
    lua.execute(
        """
        os = nil
        io = nil
        package = nil
        require = nil
        dofile = nil
        loadfile = nil
        debug = nil
        """
    )


def default_lua_script() -> str:
    return """-- Optional QQ bot script.
-- Return a string to override replies.json, return nil to keep using JSON replies.

function on_message(event, api)
  if event.message == "群人数" and event.group_id ~= nil then
    local members = api.get_group_member_list(event.group_id)
    return "当前群成员数：" .. tostring(#members)
  end

  if event.message == "登录信息" then
    local info = api.get_login_info()
    return "当前账号：" .. tostring(info.nickname or info.user_id)
  end

  return nil
end
"""


def validate_lua_script(script: str) -> None:
    from lupa import LuaRuntime

    lua = LuaRuntime(unpack_returned_tuples=True)
    _sandbox(lua)
    lua.execute(script)
    handler = lua.globals()["on_message"]
    if handler is None:
        raise ValueError("Lua script must define on_message(event, api)")


def _to_lua(lua: Any, value: Any) -> Any:
    if isinstance(value, dict):
        return lua.table_from({key: _to_lua(lua, item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return lua.table_from([_to_lua(lua, item) for item in value])
    return value


def _from_lua(value: Any) -> Any:
    if hasattr(value, "items"):
        items = list(value.items())
        if not items:
            return {}

        keys = [item[0] for item in items]
        if all(isinstance(key, int) for key in keys):
            sorted_items = sorted(items, key=lambda item: item[0])
            if [key for key, _ in sorted_items] == list(range(1, len(sorted_items) + 1)):
                return [_from_lua(item) for _, item in sorted_items]

        return {key: _from_lua(item) for key, item in items}

    return value


def _normalize_lua_result(value: Any) -> LuaMessageResult:
    if value is None or value is False:
        return LuaMessageResult()
    if isinstance(value, str):
        return LuaMessageResult(reply=value, stop=True)

    if isinstance(value, dict) or hasattr(value, "items"):
        converted = _from_lua(value)
        if not isinstance(converted, dict):
            return LuaMessageResult()
        reply = converted.get("reply")
        stop = bool(converted.get("stop", reply is not None))
        return LuaMessageResult(reply=str(reply) if reply is not None else None, stop=stop)

    return LuaMessageResult()
