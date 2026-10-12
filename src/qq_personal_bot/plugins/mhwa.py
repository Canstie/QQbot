from __future__ import annotations

import asyncio

from nonebot import logger, on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from qq_personal_bot.mhwa.store import MhwaQueryError
from qq_personal_bot.runtime import get_mhwa_service, get_policy_engine, get_store

mhwa = on_command("荒野", priority=6, block=True)
_HELP = "/荒野 技能 <技能名称>：全部等级效果图\n/荒野 任务 <任务名称或编号>：目标、单人血量、肉质及部位数据图"


@mhwa.handle()
async def handle_mhwa(
    matcher: Matcher,
    event: MessageEvent,
    args: Message = CommandArg(),  # noqa: B008 - NoneBot dependency marker
) -> None:
    await _handle_mhwa(matcher, event, args.extract_plain_text().strip())


async def _handle_mhwa(matcher: Matcher, event: MessageEvent, text: str) -> None:
    store = get_store()
    group_id = getattr(event, "group_id", None)
    if group_id is None or not store.is_feature_enabled("mhwa.master"):
        return
    if store.get_mode() == "allowlist":
        if not store.is_group_enabled(int(group_id)):
            return
    elif store.is_group_blocked(int(group_id)):
        return
    parts = text.split(maxsplit=1)
    command = parts[0] if parts else ""
    if command not in {"技能", "任务"} or len(parts) < 2:
        await matcher.finish(_HELP)
        return
    if not store.is_feature_enabled("mhwa.skills" if command == "技能" else "mhwa.quests"):
        return
    limiter = get_policy_engine().rate_limiter
    limiter.update_limits(store.get_per_group_seconds(), store.get_per_user_per_minute())
    allowed, _ = limiter.allow(int(group_id), int(event.user_id))
    if not allowed:
        return
    try:
        paths = await asyncio.to_thread(get_mhwa_service().query, command, parts[1].strip())
        # Bytes work when OneBot is on another host, without sharing local file paths.
        bodies = await asyncio.to_thread(lambda: [p.read_bytes() for p in paths])
    except MhwaQueryError as exc:
        await matcher.finish(str(exc))
        return
    except Exception:  # noqa: BLE001 - query boundary reports a clean failure
        logger.exception("MHWA image query failed")
        await matcher.finish("荒野资料图片生成失败，请稍后重试。")
        return
    await matcher.finish(Message([MessageSegment.image(body) for body in bodies]))
