from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Mapping
from typing import Any


class SteamStore:
    """Steam persistence using the owning store connection."""

    def _initialize_steam_settings(self, conn: sqlite3.Connection) -> None:
        defaults = {
            "steam_fixed_poll_interval_seconds": "0",
            "steam_smart_poll_intervals": "[1, 3, 5, 10, 20, 30]",
            "steam_retry_times": "3",
            "steam_exit_grace_seconds": "180",
            "steam_max_group_size": "20",
            "steam_price_country": "CN",
            "steam_price_currency": "CNY",
            "steam_game_filter_mode": "all",
            "steam_game_filter_ids": "[]",
        }
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                (key, value),
            )

    def get_steam_settings(self) -> dict[str, Any]:
        def integer(key: str, default: int) -> int:
            try:
                return int(self.get_setting(key, str(default)))
            except ValueError:
                return default

        try:
            intervals = [
                int(value)
                for value in json.loads(
                    self.get_setting("steam_smart_poll_intervals", "[1,3,5,10,20,30]")
                )
            ]
        except (TypeError, ValueError, json.JSONDecodeError):
            intervals = [1, 3, 5, 10, 20, 30]
        try:
            filter_ids = [
                str(value)
                for value in json.loads(self.get_setting("steam_game_filter_ids", "[]"))
                if str(value).strip()
            ]
        except (TypeError, json.JSONDecodeError):
            filter_ids = []
        return {
            "fixed_poll_interval_seconds": max(0, integer("steam_fixed_poll_interval_seconds", 0)),
            "smart_poll_intervals": intervals or [1, 3, 5, 10, 20, 30],
            "retry_times": max(0, min(integer("steam_retry_times", 3), 10)),
            "exit_grace_seconds": max(0, min(integer("steam_exit_grace_seconds", 180), 3600)),
            "max_group_size": max(1, min(integer("steam_max_group_size", 20), 200)),
            "price_country": self.get_setting("steam_price_country", "CN").upper(),
            "price_currency": self.get_setting("steam_price_currency", "CNY").upper(),
            "game_filter_mode": self.get_setting("steam_game_filter_mode", "all"),
            "game_filter_ids": filter_ids,
        }

    def set_steam_settings(self, values: Mapping[str, Any], *, actor_id: int = 0) -> dict[str, Any]:
        current = self.get_steam_settings()
        merged = {**current, **values}
        fixed = max(0, min(int(merged["fixed_poll_interval_seconds"]), 86400))
        intervals = [int(value) for value in merged["smart_poll_intervals"]]
        if not intervals or any(value < 1 or value > 1440 for value in intervals):
            raise ValueError("smart poll intervals must be between 1 and 1440 minutes")
        retry_times = max(0, min(int(merged["retry_times"]), 10))
        grace = max(0, min(int(merged["exit_grace_seconds"]), 3600))
        max_size = max(1, min(int(merged["max_group_size"]), 200))
        country = str(merged["price_country"]).strip().upper()
        currency = str(merged["price_currency"]).strip().upper()
        filter_mode = str(merged["game_filter_mode"]).strip().lower()
        filter_ids = sorted(
            {str(value).strip() for value in merged["game_filter_ids"] if str(value).strip()}
        )
        if not re.fullmatch(r"[A-Z]{2}", country):
            raise ValueError("price country must be a two-letter code")
        if not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValueError("price currency must be a three-letter code")
        if filter_mode not in {"all", "allow", "block"}:
            raise ValueError("game filter mode must be all, allow, or block")
        updates = {
            "steam_fixed_poll_interval_seconds": str(fixed),
            "steam_smart_poll_intervals": json.dumps(intervals),
            "steam_retry_times": str(retry_times),
            "steam_exit_grace_seconds": str(grace),
            "steam_max_group_size": str(max_size),
            "steam_price_country": country,
            "steam_price_currency": currency,
            "steam_game_filter_mode": filter_mode,
            "steam_game_filter_ids": json.dumps(filter_ids),
        }
        with self._connect() as conn:
            for key, value in updates.items():
                self.set_setting(key, value, conn=conn)
            self.audit(actor_id, "set_steam_settings", "steam", updates, conn=conn)
        return self.get_steam_settings()

    def set_steam_group(
        self,
        group_id: int,
        *,
        monitor_enabled: bool,
        achievement_enabled: bool = True,
        actor_id: int = 0,
    ) -> dict[str, Any]:
        normalized = int(group_id)
        if normalized <= 0:
            raise ValueError("group_id must be positive")
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO steam_group_settings(
                    group_id, monitor_enabled, achievement_enabled, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    monitor_enabled = excluded.monitor_enabled,
                    achievement_enabled = excluded.achievement_enabled,
                    updated_at = excluded.updated_at
                """,
                (normalized, int(monitor_enabled), int(achievement_enabled), now),
            )
            self.audit(
                actor_id,
                "set_steam_group",
                str(normalized),
                {
                    "monitor_enabled": bool(monitor_enabled),
                    "achievement_enabled": bool(achievement_enabled),
                },
                conn=conn,
            )
        return self.get_steam_group(normalized)

    def get_steam_group(self, group_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT settings.group_id, settings.monitor_enabled,
                       settings.achievement_enabled, settings.updated_at,
                       COUNT(subs.steam_id) AS player_count
                FROM steam_group_settings AS settings
                LEFT JOIN steam_subscriptions AS subs
                  ON subs.group_id = settings.group_id
                WHERE settings.group_id = ?
                GROUP BY settings.group_id
                """,
                (int(group_id),),
            ).fetchone()
        if row is None:
            return {
                "group_id": int(group_id),
                "monitor_enabled": False,
                "achievement_enabled": True,
                "player_count": 0,
                "updated_at": 0.0,
            }
        return {
            "group_id": int(row["group_id"]),
            "monitor_enabled": bool(row["monitor_enabled"]),
            "achievement_enabled": bool(row["achievement_enabled"]),
            "player_count": int(row["player_count"]),
            "updated_at": float(row["updated_at"]),
        }

    def list_steam_groups(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT settings.group_id, settings.monitor_enabled,
                       settings.achievement_enabled, settings.updated_at,
                       COUNT(subs.steam_id) AS player_count
                FROM steam_group_settings AS settings
                LEFT JOIN steam_subscriptions AS subs
                  ON subs.group_id = settings.group_id
                GROUP BY settings.group_id
                ORDER BY settings.group_id
                """
            ).fetchall()
        return [
            {
                "group_id": int(row["group_id"]),
                "monitor_enabled": bool(row["monitor_enabled"]),
                "achievement_enabled": bool(row["achievement_enabled"]),
                "player_count": int(row["player_count"]),
                "updated_at": float(row["updated_at"]),
            }
            for row in rows
        ]

    def delete_steam_group(self, group_id: int, *, actor_id: int = 0) -> bool:
        normalized = int(group_id)
        with self._connect() as conn:
            conn.execute("DELETE FROM steam_bindings WHERE group_id = ?", (normalized,))
            conn.execute("DELETE FROM steam_subscriptions WHERE group_id = ?", (normalized,))
            cursor = conn.execute(
                "DELETE FROM steam_group_settings WHERE group_id = ?",
                (normalized,),
            )
            if cursor.rowcount:
                self.audit(
                    actor_id,
                    "delete_steam_group",
                    str(normalized),
                    {},
                    conn=conn,
                )
            return bool(cursor.rowcount)

    def add_steam_subscription(
        self,
        group_id: int,
        steam_id: str,
        *,
        alias: str = "",
        actor_id: int = 0,
    ) -> dict[str, Any]:
        normalized_group = int(group_id)
        normalized_steam = self._normalize_steam_id(steam_id)
        normalized_alias = str(alias).strip()[:80]
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO steam_group_settings(group_id, updated_at) VALUES (?, ?)",
                (normalized_group, now),
            )
            count_row = conn.execute(
                "SELECT COUNT(*) AS count FROM steam_subscriptions WHERE group_id = ?",
                (normalized_group,),
            ).fetchone()
            existing = conn.execute(
                "SELECT 1 FROM steam_subscriptions WHERE group_id = ? AND steam_id = ?",
                (normalized_group, normalized_steam),
            ).fetchone()
            if (
                existing is None
                and int(count_row["count"]) >= self.get_steam_settings()["max_group_size"]
            ):
                raise ValueError("group Steam monitor limit reached")
            conn.execute(
                """
                INSERT INTO steam_subscriptions(group_id, steam_id, alias, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(group_id, steam_id) DO UPDATE SET alias = excluded.alias
                """,
                (normalized_group, normalized_steam, normalized_alias, now),
            )
            self.audit(
                actor_id,
                "add_steam_subscription",
                f"{normalized_group}:{normalized_steam}",
                {"alias": normalized_alias},
                conn=conn,
            )
        return self.get_steam_subscription(normalized_group, normalized_steam)

    def get_steam_subscription(self, group_id: int, steam_id: str) -> dict[str, Any]:
        items = self.list_steam_subscriptions(group_id=int(group_id), steam_id=steam_id)
        if not items:
            raise KeyError("Steam subscription not found")
        return items[0]

    def list_steam_subscriptions(
        self,
        *,
        group_id: int | None = None,
        steam_id: str | None = None,
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        params: list[Any] = []
        if group_id is not None:
            conditions.append("subs.group_id = ?")
            params.append(int(group_id))
        if steam_id is not None:
            conditions.append("subs.steam_id = ?")
            params.append(self._normalize_steam_id(steam_id))
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT subs.group_id, subs.steam_id, subs.alias, subs.created_at,
                       players.persona_name, players.profile_url, players.avatar_url,
                       players.persona_state, players.game_id, players.game_name,
                       players.last_checked_at, players.next_poll_at, bindings.qq_user_id
                FROM steam_subscriptions AS subs
                LEFT JOIN steam_players AS players ON players.steam_id = subs.steam_id
                LEFT JOIN steam_bindings AS bindings
                  ON bindings.group_id = subs.group_id AND bindings.steam_id = subs.steam_id
                {where}
                ORDER BY subs.group_id, COALESCE(NULLIF(subs.alias, ''), players.persona_name), subs.steam_id
                """,
                params,
            ).fetchall()
        return [
            {
                "group_id": int(row["group_id"]),
                "steam_id": str(row["steam_id"]),
                "alias": str(row["alias"]),
                "created_at": float(row["created_at"]),
                "persona_name": str(row["persona_name"] or ""),
                "profile_url": str(row["profile_url"] or ""),
                "avatar_url": str(row["avatar_url"] or ""),
                "persona_state": int(row["persona_state"] or 0),
                "game_id": str(row["game_id"] or ""),
                "game_name": str(row["game_name"] or ""),
                "last_checked_at": float(row["last_checked_at"] or 0),
                "next_poll_at": float(row["next_poll_at"] or 0),
                "qq_user_id": int(row["qq_user_id"]) if row["qq_user_id"] is not None else None,
            }
            for row in rows
        ]

    def remove_steam_subscription(
        self,
        group_id: int,
        steam_id: str,
        *,
        actor_id: int = 0,
    ) -> bool:
        normalized = self._normalize_steam_id(steam_id)
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM steam_subscriptions WHERE group_id = ? AND steam_id = ?",
                (int(group_id), normalized),
            )
            if cursor.rowcount:
                self.audit(
                    actor_id,
                    "remove_steam_subscription",
                    f"{int(group_id)}:{normalized}",
                    {},
                    conn=conn,
                )
            return bool(cursor.rowcount)

    def bind_steam_user(
        self,
        group_id: int,
        qq_user_id: int,
        steam_id: str,
        *,
        actor_id: int = 0,
    ) -> dict[str, Any]:
        normalized = self._normalize_steam_id(steam_id)
        try:
            self.get_steam_subscription(int(group_id), normalized)
        except KeyError:
            self.add_steam_subscription(
                int(group_id),
                normalized,
                actor_id=actor_id,
            )
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO steam_bindings(group_id, qq_user_id, steam_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(group_id, qq_user_id) DO UPDATE SET
                    steam_id = excluded.steam_id,
                    updated_at = excluded.updated_at
                """,
                (int(group_id), int(qq_user_id), normalized, now, now),
            )
            self.audit(
                actor_id,
                "bind_steam_user",
                f"{int(group_id)}:{int(qq_user_id)}",
                {"steam_id": normalized},
                conn=conn,
            )
        return self.get_steam_binding(int(group_id), int(qq_user_id))

    def get_steam_binding(self, group_id: int, qq_user_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT binding.group_id, binding.qq_user_id, binding.steam_id,
                       binding.created_at, binding.updated_at,
                       players.persona_name, players.game_id, players.game_name,
                       players.persona_state
                FROM steam_bindings AS binding
                LEFT JOIN steam_players AS players ON players.steam_id = binding.steam_id
                WHERE binding.group_id = ? AND binding.qq_user_id = ?
                """,
                (int(group_id), int(qq_user_id)),
            ).fetchone()
        if row is None:
            raise KeyError("Steam binding not found")
        return {
            "group_id": int(row["group_id"]),
            "qq_user_id": int(row["qq_user_id"]),
            "steam_id": str(row["steam_id"]),
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
            "persona_name": str(row["persona_name"] or ""),
            "persona_state": int(row["persona_state"] or 0),
            "game_id": str(row["game_id"] or ""),
            "game_name": str(row["game_name"] or ""),
        }

    def list_steam_bindings(self, group_id: int | None = None) -> list[dict[str, Any]]:
        if group_id is None:
            query = "SELECT group_id, qq_user_id FROM steam_bindings ORDER BY group_id, qq_user_id"
            params: tuple[Any, ...] = ()
        else:
            query = "SELECT group_id, qq_user_id FROM steam_bindings WHERE group_id = ? ORDER BY qq_user_id"
            params = (int(group_id),)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            self.get_steam_binding(int(row["group_id"]), int(row["qq_user_id"])) for row in rows
        ]

    def unbind_steam_user(
        self,
        group_id: int,
        qq_user_id: int,
        *,
        actor_id: int = 0,
    ) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM steam_bindings WHERE group_id = ? AND qq_user_id = ?",
                (int(group_id), int(qq_user_id)),
            )
            if cursor.rowcount:
                self.audit(
                    actor_id,
                    "unbind_steam_user",
                    f"{int(group_id)}:{int(qq_user_id)}",
                    {},
                    conn=conn,
                )
            return bool(cursor.rowcount)

    def remove_bound_steam_subscription(
        self,
        group_id: int,
        qq_user_id: int,
        *,
        actor_id: int = 0,
    ) -> str | None:
        normalized_group = int(group_id)
        normalized_user = int(qq_user_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT steam_id FROM steam_bindings WHERE group_id = ? AND qq_user_id = ?",
                (normalized_group, normalized_user),
            ).fetchone()
            if row is None:
                return None
            steam_id = str(row["steam_id"])
            cursor = conn.execute(
                "DELETE FROM steam_subscriptions WHERE group_id = ? AND steam_id = ?",
                (normalized_group, steam_id),
            )
            if not cursor.rowcount:
                return None
            self.audit(
                actor_id,
                "remove_bound_steam_subscription",
                f"{normalized_group}:{normalized_user}",
                {"steam_id": steam_id},
                conn=conn,
            )
            return steam_id

    @staticmethod
    def _normalize_steam_id(steam_id: str) -> str:
        normalized = str(steam_id).strip()
        if not re.fullmatch(r"\d{16,20}", normalized):
            raise ValueError("Steam ID must be a 16-20 digit SteamID64")
        return normalized

    def upsert_steam_player(
        self, player: Mapping[str, Any], *, checked_at: float
    ) -> dict[str, Any]:
        steam_id = self._normalize_steam_id(str(player.get("steamid", "")))
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO steam_players(
                    steam_id, persona_name, profile_url, avatar_url, persona_state,
                    game_id, game_name, last_logoff, last_checked_at, next_poll_at,
                    raw_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(steam_id) DO UPDATE SET
                    persona_name = excluded.persona_name,
                    profile_url = excluded.profile_url,
                    avatar_url = excluded.avatar_url,
                    persona_state = excluded.persona_state,
                    game_id = excluded.game_id,
                    game_name = excluded.game_name,
                    last_logoff = excluded.last_logoff,
                    last_checked_at = excluded.last_checked_at,
                    next_poll_at = excluded.next_poll_at,
                    raw_json = excluded.raw_json,
                    updated_at = excluded.updated_at
                """,
                (
                    steam_id,
                    str(player.get("personaname", "")),
                    str(player.get("profileurl", "")),
                    str(player.get("avatarfull", player.get("avatarmedium", ""))),
                    int(player.get("personastate", 0) or 0),
                    str(player.get("gameid", "") or ""),
                    str(player.get("gameextrainfo", "") or ""),
                    float(player.get("lastlogoff", 0) or 0),
                    float(checked_at),
                    float(player.get("next_poll_at", checked_at)),
                    json.dumps(dict(player), ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
        result = self.get_steam_player(steam_id)
        if result is None:
            raise RuntimeError("Steam player upsert failed")
        return result

    def get_steam_player(self, steam_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM steam_players WHERE steam_id = ?",
                (self._normalize_steam_id(steam_id),),
            ).fetchone()
        return self._steam_player_from_row(row) if row is not None else None

    def due_steam_ids(self, now: float) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT subs.steam_id
                FROM steam_subscriptions AS subs
                JOIN steam_group_settings AS groups ON groups.group_id = subs.group_id
                LEFT JOIN steam_players AS players ON players.steam_id = subs.steam_id
                WHERE groups.monitor_enabled = 1
                  AND (players.next_poll_at IS NULL OR players.next_poll_at <= ?)
                ORDER BY subs.steam_id
                """,
                (float(now),),
            ).fetchall()
        return [str(row["steam_id"]) for row in rows]

    def enabled_steam_targets(self, steam_id: str) -> list[dict[str, Any]]:
        subscriptions = self.list_steam_subscriptions(steam_id=steam_id)
        enabled = {
            item["group_id"]: item for item in self.list_steam_groups() if item["monitor_enabled"]
        }
        return [item for item in subscriptions if item["group_id"] in enabled]

    def get_open_steam_session(self, steam_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM steam_sessions
                WHERE steam_id = ? AND state IN ('playing', 'confirming_exit')
                ORDER BY started_at DESC LIMIT 1
                """,
                (self._normalize_steam_id(steam_id),),
            ).fetchone()
        return self._steam_session_from_row(row) if row is not None else None

    def start_steam_session(
        self,
        steam_id: str,
        game_id: str,
        game_name: str,
        *,
        started_at: float,
    ) -> dict[str, Any]:
        normalized = self._normalize_steam_id(steam_id)
        session_key = f"{normalized}:{game_id!s}:{int(started_at * 1000)}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO steam_sessions(
                    session_key, steam_id, game_id, game_name, state,
                    started_at, updated_at
                ) VALUES (?, ?, ?, ?, 'playing', ?, ?)
                """,
                (session_key, normalized, str(game_id), str(game_name), started_at, started_at),
            )
        session = self.get_open_steam_session(normalized)
        if session is None:
            raise RuntimeError("Steam session insert failed")
        return session

    def mark_steam_session_confirming(self, session_id: int, deadline: float) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE steam_sessions SET state = 'confirming_exit', exit_deadline = ?, updated_at = ? WHERE id = ? AND state = 'playing'",
                (float(deadline), time.time(), int(session_id)),
            )

    def resume_steam_session(self, session_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE steam_sessions SET state = 'playing', exit_deadline = NULL, updated_at = ? WHERE id = ? AND state = 'confirming_exit'",
                (time.time(), int(session_id)),
            )

    def close_steam_session(
        self,
        session_id: int,
        *,
        ended_at: float,
        reason: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE steam_sessions
                SET state = 'closed', ended_at = ?, exit_deadline = NULL,
                    close_reason = ?, updated_at = ?
                WHERE id = ? AND state IN ('playing', 'confirming_exit')
                """,
                (float(ended_at), str(reason), time.time(), int(session_id)),
            )
            row = conn.execute(
                "SELECT * FROM steam_sessions WHERE id = ?",
                (int(session_id),),
            ).fetchone()
        if not cursor.rowcount or row is None:
            return None
        return self._steam_session_from_row(row)

    def due_confirming_steam_sessions(self, now: float) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM steam_sessions
                WHERE state = 'confirming_exit' AND exit_deadline <= ?
                ORDER BY exit_deadline, id
                """,
                (float(now),),
            ).fetchall()
        return [self._steam_session_from_row(row) for row in rows]

    def close_all_open_steam_sessions(self, *, ended_at: float, reason: str) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE steam_sessions
                SET state = 'closed', ended_at = ?, exit_deadline = NULL,
                    close_reason = ?, updated_at = ?
                WHERE state IN ('playing', 'confirming_exit')
                """,
                (float(ended_at), str(reason), time.time()),
            )
            return int(cursor.rowcount)

    def list_steam_sessions(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM steam_sessions ORDER BY started_at DESC, id DESC LIMIT ?",
                (min(max(int(limit), 1), 500),),
            ).fetchall()
        return [self._steam_session_from_row(row) for row in rows]

    def steam_notification_sent(self, event_key: str, group_id: int, event_type: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM steam_notifications WHERE event_key = ? AND group_id = ? AND event_type = ?",
                (str(event_key), int(group_id), str(event_type)),
            ).fetchone()
        return row is not None

    def mark_steam_notification_sent(
        self,
        event_key: str,
        group_id: int,
        event_type: str,
        *,
        sent_at: float | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO steam_notifications(event_key, group_id, event_type, sent_at) VALUES (?, ?, ?, ?)",
                (str(event_key), int(group_id), str(event_type), float(sent_at or time.time())),
            )

    def replace_steam_achievements(
        self,
        steam_id: str,
        game_id: str,
        achievements: Mapping[str, float],
    ) -> list[str]:
        normalized = self._normalize_steam_id(steam_id)
        now = time.time()
        with self._connect() as conn:
            existing = {
                str(row["achievement_key"]): float(row["unlocked_at"])
                for row in conn.execute(
                    "SELECT achievement_key, unlocked_at FROM steam_achievement_snapshots WHERE steam_id = ? AND game_id = ?",
                    (normalized, str(game_id)),
                ).fetchall()
            }
            newly_unlocked = [
                key
                for key, unlocked_at in achievements.items()
                if float(unlocked_at) > 0 and existing.get(str(key), 0) <= 0
            ]
            for key, unlocked_at in achievements.items():
                conn.execute(
                    """
                    INSERT INTO steam_achievement_snapshots(
                        steam_id, game_id, achievement_key, unlocked_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(steam_id, game_id, achievement_key) DO UPDATE SET
                        unlocked_at = excluded.unlocked_at,
                        updated_at = excluded.updated_at
                    """,
                    (normalized, str(game_id), str(key), float(unlocked_at), now),
                )
        return newly_unlocked if existing else []

    @staticmethod
    def _steam_player_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "steam_id": str(row["steam_id"]),
            "persona_name": str(row["persona_name"]),
            "profile_url": str(row["profile_url"]),
            "avatar_url": str(row["avatar_url"]),
            "persona_state": int(row["persona_state"]),
            "game_id": str(row["game_id"]),
            "game_name": str(row["game_name"]),
            "last_logoff": float(row["last_logoff"]),
            "last_checked_at": float(row["last_checked_at"]),
            "next_poll_at": float(row["next_poll_at"]),
            "updated_at": float(row["updated_at"]),
        }

    @staticmethod
    def _steam_session_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "session_key": str(row["session_key"]),
            "steam_id": str(row["steam_id"]),
            "game_id": str(row["game_id"]),
            "game_name": str(row["game_name"]),
            "state": str(row["state"]),
            "started_at": float(row["started_at"]),
            "exit_deadline": (
                float(row["exit_deadline"]) if row["exit_deadline"] is not None else None
            ),
            "ended_at": float(row["ended_at"]) if row["ended_at"] is not None else None,
            "close_reason": str(row["close_reason"]),
            "updated_at": float(row["updated_at"]),
        }
