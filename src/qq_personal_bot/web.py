"""Assemble the admin application; route implementations live in admin_routes."""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from qq_personal_bot.admin_routes import (
    ai,
    auth,
    captcha,
    content,
    media,
    policy,
    steam,
)
from qq_personal_bot.provider_web import register_ai_provider_routes
from qq_personal_bot.runtime import get_settings, get_store


def create_app():
    app = FastAPI(title="QQ Personal Bot Admin")
    app.mount("/static", StaticFiles(directory=auth.static_directory()), name="static")

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        expected = get_settings().web_token
        if not expected or auth.is_public_admin_path(request) or auth.is_authenticated(request):
            return await call_next(request)
        if auth.is_api_path(request):
            return JSONResponse({"detail": "login required"}, status_code=401)
        return RedirectResponse("./login", status_code=303)

    def require_token(request: Request) -> None:
        if not auth.is_authenticated(request):
            raise HTTPException(status_code=401, detail="invalid admin token")

    register_ai_provider_routes(app, get_settings, get_store, require_token)
    for routes in (auth, captcha, policy, ai, content, media, steam):
        routes.register_routes(app, require_token)
    return app
