"""Administrator-maintained names and deterministic person reference resolution."""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass


def normalize_person_alias(value: str) -> str:
    alias = unicodedata.normalize("NFKC", str(value)).strip()
    if (
        not alias
        or len(alias) > 40
        or "[cq:" in alias.casefold()
        or any(unicodedata.category(c) in {"Cc", "Cs"} for c in alias)
    ):
        raise ValueError("别名须为 1—40 个字符，不能包含控制字符或 CQ 消息代码")
    return " ".join(alias.split())


def _user_id(value: int) -> int:
    if isinstance(value, bool) or not 0 < int(value) <= 2**63 - 1:
        raise ValueError("请填写有效的 QQ 号")
    return int(value)


class PersonAliasStore:
    def _create_person_alias_schema(self, conn) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS person_aliases (
                user_id INTEGER NOT NULL,
                alias_key TEXT NOT NULL,
                alias TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (user_id, alias_key)
            );
            CREATE INDEX IF NOT EXISTS person_alias_key ON person_aliases(alias_key);
        """)

    def list_person_aliases(self, user_id: int | None = None) -> list[dict]:
        def load():
            with self._connect() as conn:
                return [
                    dict(row)
                    for row in conn.execute(
                        "SELECT user_id, alias FROM person_aliases ORDER BY updated_at DESC, user_id, alias_key"
                    )
                ]

        rows = self._cached_runtime_value(("person_aliases",), load)
        return [dict(row) for row in rows if user_id is None or row["user_id"] == int(user_id)]

    def add_person_alias(self, user_id: int, alias: str, *, actor_id: int) -> bool:
        user_id, alias = _user_id(user_id), normalize_person_alias(alias)
        key = alias.casefold()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                "SELECT alias FROM person_aliases WHERE user_id=? AND alias_key=?", (user_id, key)
            ).fetchone()
            if previous and previous["alias"] == alias:
                return False
            if (
                not previous
                and conn.execute(
                    "SELECT COUNT(*) FROM person_aliases WHERE user_id=?", (user_id,)
                ).fetchone()[0]
                >= 8
            ):
                raise ValueError("同一个 QQ 最多保存 8 个别名，请先删除不用的别名")
            conn.execute(
                "INSERT INTO person_aliases(user_id,alias_key,alias,updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(user_id,alias_key) DO UPDATE SET alias=excluded.alias,updated_at=excluded.updated_at",
                (user_id, key, alias, time.time()),
            )
            self.audit(actor_id, "add_person_alias", str(user_id), {"alias": alias}, conn=conn)
        self._invalidate_runtime_cache()
        return True

    def remove_person_alias(self, user_id: int, alias: str | None = None, *, actor_id: int) -> int:
        user_id = _user_id(user_id)
        key = normalize_person_alias(alias).casefold() if alias is not None else None
        with self._connect() as conn:
            query, params = "DELETE FROM person_aliases WHERE user_id=?", [user_id]
            if key is not None:
                query += " AND alias_key=?"
                params.append(key)
            count = conn.execute(query, params).rowcount
            if count:
                self.audit(
                    actor_id, "remove_person_alias", str(user_id), {"alias": alias}, conn=conn
                )
        self._invalidate_runtime_cache()
        return count


@dataclass(frozen=True)
class PersonReferences:
    user_ids: tuple[int, ...] = ()
    ambiguous_aliases: tuple[str, ...] = ()


def person_alias_records(store) -> list[dict]:
    return store.list_person_aliases() if hasattr(store, "list_person_aliases") else []


def matched_person_aliases(records: list[dict], text: str) -> list[dict]:
    """Prefer longest overlapping names; English names require ASCII word boundaries."""
    by_key: dict[str, dict] = {}
    for record in records:
        key = record["alias"].casefold()
        entry = by_key.setdefault(key, {"alias": record["alias"], "user_ids": []})
        if record["user_id"] not in entry["user_ids"]:
            entry["user_ids"].append(record["user_id"])
    if not by_key:
        return []
    patterns = []
    for key in sorted(by_key, key=len, reverse=True):
        pattern = re.escape(key)
        if re.match(r"[a-z0-9_]", key):
            pattern = r"(?<![a-z0-9_])" + pattern
        if re.search(r"[a-z0-9_]$", key):
            pattern += r"(?![a-z0-9_])"
        patterns.append(pattern)
    normalized = unicodedata.normalize("NFKC", text).casefold()
    found = dict.fromkeys(m.group() for m in re.finditer("|".join(patterns), normalized))
    return [by_key[key] for key in found]


def resolve_person_references(store, text: str, *, self_id: int | str = 0) -> PersonReferences:
    records = person_alias_records(store)
    users, ambiguous = [], []
    for entry in matched_person_aliases(records, text):
        if len(entry["user_ids"]) != 1:
            ambiguous.append(entry["alias"])
        else:
            users.extend(entry["user_ids"])
    # An explicit QQ can also refer to a configured person without an @ segment.
    for record in records:
        if re.search(r"(?<![0-9])" + str(record["user_id"]) + r"(?![0-9])", text):
            users.append(record["user_id"])
    return PersonReferences(
        tuple(dict.fromkeys(user for user in users if str(user) != str(self_id))),
        tuple(dict.fromkeys(ambiguous)),
    )
