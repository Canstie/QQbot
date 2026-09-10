from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

SessionEventKind = Literal["start", "end", "network_recovered"]


@dataclass(frozen=True)
class SessionEvent:
    kind: SessionEventKind
    session: dict[str, Any]
    reason: str = ""


def apply_player_snapshot(
    store: Any,
    *,
    steam_id: str,
    game_id: str,
    game_name: str,
    observed_at: float,
    exit_grace_seconds: int,
    baseline: bool = False,
) -> list[SessionEvent]:
    """Apply a reliable Steam snapshot to the persisted session state machine."""
    current = store.get_open_steam_session(steam_id)
    if current is None:
        if not game_id:
            return []
        started = store.start_steam_session(
            steam_id,
            game_id,
            game_name,
            started_at=observed_at,
        )
        return [] if baseline else [SessionEvent("start", started)]

    if game_id and game_id != current["game_id"]:
        closed = store.close_steam_session(
            current["id"],
            ended_at=observed_at,
            reason="game_switch",
        )
        started = store.start_steam_session(
            steam_id,
            game_id,
            game_name,
            started_at=observed_at,
        )
        events: list[SessionEvent] = []
        if closed is not None:
            events.append(SessionEvent("end", closed, "game_switch"))
        events.append(SessionEvent("start", started, "game_switch"))
        return events

    if game_id == current["game_id"] and game_id:
        if current["state"] == "confirming_exit":
            store.resume_steam_session(current["id"])
            resumed = store.get_open_steam_session(steam_id) or current
            return [SessionEvent("network_recovered", resumed, "same_game_resumed")]
        return []

    if not game_id and current["state"] == "playing":
        store.mark_steam_session_confirming(
            current["id"],
            observed_at + max(0, int(exit_grace_seconds)),
        )
    return []


def close_due_sessions(store: Any, *, now: float) -> list[SessionEvent]:
    events: list[SessionEvent] = []
    for session in store.due_confirming_steam_sessions(now):
        closed = store.close_steam_session(
            session["id"],
            ended_at=float(session["exit_deadline"] or now),
            reason="exit_confirmed",
        )
        if closed is not None:
            events.append(SessionEvent("end", closed, "exit_confirmed"))
    return events
