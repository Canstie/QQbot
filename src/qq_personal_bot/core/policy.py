from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque
from collections.abc import Callable

from qq_personal_bot.core.models import MessageEvent, PolicyDecision
from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.replies import direct_lua_command, has_direct_reply


class RateLimiter:
    def __init__(
        self,
        per_group_seconds: float,
        per_user_per_minute: int,
        clock: Callable[[], float] | None = None,
    ):
        self.per_group_seconds = per_group_seconds
        self.per_user_per_minute = per_user_per_minute
        self.clock = clock or time.time
        self._group_last: dict[int, float] = {}
        self._user_events: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, group_id: int | None, user_id: int) -> tuple[bool, str]:
        now = self.clock()
        if group_id is not None and self.per_group_seconds > 0:
            last_seen = self._group_last.get(group_id)
            if last_seen is not None and now - last_seen < self.per_group_seconds:
                return False, "group_rate_limited"

        if self.per_user_per_minute > 0:
            user_window = self._user_events[user_id]
            while user_window and now - user_window[0] >= 60:
                user_window.popleft()
            if len(user_window) >= self.per_user_per_minute:
                return False, "user_rate_limited"

        if group_id is not None and self.per_group_seconds > 0:
            self._group_last[group_id] = now
        if self.per_user_per_minute > 0:
            self._user_events[user_id].append(now)
        return True, "ok"

    def update_limits(self, per_group_seconds: float, per_user_per_minute: int) -> None:
        self.per_group_seconds = per_group_seconds
        self.per_user_per_minute = per_user_per_minute


class PolicyEngine:
    def __init__(self, store: PolicyStore, rate_limiter: RateLimiter | None = None):
        self.store = store
        self.rate_limiter = rate_limiter or RateLimiter(
            store.get_per_group_seconds(),
            store.get_per_user_per_minute(),
        )

    def evaluate(self, event: MessageEvent, self_id: int | str) -> PolicyDecision:
        if event.group_id is None:
            return PolicyDecision(False, "private_message")

        mode = self.store.get_mode()
        if mode == "allowlist" and not self.store.is_group_enabled(event.group_id):
            return PolicyDecision(False, "group_not_enabled")
        if mode == "blocklist" and self.store.is_group_blocked(event.group_id):
            return PolicyDecision(False, "group_blocked")

        is_self_message = str(event.user_id) == str(self_id)
        trigger = (
            self._extract_self_trigger_text(event)
            if is_self_message
            else self._extract_trigger_text(event)
        )
        if trigger is None:
            reason = "self_message" if is_self_message else "no_trigger"
            return PolicyDecision(False, reason)
        trigger_text, handler = trigger

        if handler == "direct" and not self._allow_direct_trigger(event):
            return PolicyDecision(False, "direct_trigger_skipped")

        if is_self_message:
            return PolicyDecision(True, "ok", handler=handler, normalized_message=trigger_text)

        self.rate_limiter.update_limits(
            self.store.get_per_group_seconds(),
            self.store.get_per_user_per_minute(),
        )
        allowed, reason = self.rate_limiter.allow(event.group_id, event.user_id)
        if not allowed:
            return PolicyDecision(False, reason)

        return PolicyDecision(True, "ok", handler=handler, normalized_message=trigger_text)

    def _allow_direct_trigger(self, event: MessageEvent) -> bool:
        percent = self.store.get_direct_trigger_percent()
        if percent <= 0:
            return False
        if percent >= 100:
            return True

        source = "|".join(
            [
                str(event.group_id),
                str(event.user_id),
                str(event.message_id),
                str(event.timestamp),
                event.raw_message.strip(),
            ]
        )
        bucket = int(hashlib.sha256(source.encode("utf-8")).hexdigest()[:8], 16) % 10000
        return bucket < int(percent * 100)

    def _extract_self_trigger_text(self, event: MessageEvent) -> tuple[str, str] | None:
        raw_message = event.raw_message.strip()
        if raw_message.startswith("/bot"):
            return None

        for prefix in self.store.prefixes():
            if raw_message.startswith(prefix):
                return raw_message[len(prefix) :].strip(), "default"

        lua_command = direct_lua_command(raw_message)
        if lua_command is not None:
            return lua_command, "lua"

        return None

    def _extract_trigger_text(self, event: MessageEvent) -> tuple[str, str] | None:
        raw_message = event.raw_message.strip()
        if raw_message.startswith("/bot"):
            return None

        for prefix in self.store.prefixes():
            if raw_message.startswith(prefix):
                return raw_message[len(prefix) :].strip(), "default"

        if self.store.get_trigger_mention() and event.is_at_bot:
            return raw_message.strip(), "mention"

        lua_command = direct_lua_command(raw_message)
        if lua_command is not None:
            return lua_command, "lua"

        if has_direct_reply(raw_message):
            return raw_message, "direct"

        return None
