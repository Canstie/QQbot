from __future__ import annotations

import pytest

from qq_personal_bot.xiaoheihe_captcha import (
    XiaoheiheCaptchaStore,
    build_xiaoheihe_captcha_url,
)


def test_challenge_is_deduplicated_and_expires():
    store = XiaoheiheCaptchaStore()
    first, first_created = store.create(
        source_url="https://api.xiaoheihe.cn/share?link_id=example",
        group_id=123,
        bot_id="456",
        appid="199251710",
        now=100,
    )
    duplicate, duplicate_created = store.create(
        source_url=first.source_url,
        group_id=123,
        bot_id="456",
        appid="199251710",
        now=101,
    )

    assert first_created is True
    assert duplicate_created is False
    assert duplicate.token == first.token
    assert store.get(first.token, now=699) == first
    assert store.get(first.token, now=700) is None


def test_challenge_claim_prevents_parallel_processing_and_can_be_released():
    store = XiaoheiheCaptchaStore()
    challenge, _ = store.create(
        source_url="https://api.xiaoheihe.cn/share?link_id=example",
        group_id=123,
        bot_id="456",
        appid="199251710",
        now=100,
    )

    assert store.claim(challenge.token, now=101) == challenge
    assert store.claim(challenge.token, now=101) is None
    store.release(challenge.token)
    assert store.claim(challenge.token, now=102) == challenge
    assert store.consume(challenge.token) == challenge
    assert store.get(challenge.token, now=103) is None


def test_builds_captcha_url_and_rejects_non_http_base():
    assert (
        build_xiaoheihe_captcha_url(
            "https://bot.example.com/qqbot/",
            "abc_123",
        )
        == "https://bot.example.com/qqbot/xiaoheihe-captcha/abc_123"
    )

    with pytest.raises(ValueError):
        build_xiaoheihe_captcha_url("/qqbot", "abc")
