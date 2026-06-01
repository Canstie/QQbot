from __future__ import annotations

import asyncio
import json
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot

from qq_personal_bot.core.models import MessageEvent, PolicyDecision
from qq_personal_bot.menu_recipes import fetch_jisu_recipe, is_supported_image_file
from qq_personal_bot.runtime import get_settings, get_store


@dataclass(frozen=True)
class LuaMessageResult:
    reply: str | None = None
    stop: bool = False
    quote: bool = False


@dataclass(frozen=True)
class LuaCommandScript:
    command: str
    path: Path
    size: int
    modified_at: str


_WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
_WINDOWS_INVALID_FILENAME_CHARS = set('/\\:*?"<>|')


class LuaApi:
    def __init__(
        self,
        lua: Any,
        bot: Bot,
        loop: asyncio.AbstractEventLoop,
        event: MessageEvent,
        command: str,
        timeout_seconds: float,
    ):
        self._lua = lua
        self._bot = bot
        self._loop = loop
        self._event = event
        self._command = command
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

    def get_state(self, key: str, namespace: str | None = None) -> str | None:
        return get_store().get_lua_state(self._state_namespace(namespace), str(key))

    def set_state(self, key: str, value: str, namespace: str | None = None) -> bool:
        get_store().set_lua_state(self._state_namespace(namespace), str(key), str(value))
        return True

    def delete_state(self, key: str, namespace: str | None = None) -> bool:
        return get_store().delete_lua_state(self._state_namespace(namespace), str(key))

    def url_encode(self, value: str) -> str:
        return quote(str(value), safe="")

    def http_get_json(self, url: str) -> Any:
        parsed = urlparse(str(url))
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("api.http_get_json only supports http and https URLs")
        request = Request(str(url), headers={"User-Agent": "qq-personal-bot/0.1"})
        with urlopen(request, timeout=self._timeout_seconds) as response:
            body = response.read(1_000_000)
        return _to_lua(self._lua, json.loads(body.decode("utf-8")))

    def json_encode(self, value: Any) -> str:
        return json.dumps(_from_lua(value), ensure_ascii=False, separators=(",", ":"))

    def json_decode(self, value: str) -> Any:
        return _to_lua(self._lua, json.loads(str(value)))

    def pick_menu_recipe(self, target: str, seed: int) -> Any:
        settings = get_settings()
        if settings.menu_provider in {"auto", "jisu"} and settings.jisu_recipe_appkey:
            try:
                recipe = fetch_jisu_recipe(
                    settings.jisu_recipe_appkey,
                    str(target or ""),
                    int(seed),
                    settings.lua_timeout_seconds,
                )
            except Exception as exc:
                logger.warning(f"Lua: Jisu recipe API failed, falling back to local menu: {exc}")
            else:
                if recipe is not None:
                    return _to_lua(self._lua, recipe)

        recipe = get_store().pick_menu_recipe(
            str(target or ""),
            int(seed),
            seed_path=settings.menu_seed_path,
            image_dir=settings.menu_image_dir,
        )
        return _to_lua(self._lua, recipe)

    def local_image(self, relpath: str) -> str | None:
        relative_path = str(relpath or "").strip()
        if not relative_path:
            return None

        root = get_settings().menu_image_dir.resolve(strict=False)
        image_path = (root / relative_path).resolve(strict=False)
        try:
            image_path.relative_to(root)
        except ValueError:
            return None
        if not image_path.is_file():
            return None
        if not is_supported_image_file(image_path):
            logger.warning(f"Lua: skipped unsupported local image file: {image_path}")
            return None
        return f"[CQ:image,file={image_path.as_uri()}]"

    def _call_api(self, action: str, **params: Any) -> Any:
        future = asyncio.run_coroutine_threadsafe(
            self._bot.call_api(action, **params),
            self._loop,
        )
        return _to_lua(self._lua, future.result(timeout=self._timeout_seconds))

    def _state_namespace(self, namespace: str | None) -> str:
        value = str(namespace).strip() if namespace is not None else self._command
        if not value:
            raise ValueError("Lua state namespace cannot be empty")
        return value


async def run_lua_message(
    bot: Bot,
    event: MessageEvent,
    decision: PolicyDecision,
) -> LuaMessageResult:
    settings = get_settings()
    if not settings.lua_enabled:
        return LuaMessageResult()

    if decision.handler == "direct":
        return LuaMessageResult()

    command_parts = split_lua_command(decision.normalized_message)
    if command_parts is None:
        logger.debug(f"Lua: not a valid command from message: {decision.normalized_message!r}")
        return LuaMessageResult()

    command, args = command_parts
    try:
        script_path = lua_command_path(command)
    except ValueError as exc:
        logger.warning(f"Lua: invalid command path for {command!r}: {exc}")
        return LuaMessageResult()

    if not script_path.is_file():
        logger.debug(f"Lua: script not found for command {command!r} at {script_path}")
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
                command,
                args,
                loop,
                settings.lua_timeout_seconds,
            ),
            timeout=settings.lua_timeout_seconds + 0.5,
        )
    except asyncio.TimeoutError:
        logger.warning(
            f"Lua: command {command!r} timed out after "
            f"{settings.lua_timeout_seconds + 0.5:.1f}s"
        )
        return LuaMessageResult()
    except Exception:
        logger.warning(
            f"Lua: command {command!r} failed with exception:\n"
            f"{traceback.format_exc()}"
        )
        return LuaMessageResult()


def _run_lua_message_sync(
    script_path: Path,
    bot: Bot,
    event: MessageEvent,
    decision: PolicyDecision,
    command: str,
    args: str,
    loop: asyncio.AbstractEventLoop,
    timeout_seconds: float,
) -> LuaMessageResult:
    from lupa import LuaRuntime

    lua = LuaRuntime(unpack_returned_tuples=True)
    _sandbox(lua)
    try:
        lua.execute(script_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning(
            f"Lua: failed to execute script {script_path}:\n"
            f"{traceback.format_exc()}"
        )
        return LuaMessageResult()

    handler = lua.globals()["on_command"] or lua.globals()["on_message"]
    if handler is None:
        logger.debug(f"Lua: script {script_path} has no on_command or on_message handler")
        return LuaMessageResult()

    full_message = decision.normalized_message.strip()
    date = datetime.fromtimestamp(event.timestamp, timezone(timedelta(hours=8))).date().isoformat()
    lua_event = _to_lua(
        lua,
        {
            "platform": event.platform,
            "message_id": event.message_id,
            "group_id": event.group_id,
            "user_id": event.user_id,
            "raw_message": event.raw_message,
            "message": args,
            "full_message": full_message,
            "command": command,
            "args": args,
            "handler": decision.handler,
            "is_direct": decision.handler == "direct",
            "is_at_bot": event.is_at_bot,
            "timestamp": event.timestamp,
            "date": date,
        },
    )
    api = LuaApi(lua, bot, loop, event, command, timeout_seconds)
    try:
        return _normalize_lua_result(handler(lua_event, api))
    except Exception:
        logger.warning(
            f"Lua: command {command!r} handler raised an error (check script {script_path}):\n"
            f"{traceback.format_exc()}"
        )
        return LuaMessageResult()


def validate_lua_command(command: str) -> str:
    normalized = command.strip()
    if not normalized:
        raise ValueError("Lua command cannot be empty")
    if normalized in {".", ".."} or "." in normalized:
        raise ValueError("Lua command cannot contain dots")
    if any(char in _WINDOWS_INVALID_FILENAME_CHARS for char in normalized):
        raise ValueError("Lua command cannot contain path or filename separators")
    if any(ord(char) < 32 for char in normalized):
        raise ValueError("Lua command cannot contain control characters")
    if normalized.casefold() in _WINDOWS_RESERVED_NAMES:
        raise ValueError("Lua command cannot use a Windows reserved filename")
    return normalized


def split_lua_command(message: str) -> tuple[str, str] | None:
    parts = message.strip().split(maxsplit=1)
    if not parts:
        return None
    try:
        command = validate_lua_command(parts[0])
    except ValueError:
        return None
    args = parts[1].strip() if len(parts) > 1 else ""
    return command, args


def lua_command_path(command: str, lua_dir: Path | None = None) -> Path:
    command = validate_lua_command(command)
    root = lua_dir or get_settings().lua_dir
    path = root / f"{command}.lua"
    root_resolved = root.resolve(strict=False)
    path_resolved = path.resolve(strict=False)
    try:
        path_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("Lua command path escaped lua directory") from exc
    return path


def list_lua_command_scripts(lua_dir: Path | None = None) -> list[LuaCommandScript]:
    root = lua_dir or get_settings().lua_dir
    if not root.exists():
        return []

    scripts: list[LuaCommandScript] = []
    for path in sorted(root.glob("*.lua"), key=lambda item: item.stem.casefold()):
        try:
            command = validate_lua_command(path.stem)
        except ValueError:
            continue
        stat = path.stat()
        scripts.append(
            LuaCommandScript(
                command=command,
                path=path,
                size=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            )
        )
    return scripts


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


def default_lua_command_script(command: str) -> str:
    command = validate_lua_command(command)
    if command == "抽群老婆":
        return """-- Command: 抽群老婆
-- Trigger: ~抽群老婆

local function display_name(member)
  if member.card ~= nil and member.card ~= "" then
    return member.card
  end
  if member.nickname ~= nil and member.nickname ~= "" then
    return member.nickname
  end
  return tostring(member.user_id)
end

function on_command(event, api)
  if event.group_id == nil then
    return "这个功能只能在群聊里使用。"
  end

  local members = api.get_group_member_list(event.group_id)
  if members == nil or #members == 0 then
    return "没有获取到群成员列表。"
  end

  local login = api.get_login_info()
  local self_id = tostring(login.user_id)
  local candidates = {}

  for i = 1, #members do
    local member = members[i]
    if tostring(member.user_id) ~= self_id then
      table.insert(candidates, member)
    end
  end

  if #candidates == 0 then
    return "没有可抽取的群老婆。"
  end

  math.randomseed(tonumber(event.timestamp) + tonumber(event.message_id or 0))
  local picked = candidates[math.random(#candidates)]
  return "抽取完成！你的群老婆是：" .. display_name(picked) .. "（" .. tostring(picked.user_id) .. "）"
end
"""

    return f"""-- Command: {command}
-- Trigger: ~{command} [args]
-- event.args contains the text after the command.

function on_command(event, api)
  if event.args ~= "" then
    return "已执行 " .. event.command .. "，参数：" .. event.args
  end

  return "已执行 " .. event.command
end
"""


def validate_lua_script(script: str) -> None:
    from lupa import LuaRuntime

    lua = LuaRuntime(unpack_returned_tuples=True)
    _sandbox(lua)
    lua.execute(script)
    handler = lua.globals()["on_command"] or lua.globals()["on_message"]
    if handler is None:
        raise ValueError("Lua script must define on_command(event, api) or on_message(event, api)")


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
        quote = bool(converted.get("quote", False))
        return LuaMessageResult(reply=str(reply) if reply is not None else None, stop=stop, quote=quote)

    return LuaMessageResult()
