from __future__ import annotations

from typing import Any

from nonebot import logger, on, on_message
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, Message, MessageSegment

from qq_personal_bot.adapters.onebot import onebot_to_internal
from qq_personal_bot.lua_runner import run_lua_message
from qq_personal_bot.plugins.custom_flows import handle_custom_flow
from qq_personal_bot.replies import build_reply
from qq_personal_bot.runtime import get_policy_engine

chat = on_message(priority=50, block=False)
self_sent = on("message_sent", priority=50, block=False)


def _build_default_response(content: str, *, direct: bool = False) -> str:
    return build_reply(content, direct=direct)


def _build_lua_response(content: str, event: Any, *, quote: bool) -> str | Message:
    if not quote:
        return content
    return MessageSegment.reply(event.message_id) + Message(content)


async def _finish_with_response(
    matcher: Any,
    bot: Bot,
    event: Any,
    response: str | Message,
    *,
    explicit_group_send: bool,
) -> None:
    if explicit_group_send:
        group_id = getattr(event, "group_id", None)
        if group_id is not None:
            await bot.send_group_msg(group_id=int(group_id), message=response)
        await matcher.finish()

    await matcher.finish(response)


async def _handle_onebot_message(
    matcher: Any,
    bot: Bot,
    event: Any,
    *,
    explicit_group_send: bool = False,
) -> None:
    internal_event = onebot_to_internal(event, self_id=bot.self_id)
    custom_flow_reply = handle_custom_flow(internal_event)
    if custom_flow_reply:
        await _finish_with_response(
            matcher,
            bot,
            event,
            custom_flow_reply,
            explicit_group_send=explicit_group_send,
        )

    decision = get_policy_engine().evaluate(internal_event, self_id=bot.self_id)
    if not decision.allowed:
        return

    lua_result = await run_lua_message(bot, internal_event, decision)
    if lua_result.reply:
        await _finish_with_response(
            matcher,
            bot,
            event,
            _build_lua_response(lua_result.reply, event, quote=lua_result.quote),
            explicit_group_send=explicit_group_send,
        )
    if lua_result.stop:
        return

    if decision.handler in {"mention", "lua"}:
        return

    if decision.handler == "default":
        logger.debug(
            f"Lua: no result for command from message {decision.normalized_message!r}, "
            f"falling back to replies.json"
        )

    await _finish_with_response(
        matcher,
        bot,
        event,
        _build_default_response(decision.normalized_message, direct=decision.handler == "direct"),
        explicit_group_send=explicit_group_send,
    )


@chat.handle()
async def handle_group_message(bot: Bot, event: GroupMessageEvent):
    await _handle_onebot_message(chat, bot, event)


@self_sent.handle()
async def handle_self_sent_message(bot: Bot, event: Event):
    await _handle_onebot_message(self_sent, bot, event, explicit_group_send=True)
