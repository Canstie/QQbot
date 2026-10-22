from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from qq_personal_bot.core.ai_providers import AIProviderStore
from qq_personal_bot.core.classic_store import ClassicStore
from qq_personal_bot.core.conversation_store import ConversationStore
from qq_personal_bot.core.download_store import DownloadStore
from qq_personal_bot.core.group_message_store import GroupMessageStore
from qq_personal_bot.core.knowledge_store import KnowledgeStore
from qq_personal_bot.core.memory_graph import MemoryGraphStore
from qq_personal_bot.core.menu_store import MenuStore
from qq_personal_bot.core.steam_store import SteamStore
from qq_personal_bot.core.store_constants import _RUNTIME_CACHE_TTL_SECONDS, CHINA_TZ
from qq_personal_bot.core.turtle_soup import TurtleSoupStore
from qq_personal_bot.features import feature_catalog, feature_defaults
from qq_personal_bot.settings import AppSettings


class PolicyStore(
    KnowledgeStore,
    ConversationStore,
    SteamStore,
    GroupMessageStore,
    MenuStore,
    ClassicStore,
    DownloadStore,
    MemoryGraphStore,
    AIProviderStore,
    TurtleSoupStore,
):
    """Business persistence facade owning shared connections, transactions and caches."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._runtime_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}
        self._runtime_cache_lock = threading.RLock()
        self._last_group_message_cleanup_date: str | None = None
        self._group_message_cleanup_lock = threading.Lock()

    def initialize(self, settings: AppSettings) -> None:
        first_run = not self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            self._create_schema(conn)
            self._create_memory_schema(conn)
            self._create_turtle_soup_schema(conn)
            has_settings = conn.execute("SELECT 1 FROM settings LIMIT 1").fetchone() is not None
            should_seed = first_run or not has_settings
            if should_seed:
                self._seed_defaults(conn, settings)
                for admin_id in settings.admins:
                    self.add_admin(admin_id, actor_id=0, conn=conn)
            self._initialize_dsapi_settings(conn)
            self._initialize_knowledge_bases(conn, settings)
            self._initialize_ai_providers(conn, settings)
            conn.execute(
                "UPDATE dsapi_knowledge_bases SET model = 'deepseek-flash' "
                "WHERE model IN ('deepseek-v4-flash', 'deepseek-v4-flash-vision-exp')"
            )
            self._initialize_feature_flags(conn, settings)
            self._retire_quote_library(conn)
            self._initialize_steam_settings(conn)
            self.purge_legacy_menu_caches(conn=conn)
        self._invalidate_runtime_cache()

    def _time_label(self, timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp, CHINA_TZ).strftime("%H:%M")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA synchronous = NORMAL")
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()
        finally:
            conn.close()

    def _cached_runtime_value(self, key: tuple[Any, ...], loader: Any) -> Any:
        now = time.monotonic()
        with self._runtime_cache_lock:
            cached = self._runtime_cache.get(key)
            if cached is not None and cached[0] > now:
                return cached[1]
        value = loader()
        with self._runtime_cache_lock:
            self._runtime_cache[key] = (now + _RUNTIME_CACHE_TTL_SECONDS, value)
        return value

    def _invalidate_runtime_cache(self) -> None:
        with self._runtime_cache_lock:
            self._runtime_cache.clear()

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

            CREATE TABLE IF NOT EXISTS dsapi_blocked_users (
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

            CREATE TABLE IF NOT EXISTS group_daily_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                message_id TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_group_daily_messages_group_date_time
            ON group_daily_messages(group_id, date, created_at, id);

            CREATE INDEX IF NOT EXISTS idx_group_daily_messages_date
            ON group_daily_messages(date);

            CREATE UNIQUE INDEX IF NOT EXISTS idx_group_daily_messages_message_id
            ON group_daily_messages(group_id, message_id)
            WHERE message_id <> '';

            CREATE TABLE IF NOT EXISTS dsapi_chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                knowledge_id INTEGER NOT NULL DEFAULT 0,
                group_id INTEGER NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_dsapi_chat_history_group_id
            ON dsapi_chat_history(group_id, id);

            CREATE TABLE IF NOT EXISTS dsapi_group_context (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                knowledge_id INTEGER NOT NULL DEFAULT 0,
                group_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_dsapi_group_context_group_id
            ON dsapi_group_context(group_id, id);

            CREATE TABLE IF NOT EXISTS dsapi_knowledge_bases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                prompt TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                thinking_enabled INTEGER NOT NULL DEFAULT 0,
                max_tokens INTEGER NOT NULL DEFAULT 80,
                history_turns INTEGER NOT NULL DEFAULT 2,
                response_mode TEXT NOT NULL DEFAULT 'short',
                max_reply_messages INTEGER NOT NULL DEFAULT 1,
                temperature REAL,
                web_search_enabled INTEGER NOT NULL DEFAULT 0,
                relationship_memory_enabled INTEGER NOT NULL DEFAULT 0,
                persona_group_id INTEGER NOT NULL DEFAULT 0,
                persona_user_id INTEGER NOT NULL DEFAULT 0,
                context_messages INTEGER NOT NULL DEFAULT 10,
                similar_examples INTEGER NOT NULL DEFAULT 0,
                relationships_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dsapi_style_examples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                knowledge_id INTEGER NOT NULL,
                topic TEXT NOT NULL,
                response TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_dsapi_style_examples_knowledge
            ON dsapi_style_examples(knowledge_id, id);

            CREATE TABLE IF NOT EXISTS dsapi_relationship_memory (
                knowledge_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                summary TEXT NOT NULL,
                interaction_count INTEGER NOT NULL DEFAULT 0,
                last_group_id INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                PRIMARY KEY(knowledge_id, user_id)
            );

            CREATE INDEX IF NOT EXISTS idx_dsapi_relationship_memory_knowledge
            ON dsapi_relationship_memory(knowledge_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS download_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sha256 TEXT NOT NULL UNIQUE,
                object_key TEXT NOT NULL UNIQUE,
                content_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                downloaded_date TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_download_images_date_created
            ON download_images(downloaded_date, created_at DESC, id DESC);

            CREATE TABLE IF NOT EXISTS classic_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                object_key TEXT NOT NULL,
                content_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                created_at REAL NOT NULL,
                blast_count INTEGER NOT NULL DEFAULT 0,
                last_blast_at REAL NOT NULL DEFAULT 0,
                fair_order INTEGER NOT NULL DEFAULT 0,
                UNIQUE(group_id, sha256),
                UNIQUE(group_id, object_key)
            );

            CREATE INDEX IF NOT EXISTS idx_classic_images_group_created
            ON classic_images(group_id, created_at DESC, id DESC);

            CREATE TABLE IF NOT EXISTS classic_group_bindings (
                group_id INTEGER PRIMARY KEY,
                master_group_id INTEGER NOT NULL,
                created_at REAL NOT NULL,
                CHECK (group_id != master_group_id)
            );

            CREATE INDEX IF NOT EXISTS idx_classic_group_bindings_master
            ON classic_group_bindings(master_group_id);

            CREATE TABLE IF NOT EXISTS feature_flags (
                feature_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS steam_group_settings (
                group_id INTEGER PRIMARY KEY,
                monitor_enabled INTEGER NOT NULL DEFAULT 0,
                achievement_enabled INTEGER NOT NULL DEFAULT 1,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS steam_subscriptions (
                group_id INTEGER NOT NULL,
                steam_id TEXT NOT NULL,
                alias TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                PRIMARY KEY(group_id, steam_id)
            );

            CREATE INDEX IF NOT EXISTS idx_steam_subscriptions_steam_id
            ON steam_subscriptions(steam_id);

            CREATE TABLE IF NOT EXISTS steam_bindings (
                group_id INTEGER NOT NULL,
                qq_user_id INTEGER NOT NULL,
                steam_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(group_id, qq_user_id),
                FOREIGN KEY(group_id, steam_id)
                    REFERENCES steam_subscriptions(group_id, steam_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS steam_players (
                steam_id TEXT PRIMARY KEY,
                persona_name TEXT NOT NULL DEFAULT '',
                profile_url TEXT NOT NULL DEFAULT '',
                avatar_url TEXT NOT NULL DEFAULT '',
                persona_state INTEGER NOT NULL DEFAULT 0,
                game_id TEXT NOT NULL DEFAULT '',
                game_name TEXT NOT NULL DEFAULT '',
                last_logoff REAL NOT NULL DEFAULT 0,
                last_checked_at REAL NOT NULL DEFAULT 0,
                next_poll_at REAL NOT NULL DEFAULT 0,
                raw_json TEXT NOT NULL DEFAULT '{}',
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS steam_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_key TEXT NOT NULL UNIQUE,
                steam_id TEXT NOT NULL,
                game_id TEXT NOT NULL,
                game_name TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('playing', 'confirming_exit', 'closed')),
                started_at REAL NOT NULL,
                exit_deadline REAL,
                ended_at REAL,
                close_reason TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_steam_sessions_player_state
            ON steam_sessions(steam_id, state, started_at DESC);

            CREATE TABLE IF NOT EXISTS steam_achievement_snapshots (
                steam_id TEXT NOT NULL,
                game_id TEXT NOT NULL,
                achievement_key TEXT NOT NULL,
                unlocked_at REAL NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                PRIMARY KEY(steam_id, game_id, achievement_key)
            );

            CREATE TABLE IF NOT EXISTS steam_notifications (
                event_key TEXT NOT NULL,
                group_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                sent_at REAL NOT NULL,
                PRIMARY KEY(event_key, group_id, event_type)
            );
            """
        )
        self._initialize_classic_image_selection(conn)
        self._initialize_download_image_selection(conn)

    def _seed_defaults(self, conn: sqlite3.Connection, settings: AppSettings) -> None:
        defaults = {
            "policy_mode": settings.policy_mode,
            "trigger_mention": "true" if settings.trigger_mention else "false",
            "direct_trigger_percent": str(settings.direct_trigger_percent),
            "per_group_seconds": str(settings.per_group_seconds),
            "per_user_per_minute": str(settings.per_user_per_minute),
            "dsapi_enabled": "true",
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
            "dsapi_enabled": "true",
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

    def _initialize_feature_flags(
        self,
        conn: sqlite3.Connection,
        settings: AppSettings,
    ) -> None:
        defaults = feature_defaults(settings.lua_dir)
        dsapi_row = conn.execute(
            "SELECT value FROM settings WHERE key = 'dsapi_enabled'"
        ).fetchone()
        defaults["ai.master"] = (
            str(dsapi_row["value"]).lower() in {"1", "true", "yes", "on"}
            if dsapi_row is not None
            else bool(settings.dsapi_enabled)
        )
        defaults["lua.master"] = bool(settings.lua_enabled)
        defaults["steam.master"] = False
        now = time.time()
        for feature_id, enabled in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO feature_flags(feature_id, enabled, updated_at) "
                "VALUES (?, ?, ?)",
                (feature_id, int(enabled), now),
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
        self._invalidate_runtime_cache()

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
        bilibili_blocked_groups: list[int] | None = None,
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
        normalized_bilibili_blocked_groups = (
            self._normalize_int_ids(
                bilibili_blocked_groups,
                "bilibili_blocked_groups",
            )
            if bilibili_blocked_groups is not None
            else None
        )

        with self._connect() as conn:
            self.set_setting("policy_mode", mode, conn=conn)
            self.set_setting("trigger_mention", "true" if trigger_mention else "false", conn=conn)
            self.set_setting(
                "direct_trigger_percent", str(float(direct_trigger_percent)), conn=conn
            )
            self.set_setting("per_group_seconds", str(float(per_group_seconds)), conn=conn)
            self.set_setting("per_user_per_minute", str(int(per_user_per_minute)), conn=conn)
            if normalized_bilibili_blocked_groups is not None:
                self.set_setting(
                    "bilibili_blocked_groups",
                    json.dumps(normalized_bilibili_blocked_groups),
                    conn=conn,
                )

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
                    "bilibili_blocked_groups": normalized_bilibili_blocked_groups,
                },
                conn=conn,
            )
        self._invalidate_runtime_cache()

    def get_setting(self, key: str, default: str) -> str:
        def load() -> str:
            with self._connect() as conn:
                row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
                return str(row["value"]) if row else default

        return str(self._cached_runtime_value(("setting", key, default), load))

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
        self._invalidate_runtime_cache()

    def is_feature_enabled(self, feature_id: str, default: bool = True) -> bool:
        normalized = str(feature_id)

        def load() -> bool:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT enabled FROM feature_flags WHERE feature_id = ?",
                    (normalized,),
                ).fetchone()
            return bool(row["enabled"]) if row is not None else bool(default)

        return bool(self._cached_runtime_value(("feature", normalized, bool(default)), load))

    def list_feature_flags(self, settings: AppSettings) -> list[dict[str, Any]]:
        definitions = feature_catalog(settings.lua_dir)
        with self._connect() as conn:
            rows = conn.execute("SELECT feature_id, enabled FROM feature_flags").fetchall()
        values = {str(row["feature_id"]): bool(row["enabled"]) for row in rows}
        return [
            {
                **definition.as_dict(),
                "enabled": values.get(definition.id, definition.default_enabled),
                "available": not (definition.id == "steam.master" and not settings.steam_api_key),
            }
            for definition in definitions
        ]

    def set_feature_enabled(
        self,
        feature_id: str,
        enabled: bool,
        *,
        actor_id: int = 0,
    ) -> None:
        normalized = str(feature_id).strip()
        if not normalized or len(normalized) > 160:
            raise ValueError("invalid feature id")
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO feature_flags(feature_id, enabled, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(feature_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (normalized, int(bool(enabled)), now),
            )
            if normalized == "ai.master":
                self.set_setting("dsapi_enabled", "true" if enabled else "false", conn=conn)
            self.audit(
                actor_id,
                "set_feature",
                normalized,
                {"enabled": bool(enabled)},
                conn=conn,
            )
        self._invalidate_runtime_cache()

    def get_bilibili_blocked_groups(self) -> list[int]:
        raw_value = self.get_setting("bilibili_blocked_groups", "[]")
        try:
            values = json.loads(raw_value)
            if not isinstance(values, list):
                return []
            return sorted(self._normalize_int_ids(values, "bilibili_blocked_groups"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return []

    def is_bilibili_group_blocked(self, group_id: int) -> bool:
        return int(group_id) in self.get_bilibili_blocked_groups()

    def get_trigger_mention(self) -> bool:
        return self.get_setting("trigger_mention", "true").lower() in {"1", "true", "yes", "on"}

    def set_trigger_mention(self, enabled: bool, actor_id: int) -> None:
        value = "true" if enabled else "false"
        with self._connect() as conn:
            self.set_setting("trigger_mention", value, conn=conn)
            self.audit(actor_id, "set_trigger_mention", "policy", {"enabled": enabled}, conn=conn)
        self._invalidate_runtime_cache()

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
        self._invalidate_runtime_cache()

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
        self._invalidate_runtime_cache()

    def is_group_enabled(self, group_id: int) -> bool:
        normalized = int(group_id)

        def load() -> bool:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT enabled FROM groups WHERE group_id = ?",
                    (normalized,),
                ).fetchone()
                return bool(row and row["enabled"])

        return bool(self._cached_runtime_value(("group_enabled", normalized), load))

    def is_group_blocked(self, group_id: int) -> bool:
        normalized = int(group_id)

        def load() -> bool:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT blocked FROM groups WHERE group_id = ?",
                    (normalized,),
                ).fetchone()
                return bool(row and row["blocked"])

        return bool(self._cached_runtime_value(("group_blocked", normalized), load))

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
        self._invalidate_runtime_cache()

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
        self._invalidate_runtime_cache()

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

        def load() -> tuple[int, ...]:
            with self._connect() as conn:
                rows = conn.execute(
                    f"SELECT group_id FROM groups WHERE {column} = 1 ORDER BY group_id"
                ).fetchall()
                return tuple(int(row["group_id"]) for row in rows)

        return list(self._cached_runtime_value(("group_ids", column), load))

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
        self._invalidate_runtime_cache()

    def remove_admin(self, user_id: int, actor_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
            self.audit(actor_id, "remove_admin", str(user_id), {}, conn=conn)
        self._invalidate_runtime_cache()

    def is_admin(self, user_id: int) -> bool:
        normalized = int(user_id)

        def load() -> bool:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM admins WHERE user_id = ?", (normalized,)
                ).fetchone()
                return row is not None

        return bool(self._cached_runtime_value(("admin", normalized), load))

    def admins(self) -> list[int]:
        def load() -> tuple[int, ...]:
            with self._connect() as conn:
                rows = conn.execute("SELECT user_id FROM admins ORDER BY user_id").fetchall()
                return tuple(int(row["user_id"]) for row in rows)

        return list(self._cached_runtime_value(("admins",), load))

    def prefixes(self) -> list[str]:
        def load() -> tuple[str, ...]:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT prefix FROM trigger_prefixes ORDER BY prefix"
                ).fetchall()
                prefixes = tuple(str(row["prefix"]) for row in rows)
                return prefixes or ("~",)

        return list(self._cached_runtime_value(("prefixes",), load))

    def set_prefixes(self, prefixes: list[str], actor_id: int) -> None:
        normalized = self._normalize_prefixes(prefixes)

        with self._connect() as conn:
            conn.execute("DELETE FROM trigger_prefixes")
            for prefix in normalized:
                conn.execute("INSERT INTO trigger_prefixes(prefix) VALUES (?)", (prefix,))
            self.audit(actor_id, "set_prefixes", "policy", {"prefixes": normalized}, conn=conn)
        self._invalidate_runtime_cache()

    def add_prefix(self, prefix: str, actor_id: int) -> None:
        prefix = prefix.strip()
        if not prefix:
            raise ValueError("prefix cannot be empty")
        with self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO trigger_prefixes(prefix) VALUES (?)", (prefix,))
            self.audit(actor_id, "add_prefix", prefix, {}, conn=conn)
        self._invalidate_runtime_cache()

    def remove_prefix(self, prefix: str, actor_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM trigger_prefixes WHERE prefix = ?", (prefix,))
            self.audit(actor_id, "remove_prefix", prefix, {}, conn=conn)
        self._invalidate_runtime_cache()

    def snapshot(self) -> dict[str, Any]:
        return {
            "mode": self.get_mode(),
            "enabled_groups": self.enabled_groups(),
            "blocked_groups": self.blocked_groups(),
            "admins": self.admins(),
            "bilibili_blocked_groups": self.get_bilibili_blocked_groups(),
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
