from __future__ import annotations

import argparse
import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from qq_personal_bot.core.store import PolicyStore


def backup_database(db_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = backup_dir / f"memory-commands-{stamp}.sqlite3"
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    with (
        closing(sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)) as source,
        closing(sqlite3.connect(target)) as destination,
    ):
        source.backup(destination)
        if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("database backup failed verification")
    return target


def run_cleanup(
    db_path: Path, *, bot_ids: list[int], group_ids: list[int] | None = None,
    apply: bool = False, backup_dir: Path | None = None,
) -> dict:
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    if not bot_ids or any(bot_id <= 0 for bot_id in bot_ids):
        raise ValueError("at least one positive bot ID is required")
    store = PolicyStore(db_path)
    if group_ids is None:
        with store._connect() as conn:
            group_ids = [r[0] for r in conn.execute("SELECT DISTINCT group_id FROM memory_messages")]
    backup = backup_database(db_path, backup_dir or db_path.parent.parent / "sync-backups") if apply else None
    groups = [store.purge_memory_commands(group_id, bot_ids=bot_ids, dry_run=not apply)
              for group_id in group_ids]
    with store._connect() as conn:
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("database contains invalid foreign keys")
    return {"applied": apply, "backup": str(backup) if backup else None, "groups": groups}


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview or purge bot commands from person graphs")
    parser.add_argument("--db", type=Path, default=Path("data/qqbot.sqlite3"))
    parser.add_argument("--bot-id", type=int, action="append", required=True)
    parser.add_argument("--group", type=int, action="append")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    result = run_cleanup(args.db, bot_ids=args.bot_id, group_ids=args.group,
                         apply=args.apply, backup_dir=args.backup_dir)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
