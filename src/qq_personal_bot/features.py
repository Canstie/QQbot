from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class FeatureDefinition:
    id: str
    category: str
    label: str
    description: str
    default_enabled: bool = True

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


STATIC_FEATURES: tuple[FeatureDefinition, ...] = (
    FeatureDefinition("ai.master", "AI", "AI 总开关", "控制所有 AI 回复。"),
    FeatureDefinition("ai.mention", "AI", "@ 回复", "被 @ 时生成 AI 回复。"),
    FeatureDefinition("ai.random", "AI", "随机插话", "在普通群消息中随机生成回复。"),
    FeatureDefinition("cards.bilibili", "卡片解析", "B站卡片", "解析 B站小程序并发送图片卡片。"),
    FeatureDefinition("cards.xiaohongshu", "卡片解析", "小红书卡片", "提取小红书分享中的图片。"),
    FeatureDefinition("cards.xiaoheihe", "卡片解析", "小黑盒卡片", "提取小黑盒分享中的图片。"),
    FeatureDefinition("replies.fixed", "消息回复", "固定回复", "使用 replies.json 中的回复规则。"),
    FeatureDefinition("teacher.lookup", "消息回复", "查老师", "查询教师评分和评价。"),
    FeatureDefinition("gallery.random", "图片功能", "随机图库", "响应涩图命令并发送轮换图片。"),
    FeatureDefinition("activity.record", "群数据", "群消息统计", "记录群总结所需的消息统计。"),
    FeatureDefinition("classics.forward_all", "图片功能", "典图合并转发", "发送本群全部典图。"),
    FeatureDefinition("downloads.ingest", "图片功能", "下载聊天图片", "下载引用聊天记录中的图片。"),
    FeatureDefinition("downloads.overview", "图片功能", "下载图库概览", "查询已下载图片统计。"),
    FeatureDefinition("flows.menu_add", "菜单与饭店", "添加菜单", "通过群聊分步添加菜单。"),
    FeatureDefinition("flows.restaurant_add", "菜单与饭店", "添加饭店", "通过群聊分步添加饭店。"),
    FeatureDefinition("flows.restaurant_pick", "菜单与饭店", "今日饭店", "随机选择本群饭店。"),
    FeatureDefinition("lua.master", "Lua 指令", "Lua 总开关", "控制全部 Lua 指令。"),
    FeatureDefinition("steam.master", "Steam", "Steam 总开关", "控制全部 Steam 能力。", False),
    FeatureDefinition("steam.commands", "Steam", "Steam 群命令", "允许在群里使用 Steam 指令。"),
    FeatureDefinition("steam.monitoring", "Steam", "玩家状态轮询", "后台查询已订阅玩家状态。"),
    FeatureDefinition("steam.notify.start", "Steam", "开始游戏通知", "玩家开始游戏时通知。"),
    FeatureDefinition("steam.notify.end", "Steam", "结束游戏通知", "退出确认后发送结束通知。"),
    FeatureDefinition("steam.notify.network", "Steam", "网络波动通知", "短暂退出后恢复时通知。"),
    FeatureDefinition("steam.achievements", "Steam", "成就通知", "监控并通知新解锁成就。"),
    FeatureDefinition("steam.player_lookup", "Steam", "玩家查询", "玩家列表、详情和按 QQ 查询。"),
    FeatureDefinition("steam.game_detail", "Steam", "游戏详情", "查询 Steam 游戏详情。"),
    FeatureDefinition("steam.price", "Steam", "价格查询", "查询 Steam 商店价格。"),
    FeatureDefinition("steam.notify.image", "Steam", "图片通知", "发送 Steam 状态图片卡片。"),
    FeatureDefinition("steam.notify.text", "Steam", "文本通知", "发送 Steam 状态文本。"),
    FeatureDefinition("steam.notify.mention", "Steam", "@ 已绑定用户", "通知时提醒绑定的 QQ 用户。", False),
)


def dynamic_lua_features(lua_dir: Path) -> list[FeatureDefinition]:
    if not lua_dir.is_dir():
        return []
    return [
        FeatureDefinition(
            id=f"lua.command.{path.stem}",
            category="Lua 指令",
            label=path.stem,
            description=f"控制 ~{path.stem} 指令。",
        )
        for path in sorted(lua_dir.glob("*.lua"), key=lambda item: item.stem.casefold())
        if path.stem
    ]


def feature_catalog(lua_dir: Path) -> list[FeatureDefinition]:
    return [*STATIC_FEATURES, *dynamic_lua_features(lua_dir)]


def feature_defaults(lua_dir: Path) -> dict[str, bool]:
    return {item.id: item.default_enabled for item in feature_catalog(lua_dir)}


def feature_id_for_platform(platform: str) -> str | None:
    value = str(platform or "").casefold()
    if "bilibili" in value:
        return "cards.bilibili"
    if "xiaohongshu" in value or value in {"xhs", "rednote"}:
        return "cards.xiaohongshu"
    if "xiaoheihe" in value or "maxjia" in value:
        return "cards.xiaoheihe"
    return None
