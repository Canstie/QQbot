from __future__ import annotations

import json
import time
from typing import Any

from qq_personal_bot.core.models import MessageEvent
from qq_personal_bot.runtime import get_settings, get_store

_FLOW_NAMESPACE = "custom_input_flow"
_FLOW_TTL_SECONDS = 600


def handle_custom_flow(event: MessageEvent) -> str | None:
    if event.group_id is None or not _group_allows_flow(event.group_id):
        return None

    store = get_store()
    key = _flow_key(event)
    existing = _load_flow(key)
    if existing is not None:
        return _continue_flow(key, existing, event)

    command = _prefixed_command(event.raw_message)
    if command == "添加菜单":
        _save_flow(
            key,
            {
                "type": "menu",
                "step": "name",
                "updated_at": time.time(),
            },
        )
        return "请发送菜单名字，发送“取消”退出。"

    if command == "添加饭店":
        _save_flow(
            key,
            {
                "type": "restaurant",
                "step": "name",
                "dishes": [],
                "updated_at": time.time(),
            },
        )
        return "请发送饭店名字，发送“取消”退出。"

    if command == "今日饭店":
        picked = store.pick_restaurant(event.group_id, _event_seed(event))
        if picked is None:
            return "还没有可抽取的饭店，先发送 ~添加饭店 添加一个吧。"
        dishes = "、".join(picked["dishes"])
        return f"今日饭店：{picked['name']}\n招牌菜：{dishes}"

    return None


def _continue_flow(key: str, flow: dict[str, Any], event: MessageEvent) -> str:
    raw_message = event.raw_message.strip()
    if raw_message == "取消":
        get_store().delete_lua_state(_FLOW_NAMESPACE, key)
        return "已取消。"

    if flow.get("type") == "menu":
        return _continue_menu_flow(key, flow, event)
    if flow.get("type") == "restaurant":
        return _continue_restaurant_flow(key, flow, event)

    get_store().delete_lua_state(_FLOW_NAMESPACE, key)
    return "流程状态已重置，请重新发送指令。"


def _continue_menu_flow(key: str, flow: dict[str, Any], event: MessageEvent) -> str:
    if flow.get("step") == "name":
        title = event.raw_message.strip()
        if not title:
            return "菜单名字不能为空，请重新发送菜单名字，或发送“取消”退出。"
        flow["step"] = "image"
        flow["title"] = title
        flow["updated_at"] = time.time()
        _save_flow(key, flow)
        return f"菜单名字已记录：{title}\n请发送这道菜的图片。"

    if flow.get("step") == "image":
        image_source = _first_image_source(event)
        if not image_source:
            return "没有读取到图片，请直接发送一张菜单图片，或发送“取消”退出。"
        title = str(flow.get("title") or "").strip()
        try:
            get_store().save_custom_menu_recipe(
                title,
                get_settings().menu_image_dir,
                image_source=image_source,
                enabled=True,
            )
        except Exception as exc:
            return f"添加菜单失败：{exc}\n请重新发送图片，或发送“取消”退出。"
        get_store().delete_lua_state(_FLOW_NAMESPACE, key)
        return f"添加菜单成功：{title}"

    get_store().delete_lua_state(_FLOW_NAMESPACE, key)
    return "菜单添加流程状态已重置，请重新发送 ~添加菜单。"


def _continue_restaurant_flow(key: str, flow: dict[str, Any], event: MessageEvent) -> str:
    raw_message = event.raw_message.strip()
    if flow.get("step") == "name":
        if not raw_message:
            return "饭店名字不能为空，请重新发送饭店名字，或发送“取消”退出。"
        flow["step"] = "dishes"
        flow["name"] = raw_message
        flow["dishes"] = []
        flow["updated_at"] = time.time()
        _save_flow(key, flow)
        return f"饭店名字已记录：{raw_message}\n请发送招牌菜，可连续发送多条；发送“完成”保存。"

    if flow.get("step") == "dishes":
        dishes = [str(item).strip() for item in flow.get("dishes", []) if str(item).strip()]
        if raw_message == "完成":
            if not dishes:
                return "还没有记录招牌菜，请先发送至少一道招牌菜，或发送“取消”退出。"
            restaurant = get_store().save_restaurant(
                name=str(flow.get("name") or "").strip(),
                dishes=dishes,
                group_id=int(event.group_id or 0),
                created_by=event.user_id,
                enabled=True,
            )
            get_store().delete_lua_state(_FLOW_NAMESPACE, key)
            return f"添加饭店成功：{restaurant['name']}｜招牌菜：{'、'.join(restaurant['dishes'])}"
        if not raw_message:
            return "招牌菜不能为空，请继续发送招牌菜；发送“完成”保存。"
        if raw_message not in dishes:
            dishes.append(raw_message)
        flow["dishes"] = dishes
        flow["updated_at"] = time.time()
        _save_flow(key, flow)
        return f"已记录招牌菜：{raw_message}\n继续发送招牌菜，发送“完成”保存。"

    get_store().delete_lua_state(_FLOW_NAMESPACE, key)
    return "饭店添加流程状态已重置，请重新发送 ~添加饭店。"


def _load_flow(key: str) -> dict[str, Any] | None:
    raw = get_store().get_lua_state(_FLOW_NAMESPACE, key)
    if raw is None:
        return None
    try:
        flow = json.loads(raw)
    except json.JSONDecodeError:
        get_store().delete_lua_state(_FLOW_NAMESPACE, key)
        return None
    if time.time() - float(flow.get("updated_at") or 0) > _FLOW_TTL_SECONDS:
        get_store().delete_lua_state(_FLOW_NAMESPACE, key)
        return None
    return flow if isinstance(flow, dict) else None


def _save_flow(key: str, flow: dict[str, Any]) -> None:
    get_store().set_lua_state(_FLOW_NAMESPACE, key, json.dumps(flow, ensure_ascii=False))


def _flow_key(event: MessageEvent) -> str:
    return f"{event.group_id}:{event.user_id}"


def _group_allows_flow(group_id: int) -> bool:
    store = get_store()
    mode = store.get_mode()
    if mode == "allowlist":
        return store.is_group_enabled(group_id)
    return not store.is_group_blocked(group_id)


def _prefixed_command(raw_message: str) -> str | None:
    message = raw_message.strip()
    for prefix in get_store().prefixes():
        if message.startswith(prefix):
            return message[len(prefix) :].strip()
    return None


def _first_image_source(event: MessageEvent) -> str | None:
    for segment in event.segments:
        if segment.get("type") != "image":
            continue
        data = dict(segment.get("data") or {})
        for key in ("url", "file"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
    return None


def _event_seed(event: MessageEvent) -> int:
    try:
        message_id = int(event.message_id or 0)
    except (TypeError, ValueError):
        message_id = 0
    return int(float(event.timestamp or 0)) + int(event.user_id) + message_id
