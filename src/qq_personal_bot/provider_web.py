from __future__ import annotations

import asyncio
import time

from fastapi import HTTPException, Request
from pydantic import BaseModel, SecretStr

from qq_personal_bot.ai_providers import provider_settings
from qq_personal_bot.dsapi import DSAPIError, _request_chat_completion, fetch_dsapi_models


class ProviderPayload(BaseModel):
    name: str
    base_url: str
    default_model: str
    api_key: SecretStr | None = None
    api_format: str = "openai"
    tls_mode: str = "auto"
    timeout_seconds: float = 60


class ProviderTestPayload(BaseModel):
    model: str | None = None


def register_ai_provider_routes(app, get_settings, get_store, require_token):
    def public_state():
        store = get_store()
        return {
            "providers": store.list_ai_providers(),
            "active_provider_id": int(store.get_setting("ai_active_provider_id", "0")),
            "managed": store.ai_provider_managed(),
        }

    @app.get("/api/dsapi/providers")
    async def list_providers(request: Request):
        require_token(request)
        return public_state()

    def save(payload, provider_id=None):
        key = payload.api_key.get_secret_value() if payload.api_key else None
        return get_store().save_ai_provider(
            name=payload.name,
            base_url=payload.base_url,
            default_model=payload.default_model,
            api_format=payload.api_format,
            tls_mode=payload.tls_mode,
            timeout_seconds=payload.timeout_seconds,
            api_key=key or None,
            provider_id=provider_id,
        )

    @app.post("/api/dsapi/providers")
    async def create_provider(payload: ProviderPayload, request: Request):
        require_token(request)
        try:
            provider_id = save(payload)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"provider_id": provider_id, **public_state()}

    @app.put("/api/dsapi/providers/{provider_id}")
    async def update_provider(provider_id: int, payload: ProviderPayload, request: Request):
        require_token(request)
        try:
            save(payload, provider_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return public_state()

    @app.post("/api/dsapi/providers/{provider_id}/activate")
    async def activate_provider(provider_id: int, request: Request):
        require_token(request)
        try:
            get_store().activate_ai_provider(provider_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return public_state()

    @app.delete("/api/dsapi/providers/{provider_id}")
    async def delete_provider(provider_id: int, request: Request):
        require_token(request)
        try:
            get_store().delete_ai_provider(provider_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return public_state()

    @app.post("/api/dsapi/providers/{provider_id}/models/refresh")
    async def refresh_models(provider_id: int, request: Request):
        require_token(request)
        try:
            provider = get_store().get_ai_provider(provider_id)
            settings = provider_settings(get_settings(), provider)
            models = await asyncio.to_thread(fetch_dsapi_models, settings)
            get_store().save_ai_provider_models(
                provider_id, models, expected_updated_at=provider["updated_at"]
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except DSAPIError as exc:
            raise HTTPException(502, f"获取模型失败：{exc}") from exc
        return {"models": models, "count": len(models), **public_state()}

    @app.post("/api/dsapi/providers/{provider_id}/test")
    async def test_provider(provider_id: int, payload: ProviderTestPayload, request: Request):
        require_token(request)
        try:
            provider = get_store().get_ai_provider(provider_id)
            settings = provider_settings(get_settings(), provider)
            model = payload.model or settings.dsapi_model
            start = time.monotonic()
            reply = await asyncio.to_thread(
                _request_chat_completion,
                settings,
                [{"role": "user", "content": "Reply with exactly OK."}],
                model=model,
                max_tokens=128,
                thinking_enabled=False,
            )
            if not reply:
                raise DSAPIError("模型未返回文字，请选择支持对话的模型重试")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except DSAPIError as exc:
            raise HTTPException(502, f"连接测试失败：{exc}") from exc
        return {
            "ok": True,
            "model": model,
            "reply": reply[:80],
            "latency_ms": round((time.monotonic() - start) * 1000),
        }
