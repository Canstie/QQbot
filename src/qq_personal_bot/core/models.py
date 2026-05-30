from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class MessageEvent:
    platform: str
    message_id: int | str
    group_id: int | None
    user_id: int
    raw_message: str
    segments: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    is_at_bot: bool = False
    timestamp: float = 0.0


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    handler: str | None = None
    normalized_message: str = ""

