from __future__ import annotations

import json

from qq_personal_bot.core.models import MessageEvent
from qq_personal_bot.core.policy import PolicyEngine, RateLimiter
from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.replies import reload_reply_config
from qq_personal_bot.settings import AppSettings


def make_store(tmp_path, *, mode: str = "allowlist") -> PolicyStore:
    store = PolicyStore(tmp_path / "policy.sqlite3")
    store.initialize(
        AppSettings(
            db_path=tmp_path / "policy.sqlite3",
            admins=(10000,),
            policy_mode=mode,
            trigger_prefixes=("~", "#bot"),
            per_group_seconds=5,
            per_user_per_minute=5,
        )
    )
    return store


def make_event(
    *,
    group_id: int = 123,
    user_id: int = 20000,
    raw_message: str = "~hello",
    is_at_bot: bool = False,
) -> MessageEvent:
    return MessageEvent(
        platform="onebot.v11",
        message_id=1,
        group_id=group_id,
        user_id=user_id,
        raw_message=raw_message,
        is_at_bot=is_at_bot,
        timestamp=1,
    )


def test_allowlist_requires_enabled_group(tmp_path):
    store = make_store(tmp_path)
    engine = PolicyEngine(store, RateLimiter(0, 0))

    decision = engine.evaluate(make_event(), self_id=99999)

    assert not decision.allowed
    assert decision.reason == "group_not_enabled"


def test_allowlist_allows_enabled_group_with_prefix(tmp_path):
    store = make_store(tmp_path)
    store.set_group_enabled(123, True, actor_id=10000)
    engine = PolicyEngine(store, RateLimiter(0, 0))

    decision = engine.evaluate(make_event(raw_message="~hello"), self_id=99999)

    assert decision.allowed
    assert decision.handler == "default"
    assert decision.normalized_message == "hello"


def test_mention_trigger_allows_when_enabled(tmp_path):
    store = make_store(tmp_path)
    store.set_group_enabled(123, True, actor_id=10000)
    engine = PolicyEngine(store, RateLimiter(0, 0))

    decision = engine.evaluate(make_event(raw_message="hello", is_at_bot=True), self_id=99999)

    assert decision.allowed
    assert decision.handler == "mention"
    assert decision.normalized_message == "hello"


def test_no_trigger_is_rejected(tmp_path):
    store = make_store(tmp_path)
    store.set_group_enabled(123, True, actor_id=10000)
    engine = PolicyEngine(store, RateLimiter(0, 0))

    decision = engine.evaluate(make_event(raw_message="hello"), self_id=99999)

    assert not decision.allowed
    assert decision.reason == "no_trigger"


def test_direct_reply_rule_allows_without_prefix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "replies.json").write_text(
        '{"direct_rules":[{"type":"contains","pattern":"keyword","reply":"direct"}]}',
        encoding="utf-8",
    )
    reload_reply_config()
    store = make_store(tmp_path)
    store.set_group_enabled(123, True, actor_id=10000)
    store.set_limits(0, 0, actor_id=10000)
    store.set_direct_trigger_percent(100, actor_id=10000)
    engine = PolicyEngine(store, RateLimiter(0, 0))

    decision = engine.evaluate(make_event(raw_message="hello keyword"), self_id=99999)

    assert decision.allowed
    assert decision.handler == "direct"
    assert decision.normalized_message == "hello keyword"


def test_direct_lua_rule_routes_without_prefix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "replies.json").write_text(
        json.dumps(
            {
                "direct_lua_rules": [
                    {"type": "exact", "pattern": "吃什么", "command": "今日菜单"},
                    {
                        "type": "regex",
                        "pattern": "^[!！]?(?:(?:今|明|后)(?:天|日))?(?:(?:早上|上午|中午|下午|晚上|夜宵|早餐|午餐|晚餐|早|中|午|晚))?吃(?:什么|啥|点啥)[？?]?$",
                        "command": "今日菜单",
                    },
                    {"type": "exact", "pattern": "csm", "command": "今日菜单"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    reload_reply_config()
    store = make_store(tmp_path)
    store.set_group_enabled(123, True, actor_id=10000)
    store.set_limits(0, 0, actor_id=10000)
    store.set_direct_trigger_percent(100, actor_id=10000)
    engine = PolicyEngine(store, RateLimiter(0, 0))

    exact = engine.evaluate(make_event(raw_message="吃什么"), self_id=99999)
    regex_matched = engine.evaluate(make_event(raw_message="今天吃什么"), self_id=99999)
    csm = engine.evaluate(make_event(raw_message="csm"), self_id=99999)
    should_ignore = engine.evaluate(make_event(raw_message="吃什么都行"), self_id=99999)

    assert exact.allowed
    assert exact.handler == "lua"
    assert exact.normalized_message == "今日菜单"
    assert regex_matched.allowed
    assert regex_matched.handler == "lua"
    assert regex_matched.normalized_message == "今日菜单"
    assert csm.allowed
    assert csm.handler == "lua"
    assert csm.normalized_message == "今日菜单"
    assert not should_ignore.allowed
    assert should_ignore.reason == "no_trigger"


def test_direct_trigger_percent_zero_skips_direct_reply_and_lua(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "replies.json").write_text(
        json.dumps(
            {
                "direct_rules": [{"type": "contains", "pattern": "keyword", "reply": "direct"}],
                "direct_lua_rules": [{"type": "exact", "pattern": "csm", "command": "今日菜单"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    reload_reply_config()
    store = make_store(tmp_path)
    store.set_group_enabled(123, True, actor_id=10000)
    store.set_limits(0, 0, actor_id=10000)
    store.set_direct_trigger_percent(0, actor_id=10000)
    engine = PolicyEngine(store, RateLimiter(0, 0))

    direct = engine.evaluate(make_event(raw_message="hello keyword"), self_id=99999)
    lua = engine.evaluate(make_event(raw_message="csm"), self_id=99999)

    assert not direct.allowed
    assert direct.reason == "direct_trigger_skipped"
    assert not lua.allowed
    assert lua.reason == "direct_trigger_skipped"


def test_prefix_trigger_takes_priority_over_direct_rule(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "replies.json").write_text(
        '{"direct_rules":[{"type":"contains","pattern":"keyword","reply":"direct"}]}',
        encoding="utf-8",
    )
    reload_reply_config()
    store = make_store(tmp_path)
    store.set_group_enabled(123, True, actor_id=10000)
    engine = PolicyEngine(store, RateLimiter(0, 0))

    decision = engine.evaluate(make_event(raw_message="~keyword"), self_id=99999)

    assert decision.allowed
    assert decision.handler == "default"
    assert decision.normalized_message == "keyword"


def test_self_prefix_command_is_allowed(tmp_path):
    store = make_store(tmp_path)
    store.set_group_enabled(123, True, actor_id=10000)
    engine = PolicyEngine(store, RateLimiter(0, 0))

    decision = engine.evaluate(
        make_event(user_id=99999, raw_message="~\u4eca\u65e5\u4eba\u54c1"),
        self_id=99999,
    )

    assert decision.allowed
    assert decision.handler == "default"
    assert decision.normalized_message == "\u4eca\u65e5\u4eba\u54c1"


def test_self_message_without_prefix_is_ignored(tmp_path):
    store = make_store(tmp_path)
    store.set_group_enabled(123, True, actor_id=10000)
    engine = PolicyEngine(store, RateLimiter(0, 0))

    decision = engine.evaluate(make_event(user_id=99999, raw_message="hello"), self_id=99999)

    assert not decision.allowed
    assert decision.reason == "self_message"


def test_self_message_uses_direct_lua_rules(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "replies.json").write_text(
        json.dumps(
            {
                "direct_lua_rules": [
                    {"type": "exact", "pattern": "csm", "command": "今日菜单"},
                    {
                        "type": "regex",
                        "pattern": "^(?:今天)?吃(?:什么|啥)$",
                        "command": "今日菜单",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    reload_reply_config()
    store = make_store(tmp_path)
    store.set_group_enabled(123, True, actor_id=10000)
    store.set_direct_trigger_percent(100, actor_id=10000)
    engine = PolicyEngine(store, RateLimiter(0, 0))

    csm = engine.evaluate(make_event(user_id=99999, raw_message="csm"), self_id=99999)
    regex_matched = engine.evaluate(
        make_event(user_id=99999, raw_message="今天吃什么"),
        self_id=99999,
    )
    should_ignore = engine.evaluate(make_event(user_id=99999, raw_message="吃什么都行"), self_id=99999)

    assert csm.allowed
    assert csm.handler == "lua"
    assert csm.normalized_message == "今日菜单"
    assert regex_matched.allowed
    assert regex_matched.handler == "lua"
    assert regex_matched.normalized_message == "今日菜单"
    assert not should_ignore.allowed
    assert should_ignore.reason == "self_message"
    reload_reply_config()


def test_self_mention_is_ignored(tmp_path):
    store = make_store(tmp_path)
    store.set_group_enabled(123, True, actor_id=10000)
    store.set_direct_trigger_percent(100, actor_id=10000)
    engine = PolicyEngine(store, RateLimiter(0, 0))

    decision = engine.evaluate(
        make_event(user_id=99999, raw_message="hello", is_at_bot=True),
        self_id=99999,
    )

    assert not decision.allowed
    assert decision.reason == "self_message"


def test_self_prefix_command_still_requires_enabled_group(tmp_path):
    store = make_store(tmp_path)
    engine = PolicyEngine(store, RateLimiter(0, 0))

    decision = engine.evaluate(
        make_event(user_id=99999, raw_message="~\u4eca\u65e5\u4eba\u54c1"),
        self_id=99999,
    )

    assert not decision.allowed
    assert decision.reason == "group_not_enabled"


def test_self_prefix_command_does_not_update_rate_limit(tmp_path):
    store = make_store(tmp_path)
    store.set_group_enabled(123, True, actor_id=10000)
    engine = PolicyEngine(store, RateLimiter(5, 1, clock=lambda: 1000.0))

    self_decision = engine.evaluate(
        make_event(user_id=99999, raw_message="~\u4eca\u65e5\u4eba\u54c1"),
        self_id=99999,
    )
    user_decision = engine.evaluate(
        make_event(user_id=20000, raw_message="~\u4eca\u65e5\u4eba\u54c1"),
        self_id=99999,
    )

    assert self_decision.allowed
    assert user_decision.allowed


def test_blocklist_blocks_only_blocked_groups(tmp_path):
    store = make_store(tmp_path, mode="blocklist")
    engine = PolicyEngine(store, RateLimiter(0, 0))

    assert engine.evaluate(make_event(group_id=1), self_id=99999).allowed

    store.set_group_blocked(1, True, actor_id=10000)
    decision = engine.evaluate(make_event(group_id=1), self_id=99999)

    assert not decision.allowed
    assert decision.reason == "group_blocked"


def test_group_rate_limit(tmp_path):
    store = make_store(tmp_path)
    store.set_group_enabled(123, True, actor_id=10000)
    now = 1000.0
    engine = PolicyEngine(store, RateLimiter(5, 0, clock=lambda: now))

    first = engine.evaluate(make_event(user_id=1), self_id=99999)
    second = engine.evaluate(make_event(user_id=2), self_id=99999)

    assert first.allowed
    assert not second.allowed
    assert second.reason == "group_rate_limited"


def test_user_rate_limit(tmp_path):
    store = make_store(tmp_path)
    store.set_group_enabled(123, True, actor_id=10000)
    store.set_limits(0, 1, actor_id=10000)
    now = 1000.0
    engine = PolicyEngine(store, RateLimiter(0, 1, clock=lambda: now))

    first = engine.evaluate(make_event(user_id=1), self_id=99999)
    second = engine.evaluate(make_event(user_id=1), self_id=99999)

    assert first.allowed
    assert not second.allowed
    assert second.reason == "user_rate_limited"
