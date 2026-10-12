from __future__ import annotations

import base64
import importlib
import json
import sqlite3
import zipfile
from io import BytesIO
from types import SimpleNamespace

import nonebot
import pytest
from nonebot.exception import FinishedException
from PIL import Image, ImageDraw

from qq_personal_bot.core.policy import PolicyEngine
from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.help_card import build_help_sections
from qq_personal_bot.mhwa.calculate import calculate_target, game_round
from qq_personal_bot.mhwa.render import MAX_HEIGHT, Table, quest_tables, render_cards
from qq_personal_bot.mhwa.service import MhwaService
from qq_personal_bot.mhwa.store import DEFAULT_SEED_PATH, MhwaQueryError, MhwaStore
from qq_personal_bot.settings import AppSettings
from scripts.build_mhwa_seed import read_workbook

nonebot.init()
plugin = importlib.import_module("qq_personal_bot.plugins.mhwa")


@pytest.fixture
def archive(tmp_path):
    store = MhwaStore(tmp_path / "mhwa.sqlite3")
    store.initialize()
    return store


def test_seed_import_relations_and_idempotence(archive):
    second = MhwaStore(archive.path)
    second.initialize()
    with sqlite3.connect(archive.path) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        expected = {
            "monsters": 35,
            "quests": 601,
            "quest_monsters": 672,
            "quest_monster_stats": 672,
            "hitzones": 656,
            "part_thresholds": 492,
            "skills": 155,
            "skill_levels": 410,
            "skill_effects": 211,
        }
        for table, count in expected.items():
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == count
    assert second.metadata == archive.metadata


def test_failed_import_keeps_existing_database(archive, tmp_path):
    seed = json.loads(DEFAULT_SEED_PATH.read_text(encoding="utf-8"))
    seed["difficulty_profiles"] = []
    bad_seed = tmp_path / "bad.json"
    bad_seed.write_text(json.dumps(seed), encoding="utf-8")
    with pytest.raises(sqlite3.IntegrityError):
        MhwaStore(archive.path, bad_seed).initialize()
    assert archive.get_quest("5851")["targets"][0]["hp"] == 79923


def test_weakness_exploit_returns_every_level_and_both_effects(archive):
    skill = archive.get_skill("弱点特效")
    assert skill["max_level"] == 5
    assert [r["level"] for r in skill["levels"]] == [1, 2, 3, 4, 5]
    assert "会心率+30%" in skill["levels"][-1]["description"]
    assert "再+20%" in skill["levels"][-1]["description"]
    assert [(r["level"], r["condition"], r["value"]) for r in skill["effects"]] == [
        (1, "weak_spot", 5),
        (1, "wound", 3),
        (2, "weak_spot", 10),
        (2, "wound", 5),
        (3, "weak_spot", 15),
        (3, "wound", 10),
        (4, "weak_spot", 20),
        (4, "wound", 15),
        (5, "weak_spot", 30),
        (5, "wound", 20),
    ]


def test_solo_arch_tempered_arkveld_full_data(archive):
    quest = archive.get_quest("走出孤独,名为自由")
    assert quest["id"] == "5851"
    data = quest["targets"][0]
    assert data["monster"]["name"] == "锁刃龙"
    assert data["target"]["legendary"] == 2
    assert (data["hp"], data["hp_min"], data["hp_max"]) == (79923, 76726, 83120)
    assert data["parts"][0]["values"] == [11050, 11050, 1546, 845, 1105, 2]
    assert data["legendary_parts"][0]["values"] == [8936, 1437, 1]
    assert data["blowouts"][0]["values"] == [2348, 3177, 4973, 6906]
    assert all(p["raw_part"] != "HIDE" for p in data["parts"])
    assert all(p["raw_part"] != "Left Chainblade" for p in data["legendary_parts"])
    head = next(h for h in data["hitzones"] if h["part"] == "头部" and h["state"] == "普通")
    assert head["group"] == "历战王"
    assert head["values"] == [45, 48, 45, 10, 10, 10, 10, 10, 100]
    state = next(h for h in data["hitzones"] if "属性吸收" in h["state"])
    assert state["values"][3:8] == [0, 0, 0, 0, 0]
    tables = quest_tables(quest)
    for title in ("肉质", "伤口肉质", "部位硬直", "历战伤口", "炸膛", "异常状态"):
        assert any(title in table.title for table in tables)


def test_four_player_calculation_matches_reference_screenshot(archive):
    data = archive.get_quest("5851")["targets"][0]
    seed = json.loads(DEFAULT_SEED_PATH.read_text(encoding="utf-8"))
    rate = next(
        r
        for r in seed["difficulty_profiles"]
        if r["难度倍率编号"] == data["target"]["difficulty_id"]
    )
    multi = next(
        r
        for r in seed["multiplayer_profiles"]
        if r["多人倍率表编号"] == rate["多人倍率表编号"] and r["人数"] == 4
    )
    scaled = calculate_target(
        data["monster"],
        data["target"],
        rate,
        seed["random_health_rates"],
        players=4,
        multiplayer=multi,
    )
    assert scaled["parts"][0]["values"] == [38675, 38675, 6182, 3381, 3039, 2]
    assert scaled["legendary_parts"][0]["values"] == [35742, 3950, 1]
    assert scaled["blowouts"][0]["values"] == [8218, 11119, 17404, 24172]
    assert next(r for r in scaled["conditions"] if r["label"] == "初始耐性")["values"] == [
        180,
        300,
        360,
        84,
        264,
        270,
        188,
        1,
        600,
        600,
    ]
    assert next(r for r in scaled["conditions"] if r["label"] == "耐性上限")["values"] == [
        3960,
        10800,
        15840,
        4824,
        5940,
        6480,
        5731,
        601,
        17388,
        39848,
    ]


def test_fixed_and_variable_hp_multiple_targets_and_colliding_ordinals(archive):
    quest = archive.get_quest("沙海原的沙海龙")
    assert [t["hp"] for t in quest["targets"]] == [4400, 5148]
    assert all(t["hp_min"] == t["hp_max"] for t in quest["targets"])
    harvest = archive.get_quest("1935")
    assert [t["target"]["ordinal"] for t in harvest["targets"]] == [1, 2, 3]
    assert [t["hp"] for t in harvest["targets"]] == [11340] * 3
    assert len(quest_tables(harvest)[0].rows) == 3
    event = archive.get_quest("3350")
    assert len(event["targets"]) == 3
    assert [t["target"]["ordinal"] for t in event["targets"]] == [1, 1, 1]
    assert len({t["monster"]["id"] for t in event["targets"]}) == 3
    assert game_round(0.5) == 1
    assert game_round(188.5) == 189


def test_ambiguous_and_missing_queries_are_explicit(archive):
    with pytest.raises(MhwaQueryError, match="找到多个任务") as exc:
        archive.get_quest("缠蛙 调查任务")
    assert "个体强度" in str(exc.value)
    with pytest.raises(MhwaQueryError, match="没有找到技能"):
        archive.get_skill("不存在的技能")
    with pytest.raises(MhwaQueryError, match="请输入"):
        archive.get_skill("%_'")


def test_card_pagination_keeps_every_row_and_cache(tmp_path, monkeypatch):
    captured = []
    original = ImageDraw.ImageDraw.text

    def track(self, xy, text, *args, **kwargs):
        captured.append(text)
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", track)
    settings = AppSettings(db_path=tmp_path / "policy.sqlite3", admins=())
    rows = [[f"目标{i:03}", "资料" * 20] for i in range(100)]
    kwargs = {
        "title": "分页数据",
        "subtitle": "单人",
        "tables": [Table("完整表格", ["目标", "说明"], rows)],
        "metadata": {"seed_hash": "test", "data_version": 7, "saved_date": "2026-10-12"},
        "identity": "pagination",
    }
    paths = render_cards(settings, tmp_path / "cards", **kwargs)
    assert len(paths) > 1
    for i in range(100):
        assert captured.count(f"目标{i:03}") == 1
    for path in paths:
        with Image.open(path) as image:
            assert image.width == 1440
            assert image.height <= MAX_HEIGHT
            image.verify()
    captured.clear()
    assert render_cards(settings, tmp_path / "cards", **kwargs) == paths
    assert captured == []


def test_workbook_reader_handles_shared_text_sparse_rows_and_iso_dates(tmp_path):
    path = tmp_path / "sample.xlsx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="例子" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>日期</t></si></sst>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            '<row r="6"><c r="A6" t="s"><v>0</v></c><c r="C6" t="inlineStr"><is><t>数值</t></is></c></row>'
            '<row r="7"><c r="A7" t="d"><v>2026-10-12T00:00:00Z</v></c><c r="C7"><v>0</v></c></row>'
            "</sheetData></worksheet>",
        )
    assert read_workbook(path) == {"例子": [{"日期": "2026-10-12T00:00:00Z", "数值": 0}]}


class FakeMatcher:
    def __init__(self):
        self.messages = []

    async def finish(self, message):
        self.messages.append(message)
        raise FinishedException


@pytest.fixture
def command_environment(tmp_path, monkeypatch):
    settings = AppSettings(
        db_path=tmp_path / "policy.sqlite3", admins=(), per_group_seconds=0, per_user_per_minute=0
    )
    policy = PolicyStore(settings.db_path)
    policy.initialize(settings)
    policy.set_group_enabled(123, True, actor_id=0)
    service = MhwaService(settings)
    monkeypatch.setattr(plugin, "get_store", lambda: policy)
    monkeypatch.setattr(plugin, "get_policy_engine", lambda: PolicyEngine(policy))
    monkeypatch.setattr(plugin, "get_mhwa_service", lambda: service)
    return settings, policy, service


@pytest.mark.asyncio
@pytest.mark.parametrize("text,count", [("技能 弱点特效", 1), ("任务 走出孤独，名为自由", 2)])
async def test_command_sends_real_png_bytes(command_environment, text, count):
    matcher = FakeMatcher()
    with pytest.raises(FinishedException):
        await plugin._handle_mhwa(matcher, SimpleNamespace(group_id=123, user_id=10), text)
    segments = matcher.messages[0]
    assert len(segments) == count
    for segment in segments:
        assert segment.type == "image"
        assert segment.data["file"].startswith("base64://")
        body = base64.b64decode(segment.data["file"][9:])
        with Image.open(BytesIO(body)) as image:
            assert image.format == "PNG"
            assert image.width == 1440
            assert image.height <= MAX_HEIGHT
    assert next(iter(plugin.mhwa.rule.checkers)).call.cmds == (("荒野",),)


@pytest.mark.asyncio
async def test_command_respects_group_policy_and_feature_flags(command_environment):
    settings, policy, _ = command_environment
    matcher = FakeMatcher()
    for group in (None, 999):
        await plugin._handle_mhwa(
            matcher, SimpleNamespace(group_id=group, user_id=10), "技能 弱点特效"
        )
    policy.set_feature_enabled("mhwa.skills", False)
    await plugin._handle_mhwa(matcher, SimpleNamespace(group_id=123, user_id=10), "技能 弱点特效")
    policy.set_feature_enabled("mhwa.master", False)
    await plugin._handle_mhwa(matcher, SimpleNamespace(group_id=123, user_id=10), "任务 5851")
    assert matcher.messages == []
    text = "\n".join(
        item
        for section in build_help_sections(settings, policy, is_admin=False)
        for item in section.items
    )
    assert "/荒野" not in text


@pytest.mark.asyncio
async def test_command_reports_usage_and_unknown_query(command_environment):
    for text, expected in [("技能", "用法"), ("任务 不存在的任务", "没有找到任务")]:
        matcher = FakeMatcher()
        with pytest.raises(FinishedException):
            await plugin._handle_mhwa(matcher, SimpleNamespace(group_id=123, user_id=10), text)
        if expected == "用法":
            assert "/荒野 技能" in matcher.messages[0]
        else:
            assert expected in matcher.messages[0]
