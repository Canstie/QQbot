from __future__ import annotations

from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from qq_personal_bot.runtime import get_store

bot_control = on_command("bot", aliases={"qqbot"}, priority=5, block=True)


def _actor_id(event: MessageEvent) -> int:
    return int(getattr(event, "user_id"))


def _current_group_id(event: MessageEvent) -> int | None:
    if isinstance(event, GroupMessageEvent):
        return int(event.group_id)
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


def _parse_group_id(parts: list[str], event: MessageEvent) -> int:
    if len(parts) >= 2:
        return int(parts[1])
    group_id = _current_group_id(event)
    if group_id is None:
        raise ValueError("group_id is required outside a group chat")
    return group_id


async def _require_admin(matcher: Matcher, event: MessageEvent) -> int:
    actor_id = _actor_id(event)
    if not get_store().is_admin(actor_id):
        await matcher.finish("Permission denied. Add this QQ number to QQBOT_ADMINS first.")
    return actor_id


@bot_control.handle()
async def handle_bot_command(matcher: Matcher, event: MessageEvent, args: Message = CommandArg()):
    actor_id = await _require_admin(matcher, event)
    parts = str(args).strip().split()
    if not parts or parts[0] in {"status", "show"}:
        await matcher.finish(_format_status())

    store = get_store()
    command = parts[0].lower()

    try:
        if command == "mode":
            if len(parts) != 2 or parts[1] not in {"allowlist", "blocklist"}:
                await matcher.finish("Usage: /bot mode allowlist|blocklist")
            store.set_mode(parts[1], actor_id=actor_id)
            await matcher.finish(f"Policy mode set to {parts[1]}.")

        if command == "on":
            group_id = _parse_group_id(parts, event)
            if store.get_mode() == "allowlist":
                store.set_group_enabled(group_id, True, actor_id=actor_id)
                await matcher.finish(f"Group {group_id} enabled.")
            store.set_group_blocked(group_id, False, actor_id=actor_id)
            await matcher.finish(f"Group {group_id} unblocked.")

        if command == "off":
            group_id = _parse_group_id(parts, event)
            if store.get_mode() == "allowlist":
                store.set_group_enabled(group_id, False, actor_id=actor_id)
                await matcher.finish(f"Group {group_id} disabled.")
            store.set_group_blocked(group_id, True, actor_id=actor_id)
            await matcher.finish(f"Group {group_id} blocked.")

        if command == "admin":
            if len(parts) != 3 or parts[1] not in {"add", "remove"}:
                await matcher.finish("Usage: /bot admin add|remove <user_id>")
            target_user_id = int(parts[2])
            if parts[1] == "add":
                store.add_admin(target_user_id, actor_id=actor_id)
                await matcher.finish(f"Admin {target_user_id} added.")
            store.remove_admin(target_user_id, actor_id=actor_id)
            await matcher.finish(f"Admin {target_user_id} removed.")

        if command == "prefix":
            if len(parts) < 2 or parts[1] == "list":
                prefixes = ", ".join(store.prefixes()) or "-"
                await matcher.finish(f"Prefixes: {prefixes}")
            if len(parts) != 3 or parts[1] not in {"add", "remove"}:
                await matcher.finish("Usage: /bot prefix add|remove|list [prefix]")
            if parts[1] == "add":
                store.add_prefix(parts[2], actor_id=actor_id)
                await matcher.finish(f"Prefix {parts[2]} added.")
            store.remove_prefix(parts[2], actor_id=actor_id)
            await matcher.finish(f"Prefix {parts[2]} removed.")

        await matcher.finish(
            "Usage: /bot status | on [group_id] | off [group_id] | "
            "mode allowlist|blocklist | admin add|remove <user_id> | "
            "prefix add|remove|list [prefix]"
        )
    except ValueError as exc:
        await matcher.finish(str(exc))

