from __future__ import annotations

from qq_personal_bot.core.policy import PolicyEngine
from qq_personal_bot.core.store import PolicyStore
from qq_personal_bot.settings import AppSettings
from qq_personal_bot.steam.service import SteamMonitorService

_settings: AppSettings | None = None
_store: PolicyStore | None = None
_policy_engine: PolicyEngine | None = None
_steam_service: SteamMonitorService | None = None


def get_settings() -> AppSettings:
    global _settings
    if _settings is None:
        _settings = AppSettings.from_env()
        _settings.validate()
    return _settings


def get_store() -> PolicyStore:
    global _store
    if _store is None:
        settings = get_settings()
        _store = PolicyStore(settings.db_path)
        _store.initialize(settings)
    return _store


def get_policy_engine() -> PolicyEngine:
    global _policy_engine
    if _policy_engine is None:
        _policy_engine = PolicyEngine(get_store())
    return _policy_engine


def get_steam_service() -> SteamMonitorService:
    global _steam_service
    if _steam_service is None:
        _steam_service = SteamMonitorService(get_store(), get_settings())
    return _steam_service


def reset_runtime() -> None:
    global _settings, _store, _policy_engine, _steam_service
    _settings = None
    _store = None
    _policy_engine = None
    _steam_service = None

