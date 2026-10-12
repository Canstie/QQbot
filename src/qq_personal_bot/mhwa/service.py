from __future__ import annotations

import threading
from pathlib import Path

from qq_personal_bot.mhwa.render import quest_tables, render_cards, skill_tables
from qq_personal_bot.mhwa.store import MhwaQueryError, MhwaStore
from qq_personal_bot.settings import AppSettings


class MhwaService:
    def __init__(self, settings: AppSettings, store: MhwaStore | None = None):
        self.settings = settings
        self.store = store or MhwaStore(settings.db_path.with_name("mhwa.sqlite3"))
        self.output_dir = settings.db_path.parent / "mhwa_cache/cards"
        self._render_lock = threading.Lock()

    def query(self, command: str, query: str) -> list[Path]:
        with self._render_lock:
            if command == "技能":
                skill = self.store.get_skill(query)
                kind = {"EQUIP": "装备技能", "GROUP": "组合技能", "SERIES": "系列技能"}.get(
                    skill["kind"], skill["kind"]
                )
                return render_cards(
                    self.settings,
                    self.output_dir,
                    title=skill["name"],
                    subtitle=f"{kind} · 最高 Lv.{skill['max_level']} · 全部等级",
                    tables=skill_tables(skill),
                    metadata=self.store.metadata,
                    identity=f"skill:{skill['id']}",
                )
            if command == "任务":
                quest = self.store.get_quest(query)
                strength = (
                    f" · 个体强度{quest['strength']}" if quest["strength"] is not None else ""
                )
                return render_cards(
                    self.settings,
                    self.output_dir,
                    title=quest["name"],
                    subtitle=f"{quest['stars']}星{strength} · {quest['map']} · {quest['environment']} · 单人\n"
                    f"任务编号 {quest['id']} · {quest['condition']} · 目标记录 {len(quest['targets'])} 条",
                    tables=quest_tables(quest),
                    metadata=self.store.metadata,
                    identity=f"quest:{quest['id']}:solo",
                )
            raise MhwaQueryError("用法：/荒野 技能 <技能名称>\n/荒野 任务 <任务名称或编号>")
