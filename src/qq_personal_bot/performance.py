from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from nonebot import logger


@dataclass(slots=True)
class LatencyTrace:
    operation: str
    started_at: float = field(default_factory=time.perf_counter)
    phases_ms: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    metadata: dict[str, str] = field(default_factory=dict)

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        started_at = time.perf_counter()
        try:
            yield
        finally:
            self.phases_ms[str(name)] += (time.perf_counter() - started_at) * 1000

    def annotate(self, **values: Any) -> None:
        for key, value in values.items():
            if value is not None:
                self.metadata[str(key)] = str(value)

    def payload(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "total_ms": round((time.perf_counter() - self.started_at) * 1000, 3),
            "phases_ms": {key: round(value, 3) for key, value in sorted(self.phases_ms.items())},
            **self.metadata,
        }

    def emit(self) -> None:
        logger.info(
            "PERF "
            + json.dumps(
                self.payload(),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )


_current_trace: ContextVar[LatencyTrace | None] = ContextVar(
    "qqbot_current_latency_trace", default=None
)


def bind_latency_trace(trace: LatencyTrace) -> Token[LatencyTrace | None]:
    return _current_trace.set(trace)


def reset_latency_trace(token: Token[LatencyTrace | None]) -> None:
    _current_trace.reset(token)


@contextmanager
def latency_phase(name: str) -> Iterator[None]:
    trace = _current_trace.get()
    if trace is None:
        yield
        return
    with trace.phase(name):
        yield


def annotate_latency(**values: Any) -> None:
    trace = _current_trace.get()
    if trace is not None:
        trace.annotate(**values)
