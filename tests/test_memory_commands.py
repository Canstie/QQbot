from __future__ import annotations

import pytest

from qq_personal_bot.core.memory_commands import is_memory_command


@pytest.mark.parametrize("message", [
    {"raw_message": " ~总结"},
    {"raw_message": "～今日菜单"},
    {"raw_message": "#bot帮助"},
    {"raw_message": "!抽群老婆"},
    {"raw_message": "/bot status"},
    {"raw_message": "/qqbot on"},
    {"raw_message": "/steam addid 123"},
    {"raw_message": "/在干嘛"},
    {"raw_message": "/dimg 123"},
    {"raw_message": "吃什么"},
    {"raw_message": "csm"},
    {"raw_message": "[CQ:reply,id=1][CQ:at,qq=789]~锐评"},
    {"raw_message": "评价一下[CQ:at,qq=789]"},
    {"raw_message": "今天聊点什么", "is_at_bot": True},
    {"raw_message": "番茄炒蛋", "is_bot_command": True},
    {"raw_message": "[CQ:at,qq=999]今天聊点什么"},
    {"segments": [{"type": "reply", "data": {"user_id": 999}},
                  {"type": "text", "data": {"text": "聊点什么"}}]},
    {"raw_message": "CQ原文", "segments": [{"type": "reply", "data": {"id": 1}},
                                           {"type": "text", "data": {"text": "~总结"}}]},
])
def test_bot_requests_are_commands(message):
    assert is_memory_command(message, prefixes=["~", "～", "#bot", "!"], bot_ids=[999])


@pytest.mark.parametrize("message", [
    {"raw_message": "今天我去机厅出勤了"},
    {"raw_message": "我喜欢玩怪猎"},
    {"raw_message": "吃什么都行"},
    {"raw_message": "你想吃什么"},
    {"raw_message": "用~总结能看日报"},
    {"raw_message": "“~总结”"},
    {"raw_message": "评价功能是什么"},
    {"raw_message": "评价一下", "segments": []},
    {"raw_message": "/ordinary"},
    {"raw_message": "[CQ:at,qq=789]我昨天去机厅了"},
    {"raw_message": "我昨天去机厅了", "segments": [{"type": "reply", "data": {"user_id": 789}}]},
    {"raw_message": "请看这段转发", "segments": [
        {"type": "node", "data": {"content": [{"type": "at", "data": {"qq": 999}}]}}]},
])
def test_regular_conversation_and_quoted_examples_are_preserved(message):
    assert not is_memory_command(message, prefixes=["~", "～", "#bot"], bot_ids=[999])
