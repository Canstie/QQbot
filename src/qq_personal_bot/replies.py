from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

RuleType = Literal["exact", "contains", "prefix", "regex"]


@dataclass(frozen=True)
class ReplyRule:
    type: RuleType
    pattern: str
    reply: str


@dataclass(frozen=True)
class DirectLuaRule:
    type: RuleType
    pattern: str
    command: str


@dataclass(frozen=True)
class ReplyConfig:
    empty: str
    fallback: str
    rules: tuple[ReplyRule, ...]
    direct_rules: tuple[ReplyRule, ...] = ()
    direct_lua_rules: tuple[DirectLuaRule, ...] = ()


DEFAULT_CONFIG = ReplyConfig(
    empty="Bot is enabled. Send text after the trigger prefix for a response.",
    fallback="Received: {message}",
    rules=(),
)


def build_reply(content: str, config_path: str | Path = "replies.json", *, direct: bool = False) -> str:
    message = content.strip()
    config = load_reply_config(config_path)

    if not message:
        return config.empty

    rules = config.direct_rules if direct else config.rules
    for rule in rules:
        if _matches(rule, message):
            return _render(rule.reply, message, rule)

    return _render(config.fallback, message, None)


@lru_cache(maxsize=16)
def load_reply_config(config_path: str | Path = "replies.json") -> ReplyConfig:
    path = Path(config_path)
    if not path.exists():
        return DEFAULT_CONFIG

    with path.open("r", encoding="utf-8") as file:
        raw = json.load(file)

    return parse_reply_config(raw)


def parse_reply_config(raw: dict[str, Any]) -> ReplyConfig:
    rules = _parse_rules(raw.get("rules", []), "rules")
    direct_rules = _parse_rules(raw.get("direct_rules", []), "direct_rules")
    direct_lua_rules = _parse_direct_lua_rules(
        raw.get("direct_lua_rules", []),
        "direct_lua_rules",
    )

    return ReplyConfig(
        empty=str(raw.get("empty", DEFAULT_CONFIG.empty)),
        fallback=str(raw.get("fallback", DEFAULT_CONFIG.fallback)),
        rules=tuple(rules),
        direct_rules=tuple(direct_rules),
        direct_lua_rules=tuple(direct_lua_rules),
    )


def has_direct_reply(content: str, config_path: str | Path = "replies.json") -> bool:
    message = content.strip()
    if not message:
        return False

    config = load_reply_config(config_path)
    return any(_matches(rule, message) for rule in config.direct_rules)


def direct_lua_command(content: str, config_path: str | Path = "replies.json") -> str | None:
    message = content.strip()
    if not message:
        return None

    config = load_reply_config(config_path)
    for rule in config.direct_lua_rules:
        if _matches(rule, message):
            return rule.command
    return None


def reply_config_to_dict(config: ReplyConfig) -> dict[str, Any]:
    data = {
        "empty": config.empty,
        "fallback": config.fallback,
        "rules": [_rule_to_dict(rule) for rule in config.rules],
        "direct_rules": [_rule_to_dict(rule) for rule in config.direct_rules],
    }
    if config.direct_lua_rules:
        data["direct_lua_rules"] = [
            _direct_lua_rule_to_dict(rule) for rule in config.direct_lua_rules
        ]
    return data


def _parse_rules(items: Any, field_name: str) -> list[ReplyRule]:
    if items is None:
        return []
    if not isinstance(items, list):
        raise ValueError(f"{field_name} must be a list")

    rules = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"{field_name}[{index}] must be an object")

        rule_type = item.get("type")
        if rule_type not in {"exact", "contains", "prefix", "regex"}:
            raise ValueError(f"{field_name}[{index}].type must be exact, contains, prefix, or regex")

        pattern = str(item.get("pattern", ""))
        reply = str(item.get("reply", ""))
        if not pattern:
            raise ValueError(f"{field_name}[{index}].pattern cannot be empty")
        if not reply:
            raise ValueError(f"{field_name}[{index}].reply cannot be empty")

        if rule_type == "regex":
            re.compile(pattern)

        rules.append(ReplyRule(type=rule_type, pattern=pattern, reply=reply))

    return rules


def _parse_direct_lua_rules(items: Any, field_name: str) -> list[DirectLuaRule]:
    if items is None:
        return []
    if not isinstance(items, list):
        raise ValueError(f"{field_name} must be a list")

    rules = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"{field_name}[{index}] must be an object")

        rule_type = item.get("type")
        if rule_type not in {"exact", "contains", "prefix", "regex"}:
            raise ValueError(f"{field_name}[{index}].type must be exact, contains, prefix, or regex")

        pattern = str(item.get("pattern", ""))
        command = str(item.get("command", ""))
        if not pattern:
            raise ValueError(f"{field_name}[{index}].pattern cannot be empty")
        if not command:
            raise ValueError(f"{field_name}[{index}].command cannot be empty")

        if rule_type == "regex":
            re.compile(pattern)

        rules.append(DirectLuaRule(type=rule_type, pattern=pattern, command=command))

    return rules


def _rule_to_dict(rule: ReplyRule) -> dict[str, str]:
    return {"type": rule.type, "pattern": rule.pattern, "reply": rule.reply}


def _direct_lua_rule_to_dict(rule: DirectLuaRule) -> dict[str, str]:
    return {"type": rule.type, "pattern": rule.pattern, "command": rule.command}


def reload_reply_config() -> None:
    load_reply_config.cache_clear()


def _matches(rule: ReplyRule, message: str) -> bool:
    if rule.type == "exact":
        return message == rule.pattern
    if rule.type == "contains":
        return rule.pattern in message
    if rule.type == "prefix":
        return message.startswith(rule.pattern)
    if rule.type == "regex":
        return re.search(rule.pattern, message) is not None
    return False


def _render(template: str, message: str, rule: ReplyRule | None) -> str:
    stripped_message = message
    if rule and rule.type == "prefix":
        stripped_message = message.removeprefix(rule.pattern).strip()

    return template.format(
        message=stripped_message,
        raw_message=message,
        pattern=rule.pattern if rule else "",
    )
