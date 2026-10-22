from __future__ import annotations

import re
import sqlite3
import time
from datetime import datetime
from typing import Any

from qq_personal_bot.core.store_constants import CHINA_TZ


class DownloadStore:
    """Download persistence using the owning store connection."""

    def _initialize_download_image_selection(self, conn: sqlite3.Connection) -> None:
        columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(download_images)")}
        if "last_used_seq" not in columns:
            conn.execute(
                "ALTER TABLE download_images ADD COLUMN last_used_seq INTEGER NOT NULL DEFAULT 0"
            )
        if "random_order" not in columns:
            conn.execute(
                "ALTER TABLE download_images ADD COLUMN random_order INTEGER NOT NULL DEFAULT 0"
            )
            conn.execute("UPDATE download_images SET random_order = RANDOM()")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_download_images_lru "
            "ON download_images(last_used_seq, random_order, id)"
        )

    def record_download_image(
        self,
        *,
        sha256: str,
        object_key: str,
        content_type: str,
        size_bytes: int,
        downloaded_date: str,
    ) -> tuple[dict[str, Any], bool]:
        normalized_hash = str(sha256).strip().casefold()
        normalized_key = str(object_key).strip()
        normalized_date = str(downloaded_date).strip()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized_hash):
            raise ValueError("sha256 must be a 64-character lowercase hex digest")
        if not normalized_key:
            raise ValueError("object_key cannot be empty")
        if not re.fullmatch(r"\d{8}", normalized_date):
            raise ValueError("downloaded_date must use YYYYMMDD")
        if int(size_bytes) <= 0:
            raise ValueError("size_bytes must be positive")

        with self._connect() as conn:
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO download_images(
                        sha256, object_key, content_type, size_bytes, downloaded_date, created_at,
                        random_order
                    ) VALUES (?, ?, ?, ?, ?, ?, RANDOM())
                    """,
                    (
                        normalized_hash,
                        normalized_key,
                        str(content_type or "application/octet-stream"),
                        int(size_bytes),
                        normalized_date,
                        time.time(),
                    ),
                )
            except sqlite3.IntegrityError:
                row = conn.execute(
                    "SELECT * FROM download_images WHERE sha256 = ? OR object_key = ?",
                    (normalized_hash, normalized_key),
                ).fetchone()
                if row is None:
                    raise
                return self._download_image_from_row(row), False
            row = conn.execute(
                "SELECT * FROM download_images WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            if row is None:
                raise RuntimeError("download image index insert failed")
            return self._download_image_from_row(row), True

    def reserve_download_images(self, count: int) -> list[dict[str, Any]]:
        """Atomically rotate the least recently used images to the end of the queue."""
        count = max(1, min(int(count), 100))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT * FROM download_images ORDER BY last_used_seq, random_order, id LIMIT ?",
                (count,),
            ).fetchall()
            sequence_row = conn.execute(
                "SELECT value FROM settings WHERE key = 'download_lru_sequence'"
            ).fetchone()
            sequence = int(sequence_row["value"]) if sequence_row else 0
            selected = []
            for row in rows:
                sequence += 1
                conn.execute(
                    "UPDATE download_images SET last_used_seq = ? WHERE id = ?",
                    (sequence, row["id"]),
                )
                selected.append(
                    {
                        **self._download_image_from_row(row),
                        "previous_seq": int(row["last_used_seq"]),
                        "reserved_seq": sequence,
                    }
                )
            if rows:
                self.set_setting("download_lru_sequence", str(sequence), conn=conn)
        return selected

    def release_download_images(self, images: list[dict[str, Any]]) -> None:
        """Undo failed reservations without overwriting a newer use of the same image."""
        if not images:
            return
        with self._connect() as conn:
            conn.executemany(
                "UPDATE download_images SET last_used_seq = ? WHERE id = ? AND last_used_seq = ?",
                [(item["previous_seq"], item["id"], item["reserved_seq"]) for item in images],
            )

    def get_download_image_by_hash(self, sha256: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM download_images WHERE sha256 = ?",
                (str(sha256).strip().casefold(),),
            ).fetchone()
            return self._download_image_from_row(row) if row is not None else None

    def get_download_image(self, image_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM download_images WHERE id = ?",
                (int(image_id),),
            ).fetchone()
            return self._download_image_from_row(row) if row is not None else None

    def list_download_images(
        self,
        *,
        downloaded_date: str | None = None,
        offset: int = 0,
        limit: int = 60,
    ) -> dict[str, Any]:
        normalized_offset = max(0, int(offset))
        normalized_limit = min(max(1, int(limit)), 100)
        where = ""
        params: list[Any] = []
        if downloaded_date:
            normalized_date = str(downloaded_date).strip()
            if not re.fullmatch(r"\d{8}", normalized_date):
                raise ValueError("downloaded_date must use YYYYMMDD")
            where = "WHERE downloaded_date = ?"
            params.append(normalized_date)
        with self._connect() as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) AS count FROM download_images {where}",
                params,
            ).fetchone()
            rows = conn.execute(
                f"""
                SELECT * FROM download_images {where}
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, normalized_limit, normalized_offset],
            ).fetchall()
            date_rows = conn.execute(
                """
                SELECT downloaded_date, COUNT(*) AS count
                FROM download_images
                GROUP BY downloaded_date
                ORDER BY downloaded_date DESC
                """
            ).fetchall()
        return {
            "images": [self._download_image_from_row(row) for row in rows],
            "total": int(total_row["count"] if total_row is not None else 0),
            "offset": normalized_offset,
            "limit": normalized_limit,
            "dates": [
                {"date": str(row["downloaded_date"]), "count": int(row["count"])}
                for row in date_rows
            ],
        }

    def download_image_overview(self, today: str | None = None) -> dict[str, Any]:
        target_date = today or datetime.now(CHINA_TZ).strftime("%Y%m%d")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count, COALESCE(SUM(size_bytes), 0) AS total_bytes "
                "FROM download_images"
            ).fetchone()
            today_row = conn.execute(
                "SELECT COUNT(*) AS count FROM download_images WHERE downloaded_date = ?",
                (target_date,),
            ).fetchone()
        return {
            "total": int(row["count"] if row is not None else 0),
            "total_bytes": int(row["total_bytes"] if row is not None else 0),
            "today": str(target_date),
            "today_count": int(today_row["count"] if today_row is not None else 0),
        }

    def delete_download_image(self, image_id: int) -> bool:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM download_images WHERE id = ?", (int(image_id),))
            return cursor.rowcount > 0

    def _download_image_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "sha256": str(row["sha256"]),
            "object_key": str(row["object_key"]),
            "content_type": str(row["content_type"]),
            "size_bytes": int(row["size_bytes"]),
            "downloaded_date": str(row["downloaded_date"]),
            "created_at": float(row["created_at"]),
        }
