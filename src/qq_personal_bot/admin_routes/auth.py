import hashlib
import hmac
import html
import time
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    Response,
)

from qq_personal_bot.runtime import get_settings

_SESSION_COOKIE_NAME = "qqbot_admin_session"

_SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def static_directory() -> Path:
    repo_static = Path(__file__).resolve().parents[3] / "static"
    if repo_static.exists():
        return repo_static
    return Path("static").resolve()


def is_public_admin_path(request: Request) -> bool:
    path = request.scope.get("path", "")
    normalized = path.rstrip("/") or "/"
    return (
        normalized.endswith(("/login", "/logout"))
        or "/xiaoheihe-captcha/" in path
        or path.startswith("/static/")
        or "/static/" in path
    )


def is_api_path(request: Request) -> bool:
    path = request.scope.get("path", "")
    return path.startswith("/api/") or "/api/" in path


def is_authenticated(request: Request) -> bool:
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


def register_routes(app, require_token):
    static_dir = static_directory()

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/login")
    async def login_page(request: Request) -> Response:
        if is_authenticated(request):
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
