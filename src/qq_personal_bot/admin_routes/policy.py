from typing import Any, Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from qq_personal_bot.features import feature_catalog
from qq_personal_bot.runtime import get_settings, get_steam_service, get_store


class ModePayload(BaseModel):
    mode: Literal["allowlist", "blocklist"]


class PrefixesPayload(BaseModel):
    prefixes: list[str]


class DirectTriggerPercentPayload(BaseModel):
    percent: float


class CoreConfigPayload(BaseModel):
    mode: Literal["allowlist", "blocklist"]
    enabled_groups: list[int]
    blocked_groups: list[int]
    admins: list[int]
    trigger: dict[str, Any]
    limits: dict[str, Any]
    bilibili_blocked_groups: list[int] = Field(default_factory=list)


class FeatureTogglePayload(BaseModel):
    enabled: bool


def register_routes(app, require_token):
    @app.get("/api/policy")
    async def get_policy() -> dict:
        return get_store().snapshot()

    @app.post("/api/policy/mode")
    async def set_mode(payload: ModePayload, request: Request) -> dict:
        require_token(request)
        get_store().set_mode(payload.mode, actor_id=0)
        return get_store().snapshot()

    @app.post("/api/policy/prefixes")
    async def set_prefixes(payload: PrefixesPayload, request: Request) -> dict:
        require_token(request)
        get_store().set_prefixes(payload.prefixes, actor_id=0)
        return get_store().snapshot()

    @app.post("/api/policy/direct-trigger-percent")
    async def set_direct_trigger_percent(
        payload: DirectTriggerPercentPayload,
        request: Request,
    ) -> dict:
        require_token(request)
        try:
            get_store().set_direct_trigger_percent(payload.percent, actor_id=0)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return get_store().snapshot()

    @app.post("/api/policy/core")
    async def set_core_config(payload: CoreConfigPayload, request: Request) -> dict:
        require_token(request)
        try:
            get_store().set_core_config(
                mode=payload.mode,
                enabled_groups=payload.enabled_groups,
                blocked_groups=payload.blocked_groups,
                admins=payload.admins,
                trigger_mention=bool(payload.trigger.get("mention", True)),
                prefixes=list(payload.trigger.get("prefixes", [])),
                direct_trigger_percent=float(payload.trigger.get("direct_trigger_percent", 10)),
                per_group_seconds=float(payload.limits.get("per_group_seconds", 5)),
                per_user_per_minute=int(payload.limits.get("per_user_per_minute", 5)),
                actor_id=0,
                bilibili_blocked_groups=payload.bilibili_blocked_groups,
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return get_store().snapshot()

    @app.post("/api/groups/{group_id}/on")
    async def group_on(group_id: int, request: Request) -> dict:
        require_token(request)
        store = get_store()
        if store.get_mode() == "allowlist":
            store.set_group_enabled(group_id, True, actor_id=0)
        else:
            store.set_group_blocked(group_id, False, actor_id=0)
        return store.snapshot()

    @app.post("/api/groups/{group_id}/off")
    async def group_off(group_id: int, request: Request) -> dict:
        require_token(request)
        store = get_store()
        if store.get_mode() == "allowlist":
            store.set_group_enabled(group_id, False, actor_id=0)
        else:
            store.set_group_blocked(group_id, True, actor_id=0)
        return store.snapshot()

    @app.post("/api/groups/{group_id}/block")
    async def group_block(group_id: int, request: Request) -> dict:
        require_token(request)
        get_store().set_group_blocked(group_id, True, actor_id=0)
        return get_store().snapshot()

    @app.post("/api/groups/{group_id}/unblock")
    async def group_unblock(group_id: int, request: Request) -> dict:
        require_token(request)
        get_store().set_group_blocked(group_id, False, actor_id=0)
        return get_store().snapshot()

    @app.get("/api/features")
    async def get_features() -> dict:
        return {"features": get_store().list_feature_flags(get_settings())}

    @app.put("/api/features/{feature_id:path}")
    async def set_feature(
        feature_id: str,
        payload: FeatureTogglePayload,
        request: Request,
    ) -> dict:
        require_token(request)
        known_ids = {item.id for item in feature_catalog(get_settings().lua_dir)}
        if feature_id not in known_ids:
            raise HTTPException(status_code=404, detail="unknown feature")
        if feature_id == "steam.master" and payload.enabled and not get_settings().steam_api_key:
            raise HTTPException(status_code=409, detail="请先配置 QQBOT_STEAM_API_KEY")
        get_store().set_feature_enabled(feature_id, payload.enabled, actor_id=0)
        if feature_id.startswith("steam."):
            get_steam_service().wake()
        return next(
            item
            for item in get_store().list_feature_flags(get_settings())
            if item["id"] == feature_id
        )
