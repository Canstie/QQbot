from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import replace
from io import BytesIO

import httpx
import qrcode

from qq_personal_bot.settings import AppSettings

from .insights import FILTER_HELP, MODES, RESULTS, active_layers, rankings, select_history
from .renderer import WeizhuRenderer
from .skland import SklandAuthError, SklandClient, SklandError, qr_finish, qr_poll, qr_start
from .store import Binding, Chat, WeizhuStore

log = logging.getLogger(__name__)
HELP = (
    "卫戍协议助手（群内请先 @机器人）\n"
    "/卫戍 绑定 · 在当前会话获取森空岛登录二维码\n"
    "/卫戍 查询 · 本赛季统计\n"
    "/卫戍 战绩 [场数] [模式] [结果] · 最近战绩，默认 10 场\n"
    "/卫戍 趋势 [盟约] [模式] · 最近 10 场叠层，如 /卫戍 趋势 灵巧 终极\n"
    f"{FILTER_HELP}\n"
    "/卫戍 对局 [序号] · 单局详情，1 为最近一局\n"
    "/卫戍 推送 开/关 · 在当前会话订阅新战报\n"
    "/卫戍 状态 · 查看绑定和播报状态\n/卫戍 解绑 · 解除绑定或取消扫码"
)
UNBOUND = "尚未绑定，请发送 /卫戍 绑定。"
EXPIRED = "森空岛凭证已失效，请先 /卫戍 解绑，再 /卫戍 绑定。"
ALIASES = {
    "help": "帮助",
    "bind": "绑定",
    "unbind": "解绑",
    "info": "状态",
    "push": "推送",
    "统计": "查询",
    "stats": "查询",
    "history": "战绩",
    "叠层": "趋势",
    "match": "对局",
    "detail": "对局",
    "查战绩": "查",
    "看战绩": "查",
    "看统计": "查统计",
    "统计别人": "查统计",
    "绑定用户": "用户",
    "用户列表": "用户",
}
Send = Callable[[Chat, str, bytes | None], Awaitable[None]]


def end_time(record: dict) -> int:
    try:
        timestamp = int(record.get("gameEndTs") or 0)
        return timestamp // 1000 if timestamp > 10**12 else timestamp
    except (ValueError, TypeError):
        return 0


def number(value: str, default: int, maximum: int | None = None) -> int:
    try:
        n = max(1, int(value))
    except ValueError:
        n = default
    return min(maximum, n) if maximum else n


class WeizhuService:
    def __init__(
        self, settings: AppSettings, store: WeizhuStore, send: Send, allowed: Callable[[Chat], bool]
    ):
        self.settings = settings
        self.store = store
        self.send = send
        self.allowed = allowed
        self.proxy = settings.weizhu_proxy_url
        self.client = SklandClient(self.proxy)
        self.pending: dict[tuple[int, int], asyncio.Task] = {}
        self.poller: asyncio.Task | None = None

    def start(self):
        if self.poller is None:
            self.poller = asyncio.create_task(self._poll_loop())

    async def stop(self):
        tasks = list(self.pending.values())
        if self.poller is not None:
            tasks.append(self.poller)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.pending.clear()
        self.poller = None
        await self.client.close()

    async def handle(self, chat: Chat, text: str):
        if not self.allowed(chat):
            return
        parts = text.split()
        command = parts[0].lower() if parts else "帮助"
        command = ALIASES.get(command, command)
        try:
            reply, image = await self._command(chat, command, parts[1:])
        except SklandAuthError:
            reply, image = EXPIRED, None
        except Exception as exc:  # noqa: BLE001 - isolate OneBot, storage and upstream failures
            log.warning("卫戍指令处理失败 (%s)", type(exc).__name__)
            reply, image = "卫戍请求失败，请稍后重试。", None
        if (reply or image) and self.allowed(chat):
            await self.send(chat, reply, image)

    async def _command(self, chat: Chat, command: str, args: list[str]):
        if chat.temporary_group and command not in {"帮助", "绑定", "解绑"}:
            return "临时私聊支持绑定、解绑和帮助。请在群内 @机器人 查询战绩或设置推送。", None
        if command == "帮助":
            return HELP, None
        if command in {"查", "查统计", "用户"}:
            if chat.group_id or chat.user_id not in self.settings.weizhu_admin_ids:
                return "仅限授权管理员在私聊中使用。", None
            return await self._admin_query(chat, command, args)
        binding = self.store.get(chat)
        if command == "绑定":
            if binding:
                return "你已绑定 " + binding.nickname + "。换号请先 /卫戍 解绑。", None
            if chat.owner in self.pending:
                return "已有二维码等待扫码。发送 /卫戍 解绑 可以取消。", None
            self.pending[chat.owner] = asyncio.create_task(self._login(chat))
            return "", None
        if command == "解绑":
            pending = self.pending.pop(chat.owner, None)
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            if binding:
                self.store.remove(chat)
                self.client.drop_token(binding.cred)
                return "已解绑卫戍协议账号。", None
            return ("已取消本次扫码绑定。" if pending else UNBOUND), None
        if command not in {"查询", "战绩", "趋势", "对局", "状态", "推送"}:
            return HELP, None
        if binding is None:
            return UNBOUND, None
        arg = args[0] if args else ""
        if command == "推送":
            return self._toggle(chat, binding, arg.lower()), None
        if command == "状态":
            last = (
                time.strftime("%m-%d %H:%M", time.localtime(binding.last_push))
                if binding.last_push
                else "暂无"
            )
            return (
                f"{binding.nickname}（{binding.channel}） · UID {binding.uid}\n"
                f"赛季 {binding.season or '暂无'} · 播报{'开启' if binding.push else '关闭'}\n"
                f"上次播报 {last} · 轮询间隔 {self.settings.weizhu_poll_seconds} 秒"
            ), None
        return await self._query(binding, command, args)

    async def _login(self, chat: Chat):
        try:
            try:
                scan = await qr_start(self.proxy)
                qr = BytesIO()
                qrcode.make(scan["url"]).save(qr, format="PNG")
                if not self.allowed(chat):
                    return
                timeout = self.settings.weizhu_qr_timeout
                async with asyncio.timeout(timeout):
                    await self.send(
                        chat,
                        f"QQ {chat.user_id} 发起了绑定，请本人用森空岛 App 扫码。"
                        f"扫码角色将绑定到此 QQ，二维码 {timeout} 秒内有效。",
                        qr.getvalue(),
                    )
                    while True:
                        try:
                            code = await qr_poll(scan["scan_id"], self.proxy)
                        except httpx.TransportError:
                            code = ""
                        if code:
                            break
                        await asyncio.sleep(2)
                auth = await qr_finish(code, self.proxy)
                cred = auth["cred"]
                role = await self.client.get_arknights_binding(cred)
                if not self.allowed(chat):
                    return
                friend = chat.group_id is None and chat.temporary_group is None
                push = friend and self.settings.weizhu_push_default
                self.store.save(
                    Binding(
                        bot_id=chat.bot_id,
                        user_id=chat.user_id,
                        cred=cred,
                        uid=role["uid"],
                        nickname=role["nickname"],
                        channel=role["channel"],
                        season="",
                        friend=friend,
                        push=push,
                        push_group=None,
                        since=int(time.time()),
                        revision=time.time_ns(),
                    )
                )
                result = (
                    f"绑定成功 · QQ {chat.user_id} → {role['nickname']}（{role['channel']}）\n"
                    f"新对局播报{'开启' if push else '关闭'}。发送 /卫戍 查询 查看赛季统计。"
                )
            except TimeoutError:
                result = "二维码已过期，请重新发送 /卫戍 绑定。"
            except Exception as exc:  # noqa: BLE001 - isolate OneBot, storage and upstream failures
                log.warning("卫戍扫码绑定失败 (%s)", type(exc).__name__)
                result = "绑定失败，请确认森空岛账号已关联明日方舟角色，再发送 /卫戍 绑定。"
            if self.allowed(chat):
                await self.send(chat, result, None)
        except Exception as exc:  # noqa: BLE001 - isolate OneBot, storage and upstream failures
            log.warning("卫戍绑定结果发送失败 (%s)", type(exc).__name__)
        finally:
            if self.pending.get(chat.owner) is asyncio.current_task():
                self.pending.pop(chat.owner, None)

    def _toggle(self, chat: Chat, binding: Binding, arg: str) -> str:
        if arg in {"开", "开启", "on", "1", "true"}:
            if not binding.push or binding.push_group != chat.group_id:
                self.store.save(
                    replace(
                        binding,
                        push=True,
                        push_group=chat.group_id,
                        since=int(time.time()),
                        revision=time.time_ns(),
                    )
                )
            return "已开启，新对局只播报到当前会话。"
        if arg in {"关", "关闭", "off", "0", "false"}:
            self.store.save(replace(binding, push=False, push_group=None, revision=time.time_ns()))
            return "已关闭新对局播报。"
        return "发送 /卫戍 推送 开 或 /卫戍 推送 关。"

    async def _admin_query(self, chat: Chat, command: str, args: list[str]):
        users = self.store.list_users(chat.bot_id)
        if command == "用户" or not args:
            listing = "\n".join(f"{b.user_id} · {b.nickname} · UID {b.uid}" for b in users)
            return "已绑定用户\n" + (listing or "暂无绑定"), None
        target = args[0]
        matches = [b for b in users if target in (str(b.user_id), b.uid, b.nickname)]
        if not matches:
            matches = [b for b in users if target in b.nickname or target in b.uid]
        if len(matches) != 1:
            return "未找到唯一的绑定，请使用完整昵称#编号或 QQ 号。", None
        return await self._query(
            matches[0],
            "查询" if command == "查统计" else "战绩",
            args[1:],
            admin=True,
        )

    async def _history(self, b: Binding):
        resource = await self.client.get_garrison_resource(b.cred, b.uid)
        season = (resource.get("versionInfo") or {}).get("seasonId") or ""
        if not season:
            raise SklandError("缺少赛季信息")
        records = await self.client.get_garrison_history(b.cred, b.uid, season)
        return (
            resource,
            season,
            sorted((r for r in records if end_time(r)), key=end_time, reverse=True),
        )

    async def _player(self, b: Binding) -> dict:
        player = {"uid": b.uid, "nickname": b.nickname, "channel": b.channel}
        try:
            info = await self.client.get_player_info(b.cred, b.uid)
            player["avatar_url"] = ((info.get("status") or {}).get("avatar") or {}).get("url")
        except (SklandError, httpx.HTTPError):
            pass
        return player

    def _render(self, kind, resource, *args):
        with httpx.Client(proxy=self.proxy or None, trust_env=False, timeout=5) as client:
            renderer = WeizhuRenderer(self.store.path.parent / "assets", client, resource)
            methods = {
                "查询": renderer.render_stats_card,
                "战绩": renderer.render_list_card,
                "趋势": renderer.render_trend,
                "对局": renderer.render_match_card,
                "组队": renderer.render_multi_match_card,
            }
            return methods[kind](*args)

    async def _query(self, binding: Binding, kind: str, arguments: list[str], admin: bool = False):
        resource, season, records = await self._history(binding)
        current = self.store.get(binding.destination)
        if not current or current.cred != binding.cred:
            return UNBOUND, None
        if current.season != season and (not current.push or not current.season):
            self.store.save(replace(current, season=season))
        if not records:
            return "本赛季还没有卫戍协议战绩。", None
        arg = arguments[0] if arguments else ""
        caption = ""
        n = number(arg, 20 if admin else 10, 50 if admin else 20)
        if kind == "对局" or (kind == "战绩" and len(arguments) == n == 1 and not admin):
            index = number(arg, 1)
            if index > len(records):
                return f"只有 {len(records)} 局战绩。", None
            args = (await self._player(binding), records[index - 1])
            kind = "对局"
        elif kind == "战绩":
            filters = arguments if arg in MODES else arguments[1:]
            if len(filters) > 2:
                return FILTER_HELP, None
            mode, result = (filters + ["全部", "全部"])[:2]
            if mode not in MODES or result not in RESULTS:
                return FILTER_HELP, None
            selected = select_history(records, mode, result)
            if not selected:
                return "本赛季没有符合筛选条件的对局。", None
            args = (await self._player(binding), selected, n, f"{mode} / {result}")
            caption = "发送 /卫戍 对局 <序号> 查看表中对应单局。"
        elif kind == "趋势":
            name, mode = arg, arguments[1] if len(arguments) > 1 else "全部"
            if len(arguments) == 1 and name in MODES:
                name, mode = "", name
            if len(arguments) > 2 or mode not in MODES:
                return FILTER_HELP, None
            games = select_history(records, mode)
            ranked = rankings(record for _, record in games)
            if not ranked:
                return "该范围暂无激活盟约记录。", None
            names = {
                bond["bondId"]: bond.get("name") or bond["bondId"]
                for bond in resource.get("bonds") or []
                if bond.get("bondId")
            }
            candidates = {bid: names.get(bid, bid) for bid, *_ in ranked}
            bond_id = next((bid for bid, label in candidates.items() if name in ("", bid, label)), None)
            if bond_id is None:
                return "该范围内可查的盟约：" + "、".join(candidates.values()), None
            points = [(index, active_layers(record).get(bond_id)) for index, record in reversed(games[:10])]
            args = (candidates[bond_id], mode, points)
        else:
            args = (await self._player(binding), records)
        if admin:
            rows = [record for _, record in selected[:n]] if kind == "战绩" else records
            passed = sum(bool(r.get("completeState")) for r in rows)
            caption = f"{binding.nickname} · 共 {len(rows)} 场，通过 {passed} 场（{passed / len(rows):.0%}）。"
        image = await asyncio.to_thread(self._render, kind, resource, *args)
        current = self.store.get(binding.destination)
        return (caption, image) if current and current.cred == binding.cred else (UNBOUND, None)

    async def _poll_loop(self):
        await asyncio.sleep(15)
        while True:
            try:
                await self.poll_once()
            except Exception as exc:  # noqa: BLE001 - isolate OneBot, storage and upstream failures
                log.warning("卫戍轮询失败 (%s)", type(exc).__name__)
            await asyncio.sleep(self.settings.weizhu_poll_seconds)

    def _current(self, b: Binding) -> bool:
        current = self.store.get(b.destination)
        return bool(
            current
            and current.push
            and current.revision == b.revision
            and self.allowed(b.destination)
        )

    async def poll_once(self):
        groups = defaultdict(list)
        for b in self.store.subscriptions():
            if not self.allowed(b.destination):
                continue
            try:
                resource, season, records = await self._history(b)
                if not self._current(b):
                    continue
                if b.season != season:
                    # A known season change establishes a fresh baseline. On a first
                    # subscription the opt-in timestamp already excludes older games.
                    cutoff = (
                        max((end_time(r) for r in records), default=b.since)
                        if b.season
                        else b.since
                    )
                    b = replace(b, season=season, since=max(b.since, cutoff))
                    self.store.save(b)
                dest = f"group:{b.push_group}" if b.push_group else f"private:{b.user_id}"
                for record in records:
                    if end_time(record) <= b.since:
                        break
                    scene = record.get("sceneId")
                    key = (
                        b.bot_id,
                        dest,
                        season,
                        str(scene)
                        if scene
                        else f"record:{b.user_id}:{record.get('id') or end_time(record)}",
                    )
                    if key not in groups and self.store.delivered(key):
                        continue
                    groups[key].append((b, record, resource))
            except SklandAuthError:
                if self._current(b):
                    self.store.save(
                        replace(b, push=False, push_group=None, revision=time.time_ns())
                    )
                    if b.friend:
                        await self.send(Chat(b.bot_id, b.user_id), EXPIRED, None)
            except Exception as exc:  # noqa: BLE001 - isolate OneBot, storage and upstream failures
                log.warning("卫戍战绩拉取失败 (%s)", type(exc).__name__)
            await asyncio.sleep(1.2)

        sent = 0
        for key, candidates in sorted(groups.items(), key=lambda pair: end_time(pair[1][0][1])):
            items = [item for item in candidates if self._current(item[0])]
            if not items:
                continue
            b, record, resource = items[0]
            try:
                players = [(await self._player(member), rec) for member, rec, _ in items]
                if len(players) == 1:
                    image = await asyncio.to_thread(self._render, "对局", resource, *players[0])
                    label = "卫戍协议 · 新对局完成"
                else:
                    image = await asyncio.to_thread(self._render, "组队", resource, players)
                    label = f"卫戍协议 · 组队战报（{len(players)} 人同局）"
                members = [item[0] for item in items]
                if not all(self._current(member) for member in members):
                    continue
                await self.send(b.destination, label, image)
                self.store.record_delivery(key, members, int(time.time()))
            except Exception as exc:  # noqa: BLE001 - isolate OneBot, storage and upstream failures
                log.warning("卫戍播报失败 (%s)", type(exc).__name__)
                continue
            sent += 1
            if sent >= self.settings.weizhu_max_push:
                break
