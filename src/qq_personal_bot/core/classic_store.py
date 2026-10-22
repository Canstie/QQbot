from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from typing import Any


class ClassicStore:
    """Classic persistence using the owning store connection."""

    def _initialize_classic_image_selection(self, conn: sqlite3.Connection) -> None:
        columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(classic_images)").fetchall()
        }
        migrations = {
            "blast_count": "INTEGER NOT NULL DEFAULT 0",
            "last_blast_at": "REAL NOT NULL DEFAULT 0",
            "fair_order": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, definition in migrations.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE classic_images ADD COLUMN {column} {definition}")
        conn.execute("UPDATE classic_images SET fair_order = RANDOM() WHERE fair_order = 0")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_classic_images_fair_pick
            ON classic_images(group_id, blast_count, fair_order, id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_classic_images_last_blast
            ON classic_images(group_id, last_blast_at DESC, id DESC)
            """
        )

    def resolve_classic_group(self, group_id: int) -> int:
        normalized = int(group_id)
        if normalized <= 0:
            raise ValueError("group_id must be positive")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT master_group_id FROM classic_group_bindings WHERE group_id = ?",
                (normalized,),
            ).fetchone()
        return int(row["master_group_id"]) if row is not None else normalized

    def list_classic_bindings(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT group_id, master_group_id FROM classic_group_bindings "
                "ORDER BY master_group_id, group_id"
            ).fetchall()
        families: dict[int, list[int]] = {}
        for row in rows:
            families.setdefault(int(row["master_group_id"]), []).append(int(row["group_id"]))
        return [
            {"master_group_id": master, "group_ids": members}
            for master, members in families.items()
        ]

    def bind_classic_group(
        self, master_group_id: int, group_id: int, source_images: list[dict[str, Any]]
    ) -> int:
        master, member = int(master_group_id), int(group_id)
        if master <= 0 or member <= 0 or master == member:
            raise ValueError("主群和副群必须是不同的正整数群号")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if (
                conn.execute(
                    "SELECT 1 FROM classic_group_bindings WHERE group_id IN (?, ?)",
                    (master, member),
                ).fetchone()
                is not None
            ):
                raise ValueError("主群或副群已绑定其他典藏")
            if (
                conn.execute(
                    "SELECT 1 FROM classic_group_bindings WHERE master_group_id = ?",
                    (member,),
                ).fetchone()
                is not None
            ):
                raise ValueError("已有副群的主群不能作为副群")
            current = conn.execute(
                "SELECT id, sha256 FROM classic_images WHERE group_id = ?", (member,)
            ).fetchall()
            if {(int(row["id"]), str(row["sha256"])) for row in current} != {
                (int(image["id"]), str(image["sha256"])) for image in source_images
            }:
                raise RuntimeError("副群典藏在复制过程中发生变化，请重试")
            minimum = conn.execute(
                "SELECT COALESCE(MIN(blast_count), 0) FROM classic_images WHERE group_id = ?",
                (master,),
            ).fetchone()[0]
            merged = 0
            for image in source_images:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO classic_images(
                        group_id, sha256, object_key, content_type, size_bytes,
                        created_at, blast_count, last_blast_at, fair_order
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        master,
                        image["sha256"],
                        image["object_key"],
                        image["content_type"],
                        image["size_bytes"],
                        image["created_at"],
                        int(minimum),
                        self._classic_fair_order(image["sha256"]),
                    ),
                )
                merged += cursor.rowcount
                if (
                    conn.execute(
                        "SELECT 1 FROM classic_images WHERE group_id = ? AND sha256 = ?",
                        (master, image["sha256"]),
                    ).fetchone()
                    is None
                ):
                    raise RuntimeError("主群存在冲突的典图文件名，绑定已取消")
            conn.execute("DELETE FROM classic_images WHERE group_id = ?", (member,))
            conn.execute(
                "INSERT INTO classic_group_bindings(group_id, master_group_id, created_at) "
                "VALUES (?, ?, ?)",
                (member, master, time.time()),
            )
        return merged

    def dissolve_classic_group(
        self, master_group_id: int, source_images: list[dict[str, Any]]
    ) -> list[int]:
        master = int(master_group_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            members = [
                int(row["group_id"])
                for row in conn.execute(
                    "SELECT group_id FROM classic_group_bindings WHERE master_group_id = ? ORDER BY group_id",
                    (master,),
                ).fetchall()
            ]
            if not members:
                raise ValueError("这个主群没有绑定的副群")
            current = conn.execute(
                "SELECT id, sha256 FROM classic_images WHERE group_id = ?", (master,)
            ).fetchall()
            if {(int(row["id"]), str(row["sha256"])) for row in current} != {
                (int(image["id"]), str(image["sha256"])) for image in source_images
            }:
                raise RuntimeError("共享典藏在复制过程中发生变化，请重试")
            for member in members:
                if (
                    conn.execute(
                        "SELECT 1 FROM classic_images WHERE group_id = ? LIMIT 1", (member,)
                    ).fetchone()
                    is not None
                ):
                    raise RuntimeError("副群存在未预期的典图记录，解散已取消")
                for image in source_images:
                    conn.execute(
                        """
                        INSERT INTO classic_images(
                            group_id, sha256, object_key, content_type, size_bytes,
                            created_at, blast_count, last_blast_at, fair_order
                        ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?)
                        """,
                        (
                            member,
                            image["sha256"],
                            image["object_key"],
                            image["content_type"],
                            image["size_bytes"],
                            image["created_at"],
                            self._classic_fair_order(image["sha256"]),
                        ),
                    )
            conn.execute("DELETE FROM classic_group_bindings WHERE master_group_id = ?", (master,))
        return members

    def record_classic_image(
        self,
        *,
        group_id: int,
        sha256: str,
        object_key: str,
        content_type: str,
        size_bytes: int,
        created_at: float | None = None,
    ) -> tuple[dict[str, Any], bool]:
        normalized_group_id = int(group_id)
        normalized_hash = str(sha256).strip().casefold()
        normalized_key = str(object_key).strip()
        if normalized_group_id <= 0:
            raise ValueError("group_id must be positive")
        if not re.fullmatch(r"[0-9a-f]{64}", normalized_hash):
            raise ValueError("sha256 must be a 64-character lowercase hex digest")
        if not normalized_key or "/" in normalized_key or "\\" in normalized_key:
            raise ValueError("object_key must be a filename")
        if int(size_bytes) <= 0:
            raise ValueError("size_bytes must be positive")

        with self._connect() as conn:
            minimum_row = conn.execute(
                """
                SELECT COALESCE(MIN(blast_count), 0) AS minimum
                FROM classic_images
                WHERE group_id = ?
                """,
                (normalized_group_id,),
            ).fetchone()
            initial_blast_count = int(minimum_row["minimum"] if minimum_row is not None else 0)
            fair_order = self._classic_fair_order(normalized_hash)
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO classic_images(
                        group_id, sha256, object_key, content_type, size_bytes, created_at,
                        blast_count, last_blast_at, fair_order
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        normalized_group_id,
                        normalized_hash,
                        normalized_key,
                        str(content_type or "application/octet-stream"),
                        int(size_bytes),
                        float(created_at if created_at is not None else time.time()),
                        initial_blast_count,
                        fair_order,
                    ),
                )
            except sqlite3.IntegrityError:
                row = conn.execute(
                    """
                    SELECT * FROM classic_images
                    WHERE group_id = ? AND (sha256 = ? OR object_key = ?)
                    """,
                    (normalized_group_id, normalized_hash, normalized_key),
                ).fetchone()
                if row is None:
                    raise
                return self._classic_image_from_row(row), False
            row = conn.execute(
                "SELECT * FROM classic_images WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            if row is None:
                raise RuntimeError("classic image index insert failed")
            return self._classic_image_from_row(row), True

    def get_classic_image_by_hash(self, group_id: int, sha256: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM classic_images WHERE group_id = ? AND sha256 = ?",
                (int(group_id), str(sha256).strip().casefold()),
            ).fetchone()
            return self._classic_image_from_row(row) if row is not None else None

    def get_classic_image(self, image_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM classic_images WHERE id = ?",
                (int(image_id),),
            ).fetchone()
            return self._classic_image_from_row(row) if row is not None else None

    def get_classic_image_by_key(self, group_id: int, object_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM classic_images WHERE group_id = ? AND object_key = ?",
                (int(group_id), str(object_key).strip()),
            ).fetchone()
            return self._classic_image_from_row(row) if row is not None else None

    def list_classic_images(
        self, group_id: int, *, limit: int | None = 1000
    ) -> list[dict[str, Any]]:
        normalized_limit = -1 if limit is None else min(max(1, int(limit)), 100_000)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM classic_images
                WHERE group_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (int(group_id), normalized_limit),
            ).fetchall()
        return [self._classic_image_from_row(row) for row in rows]

    def list_classic_groups(self, *, search: str = "", limit: int = 200) -> list[dict[str, Any]]:
        normalized_limit = min(max(1, int(limit)), 500)
        normalized_search = str(search).strip()
        where = ""
        params: list[Any] = []
        if normalized_search:
            where = "WHERE CAST(images.group_id AS TEXT) LIKE ?"
            params.append(f"%{normalized_search}%")
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    images.group_id,
                    COUNT(*) AS count,
                    COALESCE(SUM(images.size_bytes), 0) AS total_bytes,
                    MAX(images.created_at) AS updated_at,
                    (
                        SELECT latest.id FROM classic_images AS latest
                        WHERE latest.group_id = images.group_id
                        ORDER BY latest.created_at DESC, latest.id DESC
                        LIMIT 1
                    ) AS cover_id
                FROM classic_images AS images
                {where}
                GROUP BY images.group_id
                ORDER BY updated_at DESC, images.group_id DESC
                LIMIT ?
                """,
                [*params, normalized_limit],
            ).fetchall()
        return [
            {
                "group_id": int(row["group_id"]),
                "count": int(row["count"]),
                "total_bytes": int(row["total_bytes"]),
                "updated_at": float(row["updated_at"]),
                "cover_id": int(row["cover_id"]),
            }
            for row in rows
        ]

    def get_classic_group_stat(self, group_id: int) -> dict[str, Any] | None:
        normalized = int(group_id)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count, COALESCE(SUM(size_bytes), 0) AS total_bytes,
                       MAX(created_at) AS updated_at
                FROM classic_images WHERE group_id = ?
                """,
                (normalized,),
            ).fetchone()
            if row is None or not row["count"]:
                return None
            cover = conn.execute(
                "SELECT id FROM classic_images WHERE group_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (normalized,),
            ).fetchone()
        return {
            "group_id": normalized,
            "count": int(row["count"]),
            "total_bytes": int(row["total_bytes"]),
            "updated_at": float(row["updated_at"]),
            "cover_id": int(cover["id"]),
        }

    def pick_classic_image(self, group_id: int, seed: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous_row = conn.execute(
                """
                SELECT id FROM classic_images
                WHERE group_id = ? AND last_blast_at > 0
                ORDER BY last_blast_at DESC, id DESC
                LIMIT 1
                """,
                (int(group_id),),
            ).fetchone()
            previous_id = int(previous_row["id"]) if previous_row is not None else -1
            minimum_row = conn.execute(
                """
                SELECT blast_count FROM classic_images
                WHERE group_id = ?
                ORDER BY blast_count ASC, fair_order ASC, id ASC
                LIMIT 1
                """,
                (int(group_id),),
            ).fetchone()
            if minimum_row is None:
                return None
            minimum_count = int(minimum_row["blast_count"])
            row = conn.execute(
                """
                SELECT * FROM classic_images
                WHERE group_id = ? AND blast_count = ? AND id != ?
                ORDER BY fair_order ASC, id ASC
                LIMIT 1
                """,
                (int(group_id), minimum_count, previous_id),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    """
                    SELECT * FROM classic_images
                    WHERE group_id = ? AND blast_count = ?
                    ORDER BY fair_order ASC, id ASC
                    LIMIT 1
                    """,
                    (int(group_id), minimum_count),
                ).fetchone()
            if row is None:
                return None
            next_count = int(row["blast_count"]) + 1
            fair_order = self._classic_fair_order(f"{int(seed)}:{int(row['id'])}:{next_count}")
            blasted_at = time.time()
            conn.execute(
                """
                UPDATE classic_images
                SET blast_count = ?, last_blast_at = ?, fair_order = ?
                WHERE id = ?
                """,
                (next_count, blasted_at, fair_order, int(row["id"])),
            )
            row = conn.execute(
                "SELECT * FROM classic_images WHERE id = ?",
                (int(row["id"]),),
            ).fetchone()
        return self._classic_image_from_row(row) if row is not None else None

    @staticmethod
    def _classic_fair_order(value: str) -> int:
        digest = hashlib.sha256(str(value).encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)

    def delete_classic_image(self, image_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM classic_images WHERE id = ?", (int(image_id),))
            return cursor.rowcount > 0

    def delete_classic_group(self, group_id: int) -> int:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM classic_images WHERE group_id = ?", (int(group_id),))
            return cursor.rowcount

    def _classic_image_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "group_id": int(row["group_id"]),
            "sha256": str(row["sha256"]),
            "object_key": str(row["object_key"]),
            "content_type": str(row["content_type"]),
            "size_bytes": int(row["size_bytes"]),
            "created_at": float(row["created_at"]),
            "blast_count": int(row["blast_count"]),
            "last_blast_at": float(row["last_blast_at"]),
        }
