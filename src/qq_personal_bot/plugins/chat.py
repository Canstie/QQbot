from __future__ import annotations

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent

from qq_personal_bot.adapters.onebot import onebot_to_internal
from qq_personal_bot.lua_runner import run_lua_message
from qq_personal_bot.replies import build_reply
from qq_personal_bot.runtime import get_policy_engine

chat = on_message(priority=50, block=False)


def _build_default_response(content: str, *, direct: bool = False) -> str:
    return build_reply(content, direct=direct)


@chat.handle()
async def handle_group_message(bot: Bot, event: GroupMessageEvent):
    internal_event = onebot_to_internal(event, self_id=bot.self_id)
    decision = get_policy_engine().evaluate(internal_event, self_id=bot.self_id)
    if not decision.allowed:
        return

    lua_result = await run_lua_message(bot, internal_event, decision)
    if lua_result.reply:
        await chat.finish(lua_result.reply)
    if lua_result.stop:
        return

    await chat.finish(
        _build_default_response(decision.normalized_message, direct=decision.handler == "direct")
    )
