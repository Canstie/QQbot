from __future__ import annotations

from typing import Any

from nonebot import on, on_command
from nonebot.adapters.onebot.v11 import Bot, Event, GroupMessageEvent, Message, MessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from qq_personal_bot.ai_models import (
    DSAPI_MODEL_OPTIONS,
    resolve_dsapi_model,
)
from qq_personal_bot.runtime import get_store

bot_control = on_command("bot", aliases={"qqbot"}, priority=5, block=True)
self_sent_control = on("message_sent", priority=4, block=True)


def _actor_id(event: Any) -> int:
    return int(getattr(event, "user_id"))


def _current_group_id(event: Any) -> int | None:
    if isinstance(event, GroupMessageEvent):
        return int(event.group_id)
    group_id = getattr(event, "group_id", None)
    if group_id is not None:
        return int(group_id)
    return None


def _format_status() -> str:
    snapshot = get_store().snapshot()
    enabled = ", ".join(map(str, snapshot["enabled_groups"])) or "-"
    blocked = ", ".join(map(str, snapshot["blocked_groups"])) or "-"
    admins = ", ".join(map(str, snapshot["admins"])) or "-"
    prefixes = ", ".join(snapshot["trigger"]["prefixes"]) or "-"
    return (
        "QQBot status\n"
        f"mode: {snapshot['mode']}\n"
        f"enabled_groups: {enabled}\n"
        f"blocked_groups: {blocked}\n"
        f"admins: {admins}\n"
        f"mention_trigger: {snapshot['trigger']['mention']}\n"
        f"prefixes: {prefixes}\n"
        "limits: "
        f"group={snapshot['limits']['per_group_seconds']}s, "
        f"user={snapshot['limits']['per_user_per_minute']}/min"
    )


def _parse_group_id(parts: list[str], event: Any) -> int:
    if len(parts) >= 2:
        return int(parts[1])
    group_id = _current_group_id(event)
    if group_id is None:
        raise ValueError("group_id is required outside a group chat")
    return group_id


def _format_ai_models(current_model: str) -> str:
    lines = ["AI 模型列表"]
    for index, option in enumerate(DSAPI_MODEL_OPTIONS, start=1):
        current = "（当前）" if option["id"] == current_model else ""
        vision = "，支持引用图片识别" if option["vision"] else ""
        lines.append(
            f"{index}. {option['key']} — {option['id']}{vision}{current}"
        )
    lines.append("使用：/bot aim flash|pro|vision")
    return "\n".join(lines)


def _format_ai_knowledge_bases(items: list[dict[str, Any]]) -> str:
    if not items:
        return "AI 知识库列表\n暂无知识库"
    lines = ["AI 知识库列表"]
    for index, item in enumerate(items, start=1):
        current = "（当前）" if item.get("active") else ""
        lines.append(f"{index}. {item['name']}{current}")
    lines.append("使用：/bot aik <序号>")
    return "\n".join(lines)


async def _require_admin(matcher: Matcher, event: Any) -> int:
    actor_id = _actor_id(event)
    if not get_store().is_admin(actor_id):
        await matcher.finish("Permission denied. Add this QQ number to QQBOT_ADMINS first.")
    return actor_id


async def _send_control_response(
    matcher: Matcher,
    bot: Bot | None,
    event: Any,
    message: str,
    *,
    explicit_group_send: bool,
) -> None:
    if explicit_group_send and bot is not None:
        group_id = _current_group_id(event)
        if group_id is not None:
            await bot.send_group_msg(group_id=group_id, message=message)
            await matcher.finish()
    await matcher.finish(message)


async def _handle_bot_command(
    matcher: Matcher,
    event: Any,
    parts: list[str],
    *,
    bot: Bot | None = None,
    explicit_group_send: bool = False,
) -> None:
    actor_id = await _require_admin(matcher, event)
    if not parts or parts[0] in {"status", "show"}:
        await _send_control_response(
            matcher,
            bot,
            event,
            _format_status(),
            explicit_group_send=explicit_group_send,
        )

    store = get_store()
    command = parts[0].lower()

    try:
        if command == "mode":
            if len(parts) != 2 or parts[1] not in {"allowlist", "blocklist"}:
                await _send_control_response(
                    matcher,
                    bot,
                    event,
                    "Usage: /bot mode allowlist|blocklist",
                    explicit_group_send=explicit_group_send,
                )
            store.set_mode(parts[1], actor_id=actor_id)
            await _send_control_response(
                matcher,
                bot,
                event,
                f"Policy mode set to {parts[1]}.",
                explicit_group_send=explicit_group_send,
            )

        if command == "on":
            group_id = _parse_group_id(parts, event)
            if store.get_mode() == "allowlist":
                store.set_group_enabled(group_id, True, actor_id=actor_id)
                await _send_control_response(
                    matcher,
                    bot,
                    event,
                    f"Group {group_id} enabled.",
                    explicit_group_send=explicit_group_send,
                )
            store.set_group_blocked(group_id, False, actor_id=actor_id)
            await _send_control_response(
                matcher,
                bot,
                event,
                f"Group {group_id} unblocked.",
                explicit_group_send=explicit_group_send,
            )

        if command == "off":
            group_id = _parse_group_id(parts, event)
            if store.get_mode() == "allowlist":
                store.set_group_enabled(group_id, False, actor_id=actor_id)
                await _send_control_response(
                    matcher,
                    bot,
                    event,
                    f"Group {group_id} disabled.",
                    explicit_group_send=explicit_group_send,
                )
            store.set_group_blocked(group_id, True, actor_id=actor_id)
            await _send_control_response(
                matcher,
                bot,
                event,
                f"Group {group_id} blocked.",
                explicit_group_send=explicit_group_send,
            )

        if command == "aion":
            if len(parts) > 2:
                await _send_control_response(
                    matcher,
                    bot,
                    event,
                    "Usage: /bot aion [group_id]",
                    explicit_group_send=explicit_group_send,
                )
            group_id = _parse_group_id(parts, event)
            store.enable_dsapi_group(group_id, actor_id=actor_id)
            await _send_control_response(
                matcher,
                bot,
                event,
                f"AI enabled for group {group_id}.",
                explicit_group_send=explicit_group_send,
            )

        if command == "ai":
            if len(parts) != 2 or parts[1].lower() != "rs":
                await _send_control_response(
                    matcher,
                    bot,
                    event,
                    "Usage: /bot ai rs",
                    explicit_group_send=explicit_group_send,
                )
            deleted = store.clear_dsapi_chat_history(actor_id=actor_id)
            await _send_control_response(
                matcher,
                bot,
                event,
                f"AI 上下文已清空：共删除 {deleted} 条。",
                explicit_group_send=explicit_group_send,
            )

        if command == "aim":
            config = store.get_dsapi_config()
            current_model = str((config.get("active_knowledge") or {}).get("model") or "")
            if len(parts) == 2 and parts[1].lower() == "list":
                await _send_control_response(
                    matcher,
                    bot,
                    event,
                    _format_ai_models(current_model),
                    explicit_group_send=explicit_group_send,
                )
            if len(parts) != 2:
                await _send_control_response(
                    matcher,
                    bot,
                    event,
                    "Usage: /bot aim list|flash|pro|vision",
                    explicit_group_send=explicit_group_send,
                )
            option = resolve_dsapi_model(parts[1])
            knowledge = store.set_active_dsapi_model(option["id"], actor_id=actor_id)
            suffix = (
                "\n🖼 已开启引用图片识别。"
                if option["vision"]
                else "\n📝 当前模型不识别引用图片。"
            )
            await _send_control_response(
                matcher,
                bot,
                event,
                f"AI 模型已切换为 {option['key']}（{knowledge['model']}）。{suffix}",
                explicit_group_send=explicit_group_send,
            )

        if command == "aik":
            knowledge_bases = store.list_dsapi_knowledge_bases()
            if len(parts) == 2 and parts[1].lower() == "list":
                await _send_control_response(
                    matcher,
                    bot,
                    event,
                    _format_ai_knowledge_bases(knowledge_bases),
                    explicit_group_send=explicit_group_send,
                )
            if len(parts) != 2:
                await _send_control_response(
                    matcher,
                    bot,
                    event,
                    "Usage: /bot aik list|<index>",
                    explicit_group_send=explicit_group_send,
                )
            position = int(parts[1])
            if position < 1 or position > len(knowledge_bases):
                raise ValueError("knowledge base index is out of range; use /bot aik list")
            selected = knowledge_bases[position - 1]
            store.activate_dsapi_knowledge_base(
                selected["id"],
                clear_history=True,
                actor_id=actor_id,
            )
            await _send_control_response(
                matcher,
                bot,
                event,
                f"AI 知识库已切换为 {position}. {selected['name']}。",
                explicit_group_send=explicit_group_send,
            )

        if command == "admin":
            if len(parts) >= 2 and parts[1].lower() == "remove":
                await _send_control_response(
                    matcher,
                    bot,
                    event,
                    "Admin removal is Web-only. Use the QQBot admin page.",
                    explicit_group_send=explicit_group_send,
                )
            if len(parts) != 3 or parts[1].lower() != "add":
                await _send_control_response(
                    matcher,
                    bot,
                    event,
                    "Usage: /bot admin add <user_id>",
                    explicit_group_send=explicit_group_send,
                )
            target_user_id = int(parts[2])
            store.add_admin(target_user_id, actor_id=actor_id)
            await _send_control_response(
                matcher,
                bot,
                event,
                f"Admin {target_user_id} added.",
                explicit_group_send=explicit_group_send,
            )

        if command == "prefix":
            if len(parts) < 2 or parts[1] == "list":
                prefixes = ", ".join(store.prefixes()) or "-"
                await _send_control_response(
                    matcher,
                    bot,
                    event,
                    f"Prefixes: {prefixes}",
                    explicit_group_send=explicit_group_send,
                )
            if len(parts) != 3 or parts[1] not in {"add", "remove"}:
                await _send_control_response(
                    matcher,
                    bot,
                    event,
                    "Usage: /bot prefix add|remove|list [prefix]",
                    explicit_group_send=explicit_group_send,
                )
            if parts[1] == "add":
                store.add_prefix(parts[2], actor_id=actor_id)
                await _send_control_response(
                    matcher,
                    bot,
                    event,
                    f"Prefix {parts[2]} added.",
                    explicit_group_send=explicit_group_send,
                )
            store.remove_prefix(parts[2], actor_id=actor_id)
            await _send_control_response(
                matcher,
                bot,
                event,
                f"Prefix {parts[2]} removed.",
                explicit_group_send=explicit_group_send,
            )

        await _send_control_response(
            matcher,
            bot,
            event,
            (
                "Usage: /bot status | on [group_id] | off [group_id] | "
                "aion [group_id] | ai rs | aim list|flash|pro|vision | "
                "aik list|<index> | "
                "mode allowlist|blocklist | admin add <user_id> | "
                "prefix add|remove|list [prefix]"
            ),
            explicit_group_send=explicit_group_send,
        )
    except ValueError as exc:
        await _send_control_response(
            matcher,
            bot,
            event,
            str(exc),
            explicit_group_send=explicit_group_send,
        )


@bot_control.handle()
async def handle_bot_command(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()):
    await _handle_bot_command(matcher, event, str(args).strip().split())


@self_sent_control.handle()
async def handle_self_sent_bot_command(matcher: Matcher, bot: Bot, event: Event):
    raw_message = str(getattr(event, "raw_message", "")).strip()
    for command in ("/bot", "/qqbot"):
        if raw_message == command or raw_message.startswith(f"{command} "):
            args = raw_message[len(command) :].strip().split()
            await _handle_bot_command(
                matcher,
                event,
                args,
                bot=bot,
                explicit_group_send=True,
            )
    return

