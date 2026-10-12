from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import unicodedata
from pathlib import Path

from qq_personal_bot.mhwa.calculate import calculate_target

DEFAULT_SEED_PATH = Path(__file__).resolve().parents[3] / "tools/mhwa/seed.json"
_SCHEMA_VERSION = 1


def name_key(value: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class MhwaQueryError(ValueError):
    pass


class MhwaStore:
    def __init__(self, path: Path, seed_path: Path = DEFAULT_SEED_PATH):
        self.path = path
        self.seed_path = seed_path
        self._lock = threading.RLock()
        self._initialized = False
        self.metadata: dict = {}

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def initialize(self) -> None:
        with self._lock:
            if self._initialized:
                return
            body = self.seed_path.read_bytes()
            seed_hash = hashlib.sha256(body).hexdigest()
            seed = json.loads(body)
            if seed["format_version"] != 1:
                raise ValueError("不支持的荒野资料格式。")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = self._connect()
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS dataset_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS monsters (
                        id TEXT PRIMARY KEY, name TEXT NOT NULL, data_json TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS quests (
                        id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL,
                        stars INTEGER NOT NULL, data_json TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS idx_quests_name ON quests(name_key);
                    CREATE TABLE IF NOT EXISTS difficulty_profiles (
                        id TEXT PRIMARY KEY, data_json TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS multiplayer_profiles (
                        profile_id TEXT NOT NULL, players INTEGER NOT NULL, data_json TEXT NOT NULL,
                        PRIMARY KEY(profile_id, players));
                    CREATE TABLE IF NOT EXISTS quest_monsters (
                        quest_id TEXT REFERENCES quests(id), monster_id TEXT REFERENCES monsters(id),
                        ordinal INTEGER NOT NULL, legendary INTEGER NOT NULL,
                        health_adjust INTEGER NOT NULL, difficulty_id TEXT REFERENCES difficulty_profiles(id),
                        role_id INTEGER, PRIMARY KEY(quest_id, monster_id, ordinal));
                    CREATE INDEX IF NOT EXISTS idx_targets_monster ON quest_monsters(monster_id, quest_id);
                    CREATE TABLE IF NOT EXISTS quest_monster_stats (
                        quest_id TEXT, monster_id TEXT, ordinal INTEGER,
                        hp INTEGER NOT NULL, hp_min INTEGER NOT NULL, hp_max INTEGER NOT NULL,
                        PRIMARY KEY(quest_id, monster_id, ordinal),
                        FOREIGN KEY(quest_id, monster_id, ordinal)
                            REFERENCES quest_monsters(quest_id, monster_id, ordinal));
                    CREATE TABLE IF NOT EXISTS hitzones (
                        id INTEGER PRIMARY KEY, monster_id TEXT REFERENCES monsters(id),
                        part TEXT NOT NULL, hz_group TEXT NOT NULL, state TEXT NOT NULL,
                        slash REAL, blow REAL, shot REAL, fire REAL, water REAL,
                        thunder REAL, ice REAL, dragon REAL, stun REAL, data_json TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS idx_hitzones_monster ON hitzones(monster_id, hz_group, part);
                    CREATE TABLE IF NOT EXISTS part_thresholds (
                        id INTEGER PRIMARY KEY, monster_id TEXT REFERENCES monsters(id),
                        part TEXT NOT NULL, kind TEXT NOT NULL, stage INTEGER NOT NULL,
                        flinch REAL, break_hp REAL, normal_hp REAL, tear_hp REAL, wound_hp REAL,
                        wounds INTEGER, data_json TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS idx_parts_monster ON part_thresholds(monster_id);
                    CREATE TABLE IF NOT EXISTS conditions (
                        id INTEGER PRIMARY KEY, monster_id TEXT REFERENCES monsters(id), data_json TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS idx_conditions_monster ON conditions(monster_id);
                    CREATE TABLE IF NOT EXISTS blowouts (
                        id INTEGER PRIMARY KEY, monster_id TEXT REFERENCES monsters(id), data_json TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS idx_blowouts_monster ON blowouts(monster_id);
                    CREATE TABLE IF NOT EXISTS skills (
                        id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL, data_json TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(name_key);
                    CREATE TABLE IF NOT EXISTS skill_levels (
                        skill_id TEXT REFERENCES skills(id), level INTEGER NOT NULL,
                        description TEXT, special TEXT, PRIMARY KEY(skill_id, level));
                    CREATE TABLE IF NOT EXISTS skill_effects (
                        id INTEGER PRIMARY KEY, skill_id TEXT, level INTEGER, data_json TEXT NOT NULL,
                        FOREIGN KEY(skill_id, level) REFERENCES skill_levels(skill_id, level));
                """)
                meta = {
                    r["key"]: json.loads(r["value"])
                    for r in conn.execute("SELECT * FROM dataset_meta")
                }
                if (
                    meta.get("seed_hash") != seed_hash
                    or meta.get("schema_version") != _SCHEMA_VERSION
                ):
                    self._import(conn, seed)
                    meta = {
                        k: seed[k]
                        for k in (
                            "data_version",
                            "saved_date",
                            "source",
                            "workbook_sha256",
                            "random_health_rates",
                        )
                    }
                    meta["numeric_skill_groups"] = seed.get("numeric_skill_groups", {})
                    meta["numeric_skill_notes"] = seed.get("numeric_skill_notes", {})
                    meta.update(seed_hash=seed_hash, schema_version=_SCHEMA_VERSION)
                    conn.executemany(
                        "INSERT INTO dataset_meta VALUES (?, ?)",
                        [(k, encode(v)) for k, v in meta.items()],
                    )
                    if conn.execute("PRAGMA foreign_key_check").fetchone():
                        raise ValueError("荒野资料存在失效关联，导入已撤销。")
                    conn.commit()
                self.metadata = meta
                self._initialized = True
            except BaseException:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _import(self, conn: sqlite3.Connection, seed: dict) -> None:
        for table in (
            "dataset_meta",
            "quest_monster_stats",
            "quest_monsters",
            "hitzones",
            "part_thresholds",
            "conditions",
            "blowouts",
            "skill_effects",
            "skill_levels",
            "skills",
            "quests",
            "monsters",
            "difficulty_profiles",
            "multiplayer_profiles",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.executemany(
            "INSERT INTO monsters VALUES (?, ?, ?)",
            [(r["id"], r["name"], encode(r)) for r in seed["monsters"]],
        )
        conn.executemany(
            "INSERT INTO quests VALUES (?, ?, ?, ?, ?)",
            [
                (r["id"], r["name"], name_key(r["name"]), r["stars"], encode(r))
                for r in seed["quests"]
            ],
        )
        difficulties = {r["难度倍率编号"]: r for r in seed["difficulty_profiles"]}
        conn.executemany(
            "INSERT INTO difficulty_profiles VALUES (?, ?)",
            [(key, encode(row)) for key, row in difficulties.items()],
        )
        conn.executemany(
            "INSERT INTO multiplayer_profiles VALUES (?, ?, ?)",
            [(r["多人倍率表编号"], r["人数"], encode(r)) for r in seed["multiplayer_profiles"]],
        )
        conn.executemany(
            "INSERT INTO quest_monsters VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    r["quest_id"],
                    r["monster_id"],
                    r["ordinal"],
                    r["legendary"],
                    r["health_adjust"],
                    r["difficulty_id"],
                    r["role_id"],
                )
                for r in seed["quest_monsters"]
            ],
        )
        for index, row in enumerate(seed["hitzones"], 1):
            conn.execute(
                "INSERT INTO hitzones VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    index,
                    row["monster_id"],
                    row["part"],
                    row["group"],
                    row["state"],
                    *row["values"],
                    encode(row),
                ),
            )
        for index, row in enumerate(seed["part_thresholds"], 1):
            conn.execute(
                "INSERT INTO part_thresholds VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    index,
                    row["monster_id"],
                    row["part"],
                    row["kind"],
                    row["stage"],
                    *row["values"],
                    encode(row),
                ),
            )
        for table in ("conditions", "blowouts"):
            conn.executemany(
                f"INSERT INTO {table} VALUES (?, ?, ?)",
                [(i, r["monster_id"], encode(r)) for i, r in enumerate(seed[table], 1)],
            )
        conn.executemany(
            "INSERT INTO skills VALUES (?, ?, ?, ?)",
            [(r["id"], r["name"], name_key(r["name"]), encode(r)) for r in seed["skills"]],
        )
        conn.executemany(
            "INSERT INTO skill_levels VALUES (?, ?, ?, ?)",
            [
                (r["skill_id"], r["level"], r["description"], r["special"])
                for r in seed["skill_levels"]
            ],
        )
        conn.executemany(
            "INSERT INTO skill_effects VALUES (?, ?, ?, ?)",
            [
                (i, r["skill_id"], r["level"], encode(r))
                for i, r in enumerate(seed["skill_effects"], 1)
            ],
        )
        monsters = {r["id"]: dict(r) for r in seed["monsters"]}
        for monster in monsters.values():
            for table, key in (
                ("hitzones", "hitzones"),
                ("part_thresholds", "parts"),
                ("conditions", "conditions"),
                ("blowouts", "blowouts"),
            ):
                monster[key] = [r for r in seed[table] if r["monster_id"] == monster["id"]]
        for target in seed["quest_monsters"]:
            calculated = calculate_target(
                monsters[target["monster_id"]],
                target,
                difficulties[target["difficulty_id"]],
                seed["random_health_rates"],
            )
            conn.execute(
                "INSERT INTO quest_monster_stats VALUES (?, ?, ?, ?, ?, ?)",
                (
                    target["quest_id"],
                    target["monster_id"],
                    target["ordinal"],
                    calculated["hp"],
                    calculated["hp_min"],
                    calculated["hp_max"],
                ),
            )

    def _find(self, table: str, query: str) -> dict:
        self.initialize()
        key = name_key(query)
        if not key:
            raise MhwaQueryError("请输入技能名称或任务名称。")
        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT * FROM {table} WHERE id=? OR name_key=?", (query.strip(), key)
            ).fetchall()
            if not rows:
                rows = conn.execute(
                    f"SELECT * FROM {table} WHERE name_key LIKE ? ORDER BY name, id LIMIT 13",
                    (f"%{key}%",),
                ).fetchall()
        finally:
            conn.close()
        noun = "技能" if table == "skills" else "任务"
        if not rows:
            raise MhwaQueryError(f"没有找到{noun}：{query}")
        if len(rows) > 1:
            lines = [f"找到多个{noun}，请用编号查询："]
            for row in rows[:12]:
                data = json.loads(row["data_json"])
                detail = ""
                if table == "quests":
                    strength = (
                        f" · 个体强度{data['strength']}" if data["strength"] is not None else ""
                    )
                    detail = f" · {data['stars']}星{strength} · {data['map']}"
                lines.append(f"{row['id']} · {row['name']}{detail}")
            if len(rows) > 12:
                lines.append("还有更多结果，请输入更完整的名称。")
            raise MhwaQueryError("\n".join(lines))
        return json.loads(rows[0]["data_json"])

    def get_skill(self, query: str) -> dict:
        skill = self._find("skills", query)
        skill["numeric_groups"] = self.metadata.get("numeric_skill_groups", {}).get(skill["id"], [])
        skill["numeric_notes"] = self.metadata.get("numeric_skill_notes", {}).get(skill["id"], [])
        conn = self._connect()
        try:
            skill["levels"] = [
                dict(r)
                for r in conn.execute(
                    "SELECT level, description, special FROM skill_levels WHERE skill_id=? ORDER BY level",
                    (skill["id"],),
                )
            ]
            skill["effects"] = [
                json.loads(r[0])
                for r in conn.execute(
                    "SELECT data_json FROM skill_effects WHERE skill_id=? ORDER BY level, id",
                    (skill["id"],),
                )
            ]
        finally:
            conn.close()
        return skill

    def get_quest(self, query: str) -> dict:
        quest = self._find("quests", query)
        conn = self._connect()
        try:
            targets = []
            monster_cache = {}
            rows = conn.execute(
                "SELECT * FROM quest_monsters WHERE quest_id=? ORDER BY rowid", (quest["id"],)
            )
            for row in rows.fetchall():
                target = dict(row)
                mid = target["monster_id"]
                if mid not in monster_cache:
                    monster = json.loads(
                        conn.execute(
                            "SELECT data_json FROM monsters WHERE id=?", (mid,)
                        ).fetchone()[0]
                    )
                    for table, key in (
                        ("hitzones", "hitzones"),
                        ("part_thresholds", "parts"),
                        ("conditions", "conditions"),
                        ("blowouts", "blowouts"),
                    ):
                        monster[key] = [
                            json.loads(r[0])
                            for r in conn.execute(
                                f"SELECT data_json FROM {table} WHERE monster_id=? ORDER BY id",
                                (mid,),
                            )
                        ]
                    monster_cache[mid] = monster
                difficulty = json.loads(
                    conn.execute(
                        "SELECT data_json FROM difficulty_profiles WHERE id=?",
                        (target["difficulty_id"],),
                    ).fetchone()[0]
                )
                data = calculate_target(
                    monster_cache[mid], target, difficulty, self.metadata["random_health_rates"]
                )
                stats = conn.execute(
                    "SELECT hp, hp_min, hp_max FROM quest_monster_stats "
                    "WHERE quest_id=? AND monster_id=? AND ordinal=?",
                    (quest["id"], mid, target["ordinal"]),
                ).fetchone()
                data.update(dict(stats))
                targets.append(data)
            quest["targets"] = targets
        finally:
            conn.close()
        return quest
