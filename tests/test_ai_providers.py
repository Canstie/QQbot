from __future__ import annotations

import json
import ssl
from dataclasses import replace
from unittest.mock import AsyncMock
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from qq_personal_bot import ai_transport, dsapi, group_digest, memory_graph, provider_web
from qq_personal_bot.ai_models import catalog_model_option
from qq_personal_bot.ai_providers import active_ai_model, resolve_ai_settings
from qq_personal_bot.core.models import MessageEvent
from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.runtime import get_settings, get_store, reset_runtime
from qq_personal_bot.web import create_app


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QQBOT_WEB_TOKEN", raising=False)
    monkeypatch.setenv("QQBOT_DB_PATH", str(tmp_path / "providers.sqlite3"))
    monkeypatch.setenv("QQBOT_DSAPI_API_KEY", "old-secret")
    monkeypatch.setenv("QQBOT_DSAPI_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("QQBOT_DSAPI_MODEL", "deepseek-flash")
    reset_runtime()
    client = TestClient(create_app())
    yield client, get_store(), get_settings()
    reset_runtime()


def add_provider(store, name="Gateway", model="gemini-3.8-flash-high"):
    return store.save_ai_provider(
        name=name,
        base_url="https://gateway.example",
        api_key="new-secret",
        default_model=model,
        api_format="openai",
        actor_id=10000,
    )


def test_upgrade_preserves_legacy_models_and_env_seed_without_secret_in_public_state(context):
    client, store, settings = context
    kb = store.create_dsapi_knowledge_base(
        name="Role", prompt="Role prompt", model="deepseek-v4-pro", actor_id=0
    )
    store.activate_dsapi_knowledge_base(kb["id"], clear_history=False, actor_id=0)
    routed = resolve_ai_settings(settings, store)
    assert active_ai_model(store.get_dsapi_config(), routed) == "deepseek-v4-pro"
    assert not routed.dsapi_provider_managed
    response = client.get("/api/dsapi")
    assert response.json()["model"] == "deepseek-v4-pro"
    assert "old-secret" not in response.text
    assert "api_key" not in response.json()["providers"][0]


def test_providers_survive_restart_and_switches_are_immediate_and_keep_request_snapshot(context):
    _, store, settings = context
    a = add_provider(store)
    b = add_provider(store, "Second", "qwen3.8-max")
    store.activate_ai_provider(a)
    request_a = resolve_ai_settings(settings, store)
    assert request_a.dsapi_base_url == "https://gateway.example/v1"
    assert request_a.dsapi_api_key == "new-secret"
    store.activate_ai_provider(b)
    assert resolve_ai_settings(settings, store).dsapi_model == "qwen3.8-max"
    assert resolve_ai_settings(request_a, store).dsapi_model == "gemini-3.8-flash-high"
    restarted = PolicyStore(settings.db_path)
    restarted.initialize(replace(settings, dsapi_api_key="changed-env-secret"))
    assert restarted.get_active_ai_provider()["id"] == b
    assert restarted.get_ai_provider(1)["api_key"] == "old-secret"
    restarted.activate_ai_provider(1)
    assert resolve_ai_settings(settings, restarted).dsapi_model == "deepseek-flash"
    with pytest.raises(ValueError, match="先启用"):
        restarted.delete_ai_provider(1)
    restarted.delete_ai_provider(a)
    with pytest.raises(ValueError, match="不存在"):
        restarted.get_ai_provider(a)


@pytest.mark.parametrize(
    "url", ["file:///secret", "https://user:pass@example.com", "https://example.com/?key=secret"]
)
def test_provider_rejects_invalid_urls(context, url):
    _, store, _ = context
    with pytest.raises(ValueError, match="地址"):
        store.save_ai_provider(
            name="Invalid", base_url=url, api_key="new-secret", default_model="m"
        )


def test_full_catalog_is_persistent_and_stale_fetch_cannot_overwrite_new_configuration(context):
    _, store, _ = context
    provider_id = add_provider(store)
    old = store.get_ai_provider(provider_id)
    models = [catalog_model_option(f"custom-{i}") for i in range(16)]
    models.append(catalog_model_option("gemini-3.8-flash-high"))
    store.save_ai_provider_models(provider_id, models, expected_updated_at=old["updated_at"])
    store.activate_ai_provider(provider_id)
    assert len(store.get_ai_model_options()) == 17
    store.save_ai_provider(
        name="Gateway",
        base_url=old["base_url"],
        default_model="custom-1",
        provider_id=provider_id,
        api_key=None,
    )
    assert store.get_ai_provider(provider_id)["api_key"] == "new-secret"
    assert len(store.get_ai_model_options()) == 17
    with pytest.raises(ValueError, match="配置已变化"):
        store.save_ai_provider_models(provider_id, [], expected_updated_at=old["updated_at"])
    with store._connect() as conn:
        audit = json.dumps([dict(row) for row in conn.execute("SELECT * FROM audit_log")])
    assert "new-secret" not in audit


def test_provider_web_crud_catalog_and_connection_test_never_echo_secret(context, monkeypatch):
    client, store, _ = context
    payload = {
        "name": "Gateway",
        "base_url": "https://gateway.example",
        "api_key": "new-secret",
        "default_model": "gemini-3.8-flash-high",
    }
    created = client.post("/api/dsapi/providers", json=payload)
    assert created.status_code == 200
    provider_id = created.json()["provider_id"]
    assert "new-secret" not in created.text
    models = [catalog_model_option(f"model-{i}") for i in range(16)]
    monkeypatch.setattr(provider_web, "fetch_dsapi_models", lambda settings: models)
    assert client.post(f"/api/dsapi/providers/{provider_id}/models/refresh").json()["count"] == 16
    assert client.get("/api/dsapi/providers").json()["providers"][-1]["models"] == models
    captures = []
    monkeypatch.setattr(
        provider_web,
        "_request_chat_completion",
        lambda settings, messages, **kwargs: captures.append((settings, kwargs)) or "OK",
    )
    result = client.post(f"/api/dsapi/providers/{provider_id}/test", json={})
    assert result.status_code == 200 and result.json()["reply"] == "OK"
    assert captures[0][0].dsapi_api_key == "new-secret"
    assert "new-secret" not in result.text
    assert client.post(f"/api/dsapi/providers/{provider_id}/activate").status_code == 200
    config = client.get("/api/dsapi").json()
    assert config["model"] == "gemini-3.8-flash-high" and config["provider_managed"]
    assert not config["thinking_supported"]
    assert client.delete(f"/api/dsapi/providers/{provider_id}").status_code == 400
    payload["api_key"] = None
    payload["default_model"] = "model-3"
    assert client.put(f"/api/dsapi/providers/{provider_id}", json=payload).status_code == 200
    assert store.get_ai_provider(provider_id)["api_key"] == "new-secret"
    assert client.get("/api/dsapi").json()["model"] == "model-3"
    assert client.post("/api/dsapi/providers/1/activate").status_code == 200
    assert client.delete(f"/api/dsapi/providers/{provider_id}").status_code == 200


def test_all_provider_endpoints_require_admin_session(context, monkeypatch):
    _, _, _ = context
    monkeypatch.setenv("QQBOT_WEB_TOKEN", "admin-secret")
    reset_runtime()
    client = TestClient(create_app())
    for method, path, body in [
        ("GET", "/api/dsapi/providers", None),
        ("POST", "/api/dsapi/providers", {}),
        ("PUT", "/api/dsapi/providers/1", {}),
        ("DELETE", "/api/dsapi/providers/1", None),
        ("POST", "/api/dsapi/providers/1/activate", {}),
        ("POST", "/api/dsapi/providers/1/models/refresh", {}),
        ("POST", "/api/dsapi/providers/1/test", {}),
    ]:
        assert client.request(method, path, json=body).status_code == 401


def test_switching_knowledge_bases_keeps_global_model(context):
    client, store, settings = context
    provider_id = add_provider(store)
    store.activate_ai_provider(provider_id)
    for name, old_model in [("One", "deepseek-v4-pro"), ("Two", "another-model")]:
        knowledge = store.create_dsapi_knowledge_base(
            name=name, prompt=name, model=old_model, actor_id=0
        )
        response = client.post(
            f"/api/dsapi/knowledge/{knowledge['id']}/activate", json={"clear_history": False}
        )
        assert response.status_code == 200
        assert response.json()["model"] == "gemini-3.8-flash-high"
    store.set_active_dsapi_model("qwen3.8-max", actor_id=10000)
    assert (
        active_ai_model(store.get_dsapi_config(), resolve_ai_settings(settings, store))
        == "qwen3.8-max"
    )


def test_tls_compatibility_keeps_certificate_verification_and_is_provider_specific(
    context,
):
    _, store, settings = context
    provider_id = store.save_ai_provider(
        name="TLS gateway",
        base_url="https://gateway.example",
        default_model="gemini-3.8-flash-high",
        api_key="new-secret",
        tls_mode="tls12",
    )
    store.activate_ai_provider(provider_id)
    routed = resolve_ai_settings(settings, store)
    context = ai_transport._ssl_context(routed.dsapi_tls_mode)
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.maximum_version == ssl.TLSVersion.TLSv1_2
    assert settings.dsapi_tls_mode == "auto"


def test_tls_transport_retries_transient_server_error_and_wraps_connection_reset(
    context, monkeypatch
):
    _, _, settings = context
    attempts = []

    def flaky(request, timeout, **kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise HTTPError("https://gateway.example", 500, "temporary", None, None)
        return "response"

    monkeypatch.setattr(dsapi, "urlopen", flaky)
    monkeypatch.setattr(dsapi.time, "sleep", lambda seconds: None)
    assert dsapi._open_ai_request(None, replace(settings, dsapi_tls_mode="tls12")) == "response"
    assert len(attempts) == 2

    def reset(*args, **kwargs):
        raise ConnectionResetError("peer reset")

    monkeypatch.setattr(dsapi, "urlopen", reset)
    with pytest.raises(dsapi.DSAPIError, match="network connection interrupted"):
        dsapi._request_chat_completion(settings, [{"role": "user", "content": "OK"}])


def test_provisioning_tests_catalog_before_switch_and_does_not_change_route_on_failure(
    context, monkeypatch
):
    from tools import configure_ai_provider as provision

    _, store, settings = context
    models = [catalog_model_option("gemini-3.8-flash-high"), catalog_model_option("qwen3.8-max")]
    monkeypatch.setattr(provision, "fetch_dsapi_models", lambda settings: models)

    def fail(*args, **kwargs):
        raise dsapi.DSAPIError("HTTP 503")

    monkeypatch.setattr(provision, "_request_chat_completion", fail)
    with pytest.raises(dsapi.DSAPIError, match="Default model test failed"):
        provision.configure(
            settings,
            store,
            name="Miku",
            base_url="https://gateway.example",
            model="gemini-3.8-flash-high",
            api_key="new-secret",
            test_all=True,
        )
    assert len(store.list_ai_providers()) == 1
    assert store.get_active_ai_provider()["id"] == 1
    monkeypatch.setattr(provision, "_request_chat_completion", lambda *args, **kwargs: "OK")
    provision.configure(
        settings,
        store,
        name="Miku",
        base_url="https://gateway.example",
        model="gemini-3.8-flash-high",
        api_key="new-secret",
        test_all=True,
    )
    assert len(store.get_ai_model_options()) == 2
    assert all(item["test_status"] == "ok" for item in store.get_ai_model_options())


@pytest.mark.asyncio
async def test_every_ai_entry_uses_selected_provider_and_model_without_deepseek_parameters(
    context, monkeypatch
):
    _, store, settings = context
    provider_id = add_provider(store)
    store.activate_ai_provider(provider_id)
    store.set_group_enabled(123, True, actor_id=0)
    store.enable_dsapi_group(123, actor_id=0)
    store.set_memory_groups([123], actor_id=0)
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            text = (
                '{"claims": []}'
                if "人物记忆整理器" in requests[-1]["body"]["messages"][0]["content"]
                else "OK"
            )
            return json.dumps(
                {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}
            ).encode()

    def request(req, settings):
        requests.append(
            {
                "url": req.full_url,
                "auth": req.headers["Authorization"],
                "body": json.loads(req.data),
                "timeout": settings.dsapi_timeout_seconds,
            }
        )
        return Response()

    monkeypatch.setattr(dsapi, "_open_ai_request", request)
    monkeypatch.setattr(dsapi, "build_mention_prompt", AsyncMock(return_value="你好"))
    monkeypatch.setattr(dsapi, "refresh_memory_graph", AsyncMock())
    monkeypatch.setattr(dsapi, "graph_enabled", lambda *args: True)
    monkeypatch.setattr(dsapi, "build_graph_guidance", lambda *args: "")
    monkeypatch.setattr(
        store,
        "get_person_graph",
        lambda *args, **kwargs: {
            "edges": [
                {"evidence": [{"id": 1, "user_id": 456}, {"id": 2, "user_id": 456}]},
                {"evidence": [{"id": 3, "user_id": 456}]},
            ]
        },
    )
    event = MessageEvent(
        platform="onebot.v11",
        message_id=1,
        group_id=123,
        user_id=456,
        raw_message="你好",
        is_at_bot=True,
    )
    assert await dsapi.generate_mention_reply(None, event, settings, store) == "OK"
    # Force ordinary group replies to text, without changing the routing configuration.
    monkeypatch.setattr(dsapi, "_random_reply_selected", lambda *args, **kwargs: True)
    monkeypatch.setattr(dsapi, "_pick_random_sticker", lambda *args: None)
    assert (
        await dsapi.generate_random_group_reply(replace(event, is_at_bot=False), settings, store)
        == "OK"
    )
    assert (
        await dsapi.generate_roast_reply(
            group_id=123, target_user_id=456, settings=settings, store=store
        )
        == "OK"
    )
    routed = resolve_ai_settings(settings, store)
    assert (
        await group_digest._complete(
            routed, routed.dsapi_model, system="Digest", prompt="Summarize"
        )
        == "OK"
    )
    assert "max_tokens" not in requests[-1]["body"]
    batch = {
        "group_id": 123,
        "new_ids": [1],
        "messages": [
            {
                "id": 1,
                "message_id": "1",
                "user_id": 456,
                "display_name": "Test",
                "content": "我喜欢游戏",
                "reply_to": "",
                "mentions": [],
                "created_at": 1,
            },
        ],
    }
    monkeypatch.setattr(store, "claim_memory_batch", lambda *args, **kwargs: batch)
    monkeypatch.setattr(store, "save_memory_batch", lambda *args: True)
    assert await memory_graph.refresh_memory_graph(store, settings, 123, force=True)
    assert len(requests) == 5
    for item in requests:
        assert item["url"] == "https://gateway.example/v1/chat/completions"
        assert item["auth"] == "Bearer new-secret"
        assert item["body"]["model"] == "gemini-3.8-flash-high"
        assert "thinking" not in item["body"]
