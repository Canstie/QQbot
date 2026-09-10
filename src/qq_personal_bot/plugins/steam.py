from __future__ import annotations

import re
from typing import Any

from nonebot import get_driver, logger, on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
)
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from qq_personal_bot.runtime import get_steam_service, get_store
from qq_personal_bot.steam.client import SteamClientError

steam = on_command("steam", priority=6, block=True)
steamwho = on_command("steamwho", aliases={"在干嘛"}, priority=6, block=True)
driver = get_driver()


@driver.on_startup
async def start_steam_monitor() -> None:
    await get_steam_service().start()


@driver.on_shutdown
async def stop_steam_monitor() -> None:
    await get_steam_service().stop()


@steam.handle()
async def handle_steam(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
    args: Message = CommandArg(),  # noqa: B008 - NoneBot dependency marker
) -> None:
    store = get_store()
    if not (
        store.is_feature_enabled("steam.master", False)
        and store.is_feature_enabled("steam.commands")
    ):
        return
    if not isinstance(event, GroupMessageEvent) or not _group_allowed(int(event.group_id)):
        return

    plain = args.extract_plain_text().strip()
    parts = plain.split()
    command = parts[0].casefold() if parts else "help"
    rest = parts[1:]
    group_id = int(event.group_id)
    actor_id = int(event.user_id)

    mutation_commands = {
        "on",
        "off",
        "addid",
        "delid",
        "bind",
        "unbind",
        "push_group",
        "delpush_group",
        "achievement_on",
        "achievement_off",
    }
    if command in mutation_commands and not _can_manage_group(event):
        return

    try:
        if command == "on":
            current = store.get_steam_group(group_id)
            store.set_steam_group(
                group_id,
                monitor_enabled=True,
                achievement_enabled=current["achievement_enabled"],
                actor_id=actor_id,
            )
            get_steam_service().wake()
            await matcher.finish("Steam 玩家监控已开启。")
        if command == "off":
            current = store.get_steam_group(group_id)
            store.set_steam_group(
                group_id,
                monitor_enabled=False,
                achievement_enabled=current["achievement_enabled"],
                actor_id=actor_id,
            )
            get_steam_service().wake()
            await matcher.finish("Steam 玩家监控已关闭。")
        if command in {"achievement_on", "achievement_off"}:
            current = store.get_steam_group(group_id)
            enabled = command == "achievement_on"
            store.set_steam_group(
                group_id,
                monitor_enabled=current["monitor_enabled"],
                achievement_enabled=enabled,
                actor_id=actor_id,
            )
            get_steam_service().wake()
            await matcher.finish(f"Steam 成就通知已{'开启' if enabled else '关闭'}。")
        if command in {"addid", "push_group"}:
            if not rest:
                await matcher.finish("用法：/steam addid <SteamID/链接/好友码> [@用户] [备注]")
            steam_id = await get_steam_service().client.resolve_identifier(rest[0])
            alias = " ".join(rest[1:]).strip()
            item = store.add_steam_subscription(
                group_id,
                steam_id,
                alias=alias,
                actor_id=actor_id,
            )
            at_user = _first_at(args)
            if at_user is not None:
                store.bind_steam_user(group_id, at_user, steam_id, actor_id=actor_id)
            get_steam_service().wake()
            await matcher.finish(
                f"已监控 {item['alias'] or item['persona_name'] or steam_id}"
                + (f"，并绑定 QQ {at_user}" if at_user is not None else "")
                + "。"
            )
        if command in {"delid", "delpush_group"}:
            if not rest:
                await matcher.finish("用法：/steam delid <SteamID/链接/好友码>")
            steam_id = await get_steam_service().client.resolve_identifier(rest[0])
            removed = store.remove_steam_subscription(group_id, steam_id, actor_id=actor_id)
            get_steam_service().wake()
            await matcher.finish("已移除监控。" if removed else "这个玩家不在本群监控列表中。")
        if command == "bind":
            if not rest or _first_at(args) is None:
                await matcher.finish("用法：/steam bind <SteamID/链接/好友码> @用户")
            steam_id = await get_steam_service().client.resolve_identifier(rest[0])
            user_id = _first_at(args)
            store.bind_steam_user(group_id, int(user_id), steam_id, actor_id=actor_id)
            await matcher.finish(f"已将 QQ {user_id} 绑定到 Steam {steam_id}。")
        if command == "unbind":
            user_id = _first_at(args)
            if user_id is None and rest and rest[0].isdigit():
                user_id = int(rest[0])
            if user_id is None:
                await matcher.finish("用法：/steam unbind @用户")
            removed = store.unbind_steam_user(group_id, user_id, actor_id=actor_id)
            await matcher.finish("已解除绑定，Steam 玩家仍会继续监控。" if removed else "没有找到绑定。")
        if command == "list":
            if not store.is_feature_enabled("steam.player_lookup"):
                return
            await matcher.finish(_format_players(store.list_steam_subscriptions(group_id=group_id)))
        if command == "alllist":
            if not store.is_admin(actor_id) or not store.is_feature_enabled("steam.player_lookup"):
                return
            await matcher.finish(_format_players(store.list_steam_subscriptions()))
        if command == "openbox":
            if not store.is_feature_enabled("steam.player_lookup"):
                return
            if not rest:
                await matcher.finish("用法：/steam openbox <SteamID/链接/好友码>")
            steam_id = await get_steam_service().client.resolve_identifier(rest[0])
            players = await get_steam_service().client.get_player_summaries([steam_id])
            if not players:
                await matcher.finish("没有找到这个玩家，或其资料不可见。")
            player = store.upsert_steam_player(players[0], checked_at=get_steam_service().clock())
            await matcher.finish(_format_player_detail(player))
        if command == "game":
            if not store.is_feature_enabled("steam.game_detail"):
                return
            await matcher.finish(await _game_detail(" ".join(rest)))
        if command in {"price", "px"}:
            if not store.is_feature_enabled("steam.price"):
                return
            await matcher.finish(await _price_detail(" ".join(rest)))
        await matcher.finish(_help_text())
    except FinishedException:
        raise
    except (SteamClientError, ValueError, KeyError) as exc:
        await matcher.finish(str(exc))
    except Exception as exc:  # noqa: BLE001 - command boundary must not escape
        logger.exception("Steam command failed")
        await matcher.finish(f"Steam 操作失败：{exc}")


@steamwho.handle()
async def handle_steamwho(
    matcher: Matcher,
    event: MessageEvent,
    args: Message = CommandArg(),  # noqa: B008 - NoneBot dependency marker
) -> None:
    store = get_store()
    if not all(
        (
            store.is_feature_enabled("steam.master", False),
            store.is_feature_enabled("steam.commands"),
            store.is_feature_enabled("steam.player_lookup"),
        )
    ):
        return
    if not isinstance(event, GroupMessageEvent) or not _group_allowed(int(event.group_id)):
        return
    user_id = _first_at(args)
    if user_id is None:
        plain = args.extract_plain_text().strip()
        user_id = int(plain) if plain.isdigit() else None
    if user_id is None:
        await matcher.finish("请 @ 要查询的群成员。")
    try:
        binding = store.get_steam_binding(int(event.group_id), user_id)
    except KeyError:
        await matcher.finish("这个群成员还没有绑定 Steam。")
    await matcher.finish(_format_binding(binding))


def _first_at(message: Message) -> int | None:
    for segment in message:
        if segment.type == "at":
            value = str(segment.data.get("qq", ""))
            if value.isdigit():
                return int(value)
    return None


def _can_manage_group(event: GroupMessageEvent) -> bool:
    if get_store().is_admin(int(event.user_id)):
        return True
    return str(getattr(event.sender, "role", "member")) in {"owner", "admin"}


def _group_allowed(group_id: int) -> bool:
    store = get_store()
    if store.get_mode() == "allowlist":
        return store.is_group_enabled(group_id)
    return not store.is_group_blocked(group_id)


def _format_players(items: list[dict[str, Any]]) -> str:
    if not items:
        return "Steam 监控列表为空。"
    lines = ["Steam 玩家监控"]
    for item in items:
        name = item["alias"] or item["persona_name"] or item["steam_id"]
        state = f"正在玩 {item['game_name'] or item['game_id']}" if item["game_id"] else _persona_state(item["persona_state"])
        binding = f" · QQ {item['qq_user_id']}" if item.get("qq_user_id") else ""
        lines.append(f"G/{item['group_id']} · {name} · {state}{binding}")
    return "\n".join(lines)


def _format_player_detail(player: dict[str, Any]) -> str:
    state = f"正在玩 {player['game_name'] or player['game_id']}" if player["game_id"] else _persona_state(player["persona_state"])
    return "\n".join(
        [
            player["persona_name"] or player["steam_id"],
            f"SteamID64：{player['steam_id']}",
            f"状态：{state}",
            f"主页：{player['profile_url'] or '-'}",
        ]
    )


def _format_binding(binding: dict[str, Any]) -> str:
    state = f"正在玩 {binding['game_name'] or binding['game_id']}" if binding["game_id"] else _persona_state(binding["persona_state"])
    return f"{binding['persona_name'] or binding['steam_id']}：{state}"


async def _resolve_app(query: str) -> tuple[str, dict[str, Any]]:
    value = query.strip()
    app_match = re.search(r"(?:/app/)?(\d{2,10})", value)
    settings = get_store().get_steam_settings()
    client = get_steam_service().client
    if app_match and (value.isdigit() or "/app/" in value):
        app_id = app_match.group(1)
    else:
        if not value:
            raise ValueError("请输入游戏名称、AppID 或 Steam 商店链接。")
        results = await client.search_store(value, country=settings["price_country"])
        if not results:
            raise SteamClientError("没有找到对应的 Steam 游戏。")
        app_id = str(results[0]["id"])
    return app_id, await client.get_app_details(app_id, country=settings["price_country"])


async def _game_detail(query: str) -> str:
    app_id, game = await _resolve_app(query)
    genres = "、".join(item.get("description", "") for item in game.get("genres", [])[:4])
    return "\n".join(
        [
            game.get("name", app_id),
            f"AppID：{app_id}",
            f"类型：{genres or '-'}",
            f"发行：{game.get('release_date', {}).get('date', '-')}",
            f"商店：https://store.steampowered.com/app/{app_id}",
        ]
    )


async def _price_detail(query: str) -> str:
    app_id, game = await _resolve_app(query)
    price = game.get("price_overview") or {}
    if game.get("is_free"):
        price_text = "免费"
    elif price:
        price_text = price.get("final_formatted") or str(price.get("final", "-"))
        if price.get("discount_percent"):
            price_text += f"（-{price['discount_percent']}%）"
    else:
        price_text = "当前地区暂无价格"
    lines = [game.get("name", app_id), f"当前价格：{price_text}"]
    try:
        itad = await get_steam_service().client.get_itad_price(
            app_id,
            country=get_store().get_steam_settings()["price_country"],
        )
    except SteamClientError:
        itad = {}
    history = itad.get("historyLow", {}).get("all", {}) if itad else {}
    if history.get("amount") is not None:
        lines.append(f"ITAD 史低：{history['amount']} {history.get('currency', '')}".rstrip())
    lines.append(f"https://store.steampowered.com/app/{app_id}")
    return "\n".join(lines)


def _persona_state(value: int) -> str:
    return {
        0: "离线",
        1: "在线",
        2: "忙碌",
        3: "离开",
        4: "打盹",
        5: "想交易",
        6: "想玩游戏",
    }.get(int(value), "未知")


def _help_text() -> str:
    return (
        "Steam 指令\n"
        "/steam list｜openbox <玩家>｜game <游戏>｜price <游戏>\n"
        "/steam addid <玩家> [@用户] [备注]｜delid <玩家>\n"
        "/steam bind <玩家> @用户｜unbind @用户\n"
        "/steam on｜off｜achievement_on｜achievement_off\n"
        "/steamwho @用户"
    )
