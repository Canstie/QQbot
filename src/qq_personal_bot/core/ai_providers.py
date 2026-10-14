from __future__ import annotations

import copy
import json
import time
from urllib.parse import urlsplit, urlunsplit

from qq_personal_bot.ai_models import catalog_model_option, public_dsapi_model_options


def normalize_ai_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("接口地址须为 HTTP/HTTPS 地址，不能包含账号、查询参数或片段")
    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/models"):
        if path.endswith(suffix):
            path = path.removesuffix(suffix)
    # Most compatible gateways publish their API under /v1; DeepSeek also accepts it.
    if not path:
        path = "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class AIProviderStore:
    """Provider configuration shares the bot database; secrets never enter public responses."""

    def _initialize_ai_providers(self, conn, settings) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_providers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                base_url TEXT NOT NULL,
                api_key TEXT NOT NULL DEFAULT '',
                default_model TEXT NOT NULL,
                api_format TEXT NOT NULL DEFAULT 'openai',
                timeout_seconds REAL NOT NULL DEFAULT 60,
                models_json TEXT NOT NULL DEFAULT '[]',
                models_updated_at REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(ai_providers)")}
        if "tls_mode" not in columns:
            conn.execute(
                "ALTER TABLE ai_providers ADD COLUMN tls_mode TEXT NOT NULL DEFAULT 'auto'"
            )
        legacy_flash_ids = {"deepseek-v4-flash", "deepseek-v4-flash-vision-exp"}
        conn.execute(
            "UPDATE ai_providers SET default_model='deepseek-flash' "
            "WHERE api_format='deepseek' AND default_model IN (?, ?)",
            tuple(sorted(legacy_flash_ids)),
        )
        if conn.execute("SELECT 1 FROM ai_providers LIMIT 1").fetchone() is None:
            now = time.time()
            cursor = conn.execute(
                "INSERT INTO ai_providers(name, base_url, api_key, default_model, api_format, "
                "timeout_seconds, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "DeepSeek" if "deepseek.com" in settings.dsapi_base_url else "原有接口",
                    normalize_ai_base_url(settings.dsapi_base_url),
                    settings.dsapi_api_key,
                    "deepseek-flash"
                    if settings.dsapi_model in legacy_flash_ids
                    else settings.dsapi_model,
                    settings.dsapi_api_format,
                    settings.dsapi_timeout_seconds,
                    now,
                    now,
                ),
            )
            self.set_setting("ai_active_provider_id", str(cursor.lastrowid), conn=conn)
            # Preserve knowledge-base-specific models until the first explicit provider switch.
            self.set_setting("ai_provider_managed", "false", conn=conn)

    def get_ai_provider(self, provider_id: int) -> dict:
        def load():
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM ai_providers WHERE id = ?", (provider_id,)
                ).fetchone()
                if row is None:
                    raise ValueError("供应商不存在")
                result = dict(row)
                result["models"] = json.loads(result.pop("models_json"))
                return result

        return copy.deepcopy(self._cached_runtime_value(("ai_provider", int(provider_id)), load))

    def get_active_ai_provider(self) -> dict | None:
        value = self.get_setting("ai_active_provider_id", "")
        return self.get_ai_provider(int(value)) if value else None

    def ai_provider_managed(self) -> bool:
        return self.get_setting("ai_provider_managed", "false") == "true"

    def get_ai_model_options(self) -> list[dict]:
        if not self.ai_provider_managed():
            return public_dsapi_model_options()
        provider = self.get_active_ai_provider()
        if not provider:
            return public_dsapi_model_options()
        options = provider["models"] or (
            public_dsapi_model_options() if provider["api_format"] == "deepseek" else []
        )
        if not any(item["id"] == provider["default_model"] for item in options):
            options.append(catalog_model_option(provider["default_model"]))
        return options

    def set_ai_provider_model(self, model: str, *, actor_id: int) -> None:
        provider = self.get_active_ai_provider()
        if not provider:
            raise ValueError("请先配置供应商")
        model = self._normalize_dsapi_model(model)
        with self._connect() as conn:
            conn.execute(
                "UPDATE ai_providers SET default_model=?, updated_at=? WHERE id=?",
                (model, time.time(), provider["id"]),
            )
            self.audit(
                actor_id, "set_ai_provider_model", str(provider["id"]), {"model": model}, conn=conn
            )
        self._invalidate_runtime_cache()

    def list_ai_providers(self) -> list[dict]:
        active_id = int(self.get_setting("ai_active_provider_id", "0"))
        with self._connect() as conn:
            ids = [row["id"] for row in conn.execute("SELECT id FROM ai_providers ORDER BY id")]
        result = []
        for provider_id in ids:
            item = self.get_ai_provider(provider_id)
            item["api_key_configured"] = bool(item.pop("api_key"))
            item["active"] = provider_id == active_id
            result.append(item)
        return result

    def save_ai_provider(
        self,
        *,
        name: str,
        base_url: str,
        default_model: str,
        api_format: str = "openai",
        tls_mode: str = "auto",
        timeout_seconds: float = 60,
        api_key: str | None = None,
        provider_id: int | None = None,
        actor_id: int = 0,
    ) -> int:
        name = name.strip()
        if not name or len(name) > 80:
            raise ValueError("供应商名称须为 1—80 个字符")
        base_url = normalize_ai_base_url(base_url)
        default_model = self._normalize_dsapi_model(default_model)
        if api_format not in {"openai", "deepseek"}:
            raise ValueError("请选择兼容接口或 DeepSeek 接口")
        if tls_mode not in {"auto", "tls12"}:
            raise ValueError("请选择自动 TLS 或 TLS 1.2 兼容模式")
        if not 5 <= timeout_seconds <= 600:
            raise ValueError("请求超时须在 5—600 秒之间")
        existing = self.get_ai_provider(provider_id) if provider_id is not None else None
        key = api_key.strip() if api_key is not None else existing["api_key"] if existing else ""
        if not key or len(key) > 4096 or any(ch.isspace() for ch in key):
            raise ValueError("请填写有效的 API Key；编辑时留空可保留已有密钥")
        now = time.time()
        with self._connect() as conn:
            values = (name, base_url, key, default_model, api_format, timeout_seconds, now)
            if existing:
                conn.execute(
                    "UPDATE ai_providers SET name=?, base_url=?, api_key=?, default_model=?, "
                    "api_format=?, timeout_seconds=?, updated_at=? WHERE id=?",
                    (*values, provider_id),
                )
                if base_url != existing["base_url"] or key != existing["api_key"]:
                    conn.execute(
                        "UPDATE ai_providers SET models_json='[]', models_updated_at=NULL "
                        "WHERE id=?",
                        (provider_id,),
                    )
                if str(provider_id) == self.get_setting("ai_active_provider_id", ""):
                    self.set_setting("ai_provider_managed", "true", conn=conn)
            else:
                cursor = conn.execute(
                    "INSERT INTO ai_providers(name,base_url,api_key,default_model,"
                    "api_format,timeout_seconds,updated_at,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (*values, now),
                )
                provider_id = int(cursor.lastrowid)
            self.audit(
                actor_id,
                "save_ai_provider",
                str(provider_id),
                {"name": name, "default_model": default_model, "api_format": api_format},
                conn=conn,
            )
            conn.execute("UPDATE ai_providers SET tls_mode=? WHERE id=?", (tls_mode, provider_id))
        self._invalidate_runtime_cache()
        return provider_id

    def save_ai_provider_models(
        self, provider_id: int, models: list[dict], *, expected_updated_at: float
    ) -> None:
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE ai_providers SET models_json=?, models_updated_at=? "
                "WHERE id=? AND updated_at=?",
                (
                    json.dumps(models, ensure_ascii=False),
                    time.time(),
                    provider_id,
                    expected_updated_at,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("供应商配置已变化，请重新获取模型")
        self._invalidate_runtime_cache()

    def activate_ai_provider(self, provider_id: int, *, actor_id: int = 0) -> None:
        provider = self.get_ai_provider(provider_id)
        if not provider["api_key"]:
            raise ValueError("请先配置供应商的 API Key")
        with self._connect() as conn:
            self.set_setting("ai_active_provider_id", str(provider_id), conn=conn)
            self.set_setting("ai_provider_managed", "true", conn=conn)
            self.audit(actor_id, "activate_ai_provider", str(provider_id), {}, conn=conn)
        self._invalidate_runtime_cache()

    def delete_ai_provider(self, provider_id: int, *, actor_id: int = 0) -> None:
        self.get_ai_provider(provider_id)
        with self._connect() as conn:
            active = conn.execute(
                "SELECT value FROM settings WHERE key='ai_active_provider_id'"
            ).fetchone()
            if active and str(provider_id) == active["value"]:
                raise ValueError("请先启用其他供应商，再删除当前供应商")
            conn.execute("DELETE FROM ai_providers WHERE id=?", (provider_id,))
            self.audit(actor_id, "delete_ai_provider", str(provider_id), {}, conn=conn)
        self._invalidate_runtime_cache()
