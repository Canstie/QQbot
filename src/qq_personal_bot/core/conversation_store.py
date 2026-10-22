from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from qq_personal_bot.core.store_constants import _MAX_DSAPI_HISTORY_TURNS


class ConversationStore:
    """Conversation persistence using the owning store connection."""

    def get_dsapi_blocked_users(self) -> list[int]:
        def load() -> tuple[int, ...]:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT user_id FROM dsapi_blocked_users ORDER BY user_id"
                ).fetchall()
                return tuple(int(row["user_id"]) for row in rows)

        return list(self._cached_runtime_value(("dsapi_blocked_users",), load))

    def is_dsapi_user_blocked(self, user_id: int) -> bool:
        return int(user_id) in self.get_dsapi_blocked_users()

    def set_dsapi_user_blocked(self, user_id: int, blocked: bool, *, actor_id: int) -> bool:
        user_id = int(user_id)
        if not 0 < user_id <= 2**63 - 1:
            raise ValueError("QQ 号必须为有效的正整数")
        with self._connect() as conn:
            if blocked:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO dsapi_blocked_users(user_id, created_at) VALUES (?, ?)",
                    (user_id, time.time()),
                )
            else:
                cursor = conn.execute(
                    "DELETE FROM dsapi_blocked_users WHERE user_id = ?", (user_id,)
                )
            changed = cursor.rowcount > 0
            if changed:
                self.audit(
                    actor_id,
                    "set_dsapi_user_blocked",
                    str(user_id),
                    {"blocked": blocked},
                    conn=conn,
                )
        self._invalidate_runtime_cache()
        return changed

    def get_dsapi_config(self) -> dict[str, Any]:
        try:
            history_turns = int(self.get_setting("dsapi_history_turns", "2"))
        except ValueError:
            history_turns = 2
        history_turns = max(1, min(history_turns, _MAX_DSAPI_HISTORY_TURNS))
        try:
            random_reply_percent = float(self.get_setting("dsapi_random_reply_percent", "2"))
        except ValueError:
            random_reply_percent = 2.0
        random_reply_percent = max(0.0, min(random_reply_percent, 100.0))
        try:
            random_sticker_percent = float(self.get_setting("dsapi_random_sticker_percent", "20"))
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
            knowledge_bases = self._list_dsapi_knowledge_bases(conn)
            active_knowledge_id = self._active_knowledge_id(conn)
            active_knowledge = next(
                (item for item in knowledge_bases if item["id"] == active_knowledge_id),
                None,
            )
            if active_knowledge is not None:
                history_turns = int(active_knowledge["history_turns"])
        return {
            "enabled": self.get_setting("dsapi_enabled", "true").lower()
            in {"1", "true", "yes", "on"},
            "knowledge_enabled": self.get_setting("dsapi_knowledge_enabled", "false").lower()
            in {"1", "true", "yes", "on"},
            "knowledge_prompt": active_knowledge["prompt"] if active_knowledge else "",
            "knowledge_bases": knowledge_bases,
            "active_knowledge": active_knowledge,
            "active_knowledge_id": active_knowledge_id,
            "active_knowledge_name": active_knowledge["name"] if active_knowledge else "",
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
        knowledge_prompt: str | None,
        active_knowledge_id: int | None = None,
        history_turns: int,
        enabled_groups: list[int],
        clear_history: bool,
        actor_id: int,
        random_reply_percent: float = 2.0,
        random_sticker_percent: float = 20.0,
        enabled: bool = True,
    ) -> dict[str, Any]:
        prompt = (
            self._normalize_knowledge_prompt(knowledge_prompt)
            if knowledge_prompt is not None
            else None
        )
        turns = int(history_turns)
        if turns < 1 or turns > _MAX_DSAPI_HISTORY_TURNS:
            raise ValueError(f"history_turns must be between 1 and {_MAX_DSAPI_HISTORY_TURNS}")
        percent = float(random_reply_percent)
        if percent < 0 or percent > 100:
            raise ValueError("random_reply_percent must be between 0 and 100")
        sticker_percent = float(random_sticker_percent)
        if sticker_percent < 0 or sticker_percent > 100:
            raise ValueError("random_sticker_percent must be between 0 and 100")
        normalized_enabled_groups = self._normalize_int_ids(enabled_groups, "enabled_groups")

        with self._connect() as conn:
            selected_id = self._active_knowledge_id(conn)
            if active_knowledge_id is not None:
                selected_id = int(active_knowledge_id)
                if not self._knowledge_base_exists(selected_id, conn):
                    raise ValueError("knowledge base not found")
                self._set_active_knowledge_id(selected_id, conn)
            if prompt is not None:
                if selected_id is None and prompt:
                    now = time.time()
                    default_model_row = conn.execute(
                        "SELECT value FROM settings WHERE key = 'dsapi_default_model'"
                    ).fetchone()
                    default_tokens_row = conn.execute(
                        "SELECT value FROM settings WHERE key = 'dsapi_default_max_tokens'"
                    ).fetchone()
                    default_model = (
                        str(default_model_row["value"]) if default_model_row else "deepseek-flash"
                    )
                    default_max_tokens = (
                        int(default_tokens_row["value"]) if default_tokens_row else 80
                    )
                    cursor = conn.execute(
                        """
                        INSERT INTO dsapi_knowledge_bases(
                            name, prompt, model, thinking_enabled, max_tokens,
                            history_turns, temperature,
                            created_at, updated_at
                        )
                        VALUES (?, ?, ?, 0, ?, ?, NULL, ?, ?)
                        """,
                        (
                            "默认知识库",
                            prompt,
                            default_model,
                            default_max_tokens,
                            turns,
                            now,
                            now,
                        ),
                    )
                    selected_id = int(cursor.lastrowid)
                    self._set_active_knowledge_id(selected_id, conn)
                elif selected_id is not None:
                    conn.execute(
                        """
                        UPDATE dsapi_knowledge_bases
                        SET prompt = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (prompt, time.time(), selected_id),
                    )
            if selected_id is not None:
                conn.execute(
                    "UPDATE dsapi_knowledge_bases SET history_turns = ? WHERE id = ?",
                    (turns, selected_id),
                )
            self._sync_legacy_knowledge_prompt(selected_id, conn)
            self.set_setting(
                "dsapi_enabled",
                "true" if enabled else "false",
                conn=conn,
            )
            conn.execute(
                """
                INSERT INTO feature_flags(feature_id, enabled, updated_at)
                VALUES ('ai.master', ?, ?)
                ON CONFLICT(feature_id) DO UPDATE SET
                    enabled = excluded.enabled, updated_at = excluded.updated_at
                """,
                (int(bool(enabled)), time.time()),
            )
            self.set_setting(
                "dsapi_knowledge_enabled",
                "true" if knowledge_enabled else "false",
                conn=conn,
            )
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
                cleared = self._clear_dsapi_history(conn, selected_id)
            self.audit(
                actor_id,
                "set_dsapi_config",
                "dsapi",
                {
                    "enabled": bool(enabled),
                    "knowledge_enabled": bool(knowledge_enabled),
                    "active_knowledge_id": selected_id,
                    "knowledge_prompt_chars": len(prompt or ""),
                    "history_turns": turns,
                    "random_reply_percent": percent,
                    "random_sticker_percent": sticker_percent,
                    "enabled_groups": normalized_enabled_groups,
                    "history_messages_cleared": cleared,
                },
                conn=conn,
            )
        return self.get_dsapi_config()

    def set_dsapi_enabled(self, enabled: bool, *, actor_id: int) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self.set_setting("dsapi_enabled", "true" if enabled else "false", conn=conn)
            conn.execute(
                """
                INSERT INTO feature_flags(feature_id, enabled, updated_at)
                VALUES ('ai.master', ?, ?)
                ON CONFLICT(feature_id) DO UPDATE SET
                    enabled = excluded.enabled, updated_at = excluded.updated_at
                """,
                (int(enabled), time.time()),
            )
            self.audit(actor_id, "set_dsapi_enabled", "ai", {"enabled": enabled}, conn=conn)
        self._invalidate_runtime_cache()

    def enable_dsapi_group(self, group_id: int, *, actor_id: int) -> list[int]:
        normalized_group_id = int(group_id)
        if normalized_group_id <= 0:
            raise ValueError("group_id must be a positive integer")

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'dsapi_enabled_groups'"
            ).fetchone()
            try:
                enabled_groups = self._normalize_int_ids(
                    json.loads(str(row["value"])) if row else [],
                    "enabled_groups",
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                enabled_groups = []
            if normalized_group_id not in enabled_groups:
                enabled_groups.append(normalized_group_id)

            self.set_setting(
                "dsapi_enabled_groups",
                json.dumps(enabled_groups),
                conn=conn,
            )
            self.audit(
                actor_id,
                "enable_dsapi_group",
                str(normalized_group_id),
                {"enabled_groups": enabled_groups},
                conn=conn,
            )
        return enabled_groups

    def disable_dsapi_group(self, group_id: int, *, actor_id: int) -> list[int]:
        normalized_group_id = int(group_id)
        if normalized_group_id <= 0:
            raise ValueError("group_id must be a positive integer")

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT value FROM settings WHERE key = 'dsapi_enabled_groups'"
            ).fetchone()
            try:
                enabled_groups = self._normalize_int_ids(
                    json.loads(str(row["value"])) if row else [],
                    "enabled_groups",
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                enabled_groups = []
            enabled_groups = [item for item in enabled_groups if item != normalized_group_id]

            self.set_setting(
                "dsapi_enabled_groups",
                json.dumps(enabled_groups),
                conn=conn,
            )
            self.audit(
                actor_id,
                "disable_dsapi_group",
                str(normalized_group_id),
                {"enabled_groups": enabled_groups},
                conn=conn,
            )
        return enabled_groups

    def get_dsapi_chat_history(
        self,
        group_id: int,
        history_turns: int,
        *,
        knowledge_id: int = 0,
    ) -> list[dict[str, str]]:
        message_limit = max(1, min(int(history_turns), _MAX_DSAPI_HISTORY_TURNS)) * 2
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content
                FROM (
                    SELECT id, role, content
                    FROM dsapi_chat_history
                    WHERE knowledge_id = ? AND group_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                ORDER BY id ASC
                """,
                (int(knowledge_id), int(group_id), message_limit),
            ).fetchall()
        return [{"role": str(row["role"]), "content": str(row["content"])} for row in rows]

    def expire_dsapi_chat_history(
        self,
        group_id: int,
        *,
        idle_seconds: int,
        now: float | None = None,
        knowledge_id: int = 0,
    ) -> int:
        normalized_idle_seconds = int(idle_seconds)
        if normalized_idle_seconds <= 0:
            raise ValueError("idle_seconds must be > 0")
        cutoff = (time.time() if now is None else float(now)) - normalized_idle_seconds
        with self._connect() as conn:
            deleted = conn.execute(
                """
                DELETE FROM dsapi_chat_history
                WHERE knowledge_id = ? AND group_id = ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM dsapi_chat_history
                      WHERE knowledge_id = ? AND group_id = ? AND created_at >= ?
                  )
                """,
                (
                    int(knowledge_id),
                    int(group_id),
                    int(knowledge_id),
                    int(group_id),
                    cutoff,
                ),
            ).rowcount
        return int(deleted)

    def record_dsapi_exchange(
        self,
        *,
        group_id: int,
        user_content: str,
        assistant_content: str,
        history_turns: int,
        knowledge_id: int = 0,
    ) -> None:
        message_limit = max(1, min(int(history_turns), _MAX_DSAPI_HISTORY_TURNS)) * 2
        normalized_group_id = int(group_id)
        now = time.time()
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO dsapi_chat_history(
                    knowledge_id, group_id, role, content, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        int(knowledge_id),
                        normalized_group_id,
                        "user",
                        str(user_content),
                        now,
                    ),
                    (
                        int(knowledge_id),
                        normalized_group_id,
                        "assistant",
                        str(assistant_content),
                        now,
                    ),
                ],
            )
            conn.execute(
                """
                DELETE FROM dsapi_chat_history
                WHERE knowledge_id = ? AND group_id = ?
                  AND id NOT IN (
                      SELECT id
                      FROM dsapi_chat_history
                      WHERE knowledge_id = ? AND group_id = ?
                      ORDER BY id DESC
                      LIMIT ?
                  )
                """,
                (
                    int(knowledge_id),
                    normalized_group_id,
                    int(knowledge_id),
                    normalized_group_id,
                    message_limit,
                ),
            )

    def get_dsapi_group_context(
        self,
        group_id: int,
        *,
        message_limit: int = 10,
        idle_seconds: int = 1200,
        now: float | None = None,
        knowledge_id: int = 0,
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
                WHERE knowledge_id = ? AND group_id = ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM dsapi_group_context
                      WHERE knowledge_id = ? AND group_id = ? AND created_at >= ?
                  )
                """,
                (
                    int(knowledge_id),
                    normalized_group_id,
                    int(knowledge_id),
                    normalized_group_id,
                    cutoff,
                ),
            )
            rows = conn.execute(
                """
                SELECT user_id, content
                FROM (
                    SELECT id, user_id, content
                    FROM dsapi_group_context
                    WHERE knowledge_id = ? AND group_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                ORDER BY id ASC
                """,
                (int(knowledge_id), normalized_group_id, normalized_limit),
            ).fetchall()
        return [{"user_id": int(row["user_id"]), "content": str(row["content"])} for row in rows]

    def get_recent_group_messages(
        self,
        group_id: int,
        *,
        message_limit: int = 10_000,
    ) -> list[dict[str, Any]]:
        """Return a bounded chronological window used by persona example retrieval."""
        normalized_limit = max(1, min(int(message_limit), 20_000))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT user_id, content, created_at
                FROM (
                    SELECT id, user_id, content, created_at
                    FROM group_daily_messages
                    WHERE group_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                ORDER BY id ASC
                """,
                (int(group_id), normalized_limit),
            ).fetchall()
        return [
            {
                "user_id": int(row["user_id"]),
                "content": str(row["content"]),
                "created_at": float(row["created_at"]),
            }
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
        knowledge_id: int = 0,
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
                INSERT INTO dsapi_group_context(
                    knowledge_id, group_id, user_id, content, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(knowledge_id),
                    normalized_group_id,
                    int(user_id),
                    normalized_content,
                    created_at,
                ),
            )
            conn.execute(
                """
                DELETE FROM dsapi_group_context
                WHERE knowledge_id = ? AND group_id = ?
                  AND id NOT IN (
                      SELECT id
                      FROM dsapi_group_context
                      WHERE knowledge_id = ? AND group_id = ?
                      ORDER BY id DESC
                      LIMIT ?
                  )
                """,
                (
                    int(knowledge_id),
                    normalized_group_id,
                    int(knowledge_id),
                    normalized_group_id,
                    normalized_limit,
                ),
            )

    def clear_dsapi_chat_history(self, *, actor_id: int) -> int:
        with self._connect() as conn:
            deleted = self._clear_dsapi_history(conn)
            self.audit(
                actor_id,
                "clear_dsapi_chat_history",
                "dsapi",
                {"deleted": deleted},
                conn=conn,
            )
        return int(deleted)

    def _clear_dsapi_history(
        self,
        conn: sqlite3.Connection,
        knowledge_id: int | None = None,
    ) -> int:
        if knowledge_id is None:
            deleted = conn.execute("DELETE FROM dsapi_chat_history").rowcount
            deleted += conn.execute("DELETE FROM dsapi_group_context").rowcount
            return int(deleted)
        deleted = conn.execute(
            "DELETE FROM dsapi_chat_history WHERE knowledge_id = ?",
            (int(knowledge_id),),
        ).rowcount
        deleted += conn.execute(
            "DELETE FROM dsapi_group_context WHERE knowledge_id = ?",
            (int(knowledge_id),),
        ).rowcount
        return int(deleted)
