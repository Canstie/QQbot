from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from qq_personal_bot.runtime import get_settings, get_steam_service, get_store
from qq_personal_bot.steam.client import SteamClientError


class SteamGroupPayload(BaseModel):
    monitor_enabled: bool = False
    achievement_enabled: bool = True


class SteamSubscriptionPayload(BaseModel):
    identifier: str
    alias: str = ""
    qq_user_id: int | None = None


class SteamBindingPayload(BaseModel):
    group_id: int
    qq_user_id: int
    identifier: str


class SteamSettingsPayload(BaseModel):
    fixed_poll_interval_seconds: int = 0
    smart_poll_intervals: list[int] = Field(default_factory=lambda: [1, 3, 5, 10, 20, 30])
    retry_times: int = 3
    exit_grace_seconds: int = 180
    max_group_size: int = 20
    price_country: str = "CN"
    price_currency: str = "CNY"
    game_filter_mode: Literal["all", "allow", "block"] = "all"
    game_filter_ids: list[str] = Field(default_factory=list)


def register_routes(app, require_token):
    @app.get("/api/steam/overview")
    async def get_steam_overview() -> dict:
        return get_steam_service().overview()

    @app.get("/api/steam/groups")
    async def get_steam_groups() -> dict:
        return {"groups": get_store().list_steam_groups()}

    @app.put("/api/steam/groups/{group_id}")
    async def set_steam_group(
        group_id: int,
        payload: SteamGroupPayload,
        request: Request,
    ) -> dict:
        require_token(request)
        try:
            result = get_store().set_steam_group(
                group_id,
                monitor_enabled=payload.monitor_enabled,
                achievement_enabled=payload.achievement_enabled,
                actor_id=0,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        get_steam_service().wake()
        return result

    @app.get("/api/steam/groups/{group_id}/players")
    async def get_steam_group_players(group_id: int) -> dict:
        return {"players": get_store().list_steam_subscriptions(group_id=group_id)}

    @app.delete("/api/steam/groups/{group_id}")
    async def delete_steam_group(group_id: int, request: Request) -> dict:
        require_token(request)
        if not get_store().delete_steam_group(group_id, actor_id=0):
            raise HTTPException(status_code=404, detail="Steam group not found")
        get_steam_service().wake()
        return {"deleted": True}

    @app.post("/api/steam/groups/{group_id}/players")
    async def add_steam_group_player(
        group_id: int,
        payload: SteamSubscriptionPayload,
        request: Request,
    ) -> dict:
        require_token(request)
        try:
            steam_id = await get_steam_service().client.resolve_identifier(payload.identifier)
            result = get_store().add_steam_subscription(
                group_id,
                steam_id,
                alias=payload.alias,
                actor_id=0,
            )
            if payload.qq_user_id is not None:
                get_store().bind_steam_user(
                    group_id,
                    payload.qq_user_id,
                    steam_id,
                    actor_id=0,
                )
                result = get_store().get_steam_subscription(group_id, steam_id)
        except (SteamClientError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        get_steam_service().wake()
        return result

    @app.delete("/api/steam/groups/{group_id}/players/{steam_id}")
    async def delete_steam_group_player(
        group_id: int,
        steam_id: str,
        request: Request,
    ) -> dict:
        require_token(request)
        try:
            removed = get_store().remove_steam_subscription(group_id, steam_id, actor_id=0)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not removed:
            raise HTTPException(status_code=404, detail="Steam player not found")
        get_steam_service().wake()
        return {"deleted": True}

    @app.get("/api/steam/bindings")
    async def get_steam_bindings(group_id: int | None = None) -> dict:
        return {"bindings": get_store().list_steam_bindings(group_id)}

    @app.put("/api/steam/bindings")
    async def set_steam_binding(payload: SteamBindingPayload, request: Request) -> dict:
        require_token(request)
        try:
            steam_id = await get_steam_service().client.resolve_identifier(payload.identifier)
            return get_store().bind_steam_user(
                payload.group_id,
                payload.qq_user_id,
                steam_id,
                actor_id=0,
            )
        except (SteamClientError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/steam/bindings/{group_id}/{qq_user_id}")
    async def delete_steam_binding(
        group_id: int,
        qq_user_id: int,
        request: Request,
    ) -> dict:
        require_token(request)
        if not get_store().unbind_steam_user(group_id, qq_user_id, actor_id=0):
            raise HTTPException(status_code=404, detail="Steam binding not found")
        return {"deleted": True}

    @app.get("/api/steam/sessions")
    async def get_steam_sessions(limit: int = 100) -> dict:
        return {"sessions": get_store().list_steam_sessions(limit=limit)}

    @app.get("/api/steam/settings")
    async def get_steam_settings() -> dict:
        return {
            **get_store().get_steam_settings(),
            "steam_api_configured": bool(get_settings().steam_api_key),
            "sgdb_api_configured": bool(get_settings().sgdb_api_key),
            "itad_api_configured": bool(get_settings().itad_api_key),
        }

    @app.put("/api/steam/settings")
    async def set_steam_settings(
        payload: SteamSettingsPayload,
        request: Request,
    ) -> dict:
        require_token(request)
        try:
            result = get_store().set_steam_settings(payload.model_dump(), actor_id=0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        get_steam_service().wake()
        return result

    @app.post("/api/steam/connectivity-test")
    async def test_steam_connection(request: Request) -> dict:
        require_token(request)
        if not get_settings().steam_api_key:
            raise HTTPException(status_code=409, detail="请先配置 QQBOT_STEAM_API_KEY")
        try:
            return await get_steam_service().client.test_connection()
        except SteamClientError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
