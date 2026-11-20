from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Chat:
    bot_id: int
    user_id: int
    group_id: int | None = None
    temporary_group: int | None = None

    @property
    def owner(self) -> tuple[int, int]:
        return self.bot_id, self.user_id


@dataclass(frozen=True)
class Binding:
    bot_id: int
    user_id: int
    cred: str = field(repr=False)
    uid: str
    nickname: str
    channel: str
    season: str
    friend: bool
    push: bool
    push_group: int | None
    since: int
    revision: int
    last_push: int = 0

    @property
    def destination(self) -> Chat:
        return Chat(self.bot_id, self.user_id, self.push_group)


class WeizhuStore:
    def __init__(self, directory: Path):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / "bindings.sqlite3"
        self.path.touch(mode=0o600, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS bindings (
                    bot_id INTEGER, user_id INTEGER, cred TEXT NOT NULL, uid TEXT NOT NULL,
                    nickname TEXT, channel TEXT, season TEXT, friend INTEGER, push INTEGER,
                    push_group INTEGER, since INTEGER, revision INTEGER, last_push INTEGER,
                    PRIMARY KEY (bot_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    bot_id INTEGER, destination TEXT, season TEXT, scene TEXT,
                    PRIMARY KEY (bot_id, destination, season, scene)
                );
            """)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.row_factory = sqlite3.Row
            yield conn

    def get(self, chat: Chat) -> Binding | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM bindings WHERE bot_id=? AND user_id=?", chat.owner
            ).fetchone()
        return Binding(**row) if row else None

    def list_users(self, bot_id: int) -> list[Binding]:
        with self.connect() as conn:
            return [
                Binding(**r)
                for r in conn.execute(
                    "SELECT * FROM bindings WHERE bot_id=? ORDER BY user_id", (bot_id,)
                )
            ]

    def subscriptions(self) -> list[Binding]:
        with self.connect() as conn:
            return [Binding(**r) for r in conn.execute("SELECT * FROM bindings WHERE push=1")]

    def directory(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT DISTINCT CAST(user_id AS TEXT) AS qq, nickname
                FROM bindings ORDER BY qq, nickname
            """)
            return [dict(row) for row in rows]

    def save(self, binding: Binding) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO bindings
                (bot_id,user_id,cred,uid,nickname,channel,season,friend,push,push_group,since,revision,last_push)
                VALUES (:bot_id,:user_id,:cred,:uid,:nickname,:channel,:season,:friend,:push,
                        :push_group,:since,:revision,:last_push)
            """,
                asdict(binding),
            )

    def remove(self, chat: Chat) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM bindings WHERE bot_id=? AND user_id=?", chat.owner)

    def delivered(self, key: tuple) -> bool:
        with self.connect() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM deliveries WHERE bot_id=? AND destination=? AND season=? AND scene=?",
                    key,
                ).fetchone()
                is not None
            )

    def record_delivery(self, key: tuple, bindings: list[Binding], timestamp: int) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO deliveries VALUES (?, ?, ?, ?)", key)
            conn.executemany(
                "UPDATE bindings SET last_push=? WHERE bot_id=? AND user_id=? AND revision=?",
                [(timestamp, b.bot_id, b.user_id, b.revision) for b in bindings],
            )
