from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from qq_personal_bot.menu_recipes import (
    decode_json_list,
    is_supported_image_file,
    load_howtocook_records,
    load_seed_records,
)
from qq_personal_bot.settings import AppSettings


class PolicyStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def initialize(self, settings: AppSettings) -> None:
        first_run = not self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            self._create_schema(conn)
            has_settings = conn.execute("SELECT 1 FROM settings LIMIT 1").fetchone() is not None
            if first_run or not has_settings:
                self._seed_defaults(conn, settings)
            for admin_id in settings.admins:
                self.add_admin(admin_id, actor_id=0, conn=conn)
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
            """
        )

    def _seed_defaults(self, conn: sqlite3.Connection, settings: AppSettings) -> None:
        defaults = {
            "policy_mode": settings.policy_mode,
            "trigger_mention": "true" if settings.trigger_mention else "false",
            "per_group_seconds": str(settings.per_group_seconds),
            "per_user_per_minute": str(settings.per_user_per_minute),
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

    def get_trigger_mention(self) -> bool:
        return self.get_setting("trigger_mention", "true").lower() in {"1", "true", "yes", "on"}

    def set_trigger_mention(self, enabled: bool, actor_id: int) -> None:
        value = "true" if enabled else "false"
        with self._connect() as conn:
            self.set_setting("trigger_mention", value, conn=conn)
            self.audit(actor_id, "set_trigger_mention", "policy", {"enabled": enabled}, conn=conn)

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
        normalized = []
        seen = set()
        for prefix in prefixes:
            value = prefix.strip()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        if not normalized:
            normalized = ["~"]

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

    def menu_recipe_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM menu_recipes").fetchone()
            return int(row["count"]) if row else 0

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
            region_matched = [recipe for recipe in recipes if self._menu_recipe_region_matches(recipe, normalized_target)]
            if region_matched:
                candidates = region_matched
            else:
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
            recipe["region"],
            recipe["category"],
            *recipe["aliases"],
            *recipe["tags"],
        ]
        for value in fields:
            normalized_value = str(value).strip().casefold()
            if normalized_value == target or target in normalized_value:
                return True
        return False

    def _menu_recipe_region_matches(self, recipe: dict[str, Any], target: str) -> bool:
        normalized_region = str(recipe["region"]).strip().casefold()
        if not normalized_region:
            return False
        return normalized_region == target or target in normalized_region

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
