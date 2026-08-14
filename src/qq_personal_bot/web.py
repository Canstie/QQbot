import base64
import hashlib
import hmac
import html
import json
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from qq_personal_bot.lua_runner import (
    default_lua_command_script,
    default_lua_script,
    list_lua_command_scripts,
    lua_command_path,
    validate_lua_command,
    validate_lua_script,
)
from qq_personal_bot.menu_recipes import is_supported_image_file
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


class DirectTriggerPercentPayload(BaseModel):
    percent: float


class CoreConfigPayload(BaseModel):
    mode: Literal["allowlist", "blocklist"]
    enabled_groups: list[int]
    blocked_groups: list[int]
    admins: list[int]
    trigger: dict[str, Any]
    limits: dict[str, Any]


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


class KnowledgeBasePayload(BaseModel):
    name: str
    prompt: str = ""
    model: str | None = None
    thinking_enabled: bool | None = None
    max_tokens: int | None = None
    temperature: float | None = None


class KnowledgeActivationPayload(BaseModel):
    clear_history: bool = True


class LuaPayload(BaseModel):
    content: str


class MenuPayload(BaseModel):
    title: str
    enabled: bool = True
    image_data_url: str | None = None
    image_url: str | None = None


class StickerPayload(BaseModel):
    filename: str = "sticker"
    image_data_url: str


class RestaurantPayload(BaseModel):
    name: str
    dishes: list[str]
    group_id: int
    created_by: int = 0
    enabled: bool = True


_SESSION_COOKIE_NAME = "qqbot_admin_session"
_SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def create_app():
    app = FastAPI(title="QQ Personal Bot Admin")
    static_dir = _static_dir()
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        expected = get_settings().web_token
        if not expected or _is_public_admin_path(request):
            return await call_next(request)
        if _is_authenticated(request):
            return await call_next(request)
        if _is_api_path(request):
            return JSONResponse({"detail": "login required"}, status_code=401)
        return RedirectResponse("./login", status_code=303)

    def require_token(request: Request) -> None:
        if not _is_authenticated(request):
            raise HTTPException(status_code=401, detail="invalid admin token")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/login")
    async def login_page(request: Request) -> Response:
        if _is_authenticated(request):
            return RedirectResponse("./", status_code=303)
        return HTMLResponse(_login_page_html())

    @app.post("/login")
    async def login(request: Request) -> Response:
        expected = get_settings().web_token
        if not expected:
            return RedirectResponse("./", status_code=303)

        body = (await request.body()).decode("utf-8", errors="ignore")
        password = parse_qs(body).get("password", [""])[0]
        if not hmac.compare_digest(password, expected):
            return HTMLResponse(_login_page_html("token 不正确"), status_code=401)

        response = RedirectResponse("./", status_code=303)
        response.set_cookie(
            _SESSION_COOKIE_NAME,
            _sign_session_cookie(expected),
            max_age=_SESSION_MAX_AGE_SECONDS,
            httponly=True,
            samesite="lax",
            path=_cookie_path(request),
        )
        return response

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        response = RedirectResponse("./login", status_code=303)
        response.delete_cookie(_SESSION_COOKIE_NAME, path=_cookie_path(request))
        return response

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
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return get_store().snapshot()

    @app.get("/api/dsapi")
    async def get_dsapi_config() -> dict:
        config = get_store().get_dsapi_config()
        settings = get_settings()
        active_knowledge = config.get("active_knowledge") or {}
        return {
            **config,
            "api_configured": bool(settings.dsapi_enabled and settings.dsapi_api_key),
            "model": active_knowledge.get("model") or settings.dsapi_model,
            "max_tokens": active_knowledge.get("max_tokens") or settings.dsapi_max_tokens,
            "thinking_enabled": bool(active_knowledge.get("thinking_enabled", False)),
            "temperature": active_knowledge.get("temperature"),
            "default_model": settings.dsapi_model,
            "default_max_tokens": settings.dsapi_max_tokens,
            "base_url": settings.dsapi_base_url,
            "history_idle_seconds": settings.dsapi_history_idle_seconds,
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
                    else get_settings().dsapi_model
                ),
                thinking_enabled=bool(payload.thinking_enabled),
                max_tokens=(
                    payload.max_tokens
                    if payload.max_tokens is not None
                    else get_settings().dsapi_max_tokens
                ),
                temperature=payload.temperature,
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
                temperature=payload.temperature,
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

    @app.post("/api/dsapi/knowledge/{knowledge_id}/activate")
    async def activate_knowledge_base(
        knowledge_id: int,
        payload: KnowledgeActivationPayload,
        request: Request,
    ) -> dict:
        require_token(request)
        try:
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

    @app.get("/api/stickers")
    async def get_stickers() -> dict:
        return {"stickers": _list_stickers(), "root": str(get_settings().sticker_dir)}

    @app.get("/api/sticker-images/{filename}")
    async def get_sticker_image(filename: str) -> FileResponse:
        path = _resolve_sticker(filename)
        if not path.is_file() or not is_supported_image_file(path):
            raise HTTPException(status_code=404, detail="sticker not found")
        return FileResponse(path)

    @app.post("/api/stickers")
    async def create_sticker(payload: StickerPayload, request: Request) -> dict:
        require_token(request)
        try:
            return _save_sticker(payload.filename, payload.image_data_url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/stickers/{filename}")
    async def delete_sticker(filename: str, request: Request) -> dict:
        require_token(request)
        path = _resolve_sticker(filename)
        if not path.is_file() or not is_supported_image_file(path):
            raise HTTPException(status_code=404, detail="sticker not found")
        path.unlink()
        return {"deleted": True, "filename": path.name}

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

    @app.get("/api/classics/groups")
    async def get_classic_groups(search: str = "", limit: int = 200) -> dict:
        return {
            "groups": _list_classic_groups(search=search, limit=limit),
            "root": str(get_settings().classics_image_dir),
        }

    @app.get("/api/classics/groups/{group_id}")
    async def get_classic_group(group_id: int, limit: int = 500) -> dict:
        return _classic_group_detail(group_id, limit=limit)

    @app.delete("/api/classics/groups/{group_id}")
    async def delete_classic_group(group_id: int, request: Request) -> dict:
        require_token(request)
        return _delete_classic_group(group_id)

    @app.delete("/api/classics/groups/{group_id}/images/{filename:path}")
    async def delete_classic_group_image(group_id: int, filename: str, request: Request) -> dict:
        require_token(request)
        return _delete_classic_image(group_id, filename)

    @app.get("/api/classic-images/{image_path:path}")
    async def get_classic_image(image_path: str) -> FileResponse:
        path = _resolve_classic_image(image_path)
        if not path.is_file() or not is_supported_image_file(path):
            raise HTTPException(status_code=404, detail="classic image not found")
        return FileResponse(path)

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


def _is_public_admin_path(request: Request) -> bool:
    path = request.scope.get("path", "")
    normalized = path.rstrip("/") or "/"
    return (
        normalized.endswith("/login")
        or normalized.endswith("/logout")
        or path.startswith("/static/")
        or "/static/" in path
    )


def _is_api_path(request: Request) -> bool:
    path = request.scope.get("path", "")
    return path.startswith("/api/") or "/api/" in path


def _is_authenticated(request: Request) -> bool:
    expected = get_settings().web_token
    if not expected:
        return True

    provided = request.headers.get("x-admin-token") or request.query_params.get("token")
    if provided and hmac.compare_digest(provided, expected):
        return True

    return _verify_session_cookie(request.cookies.get(_SESSION_COOKIE_NAME), expected)


def _sign_session_cookie(secret: str, now: int | None = None) -> str:
    issued_at = str(int(now if now is not None else time.time()))
    digest = hmac.new(secret.encode("utf-8"), issued_at.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{issued_at}.{digest}"


def _verify_session_cookie(value: str | None, secret: str) -> bool:
    if not value:
        return False
    issued_at, sep, digest = value.partition(".")
    if not sep or not issued_at.isdigit():
        return False

    try:
        issued_ts = int(issued_at)
    except ValueError:
        return False
    if issued_ts > int(time.time()) + 60:
        return False
    if int(time.time()) - issued_ts > _SESSION_MAX_AGE_SECONDS:
        return False

    expected = _sign_session_cookie(secret, issued_ts).split(".", 1)[1]
    return hmac.compare_digest(digest, expected)


def _cookie_path(request: Request) -> str:
    return str(request.scope.get("root_path") or "/")


def _login_page_html(error: str = "") -> str:
    error_html = (
        f'<div class="error">{html.escape(error)}</div>'
        if error
        else '<p class="hint">请输入服务器 .env 中的 QQBOT_WEB_TOKEN。</p>'
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>QQ Bot 登录</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #101114;
      --panel: #181a1f;
      --text: #f5f7fb;
      --muted: #a7adb8;
      --line: #2b3038;
      --accent: #6ee7b7;
      --danger: #fb7185;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      min-height: 100vh;
      margin: 0;
      display: grid;
      place-items: center;
      background:
        radial-gradient(circle at 20% 10%, rgba(110, 231, 183, 0.14), transparent 28rem),
        linear-gradient(135deg, #101114 0%, #17191f 100%);
      color: var(--text);
      font-family: Inter, "Segoe UI", "Microsoft YaHei", sans-serif;
    }}
    main {{
      width: min(92vw, 420px);
      padding: 32px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: rgba(24, 26, 31, 0.94);
      box-shadow: 0 24px 70px rgba(0, 0, 0, 0.35);
    }}
    p, h1 {{ margin: 0; }}
    .eyebrow {{
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    h1 {{
      margin-top: 10px;
      font-size: 30px;
      line-height: 1.15;
    }}
    .hint {{
      margin-top: 12px;
      color: var(--muted);
      line-height: 1.7;
    }}
    form {{
      margin-top: 26px;
      display: grid;
      gap: 14px;
    }}
    label {{
      display: grid;
      gap: 8px;
      color: var(--muted);
      font-size: 14px;
    }}
    input {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 13px 14px;
      background: #0f1116;
      color: var(--text);
      font: inherit;
      outline: none;
    }}
    input:focus {{
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(110, 231, 183, 0.16);
    }}
    button {{
      border: 0;
      border-radius: 10px;
      padding: 13px 16px;
      background: var(--accent);
      color: #07110d;
      font: inherit;
      font-weight: 800;
      cursor: pointer;
    }}
    .error {{
      margin-top: 14px;
      padding: 10px 12px;
      border: 1px solid rgba(251, 113, 133, 0.5);
      border-radius: 10px;
      background: rgba(251, 113, 133, 0.1);
      color: var(--danger);
    }}
  </style>
</head>
<body>
  <main>
    <p class="eyebrow">QQ Bot Admin</p>
    <h1>登录控制台</h1>
    {error_html}
    <form method="post" action="./login">
      <label>Web Token
        <input name="password" type="password" autocomplete="current-password" autofocus required />
      </label>
      <button type="submit">进入后台</button>
    </form>
  </main>
</body>
</html>"""


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


def _list_stickers() -> list[dict[str, Any]]:
    root = get_settings().sticker_dir
    if not root.is_dir():
        return []
    images = sorted(
        [path for path in root.iterdir() if path.is_file() and is_supported_image_file(path)],
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )
    return [_sticker_for_api(path) for path in images]


def _sticker_for_api(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "filename": path.name,
        "image_url": f"./api/sticker-images/{quote(path.name)}",
        "size": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
    }


def _save_sticker(filename: str, data_url: str) -> dict[str, Any]:
    body, suffix = _decode_image_payload(data_url)
    if body is None:
        raise ValueError("sticker image is required")
    if len(body) > 10 * 1024 * 1024:
        raise ValueError("sticker image must not exceed 10 MB")

    stem = re.sub(r"[^0-9A-Za-z._-]+", "-", Path(filename).stem).strip("-._")[:64]
    stem = stem or "sticker"
    digest = hashlib.sha256(body).hexdigest()[:12]
    root = get_settings().sticker_dir.resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{stem}-{digest}{suffix.lower()}"
    target.write_bytes(body)
    if not is_supported_image_file(target):
        target.unlink(missing_ok=True)
        raise ValueError("unsupported sticker image; use JPG, PNG, GIF, or WebP")
    return _sticker_for_api(target)


def _resolve_sticker(filename: str) -> Path:
    root = get_settings().sticker_dir.resolve(strict=False)
    path = (root / filename).resolve(strict=False)
    if path.parent != root:
        raise HTTPException(status_code=404, detail="sticker not found")
    return path


def _list_classic_groups(search: str = "", limit: int = 200) -> list[dict[str, Any]]:
    root = get_settings().classics_image_dir
    if not root.is_dir():
        return []

    normalized_search = search.strip()
    groups: list[dict[str, Any]] = []
    for group_dir in root.iterdir():
        if not group_dir.is_dir():
            continue
        if normalized_search and normalized_search not in group_dir.name:
            continue
        summary = _classic_group_summary(group_dir)
        if summary is not None:
            groups.append(summary)

    groups.sort(key=lambda item: (item["updated_at"], item["group_id"]), reverse=True)
    return groups[: max(1, min(int(limit), 500))]


def _classic_group_detail(group_id: int, limit: int = 500) -> dict[str, Any]:
    group_id = _validate_group_id(group_id)
    group_dir = _classic_group_dir(group_id)
    images = _classic_group_images(group_dir)
    limited_images = images[: max(1, min(int(limit), 1000))]

    return {
        "group_id": group_id,
        "exists": group_dir.is_dir(),
        "count": len(images),
        "images": [_classic_image_for_api(group_id, path) for path in limited_images],
    }


def _classic_group_summary(group_dir: Path) -> dict[str, Any] | None:
    try:
        group_id = int(group_dir.name)
    except ValueError:
        return None

    images = _classic_group_images(group_dir)
    if not images:
        return None

    latest = images[0]
    latest_stat = latest.stat()
    return {
        "group_id": group_id,
        "count": len(images),
        "cover_url": _classic_image_url(f"{group_id}/{latest.name}"),
        "updated_at": datetime.fromtimestamp(latest_stat.st_mtime, UTC).isoformat(),
        "total_bytes": sum(path.stat().st_size for path in images),
    }


def _classic_group_images(group_dir: Path) -> list[Path]:
    if not group_dir.is_dir():
        return []
    return sorted(
        [
            path
            for path in group_dir.iterdir()
            if path.is_file() and is_supported_image_file(path)
        ],
        key=lambda path: (path.stat().st_mtime, path.name),
        reverse=True,
    )


def _classic_image_for_api(group_id: int, path: Path) -> dict[str, Any]:
    relpath = f"{group_id}/{path.name}"
    stat = path.stat()
    return {
        "filename": path.name,
        "relpath": relpath,
        "image_url": _classic_image_url(relpath),
        "size": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
    }


def _classic_image_url(relpath: str) -> str:
    encoded = "/".join(quote(part) for part in Path(relpath).parts)
    return f"./api/classic-images/{encoded}"


def _delete_classic_group(group_id: int) -> dict[str, Any]:
    group_id = _validate_group_id(group_id)
    group_dir = _classic_group_dir(group_id)
    if not group_dir.exists():
        return {"deleted": False, "group_id": group_id, "deleted_count": 0}
    if not group_dir.is_dir():
        raise HTTPException(status_code=400, detail="classic group path is not a directory")

    deleted_count = len(_classic_group_images(group_dir))
    shutil.rmtree(group_dir)
    return {"deleted": True, "group_id": group_id, "deleted_count": deleted_count}


def _delete_classic_image(group_id: int, filename: str) -> dict[str, Any]:
    group_id = _validate_group_id(group_id)
    group_dir = _classic_group_dir(group_id).resolve(strict=False)
    path = _resolve_classic_image(f"{group_id}/{filename}")
    if path.parent.resolve(strict=False) != group_dir:
        raise HTTPException(status_code=404, detail="classic image not found")
    if not path.is_file() or not is_supported_image_file(path):
        raise HTTPException(status_code=404, detail="classic image not found")

    path.unlink()
    return {
        "deleted": True,
        "group_id": group_id,
        "filename": path.name,
        "group": _classic_group_detail(group_id),
    }


def _validate_group_id(group_id: int) -> int:
    group_id = int(group_id)
    if group_id <= 0:
        raise HTTPException(status_code=400, detail="group_id must be positive")
    return group_id


def _classic_group_dir(group_id: int) -> Path:
    root = get_settings().classics_image_dir.resolve(strict=False)
    path = (root / str(_validate_group_id(group_id))).resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="classic group not found") from exc
    return path


def _resolve_classic_image(relpath: str) -> Path:
    root = get_settings().classics_image_dir.resolve(strict=False)
    path = (root / relpath).resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="classic image not found") from exc
    return path
