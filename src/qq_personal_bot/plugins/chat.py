from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Any

from nonebot import logger, on, on_message
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, Message, MessageSegment

from qq_personal_bot.activity import (
    GroupActivityRecord,
    flush_group_activity,
    get_activity_recorder,
)
from qq_personal_bot.adapters.onebot import onebot_to_internal
from qq_personal_bot.classic_forward import send_all_classics
from qq_personal_bot.core.models import PolicyDecision
from qq_personal_bot.dsapi import (
    DSAPIError,
    generate_mention_reply,
    generate_random_group_reply,
)
from qq_personal_bot.features import feature_id_for_platform
from qq_personal_bot.group_digest import GroupDigestEmptyError, generate_group_digest_report
from qq_personal_bot.lua_runner import pending_lua_command, run_lua_message
from qq_personal_bot.miniapp import (
    CachedMiniAppImages,
    XiaoheiheCaptchaRequired,
    cache_miniapp_images,
    extract_miniapp_image_source,
)
from qq_personal_bot.performance import (
    LatencyTrace,
    annotate_latency,
    bind_latency_trace,
    latency_phase,
    reset_latency_trace,
)
from qq_personal_bot.plugins.custom_flows import handle_custom_flow
from qq_personal_bot.random_gallery import (
    MAX_FORWARD_IMAGES,
    send_random_gallery,
    send_random_gallery_forward,
)
from qq_personal_bot.replies import build_reply
from qq_personal_bot.runtime import get_policy_engine, get_settings, get_store
from qq_personal_bot.teachers import parse_teacher_command, query_teachers
from qq_personal_bot.xiaoheihe_captcha import (
    XIAOHEIHE_CAPTCHA_TTL_SECONDS,
    build_xiaoheihe_captcha_url,
    get_xiaoheihe_captcha_store,
)

chat = on_message(priority=50, block=False)
self_sent = on("message_sent", priority=50, block=False)
_RECENT_BOT_OUTPUT_TTL_SECONDS = 5.0
_recent_bot_outputs: deque[tuple[float, int | None, str]] = deque()


def _build_default_response(content: str, *, direct: bool = False) -> str:
    return build_reply(content, direct=direct)


def _build_lua_response(content: str, event: Any, *, quote: bool) -> str | Message:
    if not quote:
        if "[CQ:" in content:
            return Message(content)
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


async def _finish_ai_response(
    matcher: Any,
    bot: Bot,
    event: Any,
    response: str | list[str] | Path,
    *,
    explicit_group_send: bool,
    random_reply: bool = False,
) -> None:
    parts: list[str | Path] = response if isinstance(response, list) else [response]
    for part in parts[:-1]:
        rendered = _build_random_group_response(part) if random_reply else part
        await _send_response(
            matcher,
            bot,
            event,
            rendered,
            explicit_group_send=explicit_group_send,
        )
    last = parts[-1]
    rendered = _build_random_group_response(last) if random_reply else last
    await _finish_with_response(
        matcher,
        bot,
        event,
        rendered,
        explicit_group_send=explicit_group_send,
    )


async def _send_xiaoheihe_captcha_to_group(
    bot: Bot,
    event: Any,
    source_url: str,
    appid: str,
) -> None:
    group_id = getattr(event, "group_id", None)
    if group_id is None:
        return

    settings = get_settings()
    challenge, created = get_xiaoheihe_captcha_store().create(
        source_url=source_url,
        group_id=int(group_id),
        bot_id=str(bot.self_id),
        appid=appid,
    )
    if not created:
        return

    try:
        verification_url = build_xiaoheihe_captcha_url(
            settings.public_base_url,
            challenge.token,
        )
    except ValueError as exc:
        get_xiaoheihe_captcha_store().consume(challenge.token)
        logger.error(f"Cannot send Xiaoheihe CAPTCHA link to group: {exc}")
        return

    minutes = XIAOHEIHE_CAPTCHA_TTL_SECONDS // 60
    message = (
        "小黑盒图片解析需要验证码。\n"
        f"请在 {minutes} 分钟内复制链接到手机系统浏览器打开：\n{verification_url}\n"
        "验证通过后，机器人会自动把图片发回本群。"
    )
    _remember_recent_bot_output(event, message)
    try:
        await bot.send_group_msg(group_id=int(group_id), message=message)
    except Exception as exc:  # noqa: BLE001 - OneBot adapters expose varied errors
        get_xiaoheihe_captcha_store().consume(challenge.token)
        logger.warning(f"Failed to send Xiaoheihe CAPTCHA link to group {group_id}: {exc}")


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
    with latency_phase("send"):
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
    with latency_phase("send"):
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
    trace = LatencyTrace("onebot_message")
    trace.annotate(event_kind="self_sent" if explicit_group_send else "group_message")
    token = bind_latency_trace(trace)
    try:
        await _dispatch_onebot_message(
            matcher,
            bot,
            event,
            explicit_group_send=explicit_group_send,
        )
    finally:
        reset_latency_trace(token)
        trace.emit()


async def _dispatch_onebot_message(
    matcher: Any,
    bot: Bot,
    event: Any,
    *,
    explicit_group_send: bool = False,
) -> None:
    with latency_phase("parse"):
        internal_event = onebot_to_internal(event, self_id=bot.self_id)
    with latency_phase("activity_enqueue"):
        _record_group_activity(internal_event, self_id=bot.self_id)
    miniapp_image_source = extract_miniapp_image_source(internal_event.segments)
    miniapp_feature = (
        feature_id_for_platform(miniapp_image_source.platform)
        if miniapp_image_source is not None
        else None
    )
    if (
        miniapp_image_source is not None
        and (miniapp_feature is None or get_store().is_feature_enabled(miniapp_feature))
        and _automatic_reply_allowed(internal_event)
        and _miniapp_image_source_allowed(miniapp_image_source, internal_event)
    ):
        try:
            with latency_phase("miniapp_fetch"):
                cached_images = await cache_miniapp_images(miniapp_image_source)
        except XiaoheiheCaptchaRequired as exc:
            await _send_xiaoheihe_captcha_to_group(
                bot,
                event,
                miniapp_image_source.source_url,
                exc.appid,
            )
            cached_images = CachedMiniAppImages(directory=None, paths=())
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
        annotate_latency(handler="lua_pending")
        with latency_phase("lua"):
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

    with latency_phase("policy"):
        decision = get_policy_engine().evaluate(internal_event, self_id=bot.self_id)
    annotate_latency(handler=decision.handler or "none", outcome=decision.reason)
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
            if not (
                get_store().is_feature_enabled("ai.master")
                and get_store().is_feature_enabled("ai.random")
            ):
                return
            try:
                with latency_phase("deepseek"):
                    response = await generate_random_group_reply(
                        internal_event,
                        get_settings(),
                        get_store(),
                    )
            except DSAPIError as exc:
                logger.warning(f"DSAPI random group reply failed: {exc}")
                return
            if response:
                await _finish_ai_response(
                    matcher,
                    bot,
                    event,
                    response,
                    explicit_group_send=explicit_group_send,
                    random_reply=True,
                )
        return

    if decision.handler == "default" and decision.normalized_message.strip() == "爆典all":
        if not get_store().is_feature_enabled("classics.forward_all"):
            return
        async def send_classic_forward(nodes: list[dict]) -> None:
            await bot.call_api("send_group_forward_msg", group_id=int(internal_event.group_id),
                               messages=nodes, _timeout=600)

        async def send_classic_notice(text: str) -> None:
            await _send_response(matcher, bot, event, MessageSegment.text(text),
                                 explicit_group_send=explicit_group_send)

        with latency_phase("classics"):
            await send_all_classics(internal_event.group_id, str(bot.self_id),
                                    send_classic_forward, send_classic_notice)
        return

    gallery_parts = decision.normalized_message.strip().split()
    if decision.handler == "default" and gallery_parts and gallery_parts[0] == "涩图":
        if not get_store().is_feature_enabled("gallery.random"):
            return
        count = None
        if len(gallery_parts) > 1:
            value = gallery_parts[1]
            if (len(gallery_parts) != 2 or len(value) > 3 or not value.isascii()
                    or not value.isdecimal() or not 1 <= int(value) <= MAX_FORWARD_IMAGES):
                await _send_response(
                    matcher, bot, event,
                    MessageSegment.text(f"用法：~涩图 [数量]，数量须在 1—{MAX_FORWARD_IMAGES} 之间。"),
                    explicit_group_send=explicit_group_send,
                )
                return
            count = int(value)

        if count is None:
            async def send_gallery_image(path: Path) -> None:
                await _send_response(
                    matcher, bot, event, MessageSegment.image(path.resolve().as_uri()),
                    explicit_group_send=explicit_group_send,
                )

            with latency_phase("gallery"):
                notice = await send_random_gallery(send_gallery_image)
        else:
            async def send_gallery_forward(nodes: list[dict]) -> None:
                await bot.call_api("send_group_forward_msg", group_id=int(internal_event.group_id),
                                   messages=nodes, _timeout=600)

            with latency_phase("gallery"):
                notice = await send_random_gallery_forward(send_gallery_forward, str(bot.self_id), count)
        if notice:
            await _send_response(matcher, bot, event, MessageSegment.text(notice),
                                 explicit_group_send=explicit_group_send)
        return

    digest_parts = decision.normalized_message.split() if decision.handler == "default" else []
    if digest_parts and digest_parts[0] == "总结":
        store = get_store()
        if not store.is_admin(int(internal_event.user_id)):
            return
        if not all(
            store.is_feature_enabled(feature_id)
            for feature_id in ("activity.record", "lua.master", "lua.command.总结")
        ):
            return
        if digest_parts not in (["总结"], ["总结", "昨天"]):
            await _send_response(
                matcher,
                bot,
                event,
                MessageSegment.text("用法：~总结 或 ~总结 昨天"),
                explicit_group_send=explicit_group_send,
            )
            return
        yesterday = len(digest_parts) == 2
        await _send_response(
            matcher,
            bot,
            event,
            MessageSegment.text(
                f"⏳ 正在整理{'昨天' if yesterday else '今天'}的聊天记录并生成群聊速报……"
            ),
            explicit_group_send=explicit_group_send,
        )
        with latency_phase("activity_flush"):
            await flush_group_activity()
        try:
            with latency_phase("digest"):
                report = await generate_group_digest_report(
                    bot,
                    internal_event,
                    get_settings(),
                    store,
                    yesterday=yesterday,
                )
        except GroupDigestEmptyError as exc:
            await _send_response(
                matcher,
                bot,
                event,
                MessageSegment.text(str(exc)),
                explicit_group_send=explicit_group_send,
            )
            return
        except DSAPIError as exc:
            logger.warning(f"Daily group digest generation failed: {exc}")
            await _send_response(
                matcher,
                bot,
                event,
                MessageSegment.text("群聊速报生成失败，请检查 DeepSeek 配置后再试。"),
                explicit_group_send=explicit_group_send,
            )
            return
        except Exception as exc:  # noqa: BLE001 - keep the bot handler alive on render failures
            logger.exception(f"Daily group digest rendering failed: {exc}")
            await _send_response(
                matcher,
                bot,
                event,
                MessageSegment.text("群聊速报生成时出了点问题，请稍后再试。"),
                explicit_group_send=explicit_group_send,
            )
            return
        await _send_response(
            matcher,
            bot,
            event,
            MessageSegment.image(report.image_path.as_uri()),
            explicit_group_send=explicit_group_send,
        )
        return

    teacher_args = (
        parse_teacher_command(decision.normalized_message)
        if decision.handler == "default" else None
    )
    if teacher_args is not None:
        if not get_store().is_feature_enabled("teacher.lookup"):
            return
        with latency_phase("teacher"):
            teacher_results = await query_teachers(*teacher_args)
        for text in teacher_results:
            await _send_response(
                matcher, bot, event, MessageSegment.text(text),
                explicit_group_send=explicit_group_send,
            )
        return

    with latency_phase("lua"):
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
        if not (
            get_store().is_feature_enabled("ai.master")
            and get_store().is_feature_enabled("ai.mention")
        ):
            return
        try:
            with latency_phase("deepseek"):
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
            await _finish_ai_response(
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

    if not get_store().is_feature_enabled("replies.fixed"):
        return
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
    if not store.is_feature_enabled("activity.record"):
        return
    mode = store.get_mode()
    if mode == "allowlist" and not store.is_group_enabled(event.group_id):
        return
    if mode == "blocklist" and store.is_group_blocked(event.group_id):
        return

    get_activity_recorder(store).enqueue(
        GroupActivityRecord(
            group_id=event.group_id,
            user_id=event.user_id,
            timestamp=event.timestamp,
            raw_message=event.raw_message,
            segments=tuple(event.segments),
            message_id=event.message_id,
        )
    )


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
