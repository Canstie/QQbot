from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any


class MemoryGraphStore:
    """SQLite property graph: QQ subjects, typed objects, claims and source messages.

    Mixed into PolicyStore to share its transactions, settings and audit log.
    """

    def _create_memory_schema(self, conn) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS memory_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                message_id TEXT NOT NULL DEFAULT '', display_name TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL, raw_message TEXT NOT NULL,
                segments_json TEXT NOT NULL, reply_to TEXT NOT NULL DEFAULT '',
                mentions_json TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_message_id
                ON memory_messages(group_id, message_id) WHERE message_id <> '';
            CREATE INDEX IF NOT EXISTS idx_memory_group_id ON memory_messages(group_id, id);
            CREATE INDEX IF NOT EXISTS idx_memory_user ON memory_messages(group_id, user_id, id);
            CREATE TABLE IF NOT EXISTS memory_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                predicate TEXT NOT NULL, object_type TEXT NOT NULL,
                object_key TEXT NOT NULL, object_label TEXT NOT NULL,
                statement TEXT NOT NULL, certainty TEXT NOT NULL,
                fingerprint TEXT NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
                UNIQUE(group_id, user_id, fingerprint)
            );
            CREATE INDEX IF NOT EXISTS idx_memory_edges_user
                ON memory_edges(group_id, user_id, last_seen DESC);
            CREATE TABLE IF NOT EXISTS memory_evidence (
                edge_id INTEGER NOT NULL REFERENCES memory_edges(id) ON DELETE CASCADE,
                message_row_id INTEGER NOT NULL REFERENCES memory_messages(id) ON DELETE CASCADE,
                PRIMARY KEY(edge_id, message_row_id)
            );
            CREATE TABLE IF NOT EXISTS memory_progress (
                group_id INTEGER PRIMARY KEY, cursor INTEGER NOT NULL DEFAULT 0,
                lease TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0,
                retry_after REAL NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL DEFAULT 0
            );
        """)

    def _retire_quote_library(self, conn) -> None:
        # Explicit one-time replacement: never backfill deleted quotes from other archives.
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='group_quote_messages'"
        ).fetchone()
        if exists:
            count = conn.execute("SELECT COUNT(*) FROM group_quote_messages").fetchone()[0]
            conn.execute("DROP TABLE group_quote_messages")
            self.audit(0, "retire_quote_library", "memory_graph", {"deleted": count}, conn=conn)
        if conn.execute(
            "SELECT 1 FROM settings WHERE key='memory_enabled_groups'"
        ).fetchone() is None:
            old = conn.execute(
                "SELECT value FROM settings WHERE key='quote_memory_enabled_groups'"
            ).fetchone()
            self.set_setting("memory_enabled_groups", old[0] if old else "[]", conn=conn)
        conn.execute("DELETE FROM settings WHERE key='quote_memory_enabled_groups'")

    def memory_enabled_groups(self) -> list[int]:
        try:
            return self._normalize_int_ids(
                json.loads(self.get_setting("memory_enabled_groups", "[]")), "enabled_groups"
            )
        except (ValueError, TypeError):
            return []

    def is_memory_group_enabled(self, group_id: int) -> bool:
        return int(group_id) in self.memory_enabled_groups()

    def set_memory_groups(self, group_ids: list[int], *, actor_id: int) -> dict[str, Any]:
        normalized = self._normalize_int_ids(group_ids, "enabled_groups")
        with self._connect() as conn:
            self.set_setting("memory_enabled_groups", json.dumps(normalized), conn=conn)
            # Invalidate any in-flight extraction when collection settings change.
            conn.execute("UPDATE memory_progress SET lease='', lease_until=0")
            self.audit(actor_id, "set_memory_groups", "memory_graph",
                       {"enabled_groups": normalized}, conn=conn)
        return self.get_memory_config()

    def get_memory_config(self) -> dict[str, Any]:
        with self._connect() as conn:
            counts = {str(r[0]): r[1] for r in conn.execute(
                "SELECT group_id, COUNT(*) FROM memory_messages GROUP BY group_id"
            )}
            edges = conn.execute("SELECT COUNT(*) FROM memory_edges").fetchone()[0]
            pending = conn.execute(
                "SELECT COUNT(*) FROM memory_messages m JOIN memory_progress p "
                "ON p.group_id=m.group_id WHERE m.id>p.cursor"
            ).fetchone()[0]
            progress = [dict(r) for r in conn.execute(
                "SELECT group_id, cursor, failures, last_error, updated_at FROM memory_progress"
            )]
        return {"enabled_groups": self.memory_enabled_groups(), "group_counts": counts,
                "message_count": sum(counts.values()), "edge_count": edges,
                "pending_messages": pending, "progress": progress}

    def record_memory_messages(self, activities: Sequence[Mapping[str, Any]]) -> int:
        enabled = set(self.memory_enabled_groups())
        inserted = 0
        with self._connect() as conn:
            for item in activities:
                group_id, user_id = int(item["group_id"]), int(item["user_id"])
                if group_id not in enabled or user_id <= 0:
                    continue
                segments = list(self._iter_segments(item.get("segments") or ()))
                raw = str(item.get("platform_raw_message") or item.get("raw_message") or "")
                content = self._message_transcript_content(raw, segments)
                # Commands are retained as evidence/context, but the extractor ignores instructions.
                reply_to = ""
                mentions = []
                for segment in segments:
                    data = segment.get("data") or {}
                    if not isinstance(data, Mapping):
                        continue
                    if segment.get("type") == "reply":
                        reply_to = str(data.get("id") or "")
                    if segment.get("type") == "at" and str(data.get("qq", "")).isdigit():
                        mentions.append(int(data["qq"]))
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO memory_messages(group_id,user_id,message_id,"
                    "display_name,content,raw_message,segments_json,reply_to,mentions_json,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (group_id, user_id, str(item.get("message_id") or ""),
                     str(item.get("display_name") or ""), content, raw,
                     json.dumps(segments, ensure_ascii=False, default=str), reply_to,
                     json.dumps(sorted(set(mentions))), float(item.get("timestamp") or time.time())),
                )
                inserted += max(0, cursor.rowcount)
                conn.execute("INSERT OR IGNORE INTO memory_progress(group_id) VALUES (?)", (group_id,))
        return inserted

    @staticmethod
    def _memory_message(row) -> dict[str, Any]:
        result = dict(row)
        result["mentions"] = json.loads(result.pop("mentions_json"))
        result["segments"] = json.loads(result.pop("segments_json"))
        return result

    def get_memory_messages(self, group_id: int, user_id: int, *, limit: int = 300):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_messages WHERE group_id=? AND user_id=? ORDER BY id DESC LIMIT ?",
                (int(group_id), int(user_id), max(1, min(int(limit), 1000))),
            ).fetchall()
        return [self._memory_message(r) for r in rows]

    def clear_memory(self, group_id: int, *, user_id: int | None = None, actor_id: int) -> int:
        group_id = int(group_id)
        if group_id <= 0 or (user_id is not None and int(user_id) <= 0):
            raise ValueError("group_id and user_id must be positive")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Removing a person's messages can invalidate other people's context-derived claims.
            # Remove every edge that cites them, plus incoming/outgoing person edges.
            if user_id is not None:
                user_id = int(user_id)
                conn.execute(
                    "DELETE FROM memory_edges WHERE group_id=? AND (user_id=? OR "
                    "(object_type='person' AND object_key=?) OR id IN (SELECT e.edge_id "
                    "FROM memory_evidence e JOIN memory_messages m ON m.id=e.message_row_id "
                    "WHERE m.group_id=? AND m.user_id=?))",
                    (group_id, user_id, str(user_id), group_id, user_id),
                )
                deleted = conn.execute("DELETE FROM memory_messages WHERE group_id=? AND user_id=?",
                                       (group_id, user_id)).rowcount
            else:
                conn.execute("DELETE FROM memory_edges WHERE group_id=?", (group_id,))
                deleted = conn.execute("DELETE FROM memory_messages WHERE group_id=?", (group_id,)).rowcount
            conn.execute("UPDATE memory_progress SET lease='', lease_until=0 WHERE group_id=?", (group_id,))
            self.audit(actor_id, "clear_memory", str(group_id),
                       {"user_id": user_id, "deleted": deleted}, conn=conn)
        return deleted

    def claim_memory_batch(self, group_id: int, *, force: bool = False, now: float | None = None):
        """Lease bounded work in a transaction; network calls happen after releasing SQLite."""
        now = time.time() if now is None else now
        if not self.is_memory_group_enabled(group_id):
            return None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = conn.execute("SELECT * FROM memory_progress WHERE group_id=?", (group_id,)).fetchone()
            if not state or state["lease_until"] > now or state["retry_after"] > now:
                return None
            rows = conn.execute(
                "SELECT * FROM memory_messages WHERE group_id=? AND id>? ORDER BY id LIMIT 60",
                (group_id, state["cursor"]),
            ).fetchall()
            if not rows or (not force and len(rows) < 12 and rows[0]["created_at"] > now - 600):
                return None
            # Recent context and explicitly quoted older messages are evidence, never instructions.
            context = conn.execute(
                "SELECT * FROM memory_messages WHERE group_id=? AND id<=? ORDER BY id DESC LIMIT 8",
                (group_id, state["cursor"]),
            ).fetchall()
            selected = {r["id"]: self._memory_message(r) for r in [*context, *rows]}
            for row in rows:
                if row["reply_to"]:
                    quoted = conn.execute(
                        "SELECT * FROM memory_messages WHERE group_id=? AND message_id=?",
                        (group_id, row["reply_to"]),
                    ).fetchone()
                    if quoted:
                        selected[quoted["id"]] = self._memory_message(quoted)
            lease = uuid.uuid4().hex
            conn.execute("UPDATE memory_progress SET lease=?, lease_until=? WHERE group_id=?",
                         (lease, now + 600, group_id))
        return {"group_id": group_id, "lease": lease, "cursor": rows[-1]["id"],
                "new_ids": [r["id"] for r in rows], "messages": list(selected.values())}

    def save_memory_batch(self, batch: Mapping[str, Any], claims: list[dict[str, Any]]) -> bool:
        group_id = int(batch["group_id"])
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = conn.execute("SELECT lease FROM memory_progress WHERE group_id=?", (group_id,)).fetchone()
            if not state or state["lease"] != batch["lease"]:
                return False
            # Re-read after API call: a concurrent clear must never resurrect deleted data.
            allowed = {m["id"] for m in batch["messages"]}
            placeholders = ",".join("?" for _ in allowed)
            sources = {r["id"]: r for r in conn.execute(
                f"SELECT id,user_id,created_at FROM memory_messages WHERE group_id=? AND id IN ({placeholders})",
                (group_id, *sorted(allowed)),
            )}
            for claim in claims:
                evidence = claim["source_ids"]
                if not evidence or any(s not in allowed or s not in sources for s in evidence):
                    continue
                if not any(sources[s]["user_id"] == claim["user_id"] for s in evidence):
                    continue
                times = [sources[s]["created_at"] for s in evidence]
                fingerprint = hashlib.sha256(json.dumps(
                    [claim[k] for k in ("predicate", "object_type", "object_key", "statement", "certainty")],
                    ensure_ascii=False).encode()).hexdigest()
                conn.execute(
                    "INSERT INTO memory_edges(group_id,user_id,predicate,object_type,object_key,"
                    "object_label,statement,certainty,fingerprint,first_seen,last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(group_id,user_id,fingerprint) DO UPDATE SET "
                    "first_seen=MIN(first_seen,excluded.first_seen),last_seen=MAX(last_seen,excluded.last_seen)",
                    (group_id, claim["user_id"], claim["predicate"], claim["object_type"],
                     claim["object_key"], claim["object_label"], claim["statement"], claim["certainty"],
                     fingerprint, min(times), max(times)),
                )
                edge_id = conn.execute(
                    "SELECT id FROM memory_edges WHERE group_id=? AND user_id=? AND fingerprint=?",
                    (group_id, claim["user_id"], fingerprint),
                ).fetchone()[0]
                conn.executemany("INSERT OR IGNORE INTO memory_evidence VALUES (?,?)",
                                 [(edge_id, source_id) for source_id in evidence])
            conn.execute(
                "UPDATE memory_progress SET cursor=?,lease='',lease_until=0,retry_after=0,"
                "failures=0,last_error='',updated_at=? WHERE group_id=?",
                (batch["cursor"], time.time(), group_id),
            )
        return True

    def fail_memory_batch(self, batch: Mapping[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE memory_progress SET lease='',lease_until=0,failures=failures+1,"
                "retry_after=?+MIN(3600,60*(1<<MIN(failures,6))),last_error=? "
                "WHERE group_id=? AND lease=?",
                (time.time(), "提取失败，等待自动重试", batch["group_id"], batch["lease"]),
            )

    def get_person_graph(self, group_id: int, user_id: int, *, topic: str = "", limit: int = 20):
        limit = max(1, min(int(limit), 40))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_edges WHERE group_id=? AND user_id=? ORDER BY last_seen DESC LIMIT 400",
                (int(group_id), int(user_id)),
            ).fetchall()
            tokens = set(re.findall(r"[a-z0-9_]{2,}", topic.casefold()))
            for chunk in re.findall(r"[\u3400-\u9fff]+", topic):
                tokens.update(chunk[i:i+2] for i in range(len(chunk)-1))
            tokens = sorted(tokens)[:24]
            # Search relevant older memories too, rather than silently losing them behind a recency cap.
            pool = {r["id"]: r for r in rows}
            if tokens:
                match_sql = " OR ".join("instr(lower(statement || object_label),?)>0" for _ in tokens)
                matches = conn.execute(
                    f"SELECT * FROM memory_edges WHERE group_id=? AND user_id=? AND ({match_sql}) "
                    "ORDER BY last_seen DESC LIMIT 200", (int(group_id), int(user_id), *tokens),
                ).fetchall()
                pool.update({r["id"]: r for r in matches})
            ranked = sorted(pool.values(), key=lambda r: (
                sum(t in (r["statement"] + r["object_label"]).casefold() for t in tokens),
                r["last_seen"]), reverse=True)
            edges = []
            per_topic = {}
            for row in ranked:
                topic_key = (row["predicate"], row["object_type"], row["object_key"])
                if per_topic.get(topic_key, 0) >= 2:
                    continue
                evidence = conn.execute(
                    "SELECT m.id,m.user_id,m.message_id,m.content,m.created_at,m.reply_to "
                    "FROM memory_messages m JOIN memory_evidence e ON e.message_row_id=m.id "
                    "WHERE e.edge_id=? AND m.group_id=? ORDER BY m.id DESC LIMIT 3",
                    (row["id"], int(group_id)),
                ).fetchall()
                if evidence:
                    edge = dict(row)
                    edge.pop("fingerprint")
                    edge["evidence"] = [dict(r) for r in evidence]
                    for source in edge["evidence"]:
                        if len(source["content"]) > 600:
                            source["content"] = source["content"][:600] + "…（节选）"
                    edges.append(edge)
                    per_topic[topic_key] = per_topic.get(topic_key, 0) + 1
                    if len(edges) >= limit:
                        break
        nodes = {f"person:{user_id}": {"id": f"person:{user_id}", "type": "person", "label": str(user_id)}}
        for edge in edges:
            key = f"{edge['object_type']}:{edge['object_key']}"
            nodes[key] = {"id": key, "type": edge["object_type"], "label": edge["object_label"]}
            edge["source"] = f"person:{user_id}"
            edge["target"] = key
        return {"group_id": int(group_id), "user_id": int(user_id), "nodes": list(nodes.values()), "edges": edges}
