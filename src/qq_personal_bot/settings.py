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
    sticker_dir: Path = Path("data/stickers")
    download_image_dir: Path = Path("downloadimage")
    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "qqbot-downloads"
    minio_secure: bool = False
    menu_provider: str = "auto"
    jisu_recipe_appkey: str = ""
    dsapi_enabled: bool = True
    dsapi_base_url: str = "https://api.deepseek.com"
    dsapi_api_key: str = ""
    dsapi_model: str = "deepseek-v4-flash"
    dsapi_timeout_seconds: float = 30.0
    dsapi_max_tokens: int = 80
    dsapi_history_idle_seconds: int = 1200
    dsapi_system_prompt: str = (
        "你是 QQ 群里的聊天机器人。直接回答，不要复述问题。"
        "不要声称看到了未提供的图片、语音、视频或文件。"
    )
    web_token: str | None = None
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
            sticker_dir=Path(env.get("QQBOT_STICKER_DIR", "data/stickers")),
            download_image_dir=Path(env.get("QQBOT_DOWNLOAD_IMAGE_DIR", "downloadimage")),
            minio_endpoint=(
                env.get("QQBOT_MINIO_ENDPOINT", "127.0.0.1:9000").strip()
                or "127.0.0.1:9000"
            ),
            minio_access_key=env.get("QQBOT_MINIO_ACCESS_KEY", "").strip(),
            minio_secret_key=env.get("QQBOT_MINIO_SECRET_KEY", "").strip(),
            minio_bucket=(
                env.get("QQBOT_MINIO_BUCKET", "qqbot-downloads").strip()
                or "qqbot-downloads"
            ),
            minio_secure=_env_bool(env.get("QQBOT_MINIO_SECURE"), False),
            menu_provider=env.get("QQBOT_MENU_PROVIDER", "auto").strip().lower() or "auto",
            jisu_recipe_appkey=env.get("QQBOT_JISU_RECIPE_APPKEY", "").strip(),
            dsapi_enabled=_env_bool(env.get("QQBOT_DSAPI_ENABLED"), True),
            dsapi_base_url=(
                env.get("QQBOT_DSAPI_BASE_URL", "https://api.deepseek.com").strip()
                or "https://api.deepseek.com"
            ),
            dsapi_api_key=(
                env.get("QQBOT_DSAPI_API_KEY")
                or env.get("DEEPSEEK_API_KEY")
                or env.get("DS_API_KEY")
                or ""
            ).strip(),
            dsapi_model=(
                env.get("QQBOT_DSAPI_MODEL", "deepseek-v4-flash").strip()
                or "deepseek-v4-flash"
            ),
            dsapi_timeout_seconds=_env_float(env.get("QQBOT_DSAPI_TIMEOUT_SECONDS"), 30.0),
            dsapi_max_tokens=_env_int(env.get("QQBOT_DSAPI_MAX_TOKENS"), 80),
            dsapi_history_idle_seconds=_env_int(
                env.get("QQBOT_DSAPI_HISTORY_IDLE_SECONDS"), 1200
            ),
            dsapi_system_prompt=(
                env.get("QQBOT_DSAPI_SYSTEM_PROMPT", cls.dsapi_system_prompt).strip()
                or cls.dsapi_system_prompt
            ),
            web_token=web_token,
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
        if self.dsapi_timeout_seconds <= 0:
            raise ValueError("QQBOT_DSAPI_TIMEOUT_SECONDS must be > 0")
        if self.dsapi_max_tokens <= 0:
            raise ValueError("QQBOT_DSAPI_MAX_TOKENS must be > 0")
        if self.dsapi_history_idle_seconds <= 0:
            raise ValueError("QQBOT_DSAPI_HISTORY_IDLE_SECONDS must be > 0")
