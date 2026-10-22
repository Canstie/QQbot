from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from qq_personal_bot.core.store_constants import (
    _GROUP_MESSAGE_RETENTION_DAYS,
    CHINA_TZ,
    _strip_cq_segments,
)


class GroupMessageStore:
    """GroupMessage persistence using the owning store connection."""

    def record_group_message_activity(
        self,
        *,
        group_id: int,
        user_id: int,
        timestamp: float,
        raw_message: str,
        segments: Any,
        message_id: int | str = "",
    ) -> None:
        self.record_group_message_activities(
            (
                {
                    "group_id": group_id,
                    "user_id": user_id,
                    "timestamp": timestamp,
                    "raw_message": raw_message,
                    "segments": segments,
                    "message_id": message_id,
                },
            )
        )

    def record_group_message_activities(
        self,
        activities: Sequence[Mapping[str, Any]],
    ) -> int:
        """Persist a small activity batch in one transaction and one database connection."""
        if not activities:
            return 0

        aggregates: dict[tuple[str, int, int], dict[str, Any]] = {}
        newest_datetime: datetime | None = None
        inserted = 0
        now = time.time()

        with self._connect() as conn:
            for activity in activities:
                group_id = int(activity["group_id"])
                user_id = int(activity["user_id"])
                event_time = float(activity.get("timestamp") or now)
                event_datetime = datetime.fromtimestamp(event_time, CHINA_TZ)
                event_date = event_datetime.date().isoformat()
                segments = activity.get("segments") or ()
                raw_message = str(activity.get("raw_message") or "")
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO group_daily_messages(
                        date, group_id, user_id, message_id, content, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_date,
                        group_id,
                        user_id,
                        str(activity.get("message_id") or ""),
                        self._message_transcript_content(raw_message, segments),
                        event_time,
                    ),
                )
                if cursor.rowcount <= 0:
                    continue

                inserted += 1
                if newest_datetime is None or event_datetime > newest_datetime:
                    newest_datetime = event_datetime
                key = (event_date, group_id, user_id)
                aggregate = aggregates.setdefault(
                    key,
                    {
                        "message_count": 0,
                        "text_chars": 0,
                        "image_count": 0,
                        "at_count": 0,
                        "reply_count": 0,
                        "first_timestamp": event_time,
                        "last_timestamp": event_time,
                        "hourly_counts": [0] * 24,
                    },
                )
                aggregate["message_count"] += 1
                aggregate["text_chars"] += self._message_text_length(raw_message, segments)
                aggregate["image_count"] += self._count_segments(segments, "image")
                aggregate["at_count"] += self._count_segments(segments, "at")
                aggregate["reply_count"] += self._count_segments(segments, "reply")
                aggregate["first_timestamp"] = min(float(aggregate["first_timestamp"]), event_time)
                aggregate["last_timestamp"] = max(float(aggregate["last_timestamp"]), event_time)
                aggregate["hourly_counts"][event_datetime.hour] += 1

            for (event_date, group_id, user_id), aggregate in aggregates.items():
                row = conn.execute(
                    """
                    SELECT hourly_json
                    FROM group_daily_stats
                    WHERE date = ? AND group_id = ? AND user_id = ?
                    """,
                    (event_date, group_id, user_id),
                ).fetchone()
                hourly_counts = (
                    self._decode_hourly_counts(str(row["hourly_json"]))
                    if row is not None
                    else [0] * 24
                )
                hourly_counts = [
                    current + delta
                    for current, delta in zip(
                        hourly_counts, aggregate["hourly_counts"], strict=True
                    )
                ]
                conn.execute(
                    """
                    INSERT INTO group_daily_stats(
                        date, group_id, user_id, message_count, text_chars,
                        image_count, at_count, reply_count, first_timestamp,
                        last_timestamp, hourly_json, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date, group_id, user_id) DO UPDATE SET
                        message_count = message_count + excluded.message_count,
                        text_chars = text_chars + excluded.text_chars,
                        image_count = image_count + excluded.image_count,
                        at_count = at_count + excluded.at_count,
                        reply_count = reply_count + excluded.reply_count,
                        first_timestamp = MIN(first_timestamp, excluded.first_timestamp),
                        last_timestamp = MAX(last_timestamp, excluded.last_timestamp),
                        hourly_json = excluded.hourly_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        event_date,
                        group_id,
                        user_id,
                        aggregate["message_count"],
                        aggregate["text_chars"],
                        aggregate["image_count"],
                        aggregate["at_count"],
                        aggregate["reply_count"],
                        aggregate["first_timestamp"],
                        aggregate["last_timestamp"],
                        json.dumps(hourly_counts, separators=(",", ":")),
                        now,
                    ),
                )

            if newest_datetime is not None:
                self._purge_group_messages_if_due(conn, newest_datetime)
        return inserted

    def _purge_group_messages_if_due(
        self,
        conn: sqlite3.Connection,
        event_datetime: datetime,
    ) -> None:
        cleanup_date = event_datetime.date().isoformat()
        with self._group_message_cleanup_lock:
            if (
                self._last_group_message_cleanup_date is not None
                and self._last_group_message_cleanup_date >= cleanup_date
            ):
                return
            retention_cutoff = (
                event_datetime.date() - timedelta(days=_GROUP_MESSAGE_RETENTION_DAYS - 1)
            ).isoformat()
            conn.execute(
                "DELETE FROM group_daily_messages WHERE date < ?",
                (retention_cutoff,),
            )
            self._last_group_message_cleanup_date = cleanup_date

    def record_group_message_transcript(
        self,
        *,
        group_id: int,
        user_id: int,
        timestamp: float,
        raw_message: str,
        segments: Any,
        message_id: int | str = "",
    ) -> bool:
        event_time = float(timestamp or time.time())
        event_datetime = datetime.fromtimestamp(event_time, CHINA_TZ)
        event_date = event_datetime.date().isoformat()
        content = self._message_transcript_content(raw_message, segments)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO group_daily_messages(
                    date, group_id, user_id, message_id, content, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event_date,
                    int(group_id),
                    int(user_id),
                    str(message_id or ""),
                    content,
                    event_time,
                ),
            )
            self._purge_group_messages_if_due(conn, event_datetime)
            return cursor.rowcount > 0

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
            "total_text_chars": sum(item["text_chars"] for item in stats),
            "total_images": sum(item["image_count"] for item in stats),
            "total_mentions": sum(item["at_count"] for item in stats),
            "hourly_counts": hourly_counts,
            "peak_hour": peak_hour,
            "active_hours": active_hours[:limit],
            "early_bird": self._public_group_stat(early_bird) if early_bird else None,
            "night_owl": self._public_group_stat(night_owl) if night_owl else None,
            "top_messages": self._top_group_stats(stats, "message_count", limit),
            "top_text_chars": self._top_group_stats(stats, "text_chars", limit),
            "top_images": self._top_group_stats(stats, "image_count", limit, positive_only=True),
            "top_mentions": self._top_group_stats(stats, "at_count", limit, positive_only=True),
        }

    def get_group_daily_messages(
        self,
        group_id: int,
        date: str,
    ) -> list[dict[str, Any]]:
        target_date = str(date or "").strip() or datetime.now(CHINA_TZ).date().isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, date, group_id, user_id, content, created_at
                FROM group_daily_messages
                WHERE date = ? AND group_id = ?
                ORDER BY created_at, id
                """,
                (target_date, int(group_id)),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "date": str(row["date"]),
                "group_id": int(row["group_id"]),
                "user_id": int(row["user_id"]),
                "content": str(row["content"]),
                "created_at": float(row["created_at"]),
            }
            for row in rows
        ]

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

    def _message_transcript_content(self, raw_message: str, segments: Any) -> str:
        iterated = self._iter_segments(segments)
        if not iterated:
            content = re.sub(
                r"\[CQ:([a-zA-Z0-9_-]+)(?:,[^\]]*)?\]",
                lambda match: f"[{self._segment_label(match.group(1))}]",
                str(raw_message or ""),
            )
            return " ".join(content.split())[:4000] or "[空消息]"

        parts: list[str] = []
        for segment in iterated:
            segment_type = str(segment.get("type") or "").casefold()
            data = segment.get("data")
            if segment_type == "text" and isinstance(data, Mapping):
                parts.append(str(data.get("text", "")))
                continue
            if segment_type == "at" and isinstance(data, Mapping):
                target = str(data.get("qq") or "").strip()
                parts.append(f"[@{target}]" if target else "[@群友]")
                continue
            if segment_type:
                parts.append(f"[{self._segment_label(segment_type)}]")
        return " ".join("".join(parts).split())[:4000] or "[空消息]"

    def _segment_label(self, segment_type: str) -> str:
        labels = {
            "image": "图片",
            "record": "语音",
            "video": "视频",
            "file": "文件",
            "reply": "回复",
            "face": "表情",
            "mface": "表情包",
            "marketface": "表情包",
            "forward": "合并转发",
            "json": "卡片",
            "xml": "卡片",
            "lightapp": "卡片",
            "markdown": "卡片",
        }
        normalized = str(segment_type or "").casefold()
        return labels.get(normalized, normalized or "消息")

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
