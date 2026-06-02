from __future__ import annotations

import json
from pathlib import Path

from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.settings import AppSettings


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


def test_snapshot_shape(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=(10000,)))

    snapshot = store.snapshot()

    assert snapshot["mode"] == "allowlist"
    assert snapshot["admins"] == [10000]
    assert snapshot["trigger"]["mention"] is True
    assert "~" in snapshot["trigger"]["prefixes"]


def test_empty_prefixes_default_to_tilde(tmp_path):
    db_path = tmp_path / "policy.sqlite3"
    store = PolicyStore(db_path)
    store.initialize(AppSettings(db_path=db_path, admins=(10000,)))

    store.set_prefixes([], actor_id=10000)

    assert store.prefixes() == ["~"]


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
