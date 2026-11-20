"""森空岛（鹰角官方）API 客户端：登录、扫码、卫戍协议数据。

签名算法来源：社区公开实现（HMAC-SHA256 + MD5）。
"""

import hashlib
import hmac
import json
import time

import httpx

APP_CODE = "4ca99fa6b56cc2ba"
PASSPORT = "https://as.hypergryph.com"
ZONAI = "https://zonai.skland.com"
SIGN_VERSION = "1.21.0"
SKLAND_UA = "Skland/1.32.1 (com.hypergryph.skland; build:103201004; Android 33; ) Okhttp/4.11.0"
WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
AUTH_CODES = {10002, 10003, 10004, 10005}


class SklandError(Exception):
    pass


class SklandAuthError(SklandError):
    """cred 失效需要重新绑定。"""


def _make_client(proxy: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=30.0, headers={"User-Agent": SKLAND_UA}, proxy=proxy or None, trust_env=False
    )


def build_sign(token: str, path: str, query: str, cred: str, uid: str = "") -> dict:
    ts = str(int(time.time()))
    profile = {"platform": "1", "timestamp": ts, "dId": "", "vName": SIGN_VERSION}
    payload = path + query + ts + json.dumps(profile, separators=(",", ":"))
    key = token.encode("utf-8") if token else b"\x00"
    signed = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).digest()
    sign = hashlib.md5(signed.hex().encode("utf-8")).hexdigest()
    headers = {
        **profile,
        "sign": sign,
        "language": "zh-hans-CN",
        "cred": cred,
        "Accept-Encoding": "gzip",
        "Connection": "close",
    }
    if token:
        headers["token"] = token
    if uid:
        headers["sk-game-role"] = f"1_{uid}_0"
    return headers


class SklandClient:
    def __init__(self, proxy: str = ""):
        self.proxy = (proxy or "").strip()
        self._client: httpx.AsyncClient | None = None
        # {cred: (token, expire_ts)}
        self._tokens: dict[str, tuple[str, float]] = {}

    async def close(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._tokens.clear()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = _make_client(self.proxy)
        return self._client

    @staticmethod
    def _raise(payload: dict, prefix: str):
        code = payload.get("code")
        if code in AUTH_CODES:
            raise SklandAuthError(f"{prefix}: {payload.get('message')} (code={code})")
        if code not in (0, None):
            raise SklandError(f"{prefix}: {payload.get('message')} (code={code})")

    async def refresh_token(self, cred: str, force: bool = False) -> str:
        now = time.time()
        cached = self._tokens.get(cred)
        if cached and cached[1] > now and not force:
            return cached[0]
        client = await self._get_client()
        headers = build_sign("", "/api/v1/auth/refresh", "", cred)
        resp = await client.get(ZONAI + "/api/v1/auth/refresh", headers=headers)
        if resp.status_code == 401:
            raise SklandAuthError("森空岛凭证已过期")
        resp.raise_for_status()
        payload = resp.json()
        self._raise(payload, "刷新森空岛 token")
        token = (payload.get("data") or {}).get("token") or ""
        if not token:
            raise SklandAuthError("刷新森空岛 token 失败")
        expires = (
            (payload.get("data") or {}).get("expiresIn")
            or (payload.get("data") or {}).get("expireIn")
            or 0
        )
        try:
            ttl = max(30.0, min(float(expires) - 15, 300.0)) if float(expires) > 0 else 300.0
        except Exception:  # noqa: BLE001
            ttl = 300.0
        self._tokens[cred] = (token, now + ttl)
        return token

    def drop_token(self, cred: str):
        self._tokens.pop(cred, None)

    async def api_get(
        self,
        path: str,
        cred: str,
        params: dict | None = None,
        uid: str = "",
        retry: bool = True,
    ) -> dict:
        client = await self._get_client()
        token = await self.refresh_token(cred)
        query = str(httpx.QueryParams(params or {}))
        headers = build_sign(token, path, query, cred, uid)
        url = ZONAI + path + (("?" + query) if query else "")
        resp = await client.get(url, headers=headers)
        try:
            payload = resp.json()
        except ValueError as exc:
            raise SklandError(f"森空岛返回异常: {exc}") from exc
        code = payload.get("code")
        if resp.status_code == 401 or code in AUTH_CODES:
            if retry:
                self.drop_token(cred)
                return await self.api_get(path, cred, params, uid, retry=False)
            raise SklandAuthError(f"森空岛拒绝了凭证: {payload.get('message')}")
        if not resp.is_success or code not in (0, None):
            raise SklandError(f"森空岛接口失败: {payload.get('message')} (code={code})")
        return payload.get("data") or {}

    # ---------- 数据接口 ----------
    async def get_bindings(self, cred: str) -> list:
        data = await self.api_get("/api/v1/game/player/binding", cred)
        return data.get("list") or []

    async def get_arknights_binding(self, cred: str) -> dict:
        for app in await self.get_bindings(cred):
            if app.get("appCode") != "arknights":
                continue
            bindings = [
                b for b in app.get("bindingList") or [] if b.get("uid") and not b.get("isDelete")
            ]
            if not bindings:
                continue
            b = next((role for role in bindings if role.get("isDefault")), bindings[0])
            nickname = str(b.get("nickName") or b.get("nickname") or "").strip()
            if not nickname:
                raise SklandError("未获得明日方舟角色名")
            if "#" not in nickname and b.get("nickNumber") is not None:
                nickname += "#" + str(b["nickNumber"])
            return {
                "uid": str(b.get("uid") or ""),
                "nickname": nickname,
                "channel": str(b.get("channelName") or ""),
                "channel_master_id": str(b.get("channelMasterId") or "1"),
            }
        raise SklandError("该账号没有绑定明日方舟角色")

    async def get_player_info(self, cred: str, uid: str) -> dict:
        """玩家基础信息（含游戏内头像 status.avatar、助理 status.secretary）。"""
        return await self.api_get("/api/v1/game/player/info", cred, {"uid": uid}, uid)

    async def get_garrison_resource(
        self, cred: str, uid: str, season_id: str = "", version_id: str = ""
    ) -> dict:
        params = {}
        if season_id:
            params["seasonId"] = season_id
        if version_id:
            params["versionId"] = version_id
        return await self.api_get("/api/v1/game/garrison/guide/resource", cred, params, uid)

    async def get_garrison_history(self, cred: str, uid: str, season_id: str) -> list:
        data = await self.api_get(
            "/api/v1/game/garrison/guide/history-log",
            cred,
            {"seasonId": season_id},
            uid,
        )
        return data.get("records") or []


# ---------- 扫码登录（不经过密码） ----------
def qr_login_url(scan_id: str) -> str:
    return "hypergryph://scan_login?scanId=" + scan_id


async def qr_start(proxy: str = "") -> dict:
    client = _make_client(proxy)
    try:
        resp = await client.post(
            PASSPORT + "/general/v1/gen_scan/login",
            json={"appCode": APP_CODE},
            headers={"Content-Type": "application/json"},
        )
        payload = resp.json()
        if payload.get("status") != 0:
            raise SklandError(f"生成二维码失败: {payload.get('msg')}")
        scan_id = (payload.get("data") or {}).get("scanId") or ""
        if not scan_id:
            raise SklandError("生成二维码失败：没有 scanId")
        return {"scan_id": scan_id, "url": qr_login_url(scan_id)}
    finally:
        await client.aclose()


async def qr_poll(scan_id: str, proxy: str = "") -> str:
    """已确认时返回 scanCode，等待扫码时返回空字符串。"""
    client = _make_client(proxy)
    try:
        resp = await client.get(
            PASSPORT + "/general/v1/scan_status",
            params={"scanId": scan_id},
        )
        payload = resp.json()
        if payload.get("status") != 0:
            return ""
        scan_code = (payload.get("data") or {}).get("scanCode") or ""
        return scan_code
    finally:
        await client.aclose()


async def qr_finish(scan_code: str, proxy: str = "") -> dict:
    """scanCode → 鹰角 token → 森空岛 cred。"""
    client = _make_client(proxy)
    try:
        # 1) token_by_scan_code
        resp = await client.post(
            PASSPORT + "/user/auth/v1/token_by_scan_code",
            json={"scanCode": scan_code},
            headers={"Content-Type": "application/json"},
        )
        payload = resp.json()
        if payload.get("status") != 0 or not (payload.get("data") or {}).get("token"):
            raise SklandError(f"扫码登录失败: {payload.get('msg')}")
        hg_token = payload["data"]["token"]

        # 2) OAuth code
        resp = await client.post(
            PASSPORT + "/user/oauth2/v2/grant",
            json={"appCode": APP_CODE, "type": 0, "token": hg_token},
            headers={"Content-Type": "application/json", "User-Agent": SKLAND_UA},
        )
        payload = resp.json()
        code = (payload.get("data") or {}).get("code") or ""
        if payload.get("status") != 0 or not code:
            raise SklandError(f"森空岛授权失败: {payload.get('msg')}")

        # 3) 换 cred
        resp = await client.post(
            ZONAI + "/api/v1/user/auth/generate_cred_by_code",
            json={"kind": 1, "code": code},
            headers={"Content-Type": "application/json", "User-Agent": SKLAND_UA},
        )
        payload = resp.json()
        data = payload.get("data") or {}
        if payload.get("code") != 0 or not data.get("cred"):
            raise SklandError(f"换取森空岛凭证失败: {payload.get('message')}")
        return {
            "cred": data.get("cred") or "",
            "token": data.get("token") or "",
            "user_id": str(data.get("userId") or ""),
        }
    finally:
        await client.aclose()
