from __future__ import annotations

from nonebot import on_message, logger
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageSegment

from qq_personal_bot.adapters.onebot import onebot_to_internal
from qq_personal_bot.lua_runner import run_lua_message
from qq_personal_bot.replies import build_reply
from qq_personal_bot.runtime import get_policy_engine

chat = on_message(priority=50, block=False)


def _build_default_response(content: str, *, direct: bool = False) -> str:
    return build_reply(content, direct=direct)


def _build_lua_response(content: str, event: GroupMessageEvent, *, quote: bool) -> str | Message:
    if not quote:
        return content
    return MessageSegment.reply(event.message_id) + Message(content)


@chat.handle()
async def handle_group_message(bot: Bot, event: GroupMessageEvent):
    internal_event = onebot_to_internal(event, self_id=bot.self_id)
    decision = get_policy_engine().evaluate(internal_event, self_id=bot.self_id)
    if not decision.allowed:
        return

    lua_result = await run_lua_message(bot, internal_event, decision)
    if lua_result.reply:
        await chat.finish(_build_lua_response(lua_result.reply, event, quote=lua_result.quote))
    if lua_result.stop:
        return

    if decision.handler == "mention":
        return

    if decision.handler == "default":
        logger.debug(
            f"Lua: no result for command from message {decision.normalized_message!r}, "
            f"falling back to replies.json"
        )

    await chat.finish(
        _build_default_response(decision.normalized_message, direct=decision.handler == "direct")
    )
