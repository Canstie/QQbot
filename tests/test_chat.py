from __future__ import annotations

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
