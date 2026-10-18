from __future__ import annotations

import json
import uuid


class TurtleSoupStore:
    """Game state is independent of the ordinary, bounded AI chat history."""

    def _create_turtle_soup_schema(self, conn) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS turtle_soup_games (
                id TEXT PRIMARY KEY, group_id INTEGER NOT NULL, bot_id TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'starting',
                puzzle_json TEXT NOT NULL DEFAULT '{}', turns_json TEXT NOT NULL DEFAULT '[]',
                started_at REAL NOT NULL, deadline REAL, ended_at REAL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_turtle_soup_active_group
                ON turtle_soup_games(group_id) WHERE state IN ('starting', 'playing');
        """)

    @staticmethod
    def _turtle_soup_game(row):
        if row is None:
            return None
        result = dict(row)
        result['puzzle'] = json.loads(result.pop('puzzle_json'))
        result['turns'] = json.loads(result.pop('turns_json'))
        return result

    def get_turtle_soup_game(self, group_id: int):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM turtle_soup_games WHERE group_id=? "
                "AND state IN ('starting','playing')", (int(group_id),),
            ).fetchone()
        return self._turtle_soup_game(row)

    def active_turtle_soup_games(self):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM turtle_soup_games WHERE state IN ('starting','playing')"
            ).fetchall()
        return [self._turtle_soup_game(row) for row in rows]

    def create_turtle_soup_game(self, group_id: int, bot_id: str, *, now: float):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT OR IGNORE INTO turtle_soup_games(id,group_id,bot_id,started_at) "
                "VALUES (?,?,?,?)", (uuid.uuid4().hex, int(group_id), str(bot_id), now),
            )
            row = conn.execute(
                "SELECT * FROM turtle_soup_games WHERE group_id=? "
                "AND state IN ('starting','playing')", (int(group_id),),
            ).fetchone()
        return self._turtle_soup_game(row)

    def update_turtle_soup_game(self, game_id: str, *, puzzle=None, turns=None,
                               deadline: float | None = None) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE turtle_soup_games SET deadline=?, "
                "puzzle_json=COALESCE(?,puzzle_json), turns_json=COALESCE(?,turns_json), "
                "state=CASE WHEN ? THEN 'playing' ELSE state END "
                "WHERE id=? AND state IN ('starting','playing')",
                (deadline, json.dumps(puzzle, ensure_ascii=False) if puzzle is not None else None,
                 json.dumps(turns, ensure_ascii=False) if turns is not None else None,
                 puzzle is not None, game_id),
            )
        return bool(cursor.rowcount)

    def end_turtle_soup_game(self, game_id: str, reason: str, *, now: float) -> bool:
        if reason not in {'finished', 'cancelled', 'expired', 'failed'}:
            raise ValueError('invalid game end reason')
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM turtle_soup_games WHERE id=? "
                "AND state IN ('starting','playing')", (game_id,),
            ).fetchone()
            if row is None:
                return False
            conn.execute("UPDATE turtle_soup_games SET state=?,ended_at=?,deadline=NULL WHERE id=?",
                         (reason, now, game_id))
            # Freeze the ordinary conversation's idle age for the duration of the game.
            duration = max(0, now - row['started_at'])
            for table in ('dsapi_chat_history', 'dsapi_group_context'):
                conn.execute(
                    f"UPDATE {table} SET created_at=created_at+? "
                    "WHERE group_id=? AND created_at<=?",
                    (duration, row['group_id'], row['started_at']),
                )
        return True
