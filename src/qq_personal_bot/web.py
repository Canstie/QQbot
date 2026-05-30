import json
import re
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from qq_personal_bot.lua_runner import default_lua_script, validate_lua_script
from qq_personal_bot.replies import (
    DEFAULT_CONFIG,
    parse_reply_config,
    reload_reply_config,
    reply_config_to_dict,
)
from qq_personal_bot.runtime import get_settings, get_store


class ModePayload(BaseModel):
    mode: Literal["allowlist", "blocklist"]


class PrefixesPayload(BaseModel):
    prefixes: list[str]


class LuaPayload(BaseModel):
    content: str


def create_app():
    app = FastAPI(title="QQ Personal Bot Admin")
    static_dir = _static_dir()
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    def require_token(request: Request) -> None:
        expected = get_settings().web_token
        if not expected:
            return
        provided = request.headers.get("x-admin-token") or request.query_params.get("token")
        if provided != expected:
            raise HTTPException(status_code=401, detail="invalid admin token")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

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

    @app.get("/api/replies")
    async def get_replies() -> dict:
        return _load_replies_for_editor()

    @app.post("/api/replies")
    async def save_replies(payload: dict[str, Any], request: Request) -> dict:
        require_token(request)
        try:
            raw = _validate_replies_payload(payload)
        except (json.JSONDecodeError, ValueError, re.error) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        _replies_path().write_text(raw, encoding="utf-8")
        reload_reply_config()
        return _load_replies_for_editor()

    @app.get("/api/lua")
    async def get_lua_script() -> dict:
        return _load_lua_for_editor()

    @app.post("/api/lua")
    async def save_lua_script(payload: LuaPayload, request: Request) -> dict:
        require_token(request)
        try:
            validate_lua_script(payload.content)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        path = get_settings().lua_script
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload.content, encoding="utf-8")
        return _load_lua_for_editor()

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

    return app


def _static_dir() -> Path:
    repo_static = Path(__file__).resolve().parents[2] / "static"
    if repo_static.exists():
        return repo_static
    return Path("static").resolve()


def _replies_path() -> Path:
    return Path("replies.json")


def _load_lua_for_editor() -> dict:
    settings = get_settings()
    path = settings.lua_script
    exists = path.exists()
    content = path.read_text(encoding="utf-8") if exists else ""
    using_example = not content.strip()
    if using_example:
        content = default_lua_script()

    return {
        "enabled": settings.lua_enabled,
        "script_path": str(path),
        "exists": exists,
        "using_example": using_example,
        "content": content,
    }


def _default_replies_raw() -> str:
    return json.dumps(reply_config_to_dict(DEFAULT_CONFIG), ensure_ascii=False, indent=2) + "\n"


def _load_replies_for_editor() -> dict:
    path = _replies_path()
    raw = path.read_text(encoding="utf-8") if path.exists() else _default_replies_raw()

    try:
        normalized = _validate_replies_raw(raw)
        config = reply_config_to_dict(parse_reply_config(json.loads(normalized)))
    except (json.JSONDecodeError, ValueError, re.error) as exc:
        return {
            "raw": raw,
            "config": reply_config_to_dict(DEFAULT_CONFIG),
            "valid": False,
            "error": str(exc),
        }

    return {"raw": normalized, "config": config, "valid": True, "error": None}


def _validate_replies_payload(payload: dict[str, Any]) -> str:
    if "raw" in payload:
        return _validate_replies_raw(str(payload["raw"]))
    return _validate_replies_object(payload)


def _validate_replies_raw(raw: str) -> str:
    parsed = json.loads(raw)
    return _validate_replies_object(parsed)


def _validate_replies_object(parsed: Any) -> str:
    if not isinstance(parsed, dict):
        raise ValueError("replies config root must be an object")

    config = parse_reply_config(parsed)
    return json.dumps(reply_config_to_dict(config), ensure_ascii=False, indent=2) + "\n"
