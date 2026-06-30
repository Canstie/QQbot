from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv


load_dotenv()


def _split_ints(value: str | None) -> tuple[int, ...]:
    if not value:
        return ()
    result: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if item:
            result.append(int(item))
    return tuple(result)


def _split_strings(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    result = tuple(item.strip() for item in value.split(",") if item.strip())
    return result or default


def _env_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(value: str | None, default: float) -> float:
    if value is None or value.strip() == "":
        return default
    return float(value)


def _env_int(value: str | None, default: int) -> int:
    if value is None or value.strip() == "":
        return default
    return int(value)


@dataclass(frozen=True)
class AppSettings:
    db_path: Path
    admins: tuple[int, ...]
    policy_mode: str = "allowlist"
    trigger_mention: bool = True
    trigger_prefixes: tuple[str, ...] = ("~", "#bot")
    direct_trigger_percent: float = 10.0
    per_group_seconds: float = 5.0
    per_user_per_minute: int = 5
    lua_enabled: bool = True
    lua_script: Path = Path("scripts/main.lua")
    lua_dir: Path = Path("scripts/lua")
    lua_timeout_seconds: float = 3.0
    menu_seed_path: Path = Path("data/recipes_seed.jsonl")
    menu_image_dir: Path = Path("data/menu_images")
    classics_image_dir: Path = Path("data/classics")
    menu_provider: str = "auto"
    jisu_recipe_appkey: str = ""
    web_token: str | None = None
    llbot_web_url: str = "http://127.0.0.1:3080"
    nonebot_driver: str = "~fastapi"
    host: str = "127.0.0.1"
    port: int = 8080

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "AppSettings":
        env = environ if environ is not None else os.environ
        web_token = env.get("QQBOT_WEB_TOKEN")
        if web_token is not None:
            web_token = web_token.strip()
        if web_token in {"", "change-this-web-token"}:
            web_token = None

        return cls(
            db_path=Path(env.get("QQBOT_DB_PATH", "data/qqbot.sqlite3")),
            admins=_split_ints(env.get("QQBOT_ADMINS")),
            policy_mode=env.get("QQBOT_POLICY_MODE", "allowlist").strip() or "allowlist",
            trigger_mention=_env_bool(env.get("QQBOT_TRIGGER_MENTION"), True),
            trigger_prefixes=_split_strings(env.get("QQBOT_TRIGGER_PREFIXES"), ("~", "#bot")),
            direct_trigger_percent=_env_float(env.get("QQBOT_DIRECT_TRIGGER_PERCENT"), 10.0),
            per_group_seconds=_env_float(env.get("QQBOT_PER_GROUP_SECONDS"), 5.0),
            per_user_per_minute=_env_int(env.get("QQBOT_PER_USER_PER_MINUTE"), 5),
            lua_enabled=_env_bool(env.get("QQBOT_LUA_ENABLED"), True),
            lua_script=Path(env.get("QQBOT_LUA_SCRIPT", "scripts/main.lua")),
            lua_dir=Path(env.get("QQBOT_LUA_DIR", "scripts/lua")),
            lua_timeout_seconds=_env_float(env.get("QQBOT_LUA_TIMEOUT_SECONDS"), 3.0),
            menu_seed_path=Path(env.get("QQBOT_MENU_SEED_PATH", "data/recipes_seed.jsonl")),
            menu_image_dir=Path(env.get("QQBOT_MENU_IMAGE_DIR", "data/menu_images")),
            classics_image_dir=Path(env.get("QQBOT_CLASSICS_IMAGE_DIR", "data/classics")),
            menu_provider=env.get("QQBOT_MENU_PROVIDER", "auto").strip().lower() or "auto",
            jisu_recipe_appkey=env.get("QQBOT_JISU_RECIPE_APPKEY", "").strip(),
            web_token=web_token,
            llbot_web_url=env.get("QQBOT_LLBOT_WEB_URL", "http://127.0.0.1:3080").rstrip("/"),
            nonebot_driver=env.get("DRIVER", "~fastapi"),
            host=env.get("HOST", "127.0.0.1"),
            port=_env_int(env.get("PORT"), 8080),
        )

    def validate(self) -> None:
        if self.policy_mode not in {"allowlist", "blocklist"}:
            raise ValueError("QQBOT_POLICY_MODE must be allowlist or blocklist")
        if self.per_group_seconds < 0:
            raise ValueError("QQBOT_PER_GROUP_SECONDS must be >= 0")
        if self.direct_trigger_percent < 0 or self.direct_trigger_percent > 100:
            raise ValueError("QQBOT_DIRECT_TRIGGER_PERCENT must be between 0 and 100")
        if self.per_user_per_minute < 0:
            raise ValueError("QQBOT_PER_USER_PER_MINUTE must be >= 0")
        if self.lua_timeout_seconds <= 0:
            raise ValueError("QQBOT_LUA_TIMEOUT_SECONDS must be > 0")
        if self.menu_provider not in {"auto", "local", "jisu"}:
            raise ValueError("QQBOT_MENU_PROVIDER must be auto, local, or jisu")
