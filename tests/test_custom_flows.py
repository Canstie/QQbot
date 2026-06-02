from __future__ import annotations

from qq_personal_bot.core.models import MessageEvent
from qq_personal_bot.plugins.custom_flows import handle_custom_flow
from qq_personal_bot.runtime import get_store, reset_runtime

GIF_1PX = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00"
    b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
    b"\x00\x02\x02D\x01\x00;"
)


def make_event(raw_message: str, *, segments=(), user_id: int = 20000) -> MessageEvent:
    return MessageEvent(
        platform="onebot.v11",
        message_id=1,
        group_id=123,
        user_id=user_id,
        raw_message=raw_message,
        segments=segments,
        timestamp=1000,
    )


def configure(tmp_path, monkeypatch):
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "policy.sqlite3"))
    monkeypatch.setenv("QQBOT_MENU_IMAGE_DIR", str(tmp_path / "menu_images"))
    reset_runtime()
    get_store().set_group_enabled(123, True, actor_id=0)


def test_add_menu_flow_saves_custom_menu(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)
    image_path = tmp_path / "menu.gif"
    image_path.write_bytes(GIF_1PX)

    assert handle_custom_flow(make_event("~添加菜单")) == "请发送菜单名字，发送“取消”退出。"
    assert "请发送这道菜的图片" in handle_custom_flow(make_event("番茄炒蛋"))
    reply = handle_custom_flow(
        make_event(
            "",
            segments=({"type": "image", "data": {"file": str(image_path)}},),
        )
    )

    assert reply == "添加菜单成功：番茄炒蛋"
    assert get_store().list_menu_recipes()[0]["title"] == "番茄炒蛋"


def test_add_menu_flow_requires_image(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)

    handle_custom_flow(make_event("~添加菜单"))
    handle_custom_flow(make_event("番茄炒蛋"))
    reply = handle_custom_flow(make_event("不是图片"))

    assert "没有读取到图片" in reply
    assert get_store().menu_recipe_count() == 0


def test_add_restaurant_flow_and_pick(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch)

    assert handle_custom_flow(make_event("~添加饭店")) == "请发送饭店名字，发送“取消”退出。"
    assert "发送招牌菜" in handle_custom_flow(make_event("楼下小馆"))
    assert "已记录招牌菜" in handle_custom_flow(make_event("红烧肉"))
    assert "已记录招牌菜" in handle_custom_flow(make_event("炒饭"))
    assert "添加饭店成功" in handle_custom_flow(make_event("完成"))

    reply = handle_custom_flow(make_event("~今日饭店"))

    assert "今日饭店：楼下小馆" in reply
    assert "红烧肉" in reply
