import asyncio
import importlib
from dataclasses import replace
from io import BytesIO
from unittest.mock import AsyncMock, Mock

import httpx
import nonebot
import pytest
from fastapi.testclient import TestClient
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    Message,
    MessageSegment,
    PrivateMessageEvent,
)
from PIL import Image, ImageDraw

from qq_personal_bot.help_card import build_help_sections
from qq_personal_bot.runtime import get_settings, get_store, get_weizhu_store, reset_runtime
from qq_personal_bot.settings import AppSettings
from qq_personal_bot.web import create_app
from qq_personal_bot.weizhu import service, skland
from qq_personal_bot.weizhu.bonds import active_bonds
from qq_personal_bot.weizhu.renderer import BORDER, TEAL
from qq_personal_bot.weizhu.service import WeizhuService
from qq_personal_bot.weizhu.store import Binding, Chat, WeizhuStore

nonebot.init()
plugin = importlib.import_module("qq_personal_bot.plugins.weizhu")
GROUP = Chat(10, 21, group_id=33)
SEASON = {"versionInfo": {"seasonId": "s1"}}
SKILLFUL = {
    "bondId": "skillfulShip",
    "name": "灵巧",
    "activeCount": 2,
    "effect": "基础效果\n<达到<@autochess.dgreen>40</>层>提升范围",
}


def bound(chat=GROUP, **changes):
    b = Binding(
        chat.bot_id,
        chat.user_id,
        "test-cred",
        "12345678",
        "博士#0007",
        "官服",
        "s1",
        False,
        False,
        None,
        20,
        1,
    )
    return replace(b, **changes)


def event_for(msg, *, private=False, sub_type=None, sender=None):
    common = {
        "time": 10,
        "self_id": 10,
        "post_type": "message",
        "user_id": 21,
        "message_id": 1,
        "message": msg,
        "raw_message": str(msg),
        "font": 0,
        "sender": sender or {"user_id": 21},
    }
    if private:
        return PrivateMessageEvent(message_type="private", sub_type=sub_type or "friend", **common)
    return GroupMessageEvent(message_type="group", sub_type="normal", group_id=33, **common)


@pytest.fixture
def svc(tmp_path):
    obj = WeizhuService(
        AppSettings.from_env({"QQBOT_WEIZHU_ADMIN_IDS": "40"}),
        WeizhuStore(tmp_path / "weizhu"),
        AsyncMock(),
        lambda chat: True,
    )
    obj.client.get_player_info = AsyncMock(return_value={})
    return obj


@pytest.mark.asyncio
async def test_group_qr_through_onebot_and_skland(tmp_path, monkeypatch):
    seen = []

    def upstream(request):
        path = request.url.path
        seen.append(path)
        if path == "/api/v1/game/player/binding":
            assert request.headers["cred"] == "test-cred"
            assert request.headers["sign"] and request.headers["token"] == "signed"
            data = {
                "list": [
                    {
                        "appCode": "arknights",
                        "bindingList": [
                            {"uid": "111", "nickName": "旧角色#4455"},
                            {"uid": "987654", "nickName": "阿米娅#0012", "isDefault": True},
                        ],
                    }
                ]
            }
        else:
            data = {
                "/general/v1/gen_scan/login": {"scanId": "test-scan"},
                "/general/v1/scan_status": {"scanCode": "test-code"},
                "/user/auth/v1/token_by_scan_code": {"token": "transient"},
                "/user/oauth2/v2/grant": {"code": "grant"},
                "/api/v1/user/auth/generate_cred_by_code": {"cred": "test-cred"},
                "/api/v1/auth/refresh": {"token": "signed"},
            }[path]
        return httpx.Response(200, json={"status": 0, "code": 0, "data": data})

    monkeypatch.setattr(
        skland,
        "_make_client",
        lambda proxy: httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )
    bot = Mock()
    bot.send_group_msg = AsyncMock(return_value={"message_id": 9})
    bot.send_private_msg = AsyncMock()
    monkeypatch.setattr(plugin, "get_bot", lambda bot_id: bot)
    policy = Mock()
    policy.get_mode.return_value = "allowlist"
    policy.is_feature_enabled.return_value = True
    policy.is_group_enabled.return_value = True
    monkeypatch.setattr(plugin, "get_store", lambda: policy)
    engine = Mock()
    engine.rate_limiter.allow.return_value = True, "ok"
    monkeypatch.setattr(plugin, "get_policy_engine", lambda: engine)
    db = WeizhuStore(tmp_path / "weizhu")
    obj = WeizhuService(
        AppSettings.from_env({"QQBOT_WEIZHU_PUSH_DEFAULT": "true"}),
        db,
        plugin._send,
        plugin._allowed,
    )
    monkeypatch.setattr(plugin, "_service", lambda: obj)
    incoming = event_for(MessageSegment.at("10") + " /卫戍 绑定")
    # The adapter strips the leading mention before command dispatch.
    incoming.message = Message("/卫戍 绑定")
    await plugin.handle(incoming, Message("绑定"))
    await obj.pending[GROUP.owner]
    assert len(seen) == 7
    assert bot.send_group_msg.await_count == 2
    qr, result = [call.kwargs for call in bot.send_group_msg.await_args_list]
    assert qr["group_id"] == result["group_id"] == GROUP.group_id
    assert "21" in qr["message"][0].data["text"]
    assert qr["message"][1].data["file"].startswith("base64://iVBOR")
    assert "阿米娅#0012" in result["message"].extract_plain_text()
    bot.send_private_msg.assert_not_awaited()
    assert not db.get(GROUP).push
    assert WeizhuStore(db.path.parent).directory() == [{"qq": "21", "nickname": "阿米娅#0012"}]
    await obj.stop()


@pytest.mark.asyncio
async def test_mentions_policy_and_temporary_group_routing(monkeypatch):
    for msg in (
        Message("/卫戍 查询"),
        MessageSegment.at("all") + "/卫戍 查询",
        MessageSegment.reply(42) + "/卫戍 查询",
    ):
        ev = event_for(msg)
        ev.to_me = True
        assert plugin._chat(ev) is None
    store = Mock()
    monkeypatch.setattr(plugin, "get_store", lambda: store)
    store.is_feature_enabled.return_value = True
    store.get_mode.return_value = "allowlist"
    store.is_group_enabled.return_value = False
    assert not plugin._allowed(GROUP)
    store.get_mode.return_value = "blocklist"
    store.is_group_blocked.return_value = True
    assert not plugin._allowed(GROUP)
    store.is_feature_enabled.return_value = False
    assert not plugin._allowed(Chat(10, 21))
    temp = event_for(
        Message("/卫戍 绑定"),
        private=True,
        sub_type="group",
        sender={"user_id": 21, "group_id": 33},
    )
    chat = plugin._chat(temp)
    assert chat == Chat(10, 21, temporary_group=33)
    bot = AsyncMock()
    bot.send_private_msg.return_value = {"message_id": 3}
    monkeypatch.setattr(plugin, "get_bot", lambda _: bot)
    await plugin._send(chat, "帮助", None)
    assert bot.send_private_msg.await_args.kwargs["group_id"] == 33
    bot.send_group_msg.assert_not_called()
    temp.sender.user_id = 22
    assert plugin._chat(temp) is None


@pytest.mark.asyncio
async def test_simultaneous_binding_cancellation_and_cleanup(svc, monkeypatch):
    codes = iter(["first", "second"])

    async def qr_start(_):
        scan_id = next(codes)
        return {"scan_id": scan_id, "url": f"hypergryph://scan_login?scanId={scan_id}"}

    proceed = asyncio.Event()

    async def qr_poll(scan_id, _):
        await proceed.wait()
        return scan_id

    monkeypatch.setattr(service, "qr_start", qr_start)
    monkeypatch.setattr(service, "qr_poll", qr_poll)
    monkeypatch.setattr(service, "qr_finish", AsyncMock(return_value={"cred": "test-cred"}))
    svc.client.get_arknights_binding = AsyncMock(
        return_value={
            "uid": "123",
            "nickname": "博士#0020",
            "channel": "官服",
        }
    )
    other = replace(GROUP, user_id=99)
    await svc.handle(GROUP, "绑定")
    await svc.handle(other, "绑定")
    await asyncio.sleep(0)
    assert len(svc.pending) == 2
    await svc.handle(GROUP, "绑定")
    assert "等待扫码" in svc.send.await_args.args[1]
    await svc.handle(GROUP, "解绑")
    assert "取消" in svc.send.await_args.args[1]
    proceed.set()
    await svc.pending[other.owner]
    assert not svc.pending and not svc.store.get(GROUP)
    assert svc.store.get(other).nickname == "博士#0020"
    await svc.handle(other, "解绑")
    assert svc.store.directory() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cmd,width", [("查询", 1200), ("战绩 2", 1200), ("战绩 1", 1200), ("对局 2", 1200)]
)
async def test_cards_from_saved_binding(svc, cmd, width):
    svc.store.save(bound())
    svc.client.get_garrison_resource = AsyncMock(return_value=SEASON)
    svc.client.get_garrison_history = AsyncMock(
        return_value=[
            {"gameEndTs": "100", "gameStartTs": "20"},
            {"gameEndTs": 200, "completeState": 1},
        ]
    )
    await svc.handle(GROUP, cmd)
    chat, _, body = svc.send.await_args.args
    assert chat == GROUP
    with Image.open(BytesIO(body)) as card:
        assert card.format == "PNG" and card.width == width
    await svc.handle(GROUP, "对局 3")
    assert svc.send.await_args.args[1] == "只有 2 局战绩。"


@pytest.mark.asyncio
async def test_match_card_displays_all_equipment_from_history(svc, monkeypatch):
    svc.store.save(bound())
    names = ["奥术法阵", "坚守盾牌", "精准狙击镜", "变形同构体", "维式重锤"]
    item_ids = ["chess_item_3_07_e_a", "item_2", "item_3", "item_4", "item_5"]
    long_name = "非常长的装备名称需要在同一名干员下完整换行显示"
    items = [
        {"itemId": item_id, "name": name}
        for item_id, name in zip([*item_ids, "long_equipment"], [*names, long_name], strict=True)
    ]
    colors = {"hammer.png": (255, 90, 40), "shield.png": (48, 61, 233)}
    for item, file in zip(items[:3], [*colors, "missing.png"], strict=True):
        item["iconUrl"] = f"https://web.hycdn.cn/{file}"
    requests = []

    def serve_icon(request):
        file = request.url.path.rsplit("/", 1)[-1]
        requests.append(file)
        if file not in colors:
            return httpx.Response(404)
        buffer = BytesIO()
        Image.new("RGBA", (128, 128), (*colors[file], 255)).save(buffer, format="PNG")
        return httpx.Response(200, content=buffer.getvalue())

    client = httpx.Client(transport=httpx.MockTransport(serve_icon))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client)
    first_loadout = [
        {"equipId": item_ids[0]},
        {"itemId": item_ids[1]},
        {"id": item_ids[2]},
        item_ids[3],
        {"equipId": item_ids[4]},
        {"equipId": "unknown_item"},
        {},
    ]
    squad = [
        {
            "chessInfo": {"charId": "char_618_acrbot", "charName": "“暴躁铁皮”"},
            "mergeLevel": 1,
            "equips": first_loadout,
        },
        *[{"chessInfo": {"charName": "无装备干员"}, "equips": []} for _ in range(4)],
        {"chessInfo": {"charName": "干员乙"}, "equips": [{"equipId": "long_equipment"}]},
    ]
    svc.client.get_garrison_resource = AsyncMock(
        return_value={**SEASON, "items": items, "bonds": [SKILLFUL]}
    )
    svc.client.get_garrison_history = AsyncMock(
        return_value=[
            {
                "gameEndTs": 200,
                "chessSquad": squad,
                "bondInfo": [{"bondId": "skillfulShip", "activate": 1, "layer": 40}],
            }
        ]
    )
    drawn = []
    bond_labels = []
    original_text = ImageDraw.ImageDraw.text

    def capture(draw, xy, text, *args, **kwargs):
        if kwargs.get("fill") == TEAL or text in {"干员乙", "本局盟约"}:
            drawn.append((xy, text))
        if "灵巧" in text:
            bond_labels.append(text)
        return original_text(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture)
    await svc.handle(GROUP, "对局 1")
    chat, _, body = svc.send.await_args.args
    assert chat == GROUP
    with Image.open(BytesIO(body)) as card:
        assert card.format == "PNG"
        first_xy, second_xy = drawn[0][0], drawn[1][0]
        assert first_xy[1] == second_xy[1] and second_xy[0] - first_xy[0] == 86
        for xy, color in zip((first_xy, second_xy), colors.values(), strict=True):
            assert card.getpixel((xy[0], xy[1] - 32)) == color
            assert card.getpixel((xy[0] + 24, xy[1] - 32)) == color
        assert card.getpixel((first_xy[0] + 224, first_xy[1] - 32)) == BORDER
        assert card.getpixel((second_xy[0] + 224, second_xy[1] - 32)) == BORDER
    assert requests == ["hammer.png", "shield.png", "missing.png"]
    assert [text for _, text in drawn[:5]] == names
    next_member = next(i for i, (_, text) in enumerate(drawn) if text == "干员乙")
    assert "".join(text for _, text in drawn[5:next_member]) == "unknown_item"
    assert drawn[next_member - 1][0][1] + 22 < drawn[next_member][0][1]
    wrapped = drawn[next_member + 1 : -1]
    assert len(wrapped) > 1
    assert "".join(text for _, text in wrapped) == long_name
    assert drawn[-2][0][1] + 22 < drawn[-1][0][1] and drawn[-1][1] == "本局盟约"
    assert bond_labels == ["灵巧 Ⅱ · 40层"]


def test_bond_tiers_use_layers_distinct_members_and_elites():
    catalog = {
        "skillfulShip": SKILLFUL,
        "arcaneShip": {
            "name": "奥术",
            "activeCount": 2,
            "effect": "基础效果\n<在场<@autochess.dgreen>3</>名不同【奥术】干员>进阶效果",
            "chars": [{"charId": "a"}, {"charId": "b"}],
        },
        "suntShip": {
            "name": "绝技",
            "activeCount": 2,
            "effect": "<在场2名精锐状态的干员>基础效果\n<在场5名精锐状态的干员>进阶效果",
        },
        "visiShip": {
            "name": "远见",
            "activeCount": 2,
            "effect": "<每叠加10层>触发\n<首次达到80层>进阶\n<首次达到150层>最高",
        },
    }
    squad = [
        {"charId": "a", "mergeLevel": 1},
        {"charId": "a", "mergeLevel": 1},
        {"charId": "b", "mergeLevel": 1},
        {"charId": "c", "mergeLevel": 1},
        {"charId": "d", "mergeLevel": 1},
    ]
    report = [
        {"bondId": key, "activate": 1, "layer": layer}
        for key, layer in [
            ("skillfulShip", 39), ("arcaneShip", 100), ("suntShip", 0), ("visiShip", 150)
        ]
    ]
    record = {"versionId": "V3_1", "bondInfo": report, "chessSquad": squad}
    version = {"versionId": "V3_1"}

    def labels():
        return {bond.id: bond.label for bond in active_bonds(record, catalog, {}, version)}

    assert labels() == {
        "skillfulShip": "灵巧 Ⅰ · 39层",
        "arcaneShip": "奥术 Ⅰ · 2人 · 100层",
        "suntShip": "绝技 Ⅱ · 5名精锐",
        "visiShip": "远见 Ⅲ · 150层",
    }
    report[0]["layer"] = 40
    squad[3]["equips"] = [{"equipId": "chess_item_6_03_e_a"}, {"equipId": "chess_item_3_07_e_a"}]
    assert labels()["skillfulShip"] == "灵巧 Ⅱ · 40层"
    assert labels()["arcaneShip"] == "奥术 Ⅱ · 3人 · 100层"
    record["versionId"] = "old"
    assert labels()["arcaneShip"] == "奥术 · 100层"


@pytest.mark.asyncio
async def test_subscription_retries_deduplicates_and_respects_destination(svc):
    for uid, group in [(21, 33), (22, 33), (23, 44)]:
        svc.store.save(bound(replace(GROUP, user_id=uid), push=True, push_group=group))
    svc.client.get_garrison_resource = AsyncMock(return_value=SEASON)
    svc.client.get_garrison_history = AsyncMock(
        return_value=[
            {"gameEndTs": 19, "sceneId": "old"},
            {"gameEndTs": 30, "sceneId": "team"},
            {"gameEndTs": 31, "sceneId": "next"},
        ]
    )
    svc.settings = replace(svc.settings, weizhu_max_push=1)
    actual_send = AsyncMock()
    svc.send = actual_send
    actual_send.side_effect = [RuntimeError("offline"), None]
    await svc.poll_once()
    assert actual_send.await_args_list[0].args[0].group_id == 33
    assert actual_send.await_args.args[0].group_id == 44
    assert svc.store.get(GROUP).last_push == 0
    actual_send.side_effect = None
    await svc.poll_once()
    dest, label, card = actual_send.await_args.args
    assert dest.group_id == 33 and "2 人同局" in label
    assert Image.open(BytesIO(card)).width == 1400
    assert svc.store.get(GROUP).last_push > 0
    assert svc.store.delivered((10, "group:33", "s1", "team"))
    assert svc.store.delivered((10, "group:44", "s1", "team"))
    assert not svc.store.delivered((10, "group:33", "s1", "old"))
    await svc.poll_once()
    assert svc.store.delivered((10, "group:33", "s1", "next"))


@pytest.mark.asyncio
async def test_cancel_push_while_rendering(svc):
    svc.store.save(bound(push=True, push_group=33))
    svc._history = AsyncMock(return_value=(SEASON, "s1", [{"sceneId": "test", "gameEndTs": 50}]))

    def change_destination(*args):
        svc.store.save(bound(push=True, push_group=44, revision=2))
        return b"png"

    svc._render = change_destination
    await svc.poll_once()
    svc.send.assert_not_awaited()
    assert not svc.store.delivered((10, "group:33", "s1", "test"))


@pytest.mark.asyncio
async def test_lookup_privacy_push_and_failure(svc, monkeypatch):
    svc.store.save(bound())
    for chat in (GROUP, Chat(10, 21), Chat(10, 40, 33)):
        await svc.handle(chat, "用户")
        assert "博士" not in svc.send.await_args.args[1]
    await svc.handle(Chat(10, 40), "用户")
    assert "博士#0007" in svc.send.await_args.args[1]
    await svc.handle(GROUP, "推送 开")
    assert svc.store.get(GROUP).push_group == 33
    await svc.handle(Chat(10, 21), "推送 开")
    assert svc.store.get(GROUP).push and svc.store.get(GROUP).push_group is None
    await svc.handle(GROUP, "推送 关")
    assert not svc.store.subscriptions()
    await svc.handle(Chat(10, 99, temporary_group=33), "推送 开")
    assert "临时私聊" in svc.send.await_args.args[1]
    svc.client.get_garrison_resource = AsyncMock(side_effect=skland.SklandAuthError("test-cred"))
    await svc.handle(GROUP, "查询")
    assert svc.send.await_args.args[1] == service.EXPIRED
    monkeypatch.setattr(service, "qr_start", AsyncMock(side_effect=TimeoutError()))
    await svc.handle(Chat(10, 77, 33), "绑定")
    await svc.pending[(10, 77)]
    assert "过期" in svc.send.await_args.args[1]
    assert not svc.pending


def test_admin_bindings_projection_auth_and_unbind(tmp_path, monkeypatch):
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "qqbot.db"))
    monkeypatch.setenv("QQBOT_WEB_TOKEN", "unit-test-token")
    reset_runtime()
    try:
        store = get_weizhu_store()
        store.save(bound())
        client = TestClient(create_app())
        path = "/api/weizhu/bindings"
        assert client.get(path).status_code == 401
        headers = {"X-Admin-Token": "unit-test-token"}
        response = client.get(path, headers=headers)
        assert response.headers["Cache-Control"] == "no-store"
        assert response.json() == {"bindings": [{"qq": "21", "nickname": "博士#0007"}]}
        store.remove(GROUP)
        assert client.get(path, headers=headers).json() == {"bindings": []}
    finally:
        reset_runtime()


def test_feature_switch_controls_help_and_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "qqbot.db"))
    monkeypatch.setenv("QQBOT_WEB_TOKEN", "unit-test-token")
    reset_runtime()
    try:
        client = TestClient(create_app())
        headers = {"X-Admin-Token": "unit-test-token"}
        assert plugin._allowed(Chat(10, 21))
        for enabled in (False, True):
            result = client.put(
                "/api/features/weizhu.master", headers=headers, json={"enabled": enabled}
            )
            assert result.json()["enabled"] is enabled
            assert plugin._allowed(Chat(10, 21)) is enabled
            sections = build_help_sections(get_settings(), get_store(), is_admin=False)
            assert any(section.title == "明日方舟 · 卫戍协议" for section in sections) is enabled
    finally:
        reset_runtime()
