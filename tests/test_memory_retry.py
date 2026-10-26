from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from qq_personal_bot.ai_errors import DSAPIError, DSAPILengthError, DSAPITimeoutError
from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.memory_graph import MemoryGraphWorker, get_memory_status, refresh_memory_graph
from qq_personal_bot.settings import AppSettings


@pytest.fixture
def memory(tmp_path):
    settings = AppSettings(db_path=tmp_path / "retry.sqlite3", admins=(), dsapi_api_key="test",
                           web_token="test-admin")
    store = PolicyStore(settings.db_path)
    store.initialize(settings)
    store.set_group_enabled(123, True, actor_id=0)
    store.set_memory_groups([123], actor_id=0)
    store.set_dsapi_config(enabled=True, enabled_groups=[], knowledge_enabled=False,
                           knowledge_prompt="", history_turns=2, clear_history=False, actor_id=0)
    return store, settings


def record(store, count=65, *, group=123):
    return store.record_memory_messages([
        {"group_id": group, "user_id": 456, "message_id": i + 1,
         "raw_message": f"我今天去玩了游戏{i}", "timestamp": time.time()}
        for i in range(count)
    ])


def progress(store, group=123):
    return next(p for p in store.get_memory_config()["progress"] if p["group_id"] == group)


def test_manual_retry_preserves_cursor_sources_and_live_lease(memory):
    store, _ = memory
    record(store)
    batch = store.claim_memory_batch(123, force=True)
    lease = progress(store)["lease_until"]
    assert store.request_memory_retry([123], actor_id=7) == [123]
    state = progress(store)
    assert state["cursor"] == 0 and state["lease_until"] == lease
    assert state["retry_until_id"] == 65
    assert store.claim_memory_batch(123, force=True) is None
    assert store.save_memory_batch(batch, [])
    assert progress(store)["cursor"] == 60
    # The remaining five recent messages are processed without the ordinary batching wait.
    final = store.claim_memory_batch(123)
    assert len(final["new_ids"]) == 5
    assert store.save_memory_batch(final, [])
    assert progress(store)["retry_until_id"] == 0
    assert store.get_memory_config()["message_count"] == 65
    assert store.get_memory_config()["pending_messages"] == 0


def test_manual_reset_clears_backoff_without_rewinding_completed_work(memory):
    store, _ = memory
    record(store, 125)
    batch = store.claim_memory_batch(123, force=True)
    assert store.save_memory_batch(batch, [])
    batch = store.claim_memory_batch(123, force=True)
    store.fail_memory_batch(batch)
    assert store.claim_memory_batch(123, force=True) is None
    assert store.request_memory_retry([123], actor_id=0) == [123]
    state = progress(store)
    assert state["cursor"] == 60
    assert state["failures"] == state["retry_after"] == 0
    assert state["last_error"] == ""
    assert store.claim_memory_batch(123)["new_ids"][0] == 61


def test_retry_target_survives_reopen_and_new_messages_remain_pending(memory):
    store, settings = memory
    record(store, 2)
    store.request_memory_retry([123], actor_id=0)
    reopened = PolicyStore(settings.db_path)
    reopened.initialize(settings)
    assert progress(reopened)["retry_until_id"] == 2
    batch = reopened.claim_memory_batch(123)
    assert batch and len(batch["new_ids"]) == 2
    store.record_memory_messages([{"group_id": 123, "user_id": 456, "message_id": 99,
                                   "raw_message": "后来我又想玩游戏"}])
    assert reopened.save_memory_batch(batch, [])
    assert progress(reopened)["retry_until_id"] == 0
    assert reopened.get_memory_config()["pending_messages"] == 1
    assert reopened.claim_memory_batch(123) is None


def test_clearing_memory_invalidates_retry_and_stale_model_result(memory):
    store, _ = memory
    record(store)
    store.request_memory_retry([123], actor_id=0)
    batch = store.claim_memory_batch(123)
    store.clear_memory(123, actor_id=0)
    assert progress(store)["retry_until_id"] == 0
    assert not store.save_memory_batch(batch, [])
    assert store.get_memory_config()["message_count"] == 0


def test_retry_is_scoped_and_rejects_disabled_groups(memory):
    store, _ = memory
    store.set_memory_groups([123, 789], actor_id=0)
    record(store, group=123)
    record(store, group=789)
    assert store.request_memory_retry([123], actor_id=0) == [123]
    assert progress(store, 789)["retry_until_id"] == 0
    with pytest.raises(ValueError):
        store.request_memory_retry([999], actor_id=0)
    assert store.request_memory_retry([], actor_id=0) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error,reduced", [(DSAPILengthError("truncated"), True),
    (DSAPITimeoutError("timeout"), True),
    (ValueError("bad schema"), True), (DSAPIError("HTTP 503"), False)])
async def test_failed_batch_retries_without_skipping_and_adapts_size(memory, monkeypatch, error, reduced):
    store, settings = memory
    record(store)
    store.request_memory_retry([123], actor_id=0)
    monkeypatch.setattr("qq_personal_bot.ai_client.complete_text", Mock(side_effect=error))
    assert not await refresh_memory_graph(store, settings, 123)
    state = progress(store)
    assert state["cursor"] == 0 and state["retry_until_id"] == 65
    assert state["batch_size"] == (30 if reduced else 60)
    if reduced:
        assert state["retry_after"] == 0
        monkeypatch.setattr("qq_personal_bot.ai_client.complete_text", lambda *a, **kw: '{"claims":[]}')
        assert await refresh_memory_graph(store, settings, 123)
        assert progress(store)["cursor"] == 30
    else:
        assert state["retry_after"] > time.time()
    assert store.get_memory_config()["message_count"] == 65


def test_single_message_failure_backs_off_and_stale_failure_cannot_change_state(memory):
    store, _ = memory
    record(store, 2)
    store.request_memory_retry([123], actor_id=0)
    batch = store.claim_memory_batch(123)
    with store._connect() as conn:
        conn.execute("UPDATE memory_progress SET batch_size=1 WHERE group_id=123")
    store.fail_memory_batch(batch, reduce_batch=True)
    assert progress(store)["batch_size"] == 1
    assert progress(store)["retry_after"] > time.time()
    snapshot = progress(store)
    store.fail_memory_batch(batch, reduce_batch=True)
    assert progress(store) == snapshot


@pytest.mark.asyncio
async def test_worker_drains_recent_tail_and_clears_persistent_request(memory, monkeypatch):
    store, settings = memory
    record(store)
    store.request_memory_retry([123], actor_id=0)
    monkeypatch.setattr("qq_personal_bot.ai_client.complete_text", lambda *a, **kw: '{"claims":[]}')
    worker = MemoryGraphWorker(store, settings)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while store.get_memory_config()["pending_messages"] and time.monotonic() < deadline:
            await asyncio.sleep(0.03)
        assert store.get_memory_config()["pending_messages"] == 0
        assert progress(store)["retry_until_id"] == 0
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_slow_group_does_not_block_other_groups_continuing_drain(memory, monkeypatch):
    store, settings = memory
    store.set_memory_groups([123, 789], actor_id=0)
    store.set_group_enabled(789, True, actor_id=0)
    record(store, 1)
    record(store, 1, group=789)
    store.request_memory_retry([123, 789], actor_id=0)
    fast_calls = 0

    async def refresh(_store, _settings, group_id):
        nonlocal fast_calls
        if group_id == 789:
            await asyncio.sleep(30)
        else:
            fast_calls += 1
        return True

    monkeypatch.setattr("qq_personal_bot.memory_graph.refresh_memory_graph", refresh)
    worker = MemoryGraphWorker(store, settings)
    worker.start()
    try:
        deadline = time.monotonic() + 3
        while fast_calls < 2 and time.monotonic() < deadline:
            await asyncio.sleep(0.03)
        assert fast_calls >= 2
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_manual_retry_wakes_an_idle_worker_and_start_is_idempotent(memory, monkeypatch):
    store, settings = memory
    record(store, 1)
    monkeypatch.setattr("qq_personal_bot.ai_client.complete_text", lambda *a, **kw: '{"claims":[]}')
    worker = MemoryGraphWorker(store, settings)
    worker.start()
    task = worker.task
    worker.start()
    assert worker.task is task
    try:
        await asyncio.sleep(0.1)
        assert store.get_memory_config()["pending_messages"] == 1
        store.request_memory_retry([123], actor_id=0)
        worker.wake()
        deadline = time.monotonic() + 3
        while store.get_memory_config()["pending_messages"] and time.monotonic() < deadline:
            await asyncio.sleep(0.03)
        assert store.get_memory_config()["pending_messages"] == 0
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_deepseek_extraction_sends_non_thinking_json_with_larger_budget(memory, monkeypatch):
    from dataclasses import replace

    from qq_personal_bot import ai_client

    store, settings = memory
    settings = replace(settings, dsapi_model="deepseek-v4.1-flash", dsapi_api_format="openai")
    record(store, 1)
    captured = []

    def post(_settings, body):
        captured.append(body)
        return {"choices": [{"finish_reason": "stop", "message": {"content": '{"claims":[]}'}}]}

    monkeypatch.setattr(ai_client, "_post_chat_body", post)
    ai_client._json_output_disabled_until.clear()
    assert await refresh_memory_graph(store, settings, 123, force=True)
    assert captured[0]["model"] == "deepseek-v4.1-flash"
    assert captured[0]["thinking"] == {"type": "disabled"}
    assert captured[0]["max_tokens"] == 8192
    assert captured[0]["response_format"] == {"type": "json_object"}


def test_retry_api_requires_login_resets_failed_group_and_wakes_worker(memory, monkeypatch):
    from qq_personal_bot import web
    from qq_personal_bot.admin_routes import ai, auth

    store, settings = memory
    record(store)
    batch = store.claim_memory_batch(123, force=True)
    store.fail_memory_batch(batch)
    wake = Mock()
    for module in (web, ai, auth):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    for module in (web, ai):
        monkeypatch.setattr(module, "get_store", lambda: store)
    monkeypatch.setattr(ai, "get_memory_graph_worker", lambda: SimpleNamespace(wake=wake))
    with TestClient(web.create_app()) as client:
        before = progress(store)
        assert client.post("/api/dsapi/memory/retry", json={}).status_code == 401
        assert progress(store) == before
        client.post("/login", data={"password": "test-admin"})
        assert client.post("/api/dsapi/memory/retry", json={"group_id": -1}).status_code == 422
        result = client.post("/api/dsapi/memory/retry", json={"group_id": 123})
        assert result.status_code == 200
        assert result.json()["queued_groups"] == [123]
        assert result.json()["extraction_status"][0]["manual_retry_pending"]
        wake.assert_called_once()
        store.set_feature_enabled("ai.master", False)
        assert client.post("/api/dsapi/memory/retry", json={"group_id": 123}).status_code == 409
        result = client.post("/api/dsapi/memory/retry", json={}).json()
        assert result["queued_groups"] == []
        assert result["skipped_groups"][0]["group_id"] == 123
        assert result["message_count"] == 65


def test_status_marks_manual_retry_and_preserves_pause_gate(memory):
    store, settings = memory
    record(store, 1)
    assert get_memory_status(store, settings)["extraction_status"][0]["state"] == "waiting"
    store.request_memory_retry([123], actor_id=0)
    status = get_memory_status(store, settings)["extraction_status"][0]
    assert status["state"] == "queued" and status["manual_retry_pending"]
    store.set_memory_groups([], actor_id=0)
    assert get_memory_status(store, settings)["extraction_status"][0]["state"] == "paused"
