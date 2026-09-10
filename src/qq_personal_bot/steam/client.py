from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from qq_personal_bot.settings import AppSettings

STEAM_ID64_BASE = 76561197960265728
_STEAM_ID_RE = re.compile(r"^\d{16,20}$")
_FRIEND_CODE_RE = re.compile(r"^\d{1,10}$")


class SteamClientError(RuntimeError):
    pass


class SteamClient:
    WEB_API_BASE = "https://api.steampowered.com"
    STORE_BASE = "https://store.steampowered.com"
    SGDB_API_BASE = "https://www.steamgriddb.com/api/v2"
    ITAD_API_BASE = "https://api.isthereanydeal.com"

    def __init__(
        self,
        settings: AppSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_key = settings.steam_api_key
        self.sgdb_api_key = settings.sgdb_api_key
        self.itad_api_key = settings.itad_api_key
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(20.0, connect=10.0),
            "follow_redirects": True,
            "headers": {"User-Agent": "QQBot-Steam-Monitor/1.0"},
        }
        if settings.steam_proxy_url:
            kwargs["proxy"] = settings.steam_proxy_url
        if transport is not None:
            kwargs["transport"] = transport
        self._http = httpx.AsyncClient(**kwargs)

    async def close(self) -> None:
        await self._http.aclose()

    async def resolve_identifier(self, value: str) -> str:
        candidate = str(value).strip().rstrip("/")
        if _STEAM_ID_RE.fullmatch(candidate):
            return candidate
        if _FRIEND_CODE_RE.fullmatch(candidate):
            return str(STEAM_ID64_BASE + int(candidate))

        if candidate.startswith(("http://", "https://")):
            try:
                response = await self._http.get(candidate)
                response.raise_for_status()
                candidate = str(response.url).rstrip("/")
            except httpx.HTTPError as exc:
                raise SteamClientError(f"Steam 链接解析失败：{exc}") from exc
            parsed = urlparse(candidate)
            parts = [part for part in parsed.path.split("/") if part]
            if (
                len(parts) >= 2
                and parts[-2].casefold() == "profiles"
                and _STEAM_ID_RE.fullmatch(parts[-1])
            ):
                return parts[-1]
            if len(parts) >= 2 and parts[-2].casefold() == "id":
                candidate = parts[-1]

        if not candidate or any(character.isspace() for character in candidate):
            raise SteamClientError("无法识别 Steam ID、好友码或个人资料链接。")
        payload = await self._request_json(
            f"{self.WEB_API_BASE}/ISteamUser/ResolveVanityURL/v1/",
            params={"key": self._require_key(), "vanityurl": candidate},
        )
        response = payload.get("response", {})
        steam_id = str(response.get("steamid", ""))
        if response.get("success") != 1 or not _STEAM_ID_RE.fullmatch(steam_id):
            raise SteamClientError("没有找到对应的 Steam 玩家。")
        return steam_id

    async def get_player_summaries(
        self,
        steam_ids: list[str],
        *,
        retries: int = 3,
    ) -> list[dict[str, Any]]:
        if not steam_ids:
            return []
        if len(steam_ids) > 100:
            raise ValueError("Steam summaries accepts at most 100 IDs")
        payload = await self._request_json(
            f"{self.WEB_API_BASE}/ISteamUser/GetPlayerSummaries/v2/",
            params={"key": self._require_key(), "steamids": ",".join(steam_ids)},
            retries=retries,
        )
        players = payload.get("response", {}).get("players", [])
        return [dict(player) for player in players if isinstance(player, dict)]

    async def get_player_achievements(
        self,
        steam_id: str,
        game_id: str,
    ) -> dict[str, float]:
        payload = await self._request_json(
            f"{self.WEB_API_BASE}/ISteamUserStats/GetPlayerAchievements/v1/",
            params={
                "key": self._require_key(),
                "steamid": steam_id,
                "appid": game_id,
                "l": "schinese",
            },
            retries=1,
        )
        stats = payload.get("playerstats", {})
        if stats.get("success") is False:
            return {}
        return {
            str(item.get("apiname")): float(item.get("unlocktime", 0) or 0)
            for item in stats.get("achievements", [])
            if isinstance(item, dict) and item.get("apiname")
        }

    async def get_app_details(self, app_id: str, *, country: str = "CN") -> dict[str, Any]:
        payload = await self._request_json(
            f"{self.STORE_BASE}/api/appdetails",
            params={"appids": str(app_id), "cc": country, "l": "schinese"},
        )
        item = payload.get(str(app_id), {})
        if not item.get("success") or not isinstance(item.get("data"), dict):
            raise SteamClientError("没有找到对应的 Steam 游戏。")
        return dict(item["data"])

    async def search_store(self, query: str, *, country: str = "CN") -> list[dict[str, Any]]:
        payload = await self._request_json(
            f"{self.STORE_BASE}/api/storesearch/",
            params={"term": str(query).strip(), "cc": country, "l": "schinese"},
        )
        return [
            dict(item)
            for item in payload.get("items", [])[:10]
            if isinstance(item, dict) and item.get("id")
        ]

    async def get_game_cover(self, app_id: str) -> bytes | None:
        urls: list[str] = []
        if self.sgdb_api_key:
            try:
                payload = await self._request_json(
                    f"{self.SGDB_API_BASE}/grids/steam/{app_id}",
                    params={"dimensions": "920x430", "types": "static"},
                    headers={"Authorization": f"Bearer {self.sgdb_api_key}"},
                )
                urls.extend(
                    str(item["url"])
                    for item in payload.get("data", [])
                    if isinstance(item, dict) and item.get("url")
                )
            except SteamClientError:
                pass
        urls.append(f"https://cdn.cloudflare.steamstatic.com/steam/apps/{app_id}/header.jpg")
        for url in urls:
            try:
                response = await self._http.get(url)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if content_type.startswith("image/") and len(response.content) <= 10 * 1024 * 1024:
                    return bytes(response.content)
            except httpx.HTTPError:
                continue
        return None

    async def get_itad_price(self, app_id: str, *, country: str = "CN") -> dict[str, Any]:
        if not self.itad_api_key:
            return {}
        lookup = await self._request_json(
            f"{self.ITAD_API_BASE}/games/lookup/v1",
            params={"key": self.itad_api_key, "appid": str(app_id)},
        )
        game = lookup.get("game") if lookup.get("found") else None
        if not isinstance(game, dict) or not game.get("id"):
            return {}
        payload = await self._post_json(
            f"{self.ITAD_API_BASE}/games/prices/v3",
            params={"key": self.itad_api_key, "country": country, "shops": "61"},
            json_body=[str(game["id"])],
        )
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            return {}
        return dict(payload[0])

    async def test_connection(self) -> dict[str, Any]:
        started = asyncio.get_running_loop().time()
        await self.get_player_summaries(["76561197960435530"], retries=0)
        return {
            "ok": True,
            "latency_ms": round((asyncio.get_running_loop().time() - started) * 1000),
        }

    async def _request_json(
        self,
        url: str,
        *,
        params: dict[str, Any],
        retries: int = 0,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(max(0, retries) + 1):
            try:
                response = await self._http.get(url, params=params, headers=headers)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise SteamClientError("Steam API 返回了无效数据。")
                return payload
            except (httpx.HTTPError, ValueError, SteamClientError) as exc:
                last_error = exc
                if attempt < retries:
                    await asyncio.sleep(min(2**attempt, 4))
        raise SteamClientError(f"Steam API 请求失败：{last_error}") from last_error

    async def _post_json(
        self,
        url: str,
        *,
        params: dict[str, Any],
        json_body: list[str],
    ) -> Any:
        try:
            response = await self._http.post(url, params=params, json=json_body)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SteamClientError(f"第三方价格 API 请求失败：{exc}") from exc

    def _require_key(self) -> str:
        if not self.api_key:
            raise SteamClientError("尚未配置 QQBOT_STEAM_API_KEY。")
        return self.api_key
