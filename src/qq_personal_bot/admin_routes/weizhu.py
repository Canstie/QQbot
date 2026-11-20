from fastapi import Request, Response

from qq_personal_bot.runtime import get_weizhu_store


def register_routes(app, require_token):
    @app.get("/api/weizhu/bindings")
    def list_bindings(request: Request, response: Response) -> dict:
        require_token(request)
        response.headers["Cache-Control"] = "no-store"
        return {"bindings": get_weizhu_store().directory()}
