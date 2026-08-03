from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from qq_personal_bot.plugins import chat


def make_event(*, group_id: int = 123, raw_message: str = "~抽群老婆"):
    return SimpleNamespace(group_id=group_id, raw_message=raw_message, message=raw_message)


def test_recent_bot_output_event_is_ignored():
    chat._recent_bot_outputs.clear()
    original = make_event(raw_message="~抽群老婆")
    echoed = make_event(raw_message="~抽群老婆")

    chat._remember_recent_bot_output(original, "~抽群老婆", now=100.0)

    assert chat._is_recent_bot_output_event(echoed, now=101.0) is True


def test_recent_bot_output_event_expires():
    chat._recent_bot_outputs.clear()
    original = make_event(raw_message="~抽群老婆")
    echoed = make_event(raw_message="~抽群老婆")

    chat._remember_recent_bot_output(original, "~抽群老婆", now=100.0)

    assert chat._is_recent_bot_output_event(echoed, now=106.0) is False


def test_recent_bot_output_event_is_group_scoped():
    chat._recent_bot_outputs.clear()
    original = make_event(group_id=123, raw_message="~抽群老婆")
    echoed = make_event(group_id=456, raw_message="~抽群老婆")

    chat._remember_recent_bot_output(original, "~抽群老婆", now=100.0)

    assert chat._is_recent_bot_output_event(echoed, now=101.0) is False


def test_quoted_response_replies_to_original_message():
    event = SimpleNamespace(message_id=42)

    response = chat._build_quoted_response("确实。", event)

    assert response[0].type == "reply"
    assert response[0].data["id"] == "42"
    assert response.extract_plain_text() == "确实。"


def test_random_sticker_response_uses_onebot_image_segment(tmp_path):
    sticker = tmp_path / "sticker.png"
    response = chat._build_random_group_response(sticker)

    assert response.type == "image"
    assert response.data["file"] == Path(sticker).resolve().as_uri()
