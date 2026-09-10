from __future__ import annotations

from types import SimpleNamespace

import pytest
from nonebot.exception import IgnoredException

from qq_personal_bot.plugins.self_guard import ignore_self_message, is_self_message


def test_self_message_detection_handles_numeric_and_string_ids():
    assert is_self_message(SimpleNamespace(user_id=123, self_id="123"))
    assert not is_self_message(SimpleNamespace(user_id=123, self_id="456"))
    assert not is_self_message(SimpleNamespace(user_id=123))


@pytest.mark.asyncio
async def test_self_message_is_stopped_before_matchers_run():
    with pytest.raises(IgnoredException):
        await ignore_self_message(SimpleNamespace(user_id=123, self_id=123))


@pytest.mark.asyncio
async def test_other_users_continue_normally():
    await ignore_self_message(SimpleNamespace(user_id=123, self_id=456))
