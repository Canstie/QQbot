from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Any

from nonebot import logger, on, on_message
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, Message, MessageSegment

from qq_personal_bot.adapters.onebot import onebot_to_internal
from qq_personal_bot.core.models import PolicyDecision
from qq_personal_bot.dsapi import (
    DSAPIError,
    generate_mention_reply,
    generate_random_group_reply,
)
from qq_personal_bot.lua_runner import pending_lua_command, run_lua_message
from qq_personal_bot.miniapp import (
    CachedMiniAppImages,
    cache_miniapp_images,
    extract_miniapp_image_source,
)
from qq_personal_bot.plugins.custom_flows import handle_custom_flow
from qq_personal_bot.replies import build_reply
from qq_personal_bot.runtime import get_policy_engine, get_settings, get_store

chat = on_message(priority=50, block=False)
self_sent = on("message_sent", priority=50, block=False)
_RECENT_BOT_OUTPUT_TTL_SECONDS = 5.0
_recent_bot_outputs: deque[tuple[float, int | None, str]] = deque()


def _build_default_response(content: str, *, direct: bool = False) -> str:
    return build_reply(content, direct=direct)


def _build_lua_response(content: str, event: Any, *, quote: bool) -> str | Message:
    if not quote:
        return content
    return _build_quoted_response(content, event)


def _build_quoted_response(content: str, event: Any) -> Message:
    return MessageSegment.reply(event.message_id) + Message(content)


def _build_random_group_response(response: str | Path) -> str | MessageSegment:
    if isinstance(response, Path):
        return MessageSegment.image(response.resolve().as_uri())
    return response


def _build_miniapp_image_response(cached: CachedMiniAppImages) -> Message:
    response = Message()
    for path in cached.paths:
        response += MessageSegment.image(path.resolve().as_uri())
    return response


def _normalize_message_text(value: Any) -> str:
    return str(value or "").strip()


def _recent_output_signature(event: Any, value: Any) -> tuple[int | None, str]:
    group_id = getattr(event, "group_id", None)
    normalized_group_id = int(group_id) if group_id is not None else None
    return normalized_group_id, _normalize_message_text(value)


def _prune_recent_bot_outputs(now: float | None = None) -> None:
    current = time.time() if now is None else float(now)
    while _recent_bot_outputs and current - _recent_bot_outputs[0][0] > _RECENT_BOT_OUTPUT_TTL_SECONDS:
        _recent_bot_outputs.popleft()


def _remember_recent_bot_output(
    event: Any,
    response: str | Message | MessageSegment,
    *,
    now: float | None = None,
) -> None:
    signature = _recent_output_signature(event, response)
    if not signature[1]:
        return
    current = time.time() if now is None else float(now)
    _prune_recent_bot_outputs(current)
    _recent_bot_outputs.append((current, signature[0], signature[1]))


def _is_recent_bot_output_event(event: Any, *, now: float | None = None) -> bool:
    current = time.time() if now is None else float(now)
    _prune_recent_bot_outputs(current)
    raw_message = getattr(event, "raw_message", None)
    if raw_message is None:
        raw_message = getattr(event, "message", "")
    signature = _recent_output_signature(event, raw_message)
    return any((group_id, message) == signature for _, group_id, message in _recent_bot_outputs)


async def _finish_with_response(
    matcher: Any,
    bot: Bot,
    event: Any,
    response: str | Message | MessageSegment,
    *,
    explicit_group_send: bool,
) -> None:
    _remember_recent_bot_output(event, response)
    if explicit_group_send:
        group_id = getattr(event, "group_id", None)
        if group_id is not None:
            await bot.send_group_msg(group_id=int(group_id), message=response)
        await matcher.finish()

    await matcher.finish(response)


async def _send_response(
    matcher: Any,
    bot: Bot,
    event: Any,
    response: str | Message | MessageSegment,
    *,
    explicit_group_send: bool,
) -> None:
    _remember_recent_bot_output(event, response)
    if explicit_group_send:
        group_id = getattr(event, "group_id", None)
        if group_id is not None:
            await bot.send_group_msg(group_id=int(group_id), message=response)
        return
    await matcher.send(response)


async def _handle_onebot_message(
    matcher: Any,
    bot: Bot,
    event: Any,
    *,
    explicit_group_send: bool = False,
) -> None:
    internal_event = onebot_to_internal(event, self_id=bot.self_id)
    _record_group_activity(internal_event, self_id=bot.self_id)
    miniapp_image_source = extract_miniapp_image_source(internal_event.segments)
    if (
        miniapp_image_source is not None
        and _automatic_reply_allowed(internal_event)
        and _miniapp_image_source_allowed(miniapp_image_source, internal_event)
    ):
        try:
            cached_images = await cache_miniapp_images(miniapp_image_source)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning(f"Failed to cache mini app images: {exc}")
            cached_images = CachedMiniAppImages(directory=None, paths=())
        try:
            if cached_images.paths:
                await _finish_with_response(
                    matcher,
                    bot,
                    event,
                    _build_miniapp_image_response(cached_images),
                    explicit_group_send=explicit_group_send,
                )
        finally:
            cached_images.cleanup()

    pending_command = pending_lua_command(internal_event)
    if pending_command is not None:
        lua_result = await run_lua_message(
            bot,
            internal_event,
            PolicyDecision(
                True,
                "ok",
                handler="lua",
                normalized_message=pending_command,
            ),
        )
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
        if internal_event.is_at_bot and decision.reason in {
            "group_rate_limited",
            "user_rate_limited",
        }:
            await _finish_with_response(
                matcher,
                bot,
                event,
                "问得太快啦，让我缓一小会儿嘛 (｡•́︿•̀｡)",
                explicit_group_send=explicit_group_send,
            )
        if decision.reason == "no_trigger":
            try:
                response = await generate_random_group_reply(
                    internal_event,
                    get_settings(),
                    get_store(),
                )
            except DSAPIError as exc:
                logger.warning(f"DSAPI random group reply failed: {exc}")
                return
            if response:
                await _finish_with_response(
                    matcher,
                    bot,
                    event,
                    _build_random_group_response(response),
                    explicit_group_send=explicit_group_send,
                )
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

    if decision.handler == "mention":
        try:
            response = await generate_mention_reply(
                bot,
                internal_event,
                get_settings(),
                get_store(),
            )
        except DSAPIError as exc:
            logger.warning(f"DSAPI mention reply failed: {exc}")
            await _finish_with_response(
                matcher,
                bot,
                event,
                "脑袋刚刚卡住啦，再问我一次嘛 (｡•́︿•̀｡)",
                explicit_group_send=explicit_group_send,
            )
            return
        if response:
            await _finish_with_response(
                matcher,
                bot,
                event,
                response,
                explicit_group_send=explicit_group_send,
            )
        return

    if decision.handler == "lua":
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
    if _is_recent_bot_output_event(event):
        return
    await _handle_onebot_message(self_sent, bot, event, explicit_group_send=True)


def _record_group_activity(event: Any, *, self_id: int | str) -> None:
    if event.group_id is None or str(event.user_id) == str(self_id):
        return

    store = get_store()
    mode = store.get_mode()
    if mode == "allowlist" and not store.is_group_enabled(event.group_id):
        return
    if mode == "blocklist" and store.is_group_blocked(event.group_id):
        return

    try:
        store.record_group_message_activity(
            group_id=event.group_id,
            user_id=event.user_id,
            timestamp=event.timestamp,
            raw_message=event.raw_message,
            segments=event.segments,
        )
    except Exception as exc:
        logger.warning(f"Failed to record group activity: {exc}")


def _automatic_reply_allowed(event: Any) -> bool:
    if event.group_id is None:
        return False
    store = get_store()
    mode = store.get_mode()
    if mode == "allowlist":
        return store.is_group_enabled(event.group_id)
    if mode == "blocklist":
        return not store.is_group_blocked(event.group_id)
    return True


def _miniapp_image_source_allowed(source: Any, event: Any) -> bool:
    if source.platform != "bilibili":
        return True
    if event.group_id is None:
        return False
    return not get_store().is_bilibili_group_blocked(event.group_id)
