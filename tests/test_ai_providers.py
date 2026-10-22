from __future__ import annotations

import json
import ssl
from dataclasses import replace
from unittest.mock import AsyncMock
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from qq_personal_bot import ai_client, ai_transport, dsapi, group_digest, memory_graph
from qq_personal_bot.ai_models import catalog_model_option, is_vision_dsapi_model
from qq_personal_bot.ai_providers import (
    active_ai_model,
    provider_settings,
    resolve_ai_settings,
    resolve_background_ai_settings,
)
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
    monkeypatch.delenv("QQBOT_AI_PROXY_URL", raising=False)
    monkeypatch.delenv("QQBOT_AI_HOST_OVERRIDES", raising=False)
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


@pytest.mark.parametrize("model", [
    "deepseek-v4.1-flash", "deepseek/deepseek-v4.1-flash", "deepseek-flash",
    "deepseek-v4-flash-vision-exp",
])
def test_flash_vision_aliases_preserve_requested_model_id(model):
    assert is_vision_dsapi_model(model)
    option = catalog_model_option(model)
    assert option["vision"] is True
    assert option["id"] == model


@pytest.mark.parametrize("cached", [False, True])
def test_saved_text_only_flag_does_not_disable_known_flash_vision(context, cached):
    client, store, settings = context
    model = "deepseek-v4.1-flash"
    provider_id = add_provider(store, "Miku", model)
    raw = store.get_ai_provider(provider_id)
    if cached:
        store.save_ai_provider_models(
            provider_id, [{"id": model, "vision": False, "source": "live"}],
            expected_updated_at=raw["updated_at"],
        )
    # Also cover provider_settings callers that have not loaded a normalized catalog.
    raw["models"] = [{"id": model, "vision": False}] if cached else []
    assert provider_settings(settings, raw).dsapi_vision_enabled
    store.activate_ai_provider(provider_id)
    assert resolve_ai_settings(settings, store).dsapi_vision_enabled
    config = client.get("/api/dsapi").json()
    assert config["model"] == model
    assert next(option for option in config["model_options"] if option["id"] == model)["vision"]
    if cached:
        public = next(p for p in config["providers"] if p["id"] == provider_id)
        assert public["models"][0]["vision"]
    restarted = PolicyStore(settings.db_path)
    restarted.initialize(settings)
    assert resolve_ai_settings(settings, restarted).dsapi_vision_enabled


@pytest.mark.parametrize("model", ["deepseek-v4-pro", "unknown-text-model"])
def test_unrelated_text_models_are_not_promoted_to_vision(context, model):
    _, store, settings = context
    provider = add_provider(store, model=model)
    store.activate_ai_provider(provider)
    assert not is_vision_dsapi_model(model)
    assert not catalog_model_option(model)["vision"]
    assert not resolve_ai_settings(settings, store).dsapi_vision_enabled


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


def test_global_ai_proxy_survives_provider_switch(context):
    _, store, settings = context
    settings = replace(settings, ai_proxy_url="http://127.0.0.1:20171")
    first = add_provider(store)
    second = add_provider(store, "Second", "qwen3.8-max")
    for provider_id in (first, second):
        store.activate_ai_provider(provider_id)
        routed = resolve_ai_settings(settings, store)
        assert routed.ai_proxy_url == settings.ai_proxy_url
        assert routed.dsapi_provider_id == provider_id


def test_routes_are_independent_for_the_same_provider_and_survive_restart(context):
    client, store, settings = context
    miku = add_provider(store, "Miku", "background-model")
    store.set_ai_route("background", miku, "background-model")
    store.set_ai_route("message", miku, "reply-model")
    assert resolve_ai_settings(settings, store).dsapi_model == "reply-model"
    background = resolve_background_ai_settings(settings, store)
    assert background.dsapi_model == "background-model"
    store.set_ai_provider_model("another-reply-model", actor_id=0)
    assert resolve_background_ai_settings(settings, store).dsapi_model == "background-model"
    other = add_provider(store, "Other", "other-model")
    store.activate_ai_provider(other)
    # A background call must discard an already resolved message provider snapshot.
    routed = resolve_background_ai_settings(resolve_ai_settings(settings, store), store)
    assert routed.dsapi_provider_id == miku
    assert routed.dsapi_model == "background-model"
    with pytest.raises(ValueError, match="后台链路"):
        store.delete_ai_provider(miku)
    restarted = PolicyStore(settings.db_path)
    restarted.initialize(settings)
    assert resolve_background_ai_settings(settings, restarted).dsapi_model == "background-model"
    assert resolve_ai_settings(settings, restarted).dsapi_provider_id == other
    for path in ("/api/dsapi", "/api/dsapi/providers"):
        response = client.get(path)
        assert response.json()["background_route"] == {
            "provider_id": miku, "model": "background-model",
        }
        assert "new-secret" not in response.text
    store.set_ai_route("background", other, "other-background-model")
    # A running extraction keeps its original snapshot after a UI switch.
    assert resolve_background_ai_settings(background, store).dsapi_model == "background-model"
    assert resolve_ai_settings(settings, store).dsapi_model == "other-model"
    store.delete_ai_provider(miku)


def test_route_api_validates_before_switching_and_keeps_background_model(context):
    client, store, settings = context
    provider = add_provider(store)
    assert client.put("/api/dsapi/routes/background", json={
        "provider_id": provider, "model": "pinned-model",
    }).status_code == 200
    assert store.get_active_ai_provider()["id"] == 1
    assert client.put("/api/dsapi/routes/message", json={
        "provider_id": provider, "model": "reply-model",
    }).status_code == 200
    assert resolve_ai_settings(settings, store).dsapi_model == "reply-model"
    for route in ("message", "background"):
        for payload, status in [
            ({"provider_id": 99999, "model": "valid-model"}, 400),
            ({"provider_id": provider, "model": ""}, 400),
            ({"provider_id": True, "model": "valid-model"}, 422),
        ]:
            assert client.put(f"/api/dsapi/routes/{route}", json=payload).status_code == status
    assert resolve_ai_settings(settings, store).dsapi_model == "reply-model"
    assert resolve_background_ai_settings(settings, store).dsapi_model == "pinned-model"
    # Editing the shared provider model cannot change the saved background model.
    data = {"name": "Edited", "base_url": "https://other.example/v1",
            "default_model": "edited-reply", "api_key": None}
    assert client.put(f"/api/dsapi/providers/{provider}", json=data).status_code == 200
    assert resolve_background_ai_settings(settings, store).dsapi_model == "pinned-model"
    assert resolve_background_ai_settings(settings, store).dsapi_base_url == data["base_url"]
    with store._connect() as conn:
        audit = json.dumps([dict(row) for row in conn.execute("SELECT * FROM audit_log")])
    assert "new-secret" not in audit


def test_broken_background_binding_never_falls_back_to_reply_provider(context):
    _, store, settings = context
    provider = add_provider(store)
    store.activate_ai_provider(provider)
    store.set_setting("ai_background_route", json.dumps({"provider_id": 9999, "model": "pinned"}))
    with pytest.raises(ValueError, match="不存在"):
        resolve_background_ai_settings(resolve_ai_settings(settings, store), store)


@pytest.mark.parametrize("managed", [False, True])
def test_web_proxy_toggle_is_persistent_immediate_and_keeps_request_snapshot(
    context, monkeypatch, managed,
):
    client, _, _ = context
    monkeypatch.setenv("QQBOT_AI_PROXY_URL", "http://127.0.0.1:20171")
    reset_runtime()
    store, settings = get_store(), get_settings()
    if managed:
        store.activate_ai_provider(add_provider(store))
    initial = client.get("/api/dsapi").json()
    assert initial["ai_proxy_configured"] is True
    assert initial["ai_proxy_enabled"] is True
    snapshot = resolve_ai_settings(settings, store)
    response = client.put("/api/dsapi/proxy", json={"enabled": False})
    assert response.status_code == 200
    assert response.json() == {"ai_proxy_configured": True, "ai_proxy_enabled": False}
    assert resolve_ai_settings(settings, store).ai_proxy_url == ""
    assert resolve_ai_settings(snapshot, store).ai_proxy_url == settings.ai_proxy_url
    store.activate_ai_provider(add_provider(store, "Second", "qwen3.8-max"))
    assert resolve_ai_settings(settings, store).ai_proxy_url == ""
    restarted = PolicyStore(settings.db_path)
    restarted.initialize(settings)
    assert resolve_ai_settings(settings, restarted).ai_proxy_url == ""
    assert client.put("/api/dsapi/proxy", json={"enabled": True}).status_code == 200
    assert resolve_ai_settings(settings, store).ai_proxy_url == settings.ai_proxy_url
    assert client.get("/api/dsapi/providers").json()["ai_proxy_enabled"] is True


def test_proxy_toggle_never_exposes_credentials_and_rejects_unconfigured_proxy(context, monkeypatch):
    client, _, _ = context
    assert client.get("/api/dsapi").json()["ai_proxy_configured"] is False
    assert client.put("/api/dsapi/proxy", json={"enabled": True}).status_code == 400
    assert client.put("/api/dsapi/proxy", json={"enabled": "false"}).status_code == 422
    monkeypatch.setenv("QQBOT_AI_PROXY_URL", "http://proxy-user:proxy-secret@127.0.0.1:20171")
    reset_runtime()
    for path in ("/api/dsapi", "/api/dsapi/providers"):
        response = client.get(path)
        assert response.json()["ai_proxy_configured"] is True
        assert "proxy-secret" not in response.text
        assert "proxy-user" not in response.text
    assert client.put("/api/dsapi/proxy", json={"enabled": False}).status_code == 200
    with get_store()._connect() as conn:
        logs = json.dumps([tuple(row) for row in conn.execute("SELECT * FROM audit_log")])
    assert "proxy-secret" not in logs


def test_models_and_connection_test_follow_the_global_proxy_toggle(context, monkeypatch):
    client, _, _ = context
    monkeypatch.setenv("QQBOT_AI_PROXY_URL", "http://127.0.0.1:20171")
    reset_runtime()
    store = get_store()
    provider_id = add_provider(store)
    routes = []

    def models(settings):
        routes.append(settings.ai_proxy_url)
        return [catalog_model_option("gemini-3.8-flash-high")]

    def complete(settings, *args, **kwargs):
        routes.append(settings.ai_proxy_url)
        return "OK"

    monkeypatch.setattr(ai_client, "fetch_dsapi_models", models)
    monkeypatch.setattr(ai_client, "request_completion", complete)
    for enabled in (False, True):
        assert client.put("/api/dsapi/proxy", json={"enabled": enabled}).status_code == 200
        assert client.post(f"/api/dsapi/providers/{provider_id}/models/refresh").status_code == 200
        assert client.post(f"/api/dsapi/providers/{provider_id}/test", json={}).status_code == 200
    assert routes == ["", "", "http://127.0.0.1:20171", "http://127.0.0.1:20171"]


def test_explicit_direct_choice_uses_transport_without_inheriting_system_proxy(context, monkeypatch):
    _, store, settings = context
    settings = replace(settings, ai_proxy_url="http://127.0.0.1:20171")
    store.set_ai_proxy_enabled(False)
    routed = resolve_ai_settings(settings, store)
    assert routed.ai_proxy_url == ""
    assert not routed.dsapi_provider_managed
    observed = []

    def request(req, settings):
        observed.append(settings.ai_proxy_url)
        return b'{"choices":[{"message":{"content":"OK"},"finish_reason":"stop"}]}'

    def legacy_request(*args, **kwargs):
        raise AssertionError("explicit direct must not inherit urllib system proxy settings")

    monkeypatch.setenv("HTTPS_PROXY", "http://unrelated.invalid:1234")
    monkeypatch.setattr(ai_client, 'request_ai_bytes', request)
    monkeypatch.setattr(ai_client, 'urlopen', legacy_request)
    assert ai_client.request_completion(routed, [{"role": "user", "content": "hello"}]) == "OK"
    assert observed == [""]


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
    monkeypatch.setattr(ai_client, "fetch_dsapi_models", lambda settings: models)
    assert client.post(f"/api/dsapi/providers/{provider_id}/models/refresh").json()["count"] == 16
    assert client.get("/api/dsapi/providers").json()["providers"][-1]["models"] == models
    captures = []
    monkeypatch.setattr(
        ai_client,
        "request_completion",
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
        ("PUT", "/api/dsapi/proxy", {"enabled": False}),
        ("PUT", "/api/dsapi/routes/message", {"provider_id": 1, "model": "m"}),
        ("PUT", "/api/dsapi/routes/background", {"provider_id": 1, "model": "m"}),
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

    monkeypatch.setattr(ai_client, 'urlopen', flaky)
    monkeypatch.setattr(dsapi.time, "sleep", lambda seconds: None)
    assert ai_client._open_ai_request(None, replace(settings, dsapi_tls_mode="tls12")) == "response"
    assert len(attempts) == 2

    def reset(*args, **kwargs):
        raise ConnectionResetError("peer reset")

    monkeypatch.setattr(ai_client, 'urlopen', reset)
    with pytest.raises(dsapi.DSAPIError, match="network connection interrupted"):
        ai_client.request_completion(settings, [{"role": "user", "content": "OK"}])


def test_provisioning_tests_catalog_before_switch_and_does_not_change_route_on_failure(
    context, monkeypatch
):
    from tools import configure_ai_provider as provision

    _, store, settings = context
    models = [catalog_model_option("gemini-3.8-flash-high"), catalog_model_option("qwen3.8-max")]
    monkeypatch.setattr(ai_client, "fetch_dsapi_models", lambda settings: models)

    def fail(*args, **kwargs):
        raise dsapi.DSAPIError("HTTP 503")

    monkeypatch.setattr(ai_client, "request_completion", fail)
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
    monkeypatch.setattr(ai_client, "request_completion", lambda *args, **kwargs: "OK")
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
@pytest.mark.parametrize("proxy_enabled", [False, True])
@pytest.mark.parametrize("background_selected", [False, True])
async def test_every_ai_entry_uses_selected_provider_and_model_without_deepseek_parameters(
    context, monkeypatch, proxy_enabled, background_selected,
):
    _, store, settings = context
    settings = replace(settings, ai_proxy_url="http://127.0.0.1:20171")
    store.set_ai_proxy_enabled(proxy_enabled)
    provider_id = add_provider(store)
    store.activate_ai_provider(provider_id)
    if background_selected:
        background_id = store.save_ai_provider(
            name="Background", base_url="https://background.example/v1",
            api_key="background-secret", default_model="unused-default", api_format="openai",
        )
        store.set_ai_route("background", background_id, "pinned-background-model")
    store.set_group_enabled(123, True, actor_id=0)
    store.enable_dsapi_group(123, actor_id=0)
    store.set_memory_groups([123], actor_id=0)
    knowledge = store.create_dsapi_knowledge_base(name="Routing", prompt="Routing test", actor_id=0)
    store.activate_dsapi_knowledge_base(knowledge["id"], clear_history=False, actor_id=0)
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
                "proxy": settings.ai_proxy_url,
            }
        )
        return Response()

    monkeypatch.setattr(ai_client, '_open_ai_request', request)
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
    from qq_personal_bot.turtle_soup import TurtleSoupService

    assert await TurtleSoupService(store, settings)._complete(
        {"id": "routing-test"}, [{"role": "user", "content": "Puzzle"}], max_tokens=600,
    ) == "OK"
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
    await dsapi._refresh_relationship_memory(
        routed, store, knowledge_id=store.get_dsapi_config()["active_knowledge_id"],
        group_id=123, user_id=456, user_message="hello", assistant_message="OK",
    )
    assert len(requests) == 7
    for index, item in enumerate(requests):
        assert item["proxy"] == ("http://127.0.0.1:20171" if proxy_enabled else "")
        background = background_selected and index >= 5
        assert item["url"] == (
            "https://background.example/v1/chat/completions" if background
            else "https://gateway.example/v1/chat/completions"
        )
        assert item["auth"] == ("Bearer background-secret" if background else "Bearer new-secret")
        assert item["body"]["model"] == (
            "pinned-background-model" if background else "gemini-3.8-flash-high"
        )
        assert "thinking" not in item["body"]


@pytest.mark.asyncio
async def test_whole_web_reply_uses_message_snapshot_when_background_has_another_model(
    context, monkeypatch,
):
    _, store, settings = context
    message = add_provider(store, "Message", "message-model")
    background = add_provider(store, "Background", "background-model")
    store.set_ai_route("message", message, "message-model")
    store.set_ai_route("background", background, "background-model")
    store.enable_dsapi_group(123, actor_id=0)
    knowledge = store.create_dsapi_knowledge_base(
        name="Web routing", prompt="Routing test", actor_id=0, web_search_enabled=True,
    )
    store.activate_dsapi_knowledge_base(knowledge["id"], clear_history=False, actor_id=0)
    captures = []

    def complete(settings, messages, **kwargs):
        captures.append((settings.dsapi_provider_id, kwargs["model"]))
        return "OK"

    async def web(complete, *args, **kwargs):
        for phase in ("plan", "verify", "answer"):
            assert complete([{"role": "user", "content": phase}]) == "OK"
            if phase == "plan":
                store.set_ai_route("message", background, "next-message-model")
        return "OK"

    monkeypatch.setattr(ai_client, 'complete_text', complete)
    monkeypatch.setattr(dsapi, "generate_web_reply", web)
    monkeypatch.setattr(dsapi, "build_mention_prompt", AsyncMock(return_value="Search current news"))
    event = MessageEvent(
        platform="onebot.v11", message_id=1, group_id=123, user_id=456,
        raw_message="Search current news", is_at_bot=True,
    )
    assert await dsapi.generate_mention_reply(None, event, settings, store) == "OK"
    assert captures == [(message, "message-model")] * 3
    assert resolve_ai_settings(settings, store).dsapi_model == "next-message-model"
    assert resolve_background_ai_settings(settings, store).dsapi_model == "background-model"
