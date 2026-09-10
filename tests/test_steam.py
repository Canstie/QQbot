from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.runtime import reset_runtime
from qq_personal_bot.settings import AppSettings
from qq_personal_bot.steam.client import SteamClient, SteamClientError
from qq_personal_bot.steam.service import SteamMonitorService
from qq_personal_bot.steam.session import apply_player_snapshot, close_due_sessions
from qq_personal_bot.web import create_app

STEAM_ID = "76561198000000001"


class _BatchClient:
    def __init__(self, *, fail: bool = False, players=()):
        self.fail = fail
        self.players = {str(player["steamid"]): dict(player) for player in players}
        self.batches: list[list[str]] = []

    async def get_player_summaries(self, steam_ids, *, retries=3):
        self.batches.append(list(steam_ids))
        if self.fail:
            raise SteamClientError("mock outage")
        return [self.players[steam_id] for steam_id in steam_ids if steam_id in self.players]

    async def close(self):
        return None


def _store(tmp_path: Path) -> PolicyStore:
    store = PolicyStore(tmp_path / "qqbot.sqlite3")
    store.initialize(AppSettings(db_path=store.path, admins=(10000,)))
    return store


def test_feature_flags_preserve_defaults_and_persist(tmp_path):
    store = _store(tmp_path)
    assert store.is_feature_enabled("ai.master")
    assert not store.is_feature_enabled("steam.master", False)

    store.set_feature_enabled("gallery.random", False, actor_id=10000)
    reopened = PolicyStore(store.path)
    reopened.initialize(AppSettings(db_path=store.path, admins=(10000,)))

    assert not reopened.is_feature_enabled("gallery.random")
    assert any(item["id"] == "lua.command.help" for item in reopened.list_feature_flags(AppSettings(db_path=store.path, admins=())))


def test_subscription_does_not_require_binding_and_unbind_keeps_monitor(tmp_path):
    store = _store(tmp_path)
    store.set_steam_group(123, monitor_enabled=True, actor_id=10000)
    item = store.add_steam_subscription(123, STEAM_ID, alias="Alice", actor_id=10000)

    assert item["qq_user_id"] is None
    assert store.due_steam_ids(1) == [STEAM_ID]

    binding = store.bind_steam_user(123, 456, STEAM_ID, actor_id=10000)
    assert binding["steam_id"] == STEAM_ID
    assert store.get_steam_subscription(123, STEAM_ID)["qq_user_id"] == 456

    assert store.unbind_steam_user(123, 456, actor_id=10000)
    assert store.get_steam_subscription(123, STEAM_ID)["qq_user_id"] is None


def test_binding_automatically_adds_missing_subscription(tmp_path):
    store = _store(tmp_path)
    binding = store.bind_steam_user(123, 456, STEAM_ID)

    assert binding["steam_id"] == STEAM_ID
    assert store.get_steam_subscription(123, STEAM_ID)["qq_user_id"] == 456


def test_session_state_machine_debounces_exit_and_handles_switch(tmp_path):
    store = _store(tmp_path)
    started = apply_player_snapshot(
        store,
        steam_id=STEAM_ID,
        game_id="10",
        game_name="Game A",
        observed_at=1000,
        exit_grace_seconds=180,
    )
    assert [event.kind for event in started] == ["start"]

    assert apply_player_snapshot(
        store,
        steam_id=STEAM_ID,
        game_id="",
        game_name="",
        observed_at=1100,
        exit_grace_seconds=180,
    ) == []
    assert close_due_sessions(store, now=1279) == []

    recovered = apply_player_snapshot(
        store,
        steam_id=STEAM_ID,
        game_id="10",
        game_name="Game A",
        observed_at=1200,
        exit_grace_seconds=180,
    )
    assert [event.kind for event in recovered] == ["network_recovered"]

    switched = apply_player_snapshot(
        store,
        steam_id=STEAM_ID,
        game_id="20",
        game_name="Game B",
        observed_at=1300,
        exit_grace_seconds=180,
    )
    assert [event.kind for event in switched] == ["end", "start"]
    assert switched[0].session["close_reason"] == "game_switch"


def test_confirmed_exit_is_closed_once(tmp_path):
    store = _store(tmp_path)
    apply_player_snapshot(
        store,
        steam_id=STEAM_ID,
        game_id="10",
        game_name="Game A",
        observed_at=1000,
        exit_grace_seconds=180,
    )
    apply_player_snapshot(
        store,
        steam_id=STEAM_ID,
        game_id="",
        game_name="",
        observed_at=1100,
        exit_grace_seconds=180,
    )

    events = close_due_sessions(store, now=1280)
    assert [event.kind for event in events] == ["end"]
    assert events[0].session["ended_at"] == 1280
    assert close_due_sessions(store, now=1400) == []


def test_monitor_deduplicates_cross_group_ids_and_batches_at_100(tmp_path):
    store = _store(tmp_path)
    store.set_steam_settings({"max_group_size": 200})
    store.set_feature_enabled("steam.master", True)
    for group_id in (123, 456):
        store.set_steam_group(group_id, monitor_enabled=True)
    steam_ids = [str(76561198000000000 + index) for index in range(1, 102)]
    for steam_id in steam_ids:
        store.add_steam_subscription(123, steam_id)
    for steam_id in steam_ids[:20]:
        store.add_steam_subscription(456, steam_id)

    fake = _BatchClient()
    service = SteamMonitorService(
        store,
        AppSettings(db_path=store.path, admins=(), steam_api_key="test-key"),
        client_factory=lambda _settings: fake,
        clock=lambda: 1000,
    )
    asyncio.run(service.tick())

    assert [len(batch) for batch in fake.batches] == [100, 1]
    assert len({steam_id for batch in fake.batches for steam_id in batch}) == 101


def test_api_failure_does_not_close_an_active_session(tmp_path):
    store = _store(tmp_path)
    store.set_feature_enabled("steam.master", True)
    store.set_steam_group(123, monitor_enabled=True)
    store.add_steam_subscription(123, STEAM_ID)
    apply_player_snapshot(
        store,
        steam_id=STEAM_ID,
        game_id="10",
        game_name="Game A",
        observed_at=900,
        exit_grace_seconds=180,
    )
    service = SteamMonitorService(
        store,
        AppSettings(db_path=store.path, admins=(), steam_api_key="test-key"),
        client_factory=lambda _settings: _BatchClient(fail=True),
        clock=lambda: 1000,
    )

    asyncio.run(service.tick())

    assert store.get_open_steam_session(STEAM_ID)["state"] == "playing"
    assert service.last_error == "mock outage"


def test_reenable_forces_a_baseline_without_false_start_notification(tmp_path):
    store = _store(tmp_path)
    store.set_feature_enabled("steam.master", True)
    store.set_steam_group(123, monitor_enabled=True)
    store.add_steam_subscription(123, STEAM_ID)
    now = [1000.0]
    fake = _BatchClient(
        players=[
            {
                "steamid": STEAM_ID,
                "personaname": "Alice",
                "gameid": "10",
                "gameextrainfo": "Game A",
            }
        ]
    )
    service = SteamMonitorService(
        store,
        AppSettings(db_path=store.path, admins=(), steam_api_key="test-key"),
        client_factory=lambda _settings: fake,
        clock=lambda: now[0],
    )
    notifications = []

    async def capture(event, *, player=None):
        notifications.append(event.kind)

    service._notify_session_event = capture
    asyncio.run(service.tick())
    assert notifications == []

    store.set_feature_enabled("steam.master", False)
    now[0] = 1005
    asyncio.run(service.tick())
    assert store.get_open_steam_session(STEAM_ID) is None

    store.set_feature_enabled("steam.master", True)
    now[0] = 1010
    asyncio.run(service.tick())

    assert notifications == []
    assert store.get_open_steam_session(STEAM_ID)["game_id"] == "10"
    assert len(fake.batches) == 2


def test_notification_idempotency_persists(tmp_path):
    store = _store(tmp_path)
    assert not store.steam_notification_sent("session-1", 123, "start")
    store.mark_steam_notification_sent("session-1", 123, "start")

    reopened = PolicyStore(store.path)
    assert reopened.steam_notification_sent("session-1", 123, "start")


def test_optional_cover_and_price_services_use_mocked_http(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v2/grids/steam/"):
            assert request.headers["authorization"] == "Bearer sgdb-key"
            return httpx.Response(200, json={"data": [{"url": "https://images.test/cover.jpg"}]})
        if request.url.host == "images.test":
            return httpx.Response(200, content=b"cover", headers={"content-type": "image/jpeg"})
        if request.url.path == "/games/lookup/v1":
            assert request.url.params["key"] == "itad-key"
            return httpx.Response(200, json={"found": True, "game": {"id": "game-uuid"}})
        if request.url.path == "/games/prices/v3":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "game-uuid",
                        "historyLow": {
                            "all": {"amount": 12.5, "currency": "CNY"},
                        },
                    }
                ],
            )
        raise AssertionError(f"unexpected request: {request.url}")

    client = SteamClient(
        AppSettings(
            db_path=tmp_path / "qqbot.sqlite3",
            admins=(),
            sgdb_api_key="sgdb-key",
            itad_api_key="itad-key",
        ),
        transport=httpx.MockTransport(handler),
    )

    async def scenario():
        assert await client.get_game_cover("10") == b"cover"
        price = await client.get_itad_price("10", country="CN")
        assert price["historyLow"]["all"]["amount"] == 12.5
        await client.close()

    asyncio.run(scenario())


def test_feature_and_steam_web_apis(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "qqbot.sqlite3"))
    monkeypatch.setenv("QQBOT_STEAM_API_KEY", "test-key")
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    reset_runtime()
    client = TestClient(create_app())

    features = client.get("/api/features")
    assert features.status_code == 200
    assert any(item["id"] == "steam.master" for item in features.json()["features"])
    enabled = client.put("/api/features/steam.master", json={"enabled": True})
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True

    group = client.put(
        "/api/steam/groups/123",
        json={"monitor_enabled": True, "achievement_enabled": True},
    )
    assert group.status_code == 200
    player = client.post(
        "/api/steam/groups/123/players",
        json={"identifier": STEAM_ID, "alias": "Alice", "qq_user_id": None},
    )
    assert player.status_code == 200
    assert player.json()["qq_user_id"] is None
    assert client.get("/api/steam/groups/123/players").json()["players"][0]["alias"] == "Alice"

    reset_runtime()


def test_steam_master_cannot_be_enabled_without_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "qqbot.sqlite3"))
    monkeypatch.delenv("QQBOT_STEAM_API_KEY", raising=False)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    reset_runtime()

    response = TestClient(create_app()).put(
        "/api/features/steam.master",
        json={"enabled": True},
    )

    assert response.status_code == 409
    assert "QQBOT_STEAM_API_KEY" in response.json()["detail"]
    reset_runtime()
