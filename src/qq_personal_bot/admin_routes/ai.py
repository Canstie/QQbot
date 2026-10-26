import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from qq_personal_bot import ai_client
from qq_personal_bot.ai_errors import DSAPIError
from qq_personal_bot.ai_providers import (
    active_ai_model,
    ai_proxy_state,
    resolve_ai_settings,
)
from qq_personal_bot.memory_graph import get_memory_status, memory_pause_reason
from qq_personal_bot.runtime import get_memory_graph_worker, get_settings, get_store


class DSAPIConfigPayload(BaseModel):
    enabled: bool = True
    knowledge_enabled: bool = False
    knowledge_prompt: str | None = None
    active_knowledge_id: int | None = None
    history_turns: int = 2
    random_reply_percent: float = 2.0
    random_sticker_percent: float = 20.0
    enabled_groups: list[int] = Field(default_factory=list)
    clear_history: bool = True


class QuoteMemoryPayload(BaseModel):
    enabled_groups: list[int] = Field(default_factory=list)


class MemoryRetryPayload(BaseModel):
    group_id: int | None = Field(default=None, gt=0)


class KnowledgeBasePayload(BaseModel):
    name: str
    prompt: str = ""
    model: str | None = None
    thinking_enabled: bool | None = None
    max_tokens: int | None = None
    history_turns: int | None = None
    response_mode: str | None = None
    max_reply_messages: int = 1
    temperature: float | None = None
    web_search_enabled: bool = False
    relationship_memory_enabled: bool = False
    style_examples: list[dict[str, Any]] = Field(default_factory=list)
    context_messages: int = 10
    similar_examples: int = 0


class KnowledgeActivationPayload(BaseModel):
    clear_history: bool = True
    name: str | None = None
    prompt: str | None = None
    model: str | None = None
    thinking_enabled: bool | None = None
    max_tokens: int | None = None
    history_turns: int | None = None
    response_mode: str | None = None
    max_reply_messages: int | None = None
    temperature: float | None = None
    web_search_enabled: bool | None = None
    relationship_memory_enabled: bool | None = None
    style_examples: list[dict[str, Any]] | None = None
    context_messages: int | None = None
    similar_examples: int | None = None


def register_routes(app, require_token):
    @app.get("/api/dsapi")
    async def get_dsapi_config() -> dict:
        config = get_store().get_dsapi_config()
        settings = resolve_ai_settings(get_settings(), get_store())
        active_knowledge = config.get("active_knowledge") or {}
        return {
            **config,
            "api_configured": bool(settings.dsapi_enabled and settings.dsapi_api_key),
            "model": active_ai_model(config, settings),
            "max_tokens": active_knowledge.get("max_tokens") or settings.dsapi_max_tokens,
            "thinking_enabled": settings.dsapi_api_format == "deepseek"
            and bool(active_knowledge.get("thinking_enabled", False)),
            "thinking_supported": settings.dsapi_api_format == "deepseek",
            "response_mode": active_knowledge.get("response_mode") or "short",
            "max_reply_messages": active_knowledge.get("max_reply_messages") or 1,
            "temperature": active_knowledge.get("temperature"),
            "default_model": settings.dsapi_model,
            "default_max_tokens": settings.dsapi_max_tokens,
            "base_url": settings.dsapi_base_url,
            "history_idle_seconds": settings.dsapi_history_idle_seconds,
            "model_options": get_store().get_ai_model_options(),
            "providers": get_store().list_ai_providers(),
            "active_provider_id": int(get_store().get_setting("ai_active_provider_id", "0")),
            "provider_managed": settings.dsapi_provider_managed,
            "background_route": get_store().get_background_ai_route(),
            **ai_proxy_state(get_settings(), get_store()),
        }

    @app.post("/api/dsapi")
    async def save_dsapi_config(payload: DSAPIConfigPayload, request: Request) -> dict:
        require_token(request)
        try:
            get_store().set_dsapi_config(
                enabled=payload.enabled,
                knowledge_enabled=payload.knowledge_enabled,
                knowledge_prompt=payload.knowledge_prompt,
                active_knowledge_id=payload.active_knowledge_id,
                history_turns=payload.history_turns,
                random_reply_percent=payload.random_reply_percent,
                random_sticker_percent=payload.random_sticker_percent,
                enabled_groups=payload.enabled_groups,
                clear_history=payload.clear_history,
                actor_id=0,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return await get_dsapi_config()

    @app.get("/api/dsapi/quotes")
    @app.get("/api/dsapi/memory")
    async def get_memory_config() -> dict:
        return await asyncio.to_thread(get_memory_status, get_store(), get_settings())

    @app.put("/api/dsapi/quotes")
    @app.put("/api/dsapi/memory")
    async def save_quote_memory_config(payload: QuoteMemoryPayload, request: Request) -> dict:
        require_token(request)
        try:
            get_store().set_memory_groups(payload.enabled_groups, actor_id=0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return await get_memory_config()

    @app.post("/api/dsapi/memory/retry")
    async def retry_memory(payload: MemoryRetryPayload, request: Request) -> dict:
        require_token(request)
        store, settings = get_store(), get_settings()
        groups = [payload.group_id] if payload.group_id is not None else store.memory_enabled_groups()
        ready, skipped = [], []
        for group_id in groups:
            reason = await asyncio.to_thread(memory_pause_reason, store, settings, group_id)
            if reason:
                if payload.group_id is not None:
                    raise HTTPException(status_code=409, detail=reason)
                skipped.append({"group_id": group_id, "detail": reason})
            else:
                ready.append(group_id)
        try:
            queued = await asyncio.to_thread(store.request_memory_retry, ready, actor_id=0)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if queued:
            get_memory_graph_worker().wake()
        return {"queued_groups": queued, "skipped_groups": skipped, **await get_memory_config()}

    @app.delete("/api/dsapi/quotes/{group_id}")
    @app.delete("/api/dsapi/memory/{group_id}")
    async def clear_quote_memory(
        group_id: int,
        request: Request,
        user_id: int | None = None,
    ) -> dict:
        require_token(request)
        try:
            deleted = get_store().clear_memory(
                group_id,
                user_id=user_id,
                actor_id=0,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"deleted": deleted, **await get_memory_config()}

    @app.get("/api/dsapi/memory/{group_id}/{user_id}")
    async def get_person_memory(group_id: int, user_id: int, request: Request) -> dict:
        require_token(request)
        if group_id <= 0 or user_id <= 0:
            raise HTTPException(status_code=400, detail="群号和 QQ 号必须为正整数")
        return get_store().get_person_graph(group_id, user_id, limit=40)

    @app.post("/api/dsapi/models/refresh")
    async def refresh_dsapi_models(request: Request) -> dict:
        require_token(request)
        store = get_store()
        settings = resolve_ai_settings(get_settings(), store)
        provider = store.get_active_ai_provider()
        if not settings.dsapi_api_key:
            raise HTTPException(status_code=400, detail="DSAPI API Key 未配置")
        try:
            models = await asyncio.to_thread(ai_client.fetch_dsapi_models, settings)
            if provider:
                store.save_ai_provider_models(
                    provider["id"], models, expected_updated_at=provider["updated_at"]
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except DSAPIError as exc:
            raise HTTPException(status_code=502, detail=f"获取 DSAPI 模型失败：{exc}") from exc
        return {
            "models": models,
            "count": len(models),
            "fetched_at": datetime.now(UTC).isoformat(),
            "base_url": settings.dsapi_base_url,
        }

    @app.post("/api/dsapi/knowledge")
    async def create_knowledge_base(payload: KnowledgeBasePayload, request: Request) -> dict:
        require_token(request)
        try:
            knowledge_base = get_store().create_dsapi_knowledge_base(
                name=payload.name,
                prompt=payload.prompt,
                actor_id=0,
                model=(
                    payload.model
                    if payload.model is not None
                    else resolve_ai_settings(get_settings(), get_store()).dsapi_model
                ),
                thinking_enabled=bool(payload.thinking_enabled),
                max_tokens=(
                    payload.max_tokens
                    if payload.max_tokens is not None
                    else get_settings().dsapi_max_tokens
                ),
                history_turns=(payload.history_turns if payload.history_turns is not None else 2),
                response_mode=payload.response_mode or "short",
                max_reply_messages=payload.max_reply_messages,
                temperature=payload.temperature,
                web_search_enabled=payload.web_search_enabled,
                relationship_memory_enabled=payload.relationship_memory_enabled,
                style_examples=payload.style_examples,
                context_messages=payload.context_messages,
                similar_examples=payload.similar_examples,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {**(await get_dsapi_config()), "knowledge_base": knowledge_base}

    @app.put("/api/dsapi/knowledge/{knowledge_id}")
    async def update_knowledge_base(
        knowledge_id: int,
        payload: KnowledgeBasePayload,
        request: Request,
    ) -> dict:
        require_token(request)
        try:
            knowledge_base = get_store().update_dsapi_knowledge_base(
                knowledge_id,
                name=payload.name,
                prompt=payload.prompt,
                actor_id=0,
                model=payload.model,
                thinking_enabled=payload.thinking_enabled,
                max_tokens=payload.max_tokens,
                history_turns=payload.history_turns,
                response_mode=payload.response_mode,
                max_reply_messages=payload.max_reply_messages,
                temperature=payload.temperature,
                web_search_enabled=payload.web_search_enabled,
                relationship_memory_enabled=payload.relationship_memory_enabled,
                style_examples=payload.style_examples,
                context_messages=payload.context_messages,
                similar_examples=payload.similar_examples,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {**(await get_dsapi_config()), "knowledge_base": knowledge_base}

    @app.delete("/api/dsapi/knowledge/{knowledge_id}")
    async def delete_knowledge_base(knowledge_id: int, request: Request) -> dict:
        require_token(request)
        try:
            result = get_store().delete_dsapi_knowledge_base(knowledge_id, actor_id=0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {**result, **(await get_dsapi_config())}

    @app.delete("/api/dsapi/knowledge/{knowledge_id}/relationships")
    async def clear_knowledge_relationships(
        knowledge_id: int,
        request: Request,
    ) -> dict:
        require_token(request)
        try:
            deleted = get_store().clear_dsapi_relationship_memory(
                knowledge_id,
                actor_id=0,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"deleted": deleted, **(await get_dsapi_config())}

    @app.post("/api/dsapi/knowledge/{knowledge_id}/activate")
    async def activate_knowledge_base(
        knowledge_id: int,
        payload: KnowledgeActivationPayload,
        request: Request,
    ) -> dict:
        require_token(request)
        try:
            if payload.name is not None or payload.prompt is not None:
                if payload.name is None or payload.prompt is None:
                    raise ValueError("name and prompt are both required when saving")
                get_store().update_dsapi_knowledge_base(
                    knowledge_id,
                    name=payload.name,
                    prompt=payload.prompt,
                    actor_id=0,
                    model=payload.model,
                    thinking_enabled=payload.thinking_enabled,
                    max_tokens=payload.max_tokens,
                    history_turns=payload.history_turns,
                    response_mode=payload.response_mode,
                    max_reply_messages=payload.max_reply_messages,
                    temperature=payload.temperature,
                    web_search_enabled=payload.web_search_enabled,
                    relationship_memory_enabled=payload.relationship_memory_enabled,
                    style_examples=payload.style_examples,
                    context_messages=payload.context_messages,
                    similar_examples=payload.similar_examples,
                    activate=True,
                    clear_history=payload.clear_history,
                )
                result = {"active_knowledge_id": knowledge_id}
            else:
                result = get_store().activate_dsapi_knowledge_base(
                    knowledge_id,
                    clear_history=payload.clear_history,
                    actor_id=0,
                )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {**result, **(await get_dsapi_config())}

    @app.delete("/api/dsapi/history")
    async def clear_dsapi_history(request: Request) -> dict:
        require_token(request)
        deleted = get_store().clear_dsapi_chat_history(actor_id=0)
        return {"deleted": deleted, **(await get_dsapi_config())}
