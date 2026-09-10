from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nonebot import get_bots, logger
from nonebot.adapters.onebot.v11 import Message, MessageSegment

from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.settings import AppSettings
from qq_personal_bot.steam.client import SteamClient, SteamClientError
from qq_personal_bot.steam.render import render_status_card
from qq_personal_bot.steam.session import SessionEvent, apply_player_snapshot, close_due_sessions


class SteamMonitorService:
    def __init__(
        self,
        store: PolicyStore,
        settings: AppSettings,
        *,
        client_factory: Callable[[AppSettings], SteamClient] = SteamClient,
        clock: Callable[[], float] = time.time,
    ):
        self.store = store
        self.settings = settings
        self.client = client_factory(settings)
        self.clock = clock
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopping = False
        self._was_active = False
        self._baseline_pending = True
        self.last_poll_at = 0.0
        self.last_success_at = 0.0
        self.next_poll_at = 0.0
        self.last_error = ""
        self.polling = False

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="qqbot-steam-monitor")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.client.close()

    def wake(self) -> None:
        self._wake.set()

    def overview(self) -> dict[str, Any]:
        groups = self.store.list_steam_groups()
        subscriptions = self.store.list_steam_subscriptions()
        sessions = self.store.list_steam_sessions(limit=100)
        return {
            "enabled": self._active(),
            "api_configured": bool(self.settings.steam_api_key),
            "sgdb_configured": bool(self.settings.sgdb_api_key),
            "itad_configured": bool(self.settings.itad_api_key),
            "worker_running": self._task is not None and not self._task.done(),
            "polling": self.polling,
            "last_poll_at": self.last_poll_at,
            "last_success_at": self.last_success_at,
            "next_poll_at": self.next_poll_at,
            "last_error": self.last_error,
            "group_count": len(groups),
            "enabled_group_count": sum(1 for group in groups if group["monitor_enabled"]),
            "player_count": len({item["steam_id"] for item in subscriptions}),
            "active_session_count": sum(
                1 for item in sessions if item["state"] in {"playing", "confirming_exit"}
            ),
        }

    async def tick(self) -> None:
        now = self.clock()
        active = self._active()
        if not active:
            has_open_session = any(
                item["state"] in {"playing", "confirming_exit"}
                for item in self.store.list_steam_sessions(limit=500)
            )
            if self._was_active or has_open_session:
                self.store.close_all_open_steam_sessions(
                    ended_at=now,
                    reason="monitor_disabled",
                )
            self._was_active = False
            self._baseline_pending = True
            self.next_poll_at = 0.0
            return

        self._was_active = True
        self.polling = True
        self.last_poll_at = now
        try:
            for event in close_due_sessions(self.store, now=now):
                await self._notify_session_event(event)

            baseline = self._baseline_pending
            if baseline:
                enabled_groups = {
                    group["group_id"]
                    for group in self.store.list_steam_groups()
                    if group["monitor_enabled"]
                }
                steam_ids = sorted(
                    {
                        item["steam_id"]
                        for item in self.store.list_steam_subscriptions()
                        if item["group_id"] in enabled_groups
                    }
                )
            else:
                steam_ids = self.store.due_steam_ids(now)
            if not steam_ids:
                self._baseline_pending = False
                self.next_poll_at = now + 15
                return

            config = self.store.get_steam_settings()
            for start in range(0, len(steam_ids), 100):
                batch = steam_ids[start : start + 100]
                players = await self.client.get_player_summaries(
                    batch,
                    retries=config["retry_times"],
                )
                by_id = {str(player.get("steamid")): player for player in players}
                for steam_id in batch:
                    player = by_id.get(steam_id)
                    if player is None:
                        continue
                    await self._process_player(
                        player,
                        now=now,
                        config=config,
                        baseline=baseline,
                    )
            self._baseline_pending = False
            self.last_success_at = self.clock()
            self.last_error = ""
            self.next_poll_at = min(
                (
                    item["next_poll_at"]
                    for item in self.store.list_steam_subscriptions()
                    if item["next_poll_at"] > now
                ),
                default=now + 15,
            )
        except SteamClientError as exc:
            self.last_error = str(exc)
            self.next_poll_at = now + 30
            logger.warning(f"Steam monitor poll failed: {exc}")
        except Exception as exc:  # noqa: BLE001 - keep background worker alive
            self.last_error = str(exc)
            self.next_poll_at = now + 30
            logger.exception("Steam monitor poll failed")
        finally:
            self.polling = False

    async def _run(self) -> None:
        while not self._stopping:
            await self.tick()
            try:
                self._wake.clear()
                await asyncio.wait_for(self._wake.wait(), timeout=15)
            except TimeoutError:
                pass

    async def _process_player(
        self,
        player: dict[str, Any],
        *,
        now: float,
        config: dict[str, Any],
        baseline: bool = False,
    ) -> None:
        steam_id = str(player.get("steamid", ""))
        previous = self.store.get_steam_player(steam_id)
        game_id = str(player.get("gameid", "") or "")
        game_name = str(player.get("gameextrainfo", "") or "")
        events = apply_player_snapshot(
            self.store,
            steam_id=steam_id,
            game_id=game_id,
            game_name=game_name,
            observed_at=now,
            exit_grace_seconds=config["exit_grace_seconds"],
            baseline=baseline or previous is None,
        )
        player["next_poll_at"] = now + self._poll_interval(player, now, config)
        saved = self.store.upsert_steam_player(player, checked_at=now)
        for event in events:
            await self._notify_session_event(event, player=saved)
        if game_id:
            await self._poll_achievements(saved, game_id)

    def _poll_interval(self, player: dict[str, Any], now: float, config: dict[str, Any]) -> int:
        fixed = int(config["fixed_poll_interval_seconds"])
        if fixed > 0:
            return max(15, fixed)
        values = list(config["smart_poll_intervals"])
        while len(values) < 6:
            values.append(values[-1])
        if player.get("gameid"):
            return values[0] * 60
        if int(player.get("personastate", 0) or 0) > 0:
            return values[1] * 60
        age = max(0, now - float(player.get("lastlogoff", 0) or 0))
        if age <= 3 * 60 * 60:
            return values[2] * 60
        if age <= 24 * 60 * 60:
            return values[3] * 60
        if age <= 48 * 60 * 60:
            return values[4] * 60
        return values[5] * 60

    async def _poll_achievements(self, player: dict[str, Any], game_id: str) -> None:
        if not self.store.is_feature_enabled("steam.achievements"):
            return
        if not self._game_allowed(game_id):
            return
        targets = [
            target
            for target in self.store.enabled_steam_targets(player["steam_id"])
            if self.store.get_steam_group(target["group_id"])["achievement_enabled"]
        ]
        if not targets:
            return
        try:
            achievements = await self.client.get_player_achievements(player["steam_id"], game_id)
        except SteamClientError as exc:
            logger.debug(f"Steam achievements unavailable for {player['steam_id']}: {exc}")
            return
        newly_unlocked = self.store.replace_steam_achievements(
            player["steam_id"], game_id, achievements
        )
        for key in newly_unlocked:
            await self._notify_text_event(
                player,
                targets,
                event_key=f"achievement:{player['steam_id']}:{game_id}:{key}:{achievements[key]}",
                event_type="achievement",
                text=f"{player['persona_name'] or player['steam_id']} 解锁了成就：{key}",
            )

    async def _notify_session_event(
        self,
        event: SessionEvent,
        *,
        player: dict[str, Any] | None = None,
    ) -> None:
        feature = {
            "start": "steam.notify.start",
            "end": "steam.notify.end",
            "network_recovered": "steam.notify.network",
        }[event.kind]
        if not self.store.is_feature_enabled(feature):
            return
        if not self._game_allowed(str(event.session.get("game_id", ""))):
            return
        player = player or self.store.get_steam_player(event.session["steam_id"]) or {
            "steam_id": event.session["steam_id"],
            "persona_name": "",
        }
        cover_image = None
        if self.store.is_feature_enabled("steam.notify.image"):
            cover_image = await self.client.get_game_cover(str(event.session.get("game_id", "")))
        targets = self.store.enabled_steam_targets(event.session["steam_id"])
        for target in targets:
            await self._send_session_notification(player, target, event, cover_image=cover_image)

    async def _send_session_notification(
        self,
        player: dict[str, Any],
        target: dict[str, Any],
        event: SessionEvent,
        *,
        cover_image: bytes | None = None,
    ) -> None:
        event_key = event.session["session_key"]
        group_id = int(target["group_id"])
        if self.store.steam_notification_sent(event_key, group_id, event.kind):
            return
        message = Message()
        if self.store.is_feature_enabled("steam.notify.mention", False) and target.get("qq_user_id"):
            message += MessageSegment.at(int(target["qq_user_id"]))
            message += MessageSegment.text(" ")
        if self.store.is_feature_enabled("steam.notify.image"):
            card = await asyncio.to_thread(
                render_status_card,
                self.settings,
                event_type=event.kind,
                player=player,
                session=event.session,
                alias=target.get("alias", ""),
                cover_image=cover_image,
            )
            message += MessageSegment.image(Path(card).resolve().as_uri())
        if self.store.is_feature_enabled("steam.notify.text"):
            message += MessageSegment.text(self._event_text(player, target, event))
        if not message:
            return
        bot = self._onebot()
        if bot is None:
            return
        try:
            await bot.send_group_msg(group_id=group_id, message=message)
        except Exception as exc:  # noqa: BLE001 - adapter boundary
            logger.warning(f"Steam notification failed for group {group_id}: {exc}")
            return
        self.store.mark_steam_notification_sent(event_key, group_id, event.kind)

    async def _notify_text_event(
        self,
        player: dict[str, Any],
        targets: list[dict[str, Any]],
        *,
        event_key: str,
        event_type: str,
        text: str,
    ) -> None:
        if not self.store.is_feature_enabled("steam.notify.text"):
            return
        bot = self._onebot()
        if bot is None:
            return
        for target in targets:
            group_id = int(target["group_id"])
            if self.store.steam_notification_sent(event_key, group_id, event_type):
                continue
            message = Message()
            if self.store.is_feature_enabled("steam.notify.mention", False) and target.get("qq_user_id"):
                message += MessageSegment.at(int(target["qq_user_id"]))
                message += MessageSegment.text(" ")
            message += MessageSegment.text(text)
            try:
                await bot.send_group_msg(group_id=group_id, message=message)
            except Exception as exc:  # noqa: BLE001 - adapter boundary
                logger.warning(f"Steam achievement notification failed: {exc}")
                continue
            self.store.mark_steam_notification_sent(event_key, group_id, event_type)

    def _event_text(
        self,
        player: dict[str, Any],
        target: dict[str, Any],
        event: SessionEvent,
    ) -> str:
        name = target.get("alias") or player.get("persona_name") or player.get("steam_id")
        game = event.session.get("game_name") or event.session.get("game_id")
        if event.kind == "start":
            return f"{name} 开始玩 {game}"
        if event.kind == "network_recovered":
            return f"{name} 在游玩 {game} 时恢复连接"
        seconds = max(
            0,
            float(event.session.get("ended_at") or self.clock())
            - float(event.session["started_at"]),
        )
        return f"{name} 结束了 {game}，本次游玩 {max(1, round(seconds / 60))} 分钟"

    def _active(self) -> bool:
        return bool(
            self.settings.steam_api_key
            and self.store.is_feature_enabled("steam.master", False)
            and self.store.is_feature_enabled("steam.monitoring")
        )

    def _game_allowed(self, game_id: str) -> bool:
        config = self.store.get_steam_settings()
        mode = config["game_filter_mode"]
        ids = set(config["game_filter_ids"])
        if mode == "allow":
            return game_id in ids
        if mode == "block":
            return game_id not in ids
        return True

    @staticmethod
    def _onebot() -> Any | None:
        bots = get_bots()
        return next(iter(bots.values()), None)
