from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path
from typing import Any

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


class MenuStore:
    """Menu persistence using the owning store connection."""

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
            recipes = [
                recipe for recipe in recipes if self._menu_recipe_matches(recipe, normalized_search)
            ]
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

    def pick_menu_recipe(
        self, target: str, seed: int, seed_path: Path, image_dir: Path
    ) -> dict[str, Any] | None:
        self.ensure_menu_recipes(seed_path, image_dir)
        recipes = self._enabled_menu_recipes()
        if not recipes:
            return None

        normalized_target = target.strip().casefold()
        candidates = recipes
        if normalized_target:
            matched = [
                recipe for recipe in recipes if self._menu_recipe_matches(recipe, normalized_target)
            ]
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

    def save_external_menu_recipe_if_new(
        self, recipe: dict[str, Any], image_dir: Path
    ) -> dict[str, Any]:
        title = normalize_text(recipe.get("title"), "title")
        with self._connect() as conn:
            existing = self._menu_recipe_by_title(conn, title)
            if existing is not None:
                return existing

            recipe_id = normalize_text(
                recipe.get("id")
                or "external-" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:16],
                "id",
            )
            image_relpath = ""
            try:
                image_relpath = cache_image(
                    recipe.get("image_url"), recipe_id=recipe_id, image_dir=image_dir
                )
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
                "ingredients_json": encode_json(
                    normalize_text_list(recipe.get("ingredients"), "ingredients")
                ),
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
                normalize_text_list(dishes, "dishes")
                if dishes is not None
                else current_data["dishes"]
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
