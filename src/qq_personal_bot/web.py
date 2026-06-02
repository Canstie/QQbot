import json
import re
import base64
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from qq_personal_bot.lua_runner import (
    default_lua_command_script,
    default_lua_script,
    list_lua_command_scripts,
    lua_command_path,
    validate_lua_command,
    validate_lua_script,
)
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


class MenuPayload(BaseModel):
    title: str
    enabled: bool = True
    image_data_url: str | None = None
    image_url: str | None = None


class RestaurantPayload(BaseModel):
    name: str
    dishes: list[str]
    group_id: int
    created_by: int = 0
    enabled: bool = True


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

    @app.get("/api/lua/commands")
    async def get_lua_commands() -> dict:
        return _load_lua_commands()

    @app.get("/api/lua/commands/{command:path}")
    async def get_lua_command(command: str) -> dict:
        return _load_lua_command_for_editor(command)

    @app.post("/api/lua/commands/{command:path}")
    async def save_lua_command(command: str, payload: LuaPayload, request: Request) -> dict:
        require_token(request)
        try:
            command = validate_lua_command(command)
            validate_lua_script(payload.content)
            path = lua_command_path(command)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload.content, encoding="utf-8")
        return _load_lua_command_for_editor(command)

    @app.delete("/api/lua/commands/{command:path}")
    async def delete_lua_command(command: str, request: Request) -> dict:
        require_token(request)
        try:
            command = validate_lua_command(command)
            path = lua_command_path(command)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        deleted = False
        if path.exists():
            path.unlink()
            deleted = True
        data = _load_lua_commands()
        data["deleted"] = deleted
        data["command"] = command
        return data

    @app.get("/api/menus")
    async def get_menus(search: str = "", limit: int = 200) -> dict:
        settings = get_settings()
        return {
            "menus": [
                _menu_for_api(menu, settings.menu_image_dir)
                for menu in get_store().list_menu_recipes(search=search, limit=limit)
            ]
        }

    @app.get("/api/menu-images/{image_path:path}")
    async def get_menu_image(image_path: str) -> FileResponse:
        root = get_settings().menu_image_dir.resolve(strict=False)
        path = (root / image_path).resolve(strict=False)
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="image not found") from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="image not found")
        return FileResponse(path)

    @app.post("/api/menus")
    async def create_menu(payload: MenuPayload, request: Request) -> dict:
        require_token(request)
        try:
            image_body, image_suffix = _decode_image_payload(payload.image_data_url)
            menu = get_store().save_custom_menu_recipe(
                payload.title,
                get_settings().menu_image_dir,
                image_source=str(payload.image_url or ""),
                image_body=image_body,
                image_suffix=image_suffix,
                enabled=payload.enabled,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _menu_for_api(menu, get_settings().menu_image_dir)

    @app.put("/api/menus/{menu_id}")
    async def update_menu(menu_id: str, payload: MenuPayload, request: Request) -> dict:
        require_token(request)
        try:
            image_body, image_suffix = _decode_image_payload(payload.image_data_url)
            menu = get_store().update_menu_recipe(
                menu_id,
                get_settings().menu_image_dir,
                title=payload.title,
                enabled=payload.enabled,
                image_source=str(payload.image_url or ""),
                image_body=image_body,
                image_suffix=image_suffix,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _menu_for_api(menu, get_settings().menu_image_dir)

    @app.delete("/api/menus/{menu_id}")
    async def delete_menu(menu_id: str, request: Request) -> dict:
        require_token(request)
        return {"deleted": get_store().delete_menu_recipe(menu_id), "id": menu_id}

    @app.post("/api/menus/prune-howtocook-without-images")
    async def prune_howtocook_without_images(request: Request) -> dict:
        require_token(request)
        return {
            "deleted": get_store().prune_howtocook_without_images(get_settings().menu_image_dir)
        }

    @app.get("/api/restaurants")
    async def get_restaurants(group_id: int | None = None, search: str = "", limit: int = 200) -> dict:
        return {
            "restaurants": get_store().list_restaurants(
                group_id=group_id,
                search=search,
                limit=limit,
            )
        }

    @app.post("/api/restaurants")
    async def create_restaurant(payload: RestaurantPayload, request: Request) -> dict:
        require_token(request)
        try:
            return get_store().save_restaurant(
                name=payload.name,
                dishes=payload.dishes,
                group_id=payload.group_id,
                created_by=payload.created_by,
                enabled=payload.enabled,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.put("/api/restaurants/{restaurant_id}")
    async def update_restaurant(
        restaurant_id: int,
        payload: RestaurantPayload,
        request: Request,
    ) -> dict:
        require_token(request)
        try:
            return get_store().update_restaurant(
                restaurant_id,
                name=payload.name,
                dishes=payload.dishes,
                enabled=payload.enabled,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/restaurants/{restaurant_id}")
    async def delete_restaurant(restaurant_id: int, request: Request) -> dict:
        require_token(request)
        return {"deleted": get_store().delete_restaurant(restaurant_id), "id": restaurant_id}

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


def _load_lua_commands() -> dict:
    settings = get_settings()
    return {
        "enabled": settings.lua_enabled,
        "lua_dir": str(settings.lua_dir),
        "commands": [
            {
                "command": script.command,
                "path": str(script.path),
                "size": script.size,
                "modified_at": script.modified_at,
            }
            for script in list_lua_command_scripts(settings.lua_dir)
        ],
    }


def _load_lua_command_for_editor(command: str) -> dict:
    try:
        command = validate_lua_command(command)
        path = lua_command_path(command)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    exists = path.exists()
    content = path.read_text(encoding="utf-8") if exists else ""
    using_example = not content.strip()
    if using_example:
        content = default_lua_command_script(command)

    return {
        "enabled": get_settings().lua_enabled,
        "command": command,
        "path": str(path),
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


def _decode_image_payload(data_url: str | None) -> tuple[bytes | None, str]:
    if not data_url:
        return None, ".jpg"
    header, sep, payload = data_url.partition(",")
    if not sep or not header.startswith("data:image/"):
        raise ValueError("image_data_url must be a data:image/* URL")
    image_type = header.split(";", 1)[0].removeprefix("data:image/").lower()
    suffix = ".jpg" if image_type == "jpeg" else f".{image_type}"
    try:
        return base64.b64decode(payload, validate=True), suffix
    except ValueError as exc:
        raise ValueError("image_data_url is not valid base64") from exc


def _menu_for_api(menu: dict[str, Any], image_dir: Path) -> dict[str, Any]:
    image_relpath = str(menu.get("image_relpath") or "")
    image_url = ""
    if image_relpath:
        image_path = (image_dir / image_relpath).resolve(strict=False)
        image_url = f"./api/menu-images/{image_relpath}" if image_path.is_file() else ""
    return {**menu, "image_url": image_url}
