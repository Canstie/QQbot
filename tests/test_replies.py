from __future__ import annotations

import json
import re

from qq_personal_bot.replies import build_reply, has_direct_reply, parse_reply_config, reload_reply_config


def write_config(tmp_path, payload):
    path = tmp_path / "replies.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    reload_reply_config()
    return path


def test_exact_rule(tmp_path):
    path = write_config(
        tmp_path,
        {
            "fallback": "fallback {message}",
            "rules": [{"type": "exact", "pattern": "menu", "reply": "menu reply"}],
        },
    )

    assert build_reply("menu", path) == "menu reply"


def test_contains_rule(tmp_path):
    path = write_config(
        tmp_path,
        {
            "rules": [{"type": "contains", "pattern": "hello", "reply": "hello reply"}],
        },
    )

    assert build_reply("say hello", path) == "hello reply"


def test_prefix_rule_uses_stripped_message(tmp_path):
    path = write_config(
        tmp_path,
        {
            "rules": [{"type": "prefix", "pattern": "repeat ", "reply": "{message}"}],
        },
    )

    assert build_reply("repeat  hello", path) == "hello"


def test_regex_rule(tmp_path):
    path = write_config(
        tmp_path,
        {
            "rules": [{"type": "regex", "pattern": "^status$", "reply": "online"}],
        },
    )

    assert build_reply("status", path) == "online"


def test_fallback_and_empty(tmp_path):
    path = write_config(tmp_path, {"empty": "empty", "fallback": "got {message}", "rules": []})

    assert build_reply("", path) == "empty"
    assert build_reply("other", path) == "got other"


def test_direct_rules_are_separate_from_prefixed_rules(tmp_path):
    path = write_config(
        tmp_path,
        {
            "fallback": "fallback {message}",
            "rules": [{"type": "exact", "pattern": "menu", "reply": "menu reply"}],
            "direct_rules": [
                {"type": "contains", "pattern": "keyword", "reply": "direct reply"}
            ],
        },
    )

    assert has_direct_reply("this has keyword", path)
    assert build_reply("this has keyword", path, direct=True) == "direct reply"
    assert build_reply("this has keyword", path) == "fallback this has keyword"


def test_invalid_regex_fails_fast():
    try:
        parse_reply_config({"rules": [{"type": "regex", "pattern": "[", "reply": "bad"}]})
    except re.error:
        pass
    else:
        raise AssertionError("Expected invalid regex to raise")

