from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.settings import AppSettings
from tools.qqbot_launcher import DEFAULT_LOG_RETENTION_DAYS, cleanup_logs


GIF_1PX = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00"
    b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
    b"\x00\x02\x02D\x01\x00;"
)


def _write_seed(path: Path, *records: dict[str, object]) -> None:
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )


def _china_timestamp(
    year: int,
    month: int,
    day: int,
    hour: int,
    minute: int = 0,
) -> float:
    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        tzinfo=timezone(timedelta(hours=8)),
    ).timestamp()


def test_store_persists_policy_state(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    settings = AppSettings(
        db_path=db_path,
        admins=(10000,),
        policy_mode="allowlist",
        trigger_prefixes=("~",),
    )
    store = PolicyStore(db_path)
    store.initialize(settings)

    store.set_group_enabled(123, True, actor_id=10000)
    store.set_mode("blocklist", actor_id=10000)
    store.add_prefix("!", actor_id=10000)

    reopened = PolicyStore(db_path)
    reopened.initialize(settings)

    assert reopened.get_mode() == "blocklist"
    assert reopened.is_group_enabled(123)
    assert reopened.is_admin(10000)
    assert "!" in reopened.prefixes()


def test_download_image_index_deduplicates_lists_and_summarizes(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=(10000,)))
    first_hash = "a" * 64
    second_hash = "b" * 64

    first, created = store.record_download_image(
        sha256=first_hash,
        object_key=f"20260816/{first_hash}.jpg",
        content_type="image/jpeg",
        size_bytes=1024,
        downloaded_date="20260816",
    )
    duplicate, duplicate_created = store.record_download_image(
        sha256=first_hash,
        object_key=f"20260817/{first_hash}.jpg",
        content_type="image/jpeg",
        size_bytes=1024,
        downloaded_date="20260817",
    )
    second, second_created = store.record_download_image(
        sha256=second_hash,
        object_key=f"20260817/{second_hash}.png",
        content_type="image/png",
        size_bytes=2048,
        downloaded_date="20260817",
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate["id"] == first["id"]
    assert second_created is True
    assert store.download_image_overview("20260817") == {
        "total": 2,
        "total_bytes": 3072,
        "today": "20260817",
        "today_count": 1,
    }

    listing = store.list_download_images(downloaded_date="20260817", limit=1)
    assert listing["total"] == 1
    assert listing["images"][0]["id"] == second["id"]
    assert listing["dates"] == [
        {"date": "20260817", "count": 1},
        {"date": "20260816", "count": 1},
    ]
    assert store.delete_download_image(first["id"]) is True
    assert store.get_download_image(first["id"]) is None


def test_classic_image_index_deduplicates_within_each_group(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=(10000,)))
    digest = "d" * 64

    first, created = store.record_classic_image(
        group_id=123,
        sha256=digest,
        object_key=f"{digest}.gif",
        content_type="image/gif",
        size_bytes=100,
        created_at=10,
    )
    duplicate, duplicate_created = store.record_classic_image(
        group_id=123,
        sha256=digest,
        object_key=f"{digest}.gif",
        content_type="image/gif",
        size_bytes=100,
        created_at=20,
    )
    other_group, other_created = store.record_classic_image(
        group_id=456,
        sha256=digest,
        object_key=f"{digest}.gif",
        content_type="image/gif",
        size_bytes=100,
        created_at=30,
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate["id"] == first["id"]
    assert other_created is True
    assert store.get_classic_image_by_hash(123, digest)["id"] == first["id"]
    assert store.pick_classic_image(123, 99)["id"] == first["id"]
    assert store.list_classic_groups() == [
        {
            "group_id": 456,
            "count": 1,
            "total_bytes": 100,
            "updated_at": 30.0,
            "cover_id": other_group["id"],
        },
        {
            "group_id": 123,
            "count": 1,
            "total_bytes": 100,
            "updated_at": 10.0,
            "cover_id": first["id"],
        },
    ]
    assert store.delete_classic_group(123) == 1
    assert store.list_classic_images(123) == []


def test_snapshot_shape(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=(10000,)))

    snapshot = store.snapshot()

    assert snapshot["mode"] == "allowlist"
    assert snapshot["admins"] == [10000]
    assert snapshot["trigger"]["mention"] is True
    assert "~" in snapshot["trigger"]["prefixes"]
    assert snapshot["trigger"]["direct_trigger_percent"] == 10.0


def test_direct_trigger_percent_is_clamped_and_persisted(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=(10000,)))

    store.set_direct_trigger_percent(25, actor_id=10000)

    reopened = PolicyStore(db_path)
    reopened.initialize(AppSettings(db_path=db_path, admins=(10000,)))

    assert reopened.get_direct_trigger_percent() == 25.0


def test_core_config_replaces_policy_snapshot(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=(10000,)))

    store.set_core_config(
        mode="blocklist",
        enabled_groups=[123, 123, 456],
        blocked_groups=[789],
        admins=[20000],
        trigger_mention=False,
        prefixes=["!", "!", "/"],
        direct_trigger_percent=35,
        per_group_seconds=1.5,
        per_user_per_minute=9,
        actor_id=10000,
    )

    snapshot = store.snapshot()

    assert snapshot["mode"] == "blocklist"
    assert snapshot["enabled_groups"] == [123, 456]
    assert snapshot["blocked_groups"] == [789]
    assert snapshot["admins"] == [20000]
    assert snapshot["trigger"] == {
        "mention": False,
        "prefixes": ["!", "/"],
        "direct_trigger_percent": 35.0,
    }
    assert snapshot["limits"] == {"per_group_seconds": 1.5, "per_user_per_minute": 9}


def test_bootstrap_admin_is_not_restored_after_web_config_removes_it(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    settings = AppSettings(db_path=db_path, admins=(10000,))
    store = PolicyStore(db_path)
    store.initialize(settings)
    snapshot = store.snapshot()

    store.set_core_config(
        mode=snapshot["mode"],
        enabled_groups=snapshot["enabled_groups"],
        blocked_groups=snapshot["blocked_groups"],
        admins=[],
        trigger_mention=snapshot["trigger"]["mention"],
        prefixes=snapshot["trigger"]["prefixes"],
        direct_trigger_percent=snapshot["trigger"]["direct_trigger_percent"],
        per_group_seconds=snapshot["limits"]["per_group_seconds"],
        per_user_per_minute=snapshot["limits"]["per_user_per_minute"],
        actor_id=0,
    )

    reopened = PolicyStore(db_path)
    reopened.initialize(settings)

    assert reopened.admins() == []


def test_empty_prefixes_default_to_tilde(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=(10000,)))

    store.set_prefixes([], actor_id=10000)

    assert store.prefixes() == ["~"]


def test_dsapi_config_and_history_are_group_scoped_and_pruned(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=()))

    config = store.set_dsapi_config(
        enabled=False,
        knowledge_enabled=True,
        knowledge_prompt="角色设定",
        history_turns=2,
        enabled_groups=[123, 123, 456],
        clear_history=False,
        actor_id=10000,
        random_reply_percent=7.5,
        random_sticker_percent=35,
    )
    assert config["enabled"] is False
    assert config["enabled_groups"] == [123, 456]
    assert config["knowledge_prompt"] == "角色设定"
    assert config["random_reply_percent"] == 7.5
    assert config["random_sticker_percent"] == 35

    for index in range(3):
        store.record_dsapi_exchange(
            group_id=123,
            user_content=f"问{index}",
            assistant_content=f"答{index}",
            history_turns=2,
        )
    store.record_dsapi_exchange(
        group_id=456,
        user_content="另一个群",
        assistant_content="单独上下文",
        history_turns=2,
    )

    assert store.get_dsapi_chat_history(123, 2) == [
        {"role": "user", "content": "问1"},
        {"role": "assistant", "content": "答1"},
        {"role": "user", "content": "问2"},
        {"role": "assistant", "content": "答2"},
    ]
    assert store.get_dsapi_chat_history(456, 2)[0]["content"] == "另一个群"
    assert store.get_dsapi_config()["history_messages"] == 6
    assert store.clear_dsapi_chat_history(actor_id=10000) == 6
    assert store.get_dsapi_config()["history_messages"] == 0


def test_dsapi_legacy_prompt_is_migrated_to_default_knowledge_base(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    settings = AppSettings(db_path=db_path, admins=())
    store = PolicyStore(db_path)
    store.initialize(settings)
    with store._connect() as conn:
        store.set_setting("dsapi_knowledge_prompt", "旧角色设定", conn=conn)
        store.set_setting("dsapi_active_knowledge_id", "", conn=conn)
        conn.execute("DELETE FROM dsapi_knowledge_bases")

    reopened = PolicyStore(db_path)
    reopened.initialize(settings)
    config = reopened.get_dsapi_config()

    assert config["knowledge_prompt"] == "旧角色设定"
    assert config["active_knowledge_name"] == "默认知识库"
    assert config["knowledge_bases"] == [
        {
            **config["knowledge_bases"][0],
            "name": "默认知识库",
            "prompt": "旧角色设定",
            "prompt_chars": 5,
            "active": True,
        }
    ]


def test_dsapi_existing_knowledge_table_gains_runtime_configuration(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO settings(key, value) VALUES ('dsapi_knowledge_prompt', '旧知识')"
        )
        conn.execute(
            """
            CREATE TABLE dsapi_knowledge_bases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                prompt TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO dsapi_knowledge_bases(name, prompt, created_at, updated_at)
            VALUES ('旧知识库', '旧知识', 1, 1)
            """
        )

    settings = AppSettings(
        db_path=db_path,
        admins=(),
        dsapi_model="deepseek-migrated",
        dsapi_max_tokens=96,
    )
    store = PolicyStore(db_path)
    store.initialize(settings)
    knowledge = store.get_dsapi_config()["active_knowledge"]

    assert knowledge["name"] == "旧知识库"
    assert knowledge["model"] == "deepseek-migrated"
    assert knowledge["thinking_enabled"] is False
    assert knowledge["max_tokens"] == 96
    assert knowledge["temperature"] is None


def test_dsapi_knowledge_bases_can_be_edited_switched_and_deleted(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=()))
    first = store.create_dsapi_knowledge_base(name="果果", prompt="呆呆的", actor_id=1)
    second = store.create_dsapi_knowledge_base(
        name="档案员",
        prompt="只说事实",
        actor_id=1,
        model="deepseek-reasoner",
        thinking_enabled=True,
        max_tokens=512,
        temperature=0.3,
    )
    store.record_dsapi_exchange(
        group_id=123,
        user_content="问题",
        assistant_content="回答",
        history_turns=2,
    )

    switched = store.activate_dsapi_knowledge_base(
        second["id"],
        clear_history=True,
        actor_id=1,
    )
    updated = store.update_dsapi_knowledge_base(
        second["id"],
        name="群档案员",
        prompt="只依据群档案回答",
        actor_id=1,
        model="deepseek-reasoner-v2",
        thinking_enabled=True,
        max_tokens=640,
        temperature=0.2,
    )
    config = store.get_dsapi_config()

    assert switched["history_messages_cleared"] == 2
    assert updated["name"] == "群档案员"
    assert config["active_knowledge_id"] == second["id"]
    assert config["knowledge_prompt"] == "只依据群档案回答"
    assert config["active_knowledge"]["model"] == "deepseek-reasoner-v2"
    assert config["active_knowledge"]["thinking_enabled"] is True
    assert config["active_knowledge"]["max_tokens"] == 640
    assert config["active_knowledge"]["temperature"] == 0.2
    assert config["history_messages"] == 0

    store.record_dsapi_exchange(
        group_id=123,
        user_content="新问题",
        assistant_content="新回答",
        history_turns=2,
    )
    unchanged = store.activate_dsapi_knowledge_base(
        second["id"],
        clear_history=True,
        actor_id=1,
    )
    assert unchanged["history_messages_cleared"] == 0
    assert store.get_dsapi_config()["history_messages"] == 2

    store.delete_dsapi_knowledge_base(second["id"], actor_id=1)
    config = store.get_dsapi_config()
    assert config["active_knowledge_id"] == first["id"]
    assert config["active_knowledge_name"] == "果果"
    assert config["history_messages"] == 0


def test_dsapi_history_expires_after_group_is_idle(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=()))
    store.record_dsapi_exchange(
        group_id=123,
        user_content="旧问题",
        assistant_content="旧回答",
        history_turns=2,
    )
    store.record_dsapi_exchange(
        group_id=456,
        user_content="新问题",
        assistant_content="新回答",
        history_turns=2,
    )
    with store._connect() as conn:
        conn.execute(
            "UPDATE dsapi_chat_history SET created_at = ? WHERE group_id = ?",
            (100.0, 123),
        )
        conn.execute(
            "UPDATE dsapi_chat_history SET created_at = ? WHERE group_id = ?",
            (1300.0, 456),
        )

    assert store.expire_dsapi_chat_history(123, idle_seconds=1200, now=1300.0) == 0
    assert store.expire_dsapi_chat_history(123, idle_seconds=1200, now=1301.0) == 2
    assert store.get_dsapi_chat_history(123, 2) == []
    assert store.expire_dsapi_chat_history(456, idle_seconds=1200, now=1301.0) == 0
    assert len(store.get_dsapi_chat_history(456, 2)) == 2


def test_random_group_context_keeps_ten_messages_and_expires_when_idle(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=()))

    for index in range(12):
        store.record_dsapi_group_message(
            group_id=123,
            user_id=10000 + index,
            content=f"  消息 {index}  \n  后半句  ",
            now=1000.0 + index,
        )

    context = store.get_dsapi_group_context(123, idle_seconds=1200, now=2211.0)
    assert len(context) == 10
    assert context[0] == {"user_id": 10002, "content": "消息 2 后半句"}
    assert context[-1] == {"user_id": 10011, "content": "消息 11 后半句"}
    assert store.get_dsapi_config()["history_messages"] == 10
    assert store.get_dsapi_group_context(123, idle_seconds=1200, now=2212.0) == []


def test_clear_dsapi_history_also_clears_random_group_context(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=()))
    store.record_dsapi_group_message(group_id=123, user_id=10000, content="群聊消息")

    assert store.clear_dsapi_chat_history(actor_id=10000) == 1
    assert store.get_dsapi_group_context(123) == []


def test_existing_store_migrates_enabled_groups_to_ai_groups(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    settings = AppSettings(db_path=db_path, admins=())
    store = PolicyStore(db_path)
    store.initialize(settings)
    store.set_group_enabled(123, True, actor_id=10000)
    store.set_group_enabled(456, True, actor_id=10000)

    with store._connect() as conn:
        conn.execute("DELETE FROM settings WHERE key LIKE 'dsapi_%'")

    store.initialize(settings)

    assert store.get_dsapi_config()["enabled_groups"] == [123, 456]


def test_menu_recipe_import_and_pick(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    image_dir = tmp_path / "menu_images"
    seed_image = tmp_path / "white-cut-chicken.gif"
    seed_image.write_bytes(GIF_1PX)
    seed_path = tmp_path / "recipes_seed.jsonl"
    _write_seed(
        seed_path,
        {
            "id": "white-cut-chicken",
            "title": "白切鸡",
            "aliases": ["广州白切鸡"],
            "cuisine": "粤菜",
            "region": "广州",
            "category": "鸡肉",
            "tags": ["宴客", "经典"],
            "ingredients": ["三黄鸡", "姜", "葱", "盐"],
            "steps": ["浸熟", "过冰水", "斩件装盘"],
            "image_url": str(seed_image),
        },
        {
            "id": "fatty-beef-hotpot",
            "title": "肥牛火锅",
            "aliases": ["牛肉火锅"],
            "cuisine": "火锅",
            "region": "成都",
            "category": "锅物",
            "tags": ["火锅", "聚餐"],
            "ingredients": ["肥牛卷", "菌菇", "青菜", "底料"],
            "steps": ["煮汤底", "涮肥牛", "搭配蘸料"],
            "image_url": "",
        },
        {
            "id": "scallion-noodle",
            "title": "葱油拌面",
            "aliases": ["上海葱油面"],
            "cuisine": "面食",
            "region": "上海",
            "category": "主食",
            "tags": ["夜宵"],
            "ingredients": ["挂面", "小葱", "生抽", "白糖"],
            "steps": ["炸葱油", "煮面", "拌匀"],
            "image_url": "",
        },
    )

    settings = AppSettings(
        db_path=db_path,
        admins=(10000,),
        menu_seed_path=seed_path,
        menu_image_dir=image_dir,
    )
    store = PolicyStore(db_path)
    store.initialize(settings)

    assert store.menu_recipe_count() == 0
    assert store.import_menu_recipes(seed_path, image_dir) == 3
    assert store.menu_recipe_count() == 3
    assert (image_dir / "white-cut-chicken.gif").is_file()

    assert store.pick_menu_recipe("", 0, seed_path, image_dir) is not None
    assert store.pick_menu_recipe("火锅", 0, seed_path, image_dir)["title"] == "肥牛火锅"
    assert store.pick_menu_recipe("鸡肉", 0, seed_path, image_dir)["category"] == "鸡肉"
    assert store.pick_menu_recipe("不存在", 1, seed_path, image_dir) is not None


def test_menu_recipe_does_not_match_region(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    image_dir = tmp_path / "menu_images"
    pictured = tmp_path / "pictured.gif"
    pictured.write_bytes(GIF_1PX)
    seed_path = tmp_path / "recipes_seed.jsonl"
    _write_seed(
        seed_path,
        {
            "id": "guangzhou-hotpot",
            "title": "牛杂锅",
            "aliases": ["牛杂锅"],
            "cuisine": "火锅",
            "region": "广州",
            "category": "锅物",
            "tags": ["火锅"],
            "ingredients": ["牛杂", "萝卜"],
            "steps": ["炖煮", "调味"],
            "image_url": "",
        },
        {
            "id": "chengdu-hotpot",
            "title": "成都火锅",
            "aliases": ["麻辣锅"],
            "cuisine": "火锅",
            "region": "成都",
            "category": "锅物",
            "tags": ["火锅"],
            "ingredients": ["牛油", "毛肚"],
            "steps": ["煮底料", "涮菜"],
            "image_url": str(pictured),
        },
    )
    settings = AppSettings(
        db_path=db_path,
        admins=(10000,),
        menu_seed_path=seed_path,
        menu_image_dir=image_dir,
    )
    store = PolicyStore(db_path)
    store.initialize(settings)

    assert store.import_menu_recipes(seed_path, image_dir) == 2

    picked = store.pick_menu_recipe("广州", 1, seed_path, image_dir)
    assert picked is not None
    assert picked["title"] == "成都火锅"


def test_menu_recipe_prefers_image_candidates(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    image_dir = tmp_path / "menu_images"
    pictured = tmp_path / "pictured.gif"
    pictured.write_bytes(GIF_1PX)
    seed_path = tmp_path / "recipes_seed.jsonl"
    _write_seed(
        seed_path,
        {
            "id": "with-image",
            "title": "带图菜",
            "aliases": ["有图"],
            "cuisine": "家常菜",
            "region": "广州",
            "category": "热菜",
            "tags": ["家常"],
            "ingredients": ["鸡蛋", "番茄"],
            "steps": ["切菜", "下锅"],
            "image_url": str(pictured),
        },
        {
            "id": "without-image",
            "title": "无图菜",
            "aliases": ["没图"],
            "cuisine": "家常菜",
            "region": "广州",
            "category": "热菜",
            "tags": ["家常"],
            "ingredients": ["土豆", "青椒"],
            "steps": ["切菜", "下锅"],
            "image_url": "",
        },
    )
    settings = AppSettings(
        db_path=db_path,
        admins=(10000,),
        menu_seed_path=seed_path,
        menu_image_dir=image_dir,
    )
    store = PolicyStore(db_path)
    store.initialize(settings)

    assert store.import_menu_recipes(seed_path, image_dir) == 2
    assert store.pick_menu_recipe("", 1, seed_path, image_dir)["title"] == "带图菜"
    assert store.pick_menu_recipe("热菜", 1, seed_path, image_dir)["title"] == "带图菜"


def test_menu_recipe_falls_back_when_no_image_candidate_exists(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    image_dir = tmp_path / "menu_images"
    seed_path = tmp_path / "recipes_seed.jsonl"
    _write_seed(
        seed_path,
        {
            "id": "without-image",
            "title": "无图菜",
            "aliases": ["没图"],
            "cuisine": "家常菜",
            "region": "广州",
            "category": "热菜",
            "tags": ["家常"],
            "ingredients": ["土豆", "青椒"],
            "steps": ["切菜", "下锅"],
            "image_url": "",
        },
    )
    settings = AppSettings(
        db_path=db_path,
        admins=(10000,),
        menu_seed_path=seed_path,
        menu_image_dir=image_dir,
    )
    store = PolicyStore(db_path)
    store.initialize(settings)

    assert store.import_menu_recipes(seed_path, image_dir) == 1

    picked = store.pick_menu_recipe("热菜", 0, seed_path, image_dir)
    assert picked is not None
    assert picked["title"] == "无图菜"


def test_store_purges_legacy_menu_cache_namespaces(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    settings = AppSettings(db_path=db_path, admins=(10000,))
    store = PolicyStore(db_path)
    store.initialize(settings)

    store.set_lua_state("今日菜单:cache:v1", "list:world:Chinese", "old")
    store.set_lua_state("今日菜单:cache:v2", "meal:1", "old")
    store.set_lua_state("今日菜单:cachev2", "meal:2", "old")
    store.set_lua_state("今日天气", "北京", "keep")

    purged = store.purge_legacy_menu_caches()

    assert purged == 3
    assert store.get_lua_state("今日菜单:cache:v1", "list:world:Chinese") is None
    assert store.get_lua_state("今日菜单:cache:v2", "meal:1") is None
    assert store.get_lua_state("今日菜单:cachev2", "meal:2") is None
    assert store.get_lua_state("今日天气", "北京") == "keep"


def test_menu_recipe_import_upserts_existing_records(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    image_dir = tmp_path / "menu_images"
    seed_path = tmp_path / "recipes_seed.jsonl"
    settings = AppSettings(
        db_path=db_path,
        admins=(10000,),
        menu_seed_path=seed_path,
        menu_image_dir=image_dir,
    )
    store = PolicyStore(db_path)
    store.initialize(settings)

    _write_seed(
        seed_path,
        {
            "id": "tomato-egg",
            "title": "番茄炒蛋",
            "aliases": ["西红柿炒鸡蛋"],
            "cuisine": "家常菜",
            "region": "广州",
            "category": "热菜",
            "tags": ["快手菜"],
            "ingredients": ["番茄", "鸡蛋"],
            "steps": ["切菜", "下锅"],
            "image_url": "",
        },
    )
    assert store.import_menu_recipes(seed_path, image_dir) == 1

    _write_seed(
        seed_path,
        {
            "id": "tomato-egg",
            "title": "番茄滑蛋",
            "aliases": ["西红柿炒鸡蛋"],
            "cuisine": "家常菜",
            "region": "广州",
            "category": "热菜",
            "tags": ["快手菜"],
            "ingredients": ["番茄", "鸡蛋"],
            "steps": ["切菜", "下锅"],
            "image_url": "",
        },
    )
    assert store.import_menu_recipes(seed_path, image_dir) == 1

    picked = store.pick_menu_recipe("番茄", 0, seed_path, image_dir)
    assert picked is not None
    assert picked["title"] == "番茄滑蛋"


def test_external_menu_recipe_is_saved_only_when_title_is_new(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    image_dir = tmp_path / "menu_images"
    seed_path = tmp_path / "recipes_seed.jsonl"
    settings = AppSettings(
        db_path=db_path,
        admins=(10000,),
        menu_seed_path=seed_path,
        menu_image_dir=image_dir,
    )
    store = PolicyStore(db_path)
    store.initialize(settings)

    recipe = {
        "id": "jisu-1",
        "title": "醋溜白菜",
        "aliases": [],
        "cuisine": "国内菜谱",
        "region": "国内",
        "category": "家常菜",
        "tags": ["下饭"],
        "ingredients": ["白菜 380g"],
        "steps": ["快速翻炒。"],
        "image_url": "",
        "enabled": True,
        "source": "jisu",
    }

    first = store.save_external_menu_recipe_if_new(recipe, image_dir)
    duplicate = store.save_external_menu_recipe_if_new(
        {**recipe, "id": "jisu-2", "category": "重复分类"},
        image_dir,
    )

    assert store.menu_recipe_count() == 1
    assert first["title"] == "醋溜白菜"
    assert first["region"] == ""
    assert duplicate["id"] == first["id"]
    assert duplicate["category"] == "家常菜"


def test_prune_howtocook_without_images_keeps_pictured_and_custom(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    image_dir = tmp_path / "menu_images"
    pictured = tmp_path / "pictured.gif"
    pictured.write_bytes(GIF_1PX)
    seed_path = tmp_path / "recipes_seed.jsonl"
    settings = AppSettings(
        db_path=db_path,
        admins=(10000,),
        menu_seed_path=seed_path,
        menu_image_dir=image_dir,
    )
    store = PolicyStore(db_path)
    store.initialize(settings)
    _write_seed(
        seed_path,
        {
            "id": "howtocook-pictured",
            "title": "带图 HowToCook",
            "aliases": [],
            "cuisine": "测试",
            "region": "",
            "category": "测试",
            "tags": [],
            "ingredients": ["米饭"],
            "steps": ["装盘"],
            "image_url": str(pictured),
            "source": "howtocook",
        },
        {
            "id": "howtocook-missing",
            "title": "无图 HowToCook",
            "aliases": [],
            "cuisine": "测试",
            "region": "",
            "category": "测试",
            "tags": [],
            "ingredients": ["米饭"],
            "steps": ["装盘"],
            "image_url": "",
            "source": "howtocook",
        },
    )

    assert store.import_menu_recipes(seed_path, image_dir) == 2
    store.save_custom_menu_recipe("自定义带图", image_dir, image_body=GIF_1PX, image_suffix=".gif")

    assert store.prune_howtocook_without_images(image_dir) == 1
    titles = {item["title"] for item in store.list_menu_recipes()}
    assert "带图 HowToCook" in titles
    assert "自定义带图" in titles
    assert "无图 HowToCook" not in titles


def test_custom_menu_same_title_updates_existing_record(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    image_dir = tmp_path / "menu_images"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=(10000,), menu_image_dir=image_dir))

    first = store.save_custom_menu_recipe("群友菜单", image_dir, image_body=GIF_1PX, image_suffix=".gif")
    second = store.save_custom_menu_recipe("群友菜单", image_dir, image_body=GIF_1PX, image_suffix=".gif")

    assert first["id"] == second["id"]
    assert store.menu_recipe_count() == 1
    assert second["source"] == "custom"


def test_restaurants_upsert_by_group_and_name(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=(10000,)))

    first = store.save_restaurant(name="楼下小馆", dishes=["红烧肉"], group_id=1, created_by=100)
    updated = store.save_restaurant(name="楼下小馆", dishes=["干锅牛蛙"], group_id=1, created_by=101)
    other_group = store.save_restaurant(name="楼下小馆", dishes=["炒饭"], group_id=2, created_by=102)

    assert first["id"] == updated["id"]
    assert updated["dishes"] == ["干锅牛蛙"]
    assert other_group["id"] != first["id"]
    assert store.pick_restaurant(1, 0)["name"] == "楼下小馆"


def test_group_daily_summary_counts_activity_by_day(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=(10000,)))

    store.record_group_message_activity(
        group_id=123,
        user_id=1,
        timestamp=_china_timestamp(2026, 6, 19, 8, 10),
        raw_message="早上好",
        segments=(),
    )
    store.record_group_message_activity(
        group_id=123,
        user_id=1,
        timestamp=_china_timestamp(2026, 6, 19, 23, 58),
        raw_message="最后一条",
        segments=(
            {"type": "text", "data": {"text": "最后一条"}},
            {"type": "image", "data": {"file": "a.jpg"}},
        ),
    )
    store.record_group_message_activity(
        group_id=123,
        user_id=2,
        timestamp=_china_timestamp(2026, 6, 19, 12, 0),
        raw_message="hello world",
        segments=(
            {"type": "text", "data": {"text": "hello world"}},
            {"type": "at", "data": {"qq": "1"}},
            {"type": "reply", "data": {"id": "42"}},
        ),
    )
    store.record_group_message_activity(
        group_id=123,
        user_id=2,
        timestamp=_china_timestamp(2026, 6, 20, 0, 1),
        raw_message="next day",
        segments=(),
    )
    store.record_group_message_activity(
        group_id=123,
        user_id=3,
        timestamp=_china_timestamp(2026, 6, 19, 13, 0),
        raw_message="[CQ:image,file=base64://very-long-image-body]",
        segments=({"type": "image", "data": {"file": "base64://very-long-image-body"}},),
    )
    store.record_group_message_activity(
        group_id=123,
        user_id=3,
        timestamp=_china_timestamp(2026, 6, 19, 13, 5),
        raw_message="[CQ:image,file=a.jpg]看看",
        segments=(
            {"type": "image", "data": {"file": "a.jpg"}},
            {"type": "text", "data": {"text": "看看"}},
        ),
    )

    summary = store.get_group_daily_summary(123, "2026-06-19", limit=5)

    assert summary["total_messages"] == 5
    assert summary["active_users"] == 3
    assert summary["top_messages"][0]["user_id"] == 1
    assert summary["top_messages"][0]["message_count"] == 2
    assert summary["top_text_chars"][0]["user_id"] == 2
    assert summary["top_text_chars"][1]["user_id"] == 1
    assert summary["top_text_chars"][2]["user_id"] == 3
    assert summary["top_text_chars"][2]["text_chars"] == 2
    assert summary["top_images"] == [
        {
            "user_id": 3,
            "message_count": 2,
            "text_chars": 2,
            "image_count": 2,
            "at_count": 0,
            "reply_count": 0,
            "first_timestamp": _china_timestamp(2026, 6, 19, 13, 0),
            "last_timestamp": _china_timestamp(2026, 6, 19, 13, 5),
            "first_time": "13:00",
            "last_time": "13:05",
        },
        {
            "user_id": 1,
            "message_count": 2,
            "text_chars": 7,
            "image_count": 1,
            "at_count": 0,
            "reply_count": 0,
            "first_timestamp": _china_timestamp(2026, 6, 19, 8, 10),
            "last_timestamp": _china_timestamp(2026, 6, 19, 23, 58),
            "first_time": "08:10",
            "last_time": "23:58",
        }
    ]
    assert summary["top_mentions"][0]["user_id"] == 2
    assert summary["top_mentions"][0]["at_count"] == 1
    assert summary["peak_hour"] == {"hour": 13, "message_count": 2}
    assert summary["early_bird"]["user_id"] == 1
    assert summary["early_bird"]["first_time"] == "08:10"
    assert summary["night_owl"]["user_id"] == 1
    assert summary["night_owl"]["last_time"] == "23:58"

    next_day = store.get_group_daily_summary(123, "2026-06-20", limit=5)
    assert next_day["total_messages"] == 1
    assert next_day["active_users"] == 1


def test_launcher_log_cleanup_keeps_recent_two_days(tmp_path):
    assert DEFAULT_LOG_RETENTION_DAYS == 2

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    today = date.today()
    kept_today = logs_dir / f"qqbot-{today.isoformat()}.log"
    kept_yesterday = logs_dir / f"qqbot-{(today - timedelta(days=1)).isoformat()}.log"
    deleted_old = logs_dir / f"qqbot-{(today - timedelta(days=2)).isoformat()}.log"
    legacy_log = logs_dir / "qqbot.log"
    for path in (kept_today, kept_yesterday, deleted_old, legacy_log):
        path.write_text("log", encoding="utf-8")

    old_timestamp = datetime.combine(
        today - timedelta(days=2),
        datetime.min.time(),
    ).timestamp()
    os.utime(legacy_log, (old_timestamp, old_timestamp))

    cleanup_logs(logs_dir, retention_days=2)

    assert kept_today.exists()
    assert kept_yesterday.exists()
    assert not deleted_old.exists()
    assert not legacy_log.exists()
