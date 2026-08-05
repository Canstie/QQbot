from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from qq_personal_bot.menu_recipes import (
    cache_image,
    cache_image_bytes,
    decode_json_list,
    encode_json,
    is_supported_image_file,
    load_howtocook_records,
    load_seed_records,
    normalize_text,
    normalize_text_list,
    optional_text_list,
)
from qq_personal_bot.settings import AppSettings


CHINA_TZ = timezone(timedelta(hours=8))


def _strip_cq_segments(message: str) -> str:
    return re.sub(r"\[CQ:[^\]]+\]", "", message)


class PolicyStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def initialize(self, settings: AppSettings) -> None:
        first_run = not self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            self._create_schema(conn)
            has_settings = conn.execute("SELECT 1 FROM settings LIMIT 1").fetchone() is not None
            should_seed = first_run or not has_settings
            if should_seed:
                self._seed_defaults(conn, settings)
                for admin_id in settings.admins:
                    self.add_admin(admin_id, actor_id=0, conn=conn)
            self._initialize_dsapi_settings(conn)
            self.purge_legacy_menu_caches(conn=conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS groups (
                group_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                blocked INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trigger_prefixes (
                prefix TEXT PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                target TEXT NOT NULL,
                detail TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS lua_state (
                namespace TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (namespace, key)
            );

            CREATE TABLE IF NOT EXISTS menu_recipes (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                aliases_json TEXT NOT NULL,
                cuisine TEXT NOT NULL,
                region TEXT NOT NULL,
                category TEXT NOT NULL,
                tags_json TEXT NOT NULL,
                ingredients_json TEXT NOT NULL,
                steps_json TEXT NOT NULL,
                image_relpath TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                source TEXT NOT NULL DEFAULT 'local',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS restaurants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                dishes_json TEXT NOT NULL,
                group_id INTEGER NOT NULL,
                created_by INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(group_id, name)
            );

            CREATE TABLE IF NOT EXISTS group_daily_stats (
                date TEXT NOT NULL,
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                text_chars INTEGER NOT NULL DEFAULT 0,
                image_count INTEGER NOT NULL DEFAULT 0,
                at_count INTEGER NOT NULL DEFAULT 0,
                reply_count INTEGER NOT NULL DEFAULT 0,
                first_timestamp REAL NOT NULL,
                last_timestamp REAL NOT NULL,
                hourly_json TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (date, group_id, user_id)
            );

            CREATE INDEX IF NOT EXISTS idx_group_daily_stats_group_date
            ON group_daily_stats(group_id, date);

            CREATE TABLE IF NOT EXISTS dsapi_chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_dsapi_chat_history_group_id
            ON dsapi_chat_history(group_id, id);

            CREATE TABLE IF NOT EXISTS dsapi_group_context (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_dsapi_group_context_group_id
            ON dsapi_group_context(group_id, id);
            """
        )

    def _seed_defaults(self, conn: sqlite3.Connection, settings: AppSettings) -> None:
        defaults = {
            "policy_mode": settings.policy_mode,
            "trigger_mention": "true" if settings.trigger_mention else "false",
            "direct_trigger_percent": str(settings.direct_trigger_percent),
            "per_group_seconds": str(settings.per_group_seconds),
            "per_user_per_minute": str(settings.per_user_per_minute),
            "dsapi_knowledge_enabled": "false",
            "dsapi_knowledge_prompt": "",
            "dsapi_history_turns": "2",
            "dsapi_random_reply_percent": "2",
            "dsapi_random_sticker_percent": "20",
            "dsapi_enabled_groups": "[]",
        }
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                (key, value),
            )
        for prefix in settings.trigger_prefixes:
            conn.execute(
                "INSERT OR IGNORE INTO trigger_prefixes(prefix) VALUES (?)",
                (prefix,),
            )
        self.audit("0", "initialize", "store", defaults, conn=conn)

    def _initialize_dsapi_settings(self, conn: sqlite3.Connection) -> None:
        defaults = {
            "dsapi_knowledge_enabled": "false",
            "dsapi_knowledge_prompt": "",
            "dsapi_history_turns": "2",
            "dsapi_random_reply_percent": "2",
            "dsapi_random_sticker_percent": "20",
        }
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                (key, value),
            )

        has_ai_groups = conn.execute(
            "SELECT 1 FROM settings WHERE key = 'dsapi_enabled_groups'"
        ).fetchone()
        if has_ai_groups:
            return
        enabled_groups = [
            int(row["group_id"])
            for row in conn.execute(
                "SELECT group_id FROM groups WHERE enabled = 1 ORDER BY group_id"
            ).fetchall()
        ]
        conn.execute(
            "INSERT INTO settings(key, value) VALUES ('dsapi_enabled_groups', ?)",
            (json.dumps(enabled_groups),),
        )

    def get_mode(self) -> str:
        mode = self.get_setting("policy_mode", "allowlist")
        if mode not in {"allowlist", "blocklist"}:
            return "allowlist"
        return mode

    def set_mode(self, mode: str, actor_id: int) -> None:
        if mode not in {"allowlist", "blocklist"}:
            raise ValueError("mode must be allowlist or blocklist")
        with self._connect() as conn:
            self.set_setting("policy_mode", mode, conn=conn)
            self.audit(actor_id, "set_mode", "policy", {"mode": mode}, conn=conn)

    def set_core_config(
        self,
        *,
        mode: str,
        enabled_groups: list[int],
        blocked_groups: list[int],
        admins: list[int],
        trigger_mention: bool,
        prefixes: list[str],
        direct_trigger_percent: float,
        per_group_seconds: float,
        per_user_per_minute: int,
        actor_id: int,
    ) -> None:
        if mode not in {"allowlist", "blocklist"}:
            raise ValueError("mode must be allowlist or blocklist")
        if direct_trigger_percent < 0 or direct_trigger_percent > 100:
            raise ValueError("direct trigger percent must be between 0 and 100")
        if per_group_seconds < 0 or per_user_per_minute < 0:
            raise ValueError("limits must be non-negative")

        normalized_prefixes = self._normalize_prefixes(prefixes)
        normalized_enabled_groups = self._normalize_int_ids(enabled_groups, "enabled_groups")
        normalized_blocked_groups = self._normalize_int_ids(blocked_groups, "blocked_groups")
        normalized_admins = self._normalize_int_ids(admins, "admins")

        with self._connect() as conn:
            self.set_setting("policy_mode", mode, conn=conn)
            self.set_setting("trigger_mention", "true" if trigger_mention else "false", conn=conn)
            self.set_setting("direct_trigger_percent", str(float(direct_trigger_percent)), conn=conn)
            self.set_setting("per_group_seconds", str(float(per_group_seconds)), conn=conn)
            self.set_setting("per_user_per_minute", str(int(per_user_per_minute)), conn=conn)

            conn.execute("DELETE FROM trigger_prefixes")
            for prefix in normalized_prefixes:
                conn.execute("INSERT INTO trigger_prefixes(prefix) VALUES (?)", (prefix,))

            now = time.time()
            conn.execute("DELETE FROM groups")
            group_ids = sorted(set(normalized_enabled_groups) | set(normalized_blocked_groups))
            for group_id in group_ids:
                conn.execute(
                    """
                    INSERT INTO groups(group_id, enabled, blocked, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        group_id,
                        1 if group_id in normalized_enabled_groups else 0,
                        1 if group_id in normalized_blocked_groups else 0,
                        now,
                    ),
                )

            conn.execute("DELETE FROM admins")
            for admin_id in normalized_admins:
                conn.execute(
                    "INSERT INTO admins(user_id, created_at) VALUES (?, ?)",
                    (admin_id, now),
                )

            self.audit(
                actor_id,
                "set_core_config",
                "policy",
                {
                    "mode": mode,
                    "enabled_groups": normalized_enabled_groups,
                    "blocked_groups": normalized_blocked_groups,
                    "admins": normalized_admins,
                    "trigger": {
                        "mention": trigger_mention,
                        "prefixes": normalized_prefixes,
                        "direct_trigger_percent": direct_trigger_percent,
                    },
                    "limits": {
                        "per_group_seconds": per_group_seconds,
                        "per_user_per_minute": per_user_per_minute,
                    },
                },
                conn=conn,
            )

    def get_setting(self, key: str, default: str) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str, conn: sqlite3.Connection | None = None) -> None:
        if conn is None:
            with self._connect() as local_conn:
                self.set_setting(key, value, conn=local_conn)
            return
        conn.execute(
            """
            INSERT INTO settings(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def get_dsapi_config(self) -> dict[str, Any]:
        try:
            history_turns = int(self.get_setting("dsapi_history_turns", "2"))
        except ValueError:
            history_turns = 2
        history_turns = max(1, min(history_turns, 20))
        try:
            random_reply_percent = float(
                self.get_setting("dsapi_random_reply_percent", "2")
            )
        except ValueError:
            random_reply_percent = 2.0
        random_reply_percent = max(0.0, min(random_reply_percent, 100.0))
        try:
            random_sticker_percent = float(
                self.get_setting("dsapi_random_sticker_percent", "20")
            )
        except ValueError:
            random_sticker_percent = 20.0
        random_sticker_percent = max(0.0, min(random_sticker_percent, 100.0))
        try:
            enabled_groups = self._normalize_int_ids(
                json.loads(self.get_setting("dsapi_enabled_groups", "[]")),
                "enabled_groups",
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            enabled_groups = []
        with self._connect() as conn:
            chat_stats = conn.execute(
                """
                SELECT COUNT(*) AS message_count, COUNT(DISTINCT group_id) AS group_count
                FROM dsapi_chat_history
                """
            ).fetchone()
            context_stats = conn.execute(
                """
                SELECT COUNT(*) AS message_count, COUNT(DISTINCT group_id) AS group_count
                FROM dsapi_group_context
                """
            ).fetchone()
            all_group_stats = conn.execute(
                """
                SELECT COUNT(*) AS group_count
                FROM (
                    SELECT group_id FROM dsapi_chat_history
                    UNION
                    SELECT group_id FROM dsapi_group_context
                )
                """
            ).fetchone()
        return {
            "knowledge_enabled": self.get_setting(
                "dsapi_knowledge_enabled", "false"
            ).lower()
            in {"1", "true", "yes", "on"},
            "knowledge_prompt": self.get_setting("dsapi_knowledge_prompt", ""),
            "history_turns": history_turns,
            "random_reply_percent": random_reply_percent,
            "random_sticker_percent": random_sticker_percent,
            "enabled_groups": enabled_groups,
            "history_messages": int(chat_stats["message_count"])
            + int(context_stats["message_count"]),
            "history_groups": int(all_group_stats["group_count"]),
        }

    def set_dsapi_config(
        self,
        *,
        knowledge_enabled: bool,
        knowledge_prompt: str,
        history_turns: int,
        enabled_groups: list[int],
        clear_history: bool,
        actor_id: int,
        random_reply_percent: float = 2.0,
        random_sticker_percent: float = 20.0,
    ) -> dict[str, Any]:
        prompt = str(knowledge_prompt).strip()
        turns = int(history_turns)
        if turns < 1 or turns > 20:
            raise ValueError("history_turns must be between 1 and 20")
        if len(prompt) > 100_000:
            raise ValueError("knowledge_prompt must not exceed 100000 characters")
        percent = float(random_reply_percent)
        if percent < 0 or percent > 100:
            raise ValueError("random_reply_percent must be between 0 and 100")
        sticker_percent = float(random_sticker_percent)
        if sticker_percent < 0 or sticker_percent > 100:
            raise ValueError("random_sticker_percent must be between 0 and 100")
        normalized_enabled_groups = self._normalize_int_ids(enabled_groups, "enabled_groups")

        with self._connect() as conn:
            self.set_setting(
                "dsapi_knowledge_enabled",
                "true" if knowledge_enabled else "false",
                conn=conn,
            )
            self.set_setting("dsapi_knowledge_prompt", prompt, conn=conn)
            self.set_setting("dsapi_history_turns", str(turns), conn=conn)
            self.set_setting("dsapi_random_reply_percent", str(percent), conn=conn)
            self.set_setting("dsapi_random_sticker_percent", str(sticker_percent), conn=conn)
            self.set_setting(
                "dsapi_enabled_groups",
                json.dumps(normalized_enabled_groups),
                conn=conn,
            )
            cleared = 0
            if clear_history:
                cleared = conn.execute("DELETE FROM dsapi_chat_history").rowcount
                cleared += conn.execute("DELETE FROM dsapi_group_context").rowcount
            self.audit(
                actor_id,
                "set_dsapi_config",
                "dsapi",
                {
                    "knowledge_enabled": bool(knowledge_enabled),
                    "knowledge_prompt_chars": len(prompt),
                    "history_turns": turns,
                    "random_reply_percent": percent,
                    "random_sticker_percent": sticker_percent,
                    "enabled_groups": normalized_enabled_groups,
                    "history_messages_cleared": cleared,
                },
                conn=conn,
            )
        return self.get_dsapi_config()

    def get_dsapi_chat_history(
        self,
        group_id: int,
        history_turns: int,
    ) -> list[dict[str, str]]:
        message_limit = max(1, min(int(history_turns), 20)) * 2
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content
                FROM (
                    SELECT id, role, content
                    FROM dsapi_chat_history
                    WHERE group_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                ORDER BY id ASC
                """,
                (int(group_id), message_limit),
            ).fetchall()
        return [
            {"role": str(row["role"]), "content": str(row["content"])} for row in rows
        ]

    def expire_dsapi_chat_history(
        self,
        group_id: int,
        *,
        idle_seconds: int,
        now: float | None = None,
    ) -> int:
        normalized_idle_seconds = int(idle_seconds)
        if normalized_idle_seconds <= 0:
            raise ValueError("idle_seconds must be > 0")
        cutoff = (time.time() if now is None else float(now)) - normalized_idle_seconds
        with self._connect() as conn:
            deleted = conn.execute(
                """
                DELETE FROM dsapi_chat_history
                WHERE group_id = ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM dsapi_chat_history
                      WHERE group_id = ? AND created_at >= ?
                  )
                """,
                (int(group_id), int(group_id), cutoff),
            ).rowcount
        return int(deleted)

    def record_dsapi_exchange(
        self,
        *,
        group_id: int,
        user_content: str,
        assistant_content: str,
        history_turns: int,
    ) -> None:
        message_limit = max(1, min(int(history_turns), 20)) * 2
        normalized_group_id = int(group_id)
        now = time.time()
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO dsapi_chat_history(group_id, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (normalized_group_id, "user", str(user_content), now),
                    (normalized_group_id, "assistant", str(assistant_content), now),
                ],
            )
            conn.execute(
                """
                DELETE FROM dsapi_chat_history
                WHERE group_id = ?
                  AND id NOT IN (
                      SELECT id
                      FROM dsapi_chat_history
                      WHERE group_id = ?
                      ORDER BY id DESC
                      LIMIT ?
                  )
                """,
                (normalized_group_id, normalized_group_id, message_limit),
            )

    def get_dsapi_group_context(
        self,
        group_id: int,
        *,
        message_limit: int = 10,
        idle_seconds: int = 1200,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        normalized_group_id = int(group_id)
        normalized_limit = max(1, min(int(message_limit), 50))
        normalized_idle_seconds = int(idle_seconds)
        if normalized_idle_seconds <= 0:
            raise ValueError("idle_seconds must be > 0")
        cutoff = (time.time() if now is None else float(now)) - normalized_idle_seconds
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM dsapi_group_context
                WHERE group_id = ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM dsapi_group_context
                      WHERE group_id = ? AND created_at >= ?
                  )
                """,
                (normalized_group_id, normalized_group_id, cutoff),
            )
            rows = conn.execute(
                """
                SELECT user_id, content
                FROM (
                    SELECT id, user_id, content
                    FROM dsapi_group_context
                    WHERE group_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                ORDER BY id ASC
                """,
                (normalized_group_id, normalized_limit),
            ).fetchall()
        return [
            {"user_id": int(row["user_id"]), "content": str(row["content"])}
            for row in rows
        ]

    def record_dsapi_group_message(
        self,
        *,
        group_id: int,
        user_id: int,
        content: str,
        message_limit: int = 10,
        now: float | None = None,
    ) -> None:
        normalized_content = " ".join(str(content).split())[:300]
        if not normalized_content:
            return
        normalized_group_id = int(group_id)
        normalized_limit = max(1, min(int(message_limit), 50))
        created_at = time.time() if now is None else float(now)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO dsapi_group_context(group_id, user_id, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (normalized_group_id, int(user_id), normalized_content, created_at),
            )
            conn.execute(
                """
                DELETE FROM dsapi_group_context
                WHERE group_id = ?
                  AND id NOT IN (
                      SELECT id
                      FROM dsapi_group_context
                      WHERE group_id = ?
                      ORDER BY id DESC
                      LIMIT ?
                  )
                """,
                (normalized_group_id, normalized_group_id, normalized_limit),
            )

    def clear_dsapi_chat_history(self, *, actor_id: int) -> int:
        with self._connect() as conn:
            deleted = conn.execute("DELETE FROM dsapi_chat_history").rowcount
            deleted += conn.execute("DELETE FROM dsapi_group_context").rowcount
            self.audit(
                actor_id,
                "clear_dsapi_chat_history",
                "dsapi",
                {"deleted": deleted},
                conn=conn,
            )
        return int(deleted)

    def get_trigger_mention(self) -> bool:
        return self.get_setting("trigger_mention", "true").lower() in {"1", "true", "yes", "on"}

    def set_trigger_mention(self, enabled: bool, actor_id: int) -> None:
        value = "true" if enabled else "false"
        with self._connect() as conn:
            self.set_setting("trigger_mention", value, conn=conn)
            self.audit(actor_id, "set_trigger_mention", "policy", {"enabled": enabled}, conn=conn)

    def get_direct_trigger_percent(self) -> float:
        try:
            value = float(self.get_setting("direct_trigger_percent", "10"))
        except ValueError:
            return 10.0
        return max(0.0, min(value, 100.0))

    def set_direct_trigger_percent(self, percent: float, actor_id: int) -> None:
        percent = float(percent)
        if percent < 0 or percent > 100:
            raise ValueError("direct trigger percent must be between 0 and 100")
        with self._connect() as conn:
            self.set_setting("direct_trigger_percent", str(percent), conn=conn)
            self.audit(
                actor_id,
                "set_direct_trigger_percent",
                "policy",
                {"percent": percent},
                conn=conn,
            )

    def get_per_group_seconds(self) -> float:
        return float(self.get_setting("per_group_seconds", "5"))

    def get_per_user_per_minute(self) -> int:
        return int(self.get_setting("per_user_per_minute", "5"))

    def set_limits(self, per_group_seconds: float, per_user_per_minute: int, actor_id: int) -> None:
        if per_group_seconds < 0 or per_user_per_minute < 0:
            raise ValueError("limits must be non-negative")
        with self._connect() as conn:
            self.set_setting("per_group_seconds", str(per_group_seconds), conn=conn)
            self.set_setting("per_user_per_minute", str(per_user_per_minute), conn=conn)
            self.audit(
                actor_id,
                "set_limits",
                "policy",
                {
                    "per_group_seconds": per_group_seconds,
                    "per_user_per_minute": per_user_per_minute,
                },
                conn=conn,
            )

    def is_group_enabled(self, group_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT enabled FROM groups WHERE group_id = ?",
                (group_id,),
            ).fetchone()
            return bool(row and row["enabled"])

    def is_group_blocked(self, group_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT blocked FROM groups WHERE group_id = ?",
                (group_id,),
            ).fetchone()
            return bool(row and row["blocked"])

    def set_group_enabled(self, group_id: int, enabled: bool, actor_id: int) -> None:
        with self._connect() as conn:
            self._upsert_group_flag(conn, group_id, "enabled", enabled)
            self.audit(
                actor_id,
                "set_group_enabled",
                str(group_id),
                {"enabled": enabled},
                conn=conn,
            )

    def set_group_blocked(self, group_id: int, blocked: bool, actor_id: int) -> None:
        with self._connect() as conn:
            self._upsert_group_flag(conn, group_id, "blocked", blocked)
            self.audit(
                actor_id,
                "set_group_blocked",
                str(group_id),
                {"blocked": blocked},
                conn=conn,
            )

    def _upsert_group_flag(
        self,
        conn: sqlite3.Connection,
        group_id: int,
        column: str,
        enabled: bool,
    ) -> None:
        if column not in {"enabled", "blocked"}:
            raise ValueError("invalid group flag")
        now = time.time()
        conn.execute(
            "INSERT OR IGNORE INTO groups(group_id, updated_at) VALUES (?, ?)",
            (group_id, now),
        )
        conn.execute(
            f"UPDATE groups SET {column} = ?, updated_at = ? WHERE group_id = ?",
            (1 if enabled else 0, now, group_id),
        )

    def enabled_groups(self) -> list[int]:
        return self._list_group_ids("enabled")

    def blocked_groups(self) -> list[int]:
        return self._list_group_ids("blocked")

    def _list_group_ids(self, column: str) -> list[int]:
        if column not in {"enabled", "blocked"}:
            raise ValueError("invalid group flag")
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT group_id FROM groups WHERE {column} = 1 ORDER BY group_id"
            ).fetchall()
            return [int(row["group_id"]) for row in rows]

    def add_admin(
        self,
        user_id: int,
        actor_id: int,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        if conn is None:
            with self._connect() as local_conn:
                self.add_admin(user_id, actor_id, conn=local_conn)
            return
        conn.execute(
            "INSERT OR IGNORE INTO admins(user_id, created_at) VALUES (?, ?)",
            (user_id, time.time()),
        )
        self.audit(actor_id, "add_admin", str(user_id), {}, conn=conn)

    def remove_admin(self, user_id: int, actor_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
            self.audit(actor_id, "remove_admin", str(user_id), {}, conn=conn)

    def is_admin(self, user_id: int) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)).fetchone()
            return row is not None

    def admins(self) -> list[int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT user_id FROM admins ORDER BY user_id").fetchall()
            return [int(row["user_id"]) for row in rows]

    def prefixes(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT prefix FROM trigger_prefixes ORDER BY prefix").fetchall()
            prefixes = [str(row["prefix"]) for row in rows]
            return prefixes or ["~"]

    def set_prefixes(self, prefixes: list[str], actor_id: int) -> None:
        normalized = self._normalize_prefixes(prefixes)

        with self._connect() as conn:
            conn.execute("DELETE FROM trigger_prefixes")
            for prefix in normalized:
                conn.execute("INSERT INTO trigger_prefixes(prefix) VALUES (?)", (prefix,))
            self.audit(actor_id, "set_prefixes", "policy", {"prefixes": normalized}, conn=conn)

    def add_prefix(self, prefix: str, actor_id: int) -> None:
        prefix = prefix.strip()
        if not prefix:
            raise ValueError("prefix cannot be empty")
        with self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO trigger_prefixes(prefix) VALUES (?)", (prefix,))
            self.audit(actor_id, "add_prefix", prefix, {}, conn=conn)

    def remove_prefix(self, prefix: str, actor_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM trigger_prefixes WHERE prefix = ?", (prefix,))
            self.audit(actor_id, "remove_prefix", prefix, {}, conn=conn)

    def snapshot(self) -> dict[str, Any]:
        return {
            "mode": self.get_mode(),
            "enabled_groups": self.enabled_groups(),
            "blocked_groups": self.blocked_groups(),
            "admins": self.admins(),
            "trigger": {
                "mention": self.get_trigger_mention(),
                "prefixes": self.prefixes(),
                "direct_trigger_percent": self.get_direct_trigger_percent(),
            },
            "limits": {
                "per_group_seconds": self.get_per_group_seconds(),
                "per_user_per_minute": self.get_per_user_per_minute(),
            },
        }

    def get_lua_state(self, namespace: str, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM lua_state WHERE namespace = ? AND key = ?",
                (namespace, key),
            ).fetchone()
            return str(row["value"]) if row else None

    def set_lua_state(self, namespace: str, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO lua_state(namespace, key, value, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(namespace, key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (namespace, key, value, time.time()),
            )

    def delete_lua_state(self, namespace: str, key: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM lua_state WHERE namespace = ? AND key = ?",
                (namespace, key),
            )
            return cursor.rowcount > 0

    def record_group_message_activity(
        self,
        *,
        group_id: int,
        user_id: int,
        timestamp: float,
        raw_message: str,
        segments: Any,
    ) -> None:
        group_id = int(group_id)
        user_id = int(user_id)
        event_time = float(timestamp or time.time())
        event_datetime = datetime.fromtimestamp(event_time, CHINA_TZ)
        event_date = event_datetime.date().isoformat()
        hour = event_datetime.hour
        text_chars = self._message_text_length(raw_message, segments)
        image_count = self._count_segments(segments, "image")
        at_count = self._count_segments(segments, "at")
        reply_count = self._count_segments(segments, "reply")
        now = time.time()

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT hourly_json, first_timestamp, last_timestamp
                FROM group_daily_stats
                WHERE date = ? AND group_id = ? AND user_id = ?
                """,
                (event_date, group_id, user_id),
            ).fetchone()
            if row is None:
                hourly_counts = [0] * 24
                hourly_counts[hour] = 1
                conn.execute(
                    """
                    INSERT INTO group_daily_stats(
                        date,
                        group_id,
                        user_id,
                        message_count,
                        text_chars,
                        image_count,
                        at_count,
                        reply_count,
                        first_timestamp,
                        last_timestamp,
                        hourly_json,
                        updated_at
                    )
                    VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_date,
                        group_id,
                        user_id,
                        text_chars,
                        image_count,
                        at_count,
                        reply_count,
                        event_time,
                        event_time,
                        json.dumps(hourly_counts, separators=(",", ":")),
                        now,
                    ),
                )
                return

            hourly_counts = self._decode_hourly_counts(str(row["hourly_json"]))
            hourly_counts[hour] += 1
            conn.execute(
                """
                UPDATE group_daily_stats
                SET
                    message_count = message_count + 1,
                    text_chars = text_chars + ?,
                    image_count = image_count + ?,
                    at_count = at_count + ?,
                    reply_count = reply_count + ?,
                    first_timestamp = MIN(first_timestamp, ?),
                    last_timestamp = MAX(last_timestamp, ?),
                    hourly_json = ?,
                    updated_at = ?
                WHERE date = ? AND group_id = ? AND user_id = ?
                """,
                (
                    text_chars,
                    image_count,
                    at_count,
                    reply_count,
                    event_time,
                    event_time,
                    json.dumps(hourly_counts, separators=(",", ":")),
                    now,
                    event_date,
                    group_id,
                    user_id,
                ),
            )

    def get_group_daily_summary(
        self,
        group_id: int,
        date: str,
        limit: int = 5,
    ) -> dict[str, Any]:
        group_id = int(group_id)
        target_date = str(date or "").strip()
        if not target_date:
            target_date = datetime.now(CHINA_TZ).date().isoformat()
        limit = max(1, min(int(limit), 20))

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    date,
                    group_id,
                    user_id,
                    message_count,
                    text_chars,
                    image_count,
                    at_count,
                    reply_count,
                    first_timestamp,
                    last_timestamp,
                    hourly_json
                FROM group_daily_stats
                WHERE date = ? AND group_id = ?
                """,
                (target_date, group_id),
            ).fetchall()

        stats = [self._group_stat_from_row(row) for row in rows]
        total_messages = sum(item["message_count"] for item in stats)
        hourly_counts = [0] * 24
        for item in stats:
            for hour, count in enumerate(item["hourly_counts"]):
                hourly_counts[hour] += count
        active_hours = [
            {"hour": hour, "message_count": count}
            for hour, count in enumerate(hourly_counts)
            if count > 0
        ]
        active_hours.sort(key=lambda item: (-item["message_count"], item["hour"]))
        peak_hour = active_hours[0] if active_hours else None
        early_bird = min(stats, key=lambda item: item["first_timestamp"]) if stats else None
        night_owl = max(stats, key=lambda item: item["last_timestamp"]) if stats else None

        return {
            "date": target_date,
            "group_id": group_id,
            "total_messages": total_messages,
            "active_users": len(stats),
            "peak_hour": peak_hour,
            "active_hours": active_hours[:limit],
            "early_bird": self._public_group_stat(early_bird) if early_bird else None,
            "night_owl": self._public_group_stat(night_owl) if night_owl else None,
            "top_messages": self._top_group_stats(stats, "message_count", limit),
            "top_text_chars": self._top_group_stats(stats, "text_chars", limit),
            "top_images": self._top_group_stats(stats, "image_count", limit, positive_only=True),
            "top_mentions": self._top_group_stats(stats, "at_count", limit, positive_only=True),
        }

    def menu_recipe_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM menu_recipes").fetchone()
            return int(row["count"]) if row else 0

    def list_menu_recipes(self, search: str = "", limit: int = 200) -> list[dict[str, Any]]:
        normalized_search = search.strip().casefold()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    title,
                    aliases_json,
                    cuisine,
                    region,
                    category,
                    tags_json,
                    ingredients_json,
                    steps_json,
                    image_relpath,
                    enabled,
                    source
                FROM menu_recipes
                ORDER BY updated_at DESC, title
                """
            ).fetchall()
        recipes = [self._menu_recipe_from_row(row) for row in rows]
        if normalized_search:
            recipes = [recipe for recipe in recipes if self._menu_recipe_matches(recipe, normalized_search)]
        return recipes[: max(1, min(int(limit), 500))]

    def import_menu_recipes(self, seed_path: Path, image_dir: Path) -> int:
        records = load_seed_records(seed_path, image_dir)
        if not records:
            return 0

        with self._connect() as conn:
            for record in records:
                self._upsert_menu_recipe(conn, record)
        return len(records)

    def import_howtocook_recipes(self, image_dir: Path, limit: int | None = None) -> int:
        records = load_howtocook_records(image_dir=image_dir, limit=limit)
        if not records:
            return 0

        with self._connect() as conn:
            for record in records:
                self._upsert_menu_recipe(conn, record)
        return len(records)

    def ensure_menu_recipes(self, seed_path: Path, image_dir: Path) -> int:
        if self.menu_recipe_count() > 0:
            return 0
        return self.import_menu_recipes(seed_path, image_dir)

    def pick_menu_recipe(self, target: str, seed: int, seed_path: Path, image_dir: Path) -> dict[str, Any] | None:
        self.ensure_menu_recipes(seed_path, image_dir)
        recipes = self._enabled_menu_recipes()
        if not recipes:
            return None

        normalized_target = target.strip().casefold()
        candidates = recipes
        if normalized_target:
            matched = [recipe for recipe in recipes if self._menu_recipe_matches(recipe, normalized_target)]
            if matched:
                candidates = matched

        imaged_candidates = [
            recipe for recipe in candidates if self._menu_recipe_has_image(recipe, image_dir)
        ]
        if imaged_candidates:
            candidates = imaged_candidates

        index = abs(int(seed)) % len(candidates)
        return candidates[index]

    def prune_howtocook_without_images(self, image_dir: Path) -> int:
        deleted = 0
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, image_relpath FROM menu_recipes WHERE source = 'howtocook'"
            ).fetchall()
            for row in rows:
                recipe = {"image_relpath": str(row["image_relpath"])}
                if self._menu_recipe_has_image(recipe, image_dir):
                    continue
                cursor = conn.execute("DELETE FROM menu_recipes WHERE id = ?", (str(row["id"]),))
                deleted += int(cursor.rowcount)
        return deleted

    def save_custom_menu_recipe(
        self,
        title: str,
        image_dir: Path,
        *,
        image_source: str = "",
        image_body: bytes | None = None,
        image_suffix: str = ".jpg",
        enabled: bool = True,
    ) -> dict[str, Any]:
        title = normalize_text(title, "title")
        recipe_id = "custom-" + hashlib.sha1(title.casefold().encode("utf-8")).hexdigest()[:16]
        image_relpath = ""
        if image_body is not None:
            image_relpath = cache_image_bytes(
                image_body,
                recipe_id=recipe_id,
                image_dir=image_dir,
                suffix=image_suffix,
            )
        elif image_source:
            image_relpath = cache_image(image_source, recipe_id=recipe_id, image_dir=image_dir)
        if not image_relpath:
            raise ValueError("menu image is required and must be a supported image")

        with self._connect() as conn:
            existing = self._menu_recipe_by_title(conn, title, enabled_only=False)
            if existing is not None:
                recipe_id = existing["id"]
            record = self._custom_menu_record(
                recipe_id,
                title=title,
                image_relpath=image_relpath,
                enabled=enabled,
            )
            self._upsert_menu_recipe(conn, record)
            saved = self._menu_recipe_by_id(conn, recipe_id)
            if saved is None:
                raise RuntimeError(f"failed to save menu recipe: {title}")
            return saved

    def update_menu_recipe(
        self,
        recipe_id: str,
        image_dir: Path,
        *,
        title: str | None = None,
        enabled: bool | None = None,
        image_source: str = "",
        image_body: bytes | None = None,
        image_suffix: str = ".jpg",
    ) -> dict[str, Any]:
        with self._connect() as conn:
            current = self._menu_recipe_by_id(conn, recipe_id)
            if current is None:
                raise KeyError("menu recipe not found")
            next_title = normalize_text(title if title is not None else current["title"], "title")
            next_enabled = current["enabled"] if enabled is None else bool(enabled)
            image_relpath = current["image_relpath"]
            if image_body is not None:
                image_relpath = cache_image_bytes(
                    image_body,
                    recipe_id=recipe_id,
                    image_dir=image_dir,
                    suffix=image_suffix,
                )
            elif image_source:
                image_relpath = cache_image(image_source, recipe_id=recipe_id, image_dir=image_dir)
            if not image_relpath:
                raise ValueError("menu image is required and must be a supported image")
            record = self._custom_menu_record(
                recipe_id,
                title=next_title,
                image_relpath=image_relpath,
                enabled=next_enabled,
            )
            self._upsert_menu_recipe(conn, record)
            saved = self._menu_recipe_by_id(conn, recipe_id)
            if saved is None:
                raise RuntimeError(f"failed to update menu recipe: {recipe_id}")
            return saved

    def delete_menu_recipe(self, recipe_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM menu_recipes WHERE id = ?", (recipe_id,))
            return cursor.rowcount > 0

    def save_external_menu_recipe_if_new(self, recipe: dict[str, Any], image_dir: Path) -> dict[str, Any]:
        title = normalize_text(recipe.get("title"), "title")
        with self._connect() as conn:
            existing = self._menu_recipe_by_title(conn, title)
            if existing is not None:
                return existing

            recipe_id = normalize_text(
                recipe.get("id") or "external-" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:16],
                "id",
            )
            image_relpath = ""
            try:
                image_relpath = cache_image(recipe.get("image_url"), recipe_id=recipe_id, image_dir=image_dir)
            except (OSError, ValueError):
                image_relpath = ""

            record = {
                "id": recipe_id,
                "title": title,
                "aliases_json": encode_json(optional_text_list(recipe.get("aliases"))),
                "cuisine": normalize_text(recipe.get("cuisine") or "国内菜谱", "cuisine"),
                "region": "",
                "category": normalize_text(recipe.get("category") or "菜谱", "category"),
                "tags_json": encode_json(optional_text_list(recipe.get("tags"))),
                "ingredients_json": encode_json(normalize_text_list(recipe.get("ingredients"), "ingredients")),
                "steps_json": encode_json(normalize_text_list(recipe.get("steps"), "steps")),
                "image_relpath": image_relpath,
                "enabled": 1 if bool(recipe.get("enabled", True)) else 0,
                "source": str(recipe.get("source", "external") or "external").strip() or "external",
            }
            self._upsert_menu_recipe(conn, record)
            saved = self._menu_recipe_by_title(conn, title)
            if saved is None:
                raise RuntimeError(f"failed to save external menu recipe: {title}")
            return saved

    def list_restaurants(
        self,
        *,
        group_id: int | None = None,
        search: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if group_id is not None:
            clauses.append("group_id = ?")
            params.append(int(group_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, name, dishes_json, group_id, created_by, enabled, created_at, updated_at
                FROM restaurants
                {where}
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (*params, max(1, min(int(limit), 500))),
            ).fetchall()
        restaurants = [self._restaurant_from_row(row) for row in rows]
        normalized_search = search.strip().casefold()
        if normalized_search:
            restaurants = [
                restaurant
                for restaurant in restaurants
                if normalized_search in restaurant["name"].casefold()
                or any(normalized_search in dish.casefold() for dish in restaurant["dishes"])
            ]
        return restaurants

    def save_restaurant(
        self,
        *,
        name: str,
        dishes: list[str],
        group_id: int,
        created_by: int,
        enabled: bool = True,
    ) -> dict[str, Any]:
        restaurant_name = normalize_text(name, "name")
        normalized_dishes = normalize_text_list(dishes, "dishes")
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO restaurants(name, dishes_json, group_id, created_by, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(group_id, name) DO UPDATE SET
                    dishes_json = excluded.dishes_json,
                    created_by = excluded.created_by,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    restaurant_name,
                    encode_json(normalized_dishes),
                    int(group_id),
                    int(created_by),
                    1 if enabled else 0,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT id, name, dishes_json, group_id, created_by, enabled, created_at, updated_at "
                "FROM restaurants WHERE group_id = ? AND name = ?",
                (int(group_id), restaurant_name),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"failed to save restaurant: {restaurant_name}")
            return self._restaurant_from_row(row)

    def update_restaurant(
        self,
        restaurant_id: int,
        *,
        name: str | None = None,
        dishes: list[str] | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            current = conn.execute(
                "SELECT id, name, dishes_json, group_id, created_by, enabled, created_at, updated_at "
                "FROM restaurants WHERE id = ?",
                (int(restaurant_id),),
            ).fetchone()
            if current is None:
                raise KeyError("restaurant not found")
            current_data = self._restaurant_from_row(current)
            next_name = normalize_text(name if name is not None else current_data["name"], "name")
            next_dishes = (
                normalize_text_list(dishes, "dishes") if dishes is not None else current_data["dishes"]
            )
            next_enabled = current_data["enabled"] if enabled is None else bool(enabled)
            conn.execute(
                """
                UPDATE restaurants
                SET name = ?, dishes_json = ?, enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    next_name,
                    encode_json(next_dishes),
                    1 if next_enabled else 0,
                    time.time(),
                    int(restaurant_id),
                ),
            )
            row = conn.execute(
                "SELECT id, name, dishes_json, group_id, created_by, enabled, created_at, updated_at "
                "FROM restaurants WHERE id = ?",
                (int(restaurant_id),),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"failed to update restaurant: {restaurant_id}")
            return self._restaurant_from_row(row)

    def delete_restaurant(self, restaurant_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM restaurants WHERE id = ?", (int(restaurant_id),))
            return cursor.rowcount > 0

    def pick_restaurant(self, group_id: int, seed: int) -> dict[str, Any] | None:
        restaurants = [
            restaurant
            for restaurant in self.list_restaurants(group_id=int(group_id), limit=500)
            if restaurant["enabled"]
        ]
        if not restaurants:
            return None
        return restaurants[abs(int(seed)) % len(restaurants)]

    def purge_legacy_menu_caches(self, conn: sqlite3.Connection | None = None) -> int:
        if conn is None:
            with self._connect() as local_conn:
                return self.purge_legacy_menu_caches(conn=local_conn)
        cursor = conn.execute(
            """
            DELETE FROM lua_state
            WHERE namespace IN (
                '今日菜单:cache:v1',
                '今日菜单:cache:v2',
                '今日菜单:cachev1',
                '今日菜单:cachev2'
            )
            OR namespace LIKE '今日菜单:cache:%'
            OR namespace LIKE '今日菜单:cachev%'
            """
        )
        return int(cursor.rowcount)

    def _enabled_menu_recipes(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    title,
                    aliases_json,
                    cuisine,
                    region,
                    category,
                    tags_json,
                    ingredients_json,
                    steps_json,
                    image_relpath,
                    enabled,
                    source
                FROM menu_recipes
                WHERE enabled = 1
                ORDER BY id
                """
            ).fetchall()
        return [self._menu_recipe_from_row(row) for row in rows]

    def _menu_recipe_by_title(
        self,
        conn: sqlite3.Connection,
        title: str,
        *,
        enabled_only: bool = True,
    ) -> dict[str, Any] | None:
        where = "WHERE enabled = 1" if enabled_only else ""
        rows = conn.execute(
            f"""
            SELECT
                id,
                title,
                aliases_json,
                cuisine,
                region,
                category,
                tags_json,
                ingredients_json,
                steps_json,
                image_relpath,
                enabled,
                source
            FROM menu_recipes
            {where}
            ORDER BY id
            """
        ).fetchall()
        normalized_title = title.strip().casefold()
        for row in rows:
            if str(row["title"]).strip().casefold() == normalized_title:
                return self._menu_recipe_from_row(row)
        return None

    def _menu_recipe_by_id(self, conn: sqlite3.Connection, recipe_id: str) -> dict[str, Any] | None:
        row = conn.execute(
            """
            SELECT
                id,
                title,
                aliases_json,
                cuisine,
                region,
                category,
                tags_json,
                ingredients_json,
                steps_json,
                image_relpath,
                enabled,
                source
            FROM menu_recipes
            WHERE id = ?
            """,
            (recipe_id,),
        ).fetchone()
        return self._menu_recipe_from_row(row) if row else None

    def _custom_menu_record(
        self,
        recipe_id: str,
        *,
        title: str,
        image_relpath: str,
        enabled: bool,
    ) -> dict[str, Any]:
        return {
            "id": recipe_id,
            "title": title,
            "aliases_json": encode_json([]),
            "cuisine": "自定义",
            "region": "",
            "category": "自定义菜单",
            "tags_json": encode_json(["自定义"]),
            "ingredients_json": encode_json([title]),
            "steps_json": encode_json(["自定义添加"]),
            "image_relpath": image_relpath,
            "enabled": 1 if enabled else 0,
            "source": "custom",
        }

    def _normalize_prefixes(self, prefixes: list[str]) -> list[str]:
        normalized = []
        seen = set()
        for prefix in prefixes:
            value = str(prefix).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        return normalized or ["~"]

    def _normalize_int_ids(self, values: list[int], field_name: str) -> list[int]:
        normalized = []
        seen = set()
        for value in values:
            item = int(value)
            if item <= 0:
                raise ValueError(f"{field_name} must contain positive integers")
            if item in seen:
                continue
            seen.add(item)
            normalized.append(item)
        return normalized

    def _upsert_menu_recipe(self, conn: sqlite3.Connection, recipe: dict[str, Any]) -> None:
        now = time.time()
        conn.execute(
            """
            INSERT INTO menu_recipes(
                id,
                title,
                aliases_json,
                cuisine,
                region,
                category,
                tags_json,
                ingredients_json,
                steps_json,
                image_relpath,
                enabled,
                source,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                aliases_json = excluded.aliases_json,
                cuisine = excluded.cuisine,
                region = excluded.region,
                category = excluded.category,
                tags_json = excluded.tags_json,
                ingredients_json = excluded.ingredients_json,
                steps_json = excluded.steps_json,
                image_relpath = excluded.image_relpath,
                enabled = excluded.enabled,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            (
                recipe["id"],
                recipe["title"],
                recipe["aliases_json"],
                recipe["cuisine"],
                recipe["region"],
                recipe["category"],
                recipe["tags_json"],
                recipe["ingredients_json"],
                recipe["steps_json"],
                recipe["image_relpath"],
                recipe["enabled"],
                recipe["source"],
                now,
                now,
            ),
        )

    def _menu_recipe_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "title": str(row["title"]),
            "aliases": decode_json_list(str(row["aliases_json"])),
            "cuisine": str(row["cuisine"]),
            "region": str(row["region"]),
            "category": str(row["category"]),
            "tags": decode_json_list(str(row["tags_json"])),
            "ingredients": decode_json_list(str(row["ingredients_json"])),
            "steps": decode_json_list(str(row["steps_json"])),
            "image_relpath": str(row["image_relpath"]),
            "enabled": bool(row["enabled"]),
            "source": str(row["source"]),
        }

    def _menu_recipe_matches(self, recipe: dict[str, Any], target: str) -> bool:
        fields = [
            recipe["title"],
            recipe["cuisine"],
            recipe["category"],
            *recipe["aliases"],
            *recipe["tags"],
        ]
        for value in fields:
            normalized_value = str(value).strip().casefold()
            if normalized_value == target or target in normalized_value:
                return True
        return False

    def _menu_recipe_has_image(self, recipe: dict[str, Any], image_dir: Path) -> bool:
        relative_path = str(recipe["image_relpath"]).strip()
        if not relative_path:
            return False

        root = image_dir.resolve(strict=False)
        image_path = (root / relative_path).resolve(strict=False)
        try:
            image_path.relative_to(root)
        except ValueError:
            return False
        return is_supported_image_file(image_path)

    def _restaurant_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "dishes": decode_json_list(str(row["dishes_json"])),
            "group_id": int(row["group_id"]),
            "created_by": int(row["created_by"]),
            "enabled": bool(row["enabled"]),
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
        }

    def _count_segments(self, segments: Any, segment_type: str) -> int:
        count = 0
        for segment in self._iter_segments(segments):
            if str(segment.get("type") or "") == segment_type:
                count += 1
        return count

    def _message_text_length(self, raw_message: str, segments: Any) -> int:
        iterated_segments = self._iter_segments(segments)
        if iterated_segments:
            parts = []
            for segment in iterated_segments:
                if str(segment.get("type") or "") != "text":
                    continue
                data = segment.get("data")
                if isinstance(data, Mapping):
                    parts.append(str(data.get("text", "")))
            return len("".join(parts).strip())
        return len(_strip_cq_segments(str(raw_message or "")).strip())

    def _text_from_segments(self, segments: Any) -> str | None:
        iterated = self._iter_segments(segments)
        if not iterated:
            return None
        parts = []
        for segment in iterated:
            if str(segment.get("type") or "") != "text":
                continue
            data = segment.get("data")
            if isinstance(data, Mapping):
                parts.append(str(data.get("text", "")))
        return "".join(parts)

    def _iter_segments(self, segments: Any) -> list[Mapping[str, Any]]:
        if segments is None or isinstance(segments, str):
            return []
        if isinstance(segments, Mapping):
            return [segments] if "type" in segments else []

        result = []
        try:
            iterator = iter(segments)
        except TypeError:
            return []
        for item in iterator:
            if isinstance(item, Mapping):
                result.append(item)
        return result

    def _decode_hourly_counts(self, value: str) -> list[int]:
        try:
            raw = json.loads(value)
        except json.JSONDecodeError:
            raw = []
        counts = []
        if isinstance(raw, list):
            for item in raw[:24]:
                try:
                    counts.append(max(0, int(item)))
                except (TypeError, ValueError):
                    counts.append(0)
        counts.extend([0] * (24 - len(counts)))
        return counts[:24]

    def _group_stat_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        first_timestamp = float(row["first_timestamp"])
        last_timestamp = float(row["last_timestamp"])
        return {
            "date": str(row["date"]),
            "group_id": int(row["group_id"]),
            "user_id": int(row["user_id"]),
            "message_count": int(row["message_count"]),
            "text_chars": int(row["text_chars"]),
            "image_count": int(row["image_count"]),
            "at_count": int(row["at_count"]),
            "reply_count": int(row["reply_count"]),
            "first_timestamp": first_timestamp,
            "last_timestamp": last_timestamp,
            "first_time": self._time_label(first_timestamp),
            "last_time": self._time_label(last_timestamp),
            "hourly_counts": self._decode_hourly_counts(str(row["hourly_json"])),
        }

    def _public_group_stat(self, stat: dict[str, Any] | None) -> dict[str, Any] | None:
        if stat is None:
            return None
        return {
            "user_id": stat["user_id"],
            "message_count": stat["message_count"],
            "text_chars": stat["text_chars"],
            "image_count": stat["image_count"],
            "at_count": stat["at_count"],
            "reply_count": stat["reply_count"],
            "first_timestamp": stat["first_timestamp"],
            "last_timestamp": stat["last_timestamp"],
            "first_time": stat["first_time"],
            "last_time": stat["last_time"],
        }

    def _top_group_stats(
        self,
        stats: list[dict[str, Any]],
        metric: str,
        limit: int,
        *,
        positive_only: bool = False,
    ) -> list[dict[str, Any]]:
        ranked = [item for item in stats if not positive_only or item[metric] > 0]
        ranked.sort(
            key=lambda item: (
                -int(item[metric]),
                -int(item["message_count"]),
                int(item["user_id"]),
            )
        )
        return [
            public
            for item in ranked[:limit]
            if (public := self._public_group_stat(item)) is not None
        ]

    def _time_label(self, timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp, CHINA_TZ).strftime("%H:%M")

    def audit(
        self,
        actor_id: int | str,
        action: str,
        target: str,
        detail: dict[str, Any],
        conn: sqlite3.Connection | None = None,
    ) -> None:
        if conn is None:
            with self._connect() as local_conn:
                self.audit(actor_id, action, target, detail, conn=local_conn)
            return
        conn.execute(
            """
            INSERT INTO audit_log(actor_id, action, target, detail, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(actor_id), action, target, json.dumps(detail, sort_keys=True), time.time()),
        )
