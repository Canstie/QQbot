from __future__ import annotations

from functools import lru_cache

from nonebot import get_bot, get_driver, on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg

from qq_personal_bot.runtime import get_policy_engine, get_settings, get_store, get_weizhu_store
from qq_personal_bot.weizhu.service import WeizhuService
from qq_personal_bot.weizhu.store import Chat

weizhu = on_command("卫戍", force_whitespace=True, priority=6, block=True)


def _chat(event: MessageEvent) -> Chat | None:
    if isinstance(event, GroupMessageEvent):
        if any(
            s.type == "at" and str(s.data.get("qq")) == str(event.self_id)
            for s in event.original_message
        ):
            return Chat(event.self_id, event.user_id, group_id=event.group_id)
    elif event.message_type == "private":
        if event.sub_type == "friend":
            return Chat(event.self_id, event.user_id)
        source = str(getattr(event.sender, "group_id", ""))
        if (
            event.sub_type == "group"
            and event.sender.user_id in (None, event.user_id)
            and source.isascii()
            and source.isdecimal()
            and 0 < len(source) <= 15
            and int(source) > 0
        ):
            return Chat(event.self_id, event.user_id, temporary_group=int(source))
    return None


def _allowed(chat: Chat) -> bool:
    store = get_store()
    if not store.is_feature_enabled("weizhu.master"):
        return False
    if chat.group_id is None:
        return True
    if store.get_mode() == "allowlist":
        return store.is_group_enabled(chat.group_id)
    return not store.is_group_blocked(chat.group_id)


async def _send(chat: Chat, text: str, image: bytes | None):
    bot = get_bot(str(chat.bot_id))
    msg = Message()
    if text:
        msg.append(MessageSegment.text(text))
    if image:
        msg.append(MessageSegment.image(image))
    if chat.group_id is not None:
        response = await bot.send_group_msg(group_id=chat.group_id, message=msg)
    else:
        route = (
            {"group_id": chat.temporary_group, "self_id": chat.bot_id}
            if chat.temporary_group
            else {}
        )
        response = await bot.send_private_msg(user_id=chat.user_id, message=msg, **route)
    if not isinstance(response, dict) or not response.get("message_id"):
        raise RuntimeError("OneBot 未确认消息送达")


@lru_cache(maxsize=1)
def _service():
    return WeizhuService(get_settings(), get_weizhu_store(), _send, _allowed)


@get_driver().on_startup
async def start():
    _service().start()


@get_driver().on_shutdown
async def stop():
    await _service().stop()


@weizhu.handle()
async def handle(event: MessageEvent, args: Message = CommandArg()):  # noqa: B008 - NoneBot injection
    chat = _chat(event)
    if chat is None or not _allowed(chat):
        return
    store = get_store()
    limiter = get_policy_engine().rate_limiter
    limiter.update_limits(store.get_per_group_seconds(), store.get_per_user_per_minute())
    allowed, _ = limiter.allow(chat.group_id, chat.user_id)
    if allowed:
        await _service().handle(chat, args.extract_plain_text().strip())
