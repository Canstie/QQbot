from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Mapping, Sequence
from typing import Any

from qq_personal_bot.core.store_constants import (
    _MAX_DSAPI_CONTEXT_MESSAGES,
    _MAX_DSAPI_HISTORY_TURNS,
    _MAX_DSAPI_REPLY_MESSAGES,
    _MAX_DSAPI_SIMILAR_EXAMPLES,
)
from qq_personal_bot.settings import AppSettings


class KnowledgeStore:
    """Knowledge persistence using the owning store connection."""

    def _initialize_knowledge_bases(
        self,
        conn: sqlite3.Connection,
        settings: AppSettings,
    ) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(dsapi_knowledge_bases)").fetchall()
        }
        migrations = {
            "model": "TEXT NOT NULL DEFAULT ''",
            "thinking_enabled": "INTEGER NOT NULL DEFAULT 0",
            "max_tokens": "INTEGER NOT NULL DEFAULT 0",
            "history_turns": "INTEGER NOT NULL DEFAULT 0",
            "response_mode": "TEXT NOT NULL DEFAULT 'short'",
            "max_reply_messages": "INTEGER NOT NULL DEFAULT 1",
            "temperature": "REAL",
            "web_search_enabled": "INTEGER NOT NULL DEFAULT 0",
            "relationship_memory_enabled": "INTEGER NOT NULL DEFAULT 0",
            "persona_group_id": "INTEGER NOT NULL DEFAULT 0",
            "persona_user_id": "INTEGER NOT NULL DEFAULT 0",
            "context_messages": "INTEGER NOT NULL DEFAULT 10",
            "similar_examples": "INTEGER NOT NULL DEFAULT 0",
            "relationships_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for column, definition in migrations.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE dsapi_knowledge_bases ADD COLUMN {column} {definition}")
        conn.execute(
            "UPDATE dsapi_knowledge_bases SET model = ? WHERE TRIM(model) = ''",
            (settings.dsapi_model,),
        )
        conn.execute(
            "UPDATE dsapi_knowledge_bases SET max_tokens = ? WHERE max_tokens <= 0",
            (settings.dsapi_max_tokens,),
        )
        history_turns_row = conn.execute(
            "SELECT value FROM settings WHERE key = 'dsapi_history_turns'"
        ).fetchone()
        try:
            default_history_turns = int(history_turns_row["value"] if history_turns_row else 2)
        except ValueError:
            default_history_turns = 2
        default_history_turns = max(
            1,
            min(default_history_turns, _MAX_DSAPI_HISTORY_TURNS),
        )
        conn.execute(
            "UPDATE dsapi_knowledge_bases SET history_turns = ? WHERE history_turns <= 0",
            (default_history_turns,),
        )
        self.set_setting("dsapi_default_model", settings.dsapi_model, conn=conn)
        self.set_setting(
            "dsapi_default_max_tokens",
            str(settings.dsapi_max_tokens),
            conn=conn,
        )

        bases = conn.execute("SELECT id FROM dsapi_knowledge_bases ORDER BY id").fetchall()
        legacy_row = conn.execute(
            "SELECT value FROM settings WHERE key = 'dsapi_knowledge_prompt'"
        ).fetchone()
        legacy_prompt = str(legacy_row["value"]).strip() if legacy_row else ""
        if not bases and legacy_prompt:
            now = time.time()
            cursor = conn.execute(
                """
                INSERT INTO dsapi_knowledge_bases(
                    name, prompt, model, thinking_enabled, max_tokens, history_turns,
                    temperature,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "默认知识库",
                    legacy_prompt,
                    settings.dsapi_model,
                    0,
                    settings.dsapi_max_tokens,
                    default_history_turns,
                    None,
                    now,
                    now,
                ),
            )
            bases = [{"id": int(cursor.lastrowid)}]

        active_id = self._active_knowledge_id(conn)
        available_ids = {int(row["id"]) for row in bases}
        if active_id not in available_ids:
            active_id = min(available_ids) if available_ids else None
        self._set_active_knowledge_id(active_id, conn)
        self._sync_legacy_knowledge_prompt(active_id, conn)
        self._initialize_dsapi_history_scope(conn, active_id)

    def _initialize_dsapi_history_scope(
        self,
        conn: sqlite3.Connection,
        active_knowledge_id: int | None,
    ) -> None:
        for table in ("dsapi_chat_history", "dsapi_group_context"):
            columns = {
                str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if "knowledge_id" not in columns:
                conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN knowledge_id INTEGER NOT NULL DEFAULT 0"
                )
            if active_knowledge_id is not None:
                conn.execute(
                    f"UPDATE {table} SET knowledge_id = ? WHERE knowledge_id = 0",
                    (int(active_knowledge_id),),
                )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_dsapi_chat_history_knowledge_group
            ON dsapi_chat_history(knowledge_id, group_id, id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_dsapi_group_context_knowledge_group
            ON dsapi_group_context(knowledge_id, group_id, id)
            """
        )

    def list_dsapi_knowledge_bases(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return self._list_dsapi_knowledge_bases(conn)

    def create_dsapi_knowledge_base(
        self,
        *,
        name: str,
        prompt: str,
        actor_id: int,
        model: str = "deepseek-flash",
        thinking_enabled: bool = False,
        max_tokens: int = 80,
        history_turns: int = 2,
        response_mode: str = "short",
        max_reply_messages: int = 1,
        temperature: float | None = None,
        web_search_enabled: bool = False,
        relationship_memory_enabled: bool = False,
        style_examples: Sequence[Mapping[str, Any]] = (),
        persona_group_id: int = 0,
        persona_user_id: int = 0,
        context_messages: int = 10,
        similar_examples: int = 0,
        relationships: Mapping[int | str, str] | Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        normalized_name = self._normalize_knowledge_name(name)
        normalized_prompt = self._normalize_knowledge_prompt(prompt)
        normalized_model = self._normalize_dsapi_model(model)
        normalized_max_tokens = self._normalize_dsapi_max_tokens(max_tokens)
        normalized_history_turns = self._normalize_dsapi_history_turns(history_turns)
        normalized_response_mode = self._normalize_dsapi_response_mode(response_mode)
        normalized_max_reply_messages = self._normalize_dsapi_max_reply_messages(max_reply_messages)
        normalized_temperature = self._normalize_dsapi_temperature(temperature)
        normalized_persona_group_id = self._normalize_optional_positive_id(
            persona_group_id,
            "persona_group_id",
        )
        normalized_persona_user_id = self._normalize_optional_positive_id(
            persona_user_id,
            "persona_user_id",
        )
        normalized_context_messages = self._normalize_dsapi_context_messages(context_messages)
        normalized_similar_examples = self._normalize_dsapi_similar_examples(similar_examples)
        normalized_style_examples = self._normalize_dsapi_style_examples(style_examples)
        normalized_relationships = self._normalize_dsapi_relationships(relationships)
        now = time.time()
        with self._connect() as conn:
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO dsapi_knowledge_bases(
                        name, prompt, model, thinking_enabled, max_tokens, history_turns,
                        response_mode, max_reply_messages, temperature, web_search_enabled,
                        relationship_memory_enabled,
                        persona_group_id, persona_user_id, context_messages,
                        similar_examples, relationships_json,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_name,
                        normalized_prompt,
                        normalized_model,
                        1 if thinking_enabled else 0,
                        normalized_max_tokens,
                        normalized_history_turns,
                        normalized_response_mode,
                        normalized_max_reply_messages,
                        normalized_temperature,
                        1 if web_search_enabled else 0,
                        1 if relationship_memory_enabled else 0,
                        normalized_persona_group_id,
                        normalized_persona_user_id,
                        normalized_context_messages,
                        normalized_similar_examples,
                        json.dumps(normalized_relationships, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("knowledge base name already exists") from exc
            knowledge_id = int(cursor.lastrowid)
            self._replace_dsapi_style_examples(
                conn,
                knowledge_id,
                normalized_style_examples,
            )
            if self._active_knowledge_id(conn) is None:
                self._set_active_knowledge_id(knowledge_id, conn)
                self._sync_legacy_knowledge_prompt(knowledge_id, conn)
            self.audit(
                actor_id,
                "create_dsapi_knowledge_base",
                str(knowledge_id),
                {
                    "name": normalized_name,
                    "prompt_chars": len(normalized_prompt),
                    "model": normalized_model,
                    "thinking_enabled": bool(thinking_enabled),
                    "max_tokens": normalized_max_tokens,
                    "history_turns": normalized_history_turns,
                    "response_mode": normalized_response_mode,
                    "max_reply_messages": normalized_max_reply_messages,
                    "temperature": normalized_temperature,
                    "web_search_enabled": bool(web_search_enabled),
                    "relationship_memory_enabled": bool(relationship_memory_enabled),
                    "style_examples": len(normalized_style_examples),
                    "persona_group_id": normalized_persona_group_id,
                    "persona_user_id": normalized_persona_user_id,
                    "context_messages": normalized_context_messages,
                    "similar_examples": normalized_similar_examples,
                    "relationships": len(normalized_relationships),
                },
                conn=conn,
            )
            row = conn.execute(
                "SELECT * FROM dsapi_knowledge_bases WHERE id = ?",
                (knowledge_id,),
            ).fetchone()
            result = self._public_knowledge_base(
                row,
                style_examples=normalized_style_examples,
                relationship_count=0,
            )
        return result

    def update_dsapi_knowledge_base(
        self,
        knowledge_id: int,
        *,
        name: str,
        prompt: str,
        actor_id: int,
        model: str | None = None,
        thinking_enabled: bool | None = None,
        max_tokens: int | None = None,
        history_turns: int | None = None,
        response_mode: str | None = None,
        max_reply_messages: int | None = None,
        temperature: float | None = None,
        web_search_enabled: bool | None = None,
        relationship_memory_enabled: bool | None = None,
        style_examples: Sequence[Mapping[str, Any]] | None = None,
        persona_group_id: int | None = None,
        persona_user_id: int | None = None,
        context_messages: int | None = None,
        similar_examples: int | None = None,
        relationships: Mapping[int | str, str] | Sequence[Mapping[str, Any]] | None = None,
        activate: bool = False,
        clear_history: bool = False,
    ) -> dict[str, Any]:
        normalized_id = int(knowledge_id)
        normalized_name = self._normalize_knowledge_name(name)
        normalized_prompt = self._normalize_knowledge_prompt(prompt)
        with self._connect() as conn:
            current = conn.execute(
                "SELECT * FROM dsapi_knowledge_bases WHERE id = ?",
                (normalized_id,),
            ).fetchone()
            if current is None:
                raise ValueError("knowledge base not found")
            normalized_model = self._normalize_dsapi_model(
                current["model"] if model is None else model
            )
            normalized_thinking = (
                bool(current["thinking_enabled"])
                if thinking_enabled is None
                else bool(thinking_enabled)
            )
            normalized_max_tokens = self._normalize_dsapi_max_tokens(
                current["max_tokens"] if max_tokens is None else max_tokens
            )
            normalized_history_turns = self._normalize_dsapi_history_turns(
                current["history_turns"] if history_turns is None else history_turns
            )
            normalized_response_mode = self._normalize_dsapi_response_mode(
                current["response_mode"] if response_mode is None else response_mode
            )
            normalized_max_reply_messages = self._normalize_dsapi_max_reply_messages(
                current["max_reply_messages"] if max_reply_messages is None else max_reply_messages
            )
            normalized_temperature = self._normalize_dsapi_temperature(temperature)
            normalized_web_search = (
                bool(current["web_search_enabled"])
                if web_search_enabled is None
                else bool(web_search_enabled)
            )
            normalized_relationship_memory = (
                bool(current["relationship_memory_enabled"])
                if relationship_memory_enabled is None
                else bool(relationship_memory_enabled)
            )
            normalized_style_examples = (
                self._get_dsapi_style_examples(conn, normalized_id)
                if style_examples is None
                else self._normalize_dsapi_style_examples(style_examples)
            )
            normalized_persona_group_id = self._normalize_optional_positive_id(
                current["persona_group_id"] if persona_group_id is None else persona_group_id,
                "persona_group_id",
            )
            normalized_persona_user_id = self._normalize_optional_positive_id(
                current["persona_user_id"] if persona_user_id is None else persona_user_id,
                "persona_user_id",
            )
            normalized_context_messages = self._normalize_dsapi_context_messages(
                current["context_messages"] if context_messages is None else context_messages
            )
            normalized_similar_examples = self._normalize_dsapi_similar_examples(
                current["similar_examples"] if similar_examples is None else similar_examples
            )
            normalized_relationships = (
                self._decode_dsapi_relationships(current["relationships_json"])
                if relationships is None
                else self._normalize_dsapi_relationships(relationships)
            )
            try:
                conn.execute(
                    """
                    UPDATE dsapi_knowledge_bases
                    SET name = ?, prompt = ?, model = ?, thinking_enabled = ?,
                        max_tokens = ?, history_turns = ?, response_mode = ?,
                        max_reply_messages = ?, temperature = ?, web_search_enabled = ?,
                        relationship_memory_enabled = ?, persona_group_id = ?,
                        persona_user_id = ?, context_messages = ?, similar_examples = ?,
                        relationships_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        normalized_name,
                        normalized_prompt,
                        normalized_model,
                        1 if normalized_thinking else 0,
                        normalized_max_tokens,
                        normalized_history_turns,
                        normalized_response_mode,
                        normalized_max_reply_messages,
                        normalized_temperature,
                        1 if normalized_web_search else 0,
                        1 if normalized_relationship_memory else 0,
                        normalized_persona_group_id,
                        normalized_persona_user_id,
                        normalized_context_messages,
                        normalized_similar_examples,
                        json.dumps(normalized_relationships, ensure_ascii=False),
                        time.time(),
                        normalized_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("knowledge base name already exists") from exc
            self._replace_dsapi_style_examples(
                conn,
                normalized_id,
                normalized_style_examples,
            )
            previous_id = self._active_knowledge_id(conn)
            changed = bool(activate and previous_id != normalized_id)
            cleared = 0
            if activate:
                self._set_active_knowledge_id(normalized_id, conn)
                self._sync_legacy_knowledge_prompt(normalized_id, conn)
                if clear_history and changed:
                    cleared = self._clear_dsapi_history(conn, normalized_id)
            elif previous_id == normalized_id:
                self._sync_legacy_knowledge_prompt(normalized_id, conn)
            self.audit(
                actor_id,
                "update_dsapi_knowledge_base",
                str(normalized_id),
                {
                    "name": normalized_name,
                    "prompt_chars": len(normalized_prompt),
                    "model": normalized_model,
                    "thinking_enabled": normalized_thinking,
                    "max_tokens": normalized_max_tokens,
                    "history_turns": normalized_history_turns,
                    "response_mode": normalized_response_mode,
                    "max_reply_messages": normalized_max_reply_messages,
                    "temperature": normalized_temperature,
                    "web_search_enabled": normalized_web_search,
                    "relationship_memory_enabled": normalized_relationship_memory,
                    "style_examples": len(normalized_style_examples),
                    "persona_group_id": normalized_persona_group_id,
                    "persona_user_id": normalized_persona_user_id,
                    "context_messages": normalized_context_messages,
                    "similar_examples": normalized_similar_examples,
                    "relationships": len(normalized_relationships),
                    "activated": bool(activate),
                    "previous_id": previous_id,
                    "history_messages_cleared": cleared,
                },
                conn=conn,
            )
            row = conn.execute(
                "SELECT * FROM dsapi_knowledge_bases WHERE id = ?",
                (normalized_id,),
            ).fetchone()
            relationship_count = self._dsapi_relationship_count(conn, normalized_id)
            result = self._public_knowledge_base(
                row,
                style_examples=normalized_style_examples,
                relationship_count=relationship_count,
            )
        return result

    def delete_dsapi_knowledge_base(self, knowledge_id: int, *, actor_id: int) -> dict[str, Any]:
        normalized_id = int(knowledge_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT name FROM dsapi_knowledge_bases WHERE id = ?",
                (normalized_id,),
            ).fetchone()
            if row is None:
                raise ValueError("knowledge base not found")
            was_active = self._active_knowledge_id(conn) == normalized_id
            conn.execute(
                "DELETE FROM dsapi_style_examples WHERE knowledge_id = ?",
                (normalized_id,),
            )
            conn.execute(
                "DELETE FROM dsapi_relationship_memory WHERE knowledge_id = ?",
                (normalized_id,),
            )
            conn.execute("DELETE FROM dsapi_knowledge_bases WHERE id = ?", (normalized_id,))
            cleared = self._clear_dsapi_history(conn, normalized_id)
            if was_active:
                next_row = conn.execute(
                    "SELECT id FROM dsapi_knowledge_bases ORDER BY id LIMIT 1"
                ).fetchone()
                next_id = int(next_row["id"]) if next_row else None
                self._set_active_knowledge_id(next_id, conn)
                self._sync_legacy_knowledge_prompt(next_id, conn)
            self.audit(
                actor_id,
                "delete_dsapi_knowledge_base",
                str(normalized_id),
                {"name": str(row["name"]), "was_active": was_active, "cleared": cleared},
                conn=conn,
            )
        return {"deleted": True, "history_messages_cleared": cleared}

    def activate_dsapi_knowledge_base(
        self,
        knowledge_id: int,
        *,
        clear_history: bool,
        actor_id: int,
    ) -> dict[str, Any]:
        normalized_id = int(knowledge_id)
        with self._connect() as conn:
            if not self._knowledge_base_exists(normalized_id, conn):
                raise ValueError("knowledge base not found")
            previous_id = self._active_knowledge_id(conn)
            self._set_active_knowledge_id(normalized_id, conn)
            self._sync_legacy_knowledge_prompt(normalized_id, conn)
            changed = previous_id != normalized_id
            cleared = (
                self._clear_dsapi_history(conn, normalized_id) if clear_history and changed else 0
            )
            self.audit(
                actor_id,
                "activate_dsapi_knowledge_base",
                str(normalized_id),
                {
                    "previous_id": previous_id,
                    "changed": changed,
                    "clear_history": bool(clear_history),
                    "history_messages_cleared": cleared,
                },
                conn=conn,
            )
        return {"active_knowledge_id": normalized_id, "history_messages_cleared": cleared}

    def set_active_dsapi_model(self, model: str, *, actor_id: int) -> dict[str, Any]:
        normalized_model = self._normalize_dsapi_model(model)
        if self.ai_provider_managed():
            self.set_ai_provider_model(normalized_model, actor_id=actor_id)
            return {"model": normalized_model}
        with self._connect() as conn:
            active_id = self._active_knowledge_id(conn)
            if active_id is None:
                default_tokens_row = conn.execute(
                    "SELECT value FROM settings WHERE key = 'dsapi_default_max_tokens'"
                ).fetchone()
                default_max_tokens = int(default_tokens_row["value"]) if default_tokens_row else 80
                default_history_row = conn.execute(
                    "SELECT value FROM settings WHERE key = 'dsapi_history_turns'"
                ).fetchone()
                try:
                    default_history_turns = self._normalize_dsapi_history_turns(
                        default_history_row["value"] if default_history_row else 2
                    )
                except ValueError:
                    default_history_turns = 2
                now = time.time()
                cursor = conn.execute(
                    """
                    INSERT INTO dsapi_knowledge_bases(
                        name, prompt, model, thinking_enabled, max_tokens, history_turns,
                        temperature,
                        created_at, updated_at
                    )
                    VALUES ('默认知识库', '', ?, 0, ?, ?, NULL, ?, ?)
                    """,
                    (
                        normalized_model,
                        default_max_tokens,
                        default_history_turns,
                        now,
                        now,
                    ),
                )
                active_id = int(cursor.lastrowid)
                self._set_active_knowledge_id(active_id, conn)
                self._sync_legacy_knowledge_prompt(active_id, conn)
            conn.execute(
                """
                UPDATE dsapi_knowledge_bases
                SET model = ?, updated_at = ?
                WHERE id = ?
                """,
                (normalized_model, time.time(), active_id),
            )
            self.audit(
                actor_id,
                "set_active_dsapi_model",
                str(active_id),
                {"model": normalized_model},
                conn=conn,
            )
            row = conn.execute(
                "SELECT * FROM dsapi_knowledge_bases WHERE id = ?",
                (active_id,),
            ).fetchone()
        return self._public_knowledge_base(row)

    def get_dsapi_style_examples(self, knowledge_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return self._get_dsapi_style_examples(conn, int(knowledge_id))

    def replace_dsapi_style_examples(
        self,
        knowledge_id: int,
        examples: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        normalized = self._normalize_dsapi_style_examples(examples)
        with self._connect() as conn:
            if not self._knowledge_base_exists(int(knowledge_id), conn):
                raise ValueError("knowledge base not found")
            self._replace_dsapi_style_examples(conn, int(knowledge_id), normalized)
        return normalized

    def get_dsapi_relationship_memory(
        self,
        knowledge_id: int,
        user_id: int,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT summary, interaction_count, last_group_id, updated_at
                FROM dsapi_relationship_memory
                WHERE knowledge_id = ? AND user_id = ?
                """,
                (int(knowledge_id), int(user_id)),
            ).fetchone()
        if row is None:
            return None
        return {
            "knowledge_id": int(knowledge_id),
            "user_id": int(user_id),
            "summary": str(row["summary"]),
            "interaction_count": int(row["interaction_count"]),
            "last_group_id": int(row["last_group_id"]),
            "updated_at": float(row["updated_at"]),
        }

    def upsert_dsapi_relationship_memory(
        self,
        *,
        knowledge_id: int,
        user_id: int,
        group_id: int,
        summary: str,
    ) -> dict[str, Any]:
        normalized_summary = " ".join(str(summary).split())
        if not normalized_summary:
            raise ValueError("relationship summary is required")
        if len(normalized_summary) > 500:
            raise ValueError("relationship summary must not exceed 500 characters")
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO dsapi_relationship_memory(
                    knowledge_id, user_id, summary, interaction_count,
                    last_group_id, updated_at
                )
                VALUES (?, ?, ?, 1, ?, ?)
                ON CONFLICT(knowledge_id, user_id) DO UPDATE SET
                    summary = excluded.summary,
                    interaction_count = dsapi_relationship_memory.interaction_count + 1,
                    last_group_id = excluded.last_group_id,
                    updated_at = excluded.updated_at
                """,
                (
                    int(knowledge_id),
                    int(user_id),
                    normalized_summary,
                    int(group_id),
                    now,
                ),
            )
        return self.get_dsapi_relationship_memory(knowledge_id, user_id) or {}

    def clear_dsapi_relationship_memory(
        self,
        knowledge_id: int,
        *,
        actor_id: int,
    ) -> int:
        with self._connect() as conn:
            deleted = conn.execute(
                "DELETE FROM dsapi_relationship_memory WHERE knowledge_id = ?",
                (int(knowledge_id),),
            ).rowcount
            self.audit(
                actor_id,
                "clear_dsapi_relationship_memory",
                str(int(knowledge_id)),
                {"deleted": int(deleted)},
                conn=conn,
            )
        return int(deleted)

    def _active_knowledge_id(self, conn: sqlite3.Connection) -> int | None:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'dsapi_active_knowledge_id'"
        ).fetchone()
        if row is None or not str(row["value"]).strip():
            return None
        try:
            value = int(row["value"])
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def _set_active_knowledge_id(
        self,
        knowledge_id: int | None,
        conn: sqlite3.Connection,
    ) -> None:
        self.set_setting(
            "dsapi_active_knowledge_id",
            str(int(knowledge_id)) if knowledge_id is not None else "",
            conn=conn,
        )

    def _sync_legacy_knowledge_prompt(
        self,
        knowledge_id: int | None,
        conn: sqlite3.Connection,
    ) -> None:
        row = None
        if knowledge_id is not None:
            row = conn.execute(
                "SELECT prompt FROM dsapi_knowledge_bases WHERE id = ?",
                (int(knowledge_id),),
            ).fetchone()
        self.set_setting(
            "dsapi_knowledge_prompt",
            str(row["prompt"]) if row else "",
            conn=conn,
        )

    def _knowledge_base_exists(self, knowledge_id: int, conn: sqlite3.Connection) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM dsapi_knowledge_bases WHERE id = ?",
                (int(knowledge_id),),
            ).fetchone()
            is not None
        )

    def _list_dsapi_knowledge_bases(
        self,
        conn: sqlite3.Connection,
    ) -> list[dict[str, Any]]:
        active_id = self._active_knowledge_id(conn)
        rows = conn.execute(
            """
            SELECT id, name, prompt, model, thinking_enabled, max_tokens, history_turns,
                   response_mode, max_reply_messages, temperature, web_search_enabled,
                   relationship_memory_enabled,
                   persona_group_id, persona_user_id, context_messages,
                   similar_examples, relationships_json, created_at, updated_at
            FROM dsapi_knowledge_bases
            ORDER BY updated_at DESC, id DESC
            """
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            knowledge_id = int(row["id"])
            result.append(
                {
                    **self._public_knowledge_base(
                        row,
                        style_examples=self._get_dsapi_style_examples(conn, knowledge_id),
                        relationship_count=self._dsapi_relationship_count(
                            conn,
                            knowledge_id,
                        ),
                    ),
                    "active": knowledge_id == active_id,
                }
            )
        return result

    def _public_knowledge_base(
        self,
        row: sqlite3.Row | Mapping[str, Any],
        *,
        style_examples: Sequence[Mapping[str, Any]] = (),
        relationship_count: int = 0,
    ) -> dict[str, Any]:
        prompt = str(row["prompt"])
        return {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "prompt": prompt,
            "prompt_chars": len(prompt),
            "model": str(row["model"]),
            "thinking_enabled": bool(row["thinking_enabled"]),
            "max_tokens": int(row["max_tokens"]),
            "history_turns": int(row["history_turns"]),
            "response_mode": str(row["response_mode"]),
            "max_reply_messages": int(row["max_reply_messages"]),
            "temperature": (float(row["temperature"]) if row["temperature"] is not None else None),
            "web_search_enabled": bool(row["web_search_enabled"]),
            "relationship_memory_enabled": bool(row["relationship_memory_enabled"]),
            "style_examples": [
                {
                    "topic": str(item.get("topic") or ""),
                    "response": str(item.get("response") or ""),
                }
                for item in style_examples
            ],
            "relationship_count": int(relationship_count),
            "context_messages": int(row["context_messages"]),
            "similar_examples": int(row["similar_examples"]),
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
        }

    def _get_dsapi_style_examples(
        self,
        conn: sqlite3.Connection,
        knowledge_id: int,
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT topic, response
            FROM dsapi_style_examples
            WHERE knowledge_id = ?
            ORDER BY id
            """,
            (int(knowledge_id),),
        ).fetchall()
        return [{"topic": str(row["topic"]), "response": str(row["response"])} for row in rows]

    def _replace_dsapi_style_examples(
        self,
        conn: sqlite3.Connection,
        knowledge_id: int,
        examples: Sequence[Mapping[str, Any]],
    ) -> None:
        conn.execute(
            "DELETE FROM dsapi_style_examples WHERE knowledge_id = ?",
            (int(knowledge_id),),
        )
        now = time.time()
        conn.executemany(
            """
            INSERT INTO dsapi_style_examples(knowledge_id, topic, response, created_at)
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    int(knowledge_id),
                    str(item["topic"]),
                    str(item["response"]),
                    now,
                )
                for item in examples
            ],
        )

    def _dsapi_relationship_count(
        self,
        conn: sqlite3.Connection,
        knowledge_id: int,
    ) -> int:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM dsapi_relationship_memory
            WHERE knowledge_id = ?
            """,
            (int(knowledge_id),),
        ).fetchone()
        return int(row["count"] if row else 0)

    def _normalize_knowledge_name(self, name: str) -> str:
        normalized = " ".join(str(name).split())
        if not normalized:
            raise ValueError("knowledge base name is required")
        if len(normalized) > 80:
            raise ValueError("knowledge base name must not exceed 80 characters")
        return normalized

    def _normalize_knowledge_prompt(self, prompt: str) -> str:
        normalized = str(prompt).strip()
        if len(normalized) > 100_000:
            raise ValueError("knowledge prompt must not exceed 100000 characters")
        return normalized

    def _normalize_dsapi_model(self, model: str) -> str:
        normalized = str(model).strip()
        if not normalized:
            raise ValueError("DSAPI model is required")
        if len(normalized) > 200:
            raise ValueError("DSAPI model must not exceed 200 characters")
        return normalized

    def _normalize_dsapi_max_tokens(self, max_tokens: int) -> int:
        normalized = int(max_tokens)
        if normalized < 1 or normalized > 32_768:
            raise ValueError("DSAPI max_tokens must be between 1 and 32768")
        return normalized

    def _normalize_dsapi_history_turns(self, history_turns: int) -> int:
        normalized = int(history_turns)
        if normalized < 1 or normalized > _MAX_DSAPI_HISTORY_TURNS:
            raise ValueError(f"history_turns must be between 1 and {_MAX_DSAPI_HISTORY_TURNS}")
        return normalized

    def _normalize_dsapi_context_messages(self, context_messages: int) -> int:
        normalized = int(context_messages)
        if normalized < 1 or normalized > _MAX_DSAPI_CONTEXT_MESSAGES:
            raise ValueError(
                f"context_messages must be between 1 and {_MAX_DSAPI_CONTEXT_MESSAGES}"
            )
        return normalized

    def _normalize_dsapi_similar_examples(self, similar_examples: int) -> int:
        normalized = int(similar_examples)
        if normalized < 0 or normalized > _MAX_DSAPI_SIMILAR_EXAMPLES:
            raise ValueError(
                f"similar_examples must be between 0 and {_MAX_DSAPI_SIMILAR_EXAMPLES}"
            )
        return normalized

    def _normalize_dsapi_style_examples(
        self,
        examples: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, str]]:
        if isinstance(examples, (str, bytes)) or not isinstance(examples, Sequence):
            raise TypeError("style_examples must be a list")
        normalized: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for item in examples:
            if not isinstance(item, Mapping):
                continue
            topic = " ".join(str(item.get("topic") or "").split())
            response = " ".join(str(item.get("response") or "").split())
            if not topic or not response:
                continue
            if len(topic) > 500:
                raise ValueError("style example topic must not exceed 500 characters")
            if len(response) > 500:
                raise ValueError("style example response must not exceed 500 characters")
            key = (topic, response)
            if key in seen:
                continue
            seen.add(key)
            normalized.append({"topic": topic, "response": response})
        if len(normalized) > 500:
            raise ValueError("style_examples must not contain more than 500 items")
        return normalized

    def _normalize_optional_positive_id(self, value: int, field_name: str) -> int:
        normalized = int(value)
        if normalized < 0:
            raise ValueError(f"{field_name} must be zero or a positive integer")
        return normalized

    def _normalize_dsapi_relationships(
        self,
        relationships: Mapping[int | str, str] | Sequence[Mapping[str, Any]],
    ) -> dict[str, str]:
        if isinstance(relationships, Mapping):
            items = relationships.items()
        elif isinstance(relationships, Sequence) and not isinstance(
            relationships,
            (str, bytes),
        ):
            items = (
                (item.get("user_id"), item.get("note"))
                for item in relationships
                if isinstance(item, Mapping)
            )
        else:
            raise TypeError("relationships must be a mapping or list")

        normalized: dict[str, str] = {}
        for raw_user_id, raw_note in items:
            user_id = self._normalize_optional_positive_id(raw_user_id, "relationship user_id")
            note = " ".join(str(raw_note or "").split())
            if user_id <= 0 or not note:
                continue
            if len(note) > 500:
                raise ValueError("relationship note must not exceed 500 characters")
            normalized[str(user_id)] = note
        if len(normalized) > 500:
            raise ValueError("relationships must not contain more than 500 users")
        return normalized

    def _decode_dsapi_relationships(self, value: Any) -> dict[str, str]:
        try:
            decoded = json.loads(str(value or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        if not isinstance(decoded, Mapping):
            return {}
        try:
            return self._normalize_dsapi_relationships(decoded)
        except (TypeError, ValueError):
            return {}

    def _normalize_dsapi_response_mode(self, response_mode: str) -> str:
        normalized = str(response_mode).strip().lower()
        if normalized not in {"short", "normal", "detailed"}:
            raise ValueError("response_mode must be short, normal, or detailed")
        return normalized

    def _normalize_dsapi_max_reply_messages(self, max_reply_messages: int) -> int:
        normalized = int(max_reply_messages)
        if normalized < 1 or normalized > _MAX_DSAPI_REPLY_MESSAGES:
            raise ValueError(
                f"max_reply_messages must be between 1 and {_MAX_DSAPI_REPLY_MESSAGES}"
            )
        return normalized

    def _normalize_dsapi_temperature(self, temperature: float | None) -> float | None:
        if temperature is None:
            return None
        normalized = float(temperature)
        if normalized < 0 or normalized > 2:
            raise ValueError("DSAPI temperature must be between 0 and 2")
        return normalized
