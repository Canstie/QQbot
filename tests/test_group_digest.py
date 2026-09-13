from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from qq_personal_bot.core.models import MessageEvent
from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.group_digest import _backfill_today_history, generate_group_digest_report
from qq_personal_bot.group_digest_card import _report_title
from qq_personal_bot.settings import AppSettings

CHINA_TZ = timezone(timedelta(hours=8))


class DigestBot:
    async def call_api(self, action: str, **params):
        assert params["group_id"] == 123
        if action == "get_group_info":
            return {"group_id": 123, "group_name": "测试群"}
        if action == "get_group_member_list":
            return [
                {"user_id": 1, "nickname": "Alpha", "card": ""},
                {"user_id": 2, "nickname": "Beta", "card": "BetaCard"},
            ]
        raise AssertionError(action)


class HistoryBot:
    self_id = 999

    async def call_api(self, action: str, **params):
        assert action == "get_group_msg_history"
        return {
            "messages": [
                {
                    "message_id": 10,
                    "message_seq": 10,
                    "time": _timestamp(10),
                    "user_id": 1,
                    "message": [
                        {"type": "text", "data": {"text": "历史消息"}},
                        {"type": "image", "data": {"file": "https://example/image"}},
                    ],
                },
                {
                    "message_id": 9,
                    "message_seq": 9,
                    "time": datetime(2026, 9, 12, 23, 59, tzinfo=CHINA_TZ).timestamp(),
                    "user_id": 2,
                    "message": [{"type": "text", "data": {"text": "昨天"}}],
                },
            ]
        }


def _timestamp(hour: int, minute: int = 0) -> float:
    return datetime(2026, 9, 13, hour, minute, tzinfo=CHINA_TZ).timestamp()


def test_report_title_uses_group_name_and_chinese_date():
    assert _report_title("aaa", "2026-09-13") == "aaa9月13日总结"


@pytest.mark.asyncio
async def test_generate_group_digest_writes_transcript_and_card(tmp_path, monkeypatch):
    db_path = tmp_path / "qqbot.sqlite3"
    settings = AppSettings(db_path=db_path, admins=(), dsapi_api_key="test-key")
    store = PolicyStore(db_path)
    store.initialize(settings)
    store.record_group_message_activity(
        group_id=123,
        user_id=1,
        timestamp=_timestamp(9, 5),
        raw_message="早上好",
        segments=({"type": "text", "data": {"text": "早上好"}},),
    )
    store.record_group_message_activity(
        group_id=123,
        user_id=2,
        timestamp=_timestamp(10, 10),
        raw_message="发图",
        segments=(
            {"type": "text", "data": {"text": "发图"}},
            {"type": "image", "data": {"file": "base64://not-persisted"}},
        ),
    )

    async def fake_summary(*args, **kwargs):
        return {
            "overview": "上午的群聊从问候开始。",
            "atmosphere": {"label": "轻松", "score": 72, "comment": "节奏轻快。"},
            "topics": [
                {"title": "晨间问候", "summary": "两位群友简单打招呼。", "participants": ["Alpha"]}
            ],
            "portraits": [
                {
                    "user_id": 1,
                    "name": "Alpha",
                    "title": "早鸟",
                    "tags": ["问候"],
                    "description": "最早打开话题。",
                }
            ],
            "quotes": [
                {"user_id": 1, "name": "Alpha", "quote": "早上好", "comment": "朴素的开场。"}
            ],
            "closing": "今天从一句早上好开始。",
        }

    monkeypatch.setattr("qq_personal_bot.group_digest._summarize_transcript", fake_summary)
    event = MessageEvent(
        platform="onebot.v11",
        message_id=3,
        group_id=123,
        user_id=1,
        raw_message="~总结",
        timestamp=_timestamp(12),
    )

    result = await generate_group_digest_report(DigestBot(), event, settings, store)

    transcript = result.transcript_path.read_text(encoding="utf-8")
    assert "[09:05:00] Alpha（QQ 1）：早上好" in transcript
    assert "[10:10:00] BetaCard（QQ 2）：发图[图片]" in transcript
    assert "base64" not in transcript
    with Image.open(result.image_path) as image:
        assert image.format == "PNG"
        assert image.width == 1080
        assert image.height > 1200


@pytest.mark.asyncio
async def test_history_backfill_keeps_only_today_and_deduplicates(tmp_path):
    db_path = tmp_path / "qqbot.sqlite3"
    settings = AppSettings(db_path=db_path, admins=())
    store = PolicyStore(db_path)
    store.initialize(settings)

    await _backfill_today_history(HistoryBot(), 123, "2026-09-13", store)
    await _backfill_today_history(HistoryBot(), 123, "2026-09-13", store)

    messages = store.get_group_daily_messages(123, "2026-09-13")
    assert len(messages) == 1
    assert messages[0]["content"] == "历史消息[图片]"
