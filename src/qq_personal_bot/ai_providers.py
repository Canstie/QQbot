from __future__ import annotations

from dataclasses import replace

from qq_personal_bot.settings import AppSettings


def provider_settings(
    settings: AppSettings, provider: dict, *, managed: bool = True
) -> AppSettings:
    model = provider["default_model"]
    option = next((item for item in provider["models"] if item["id"] == model), {})
    return replace(
        settings,
        dsapi_base_url=provider["base_url"],
        dsapi_api_key=provider["api_key"],
        dsapi_model=model,
        dsapi_api_format=provider["api_format"],
        dsapi_timeout_seconds=provider["timeout_seconds"],
        dsapi_provider_id=provider["id"],
        dsapi_provider_managed=managed,
        dsapi_vision_enabled=bool(option.get("vision", False)),
    )


def resolve_ai_settings(settings: AppSettings, store) -> AppSettings:
    # A request keeps its provider snapshot, even if the UI switches during generation.
    if settings.dsapi_provider_id or not hasattr(store, "get_active_ai_provider"):
        return settings
    if not store.ai_provider_managed():
        return settings
    provider = store.get_active_ai_provider()
    return (
        provider_settings(settings, provider, managed=store.ai_provider_managed())
        if provider
        else settings
    )


def active_ai_model(config: dict, settings: AppSettings) -> str:
    if settings.dsapi_provider_managed:
        return settings.dsapi_model
    return str((config.get("active_knowledge") or {}).get("model") or settings.dsapi_model)
