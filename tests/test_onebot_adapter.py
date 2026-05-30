from __future__ import annotations

from qq_personal_bot.adapters.onebot import onebot_to_internal


class FakeSegment:
    def __init__(self, segment_type, data):
        self.type = segment_type
        self.data = data


class FakeEvent:
    user_id = 10000
    group_id = 123
    message_id = 456
    time = 1000
    to_me = False

    def __init__(self, message):
        self.message = message


def test_onebot_event_conversion_detects_at_and_text():
    event = FakeEvent(
        [
            FakeSegment("at", {"qq": "99999"}),
            FakeSegment("text", {"text": " ~hello"}),
        ]
    )

    converted = onebot_to_internal(event, self_id=99999)

    assert converted.platform == "onebot.v11"
    assert converted.group_id == 123
    assert converted.user_id == 10000
    assert converted.is_at_bot is True
    assert converted.raw_message == "~hello"
