from __future__ import annotations

from dataclasses import replace

from qq_personal_bot.ai_models import catalog_model_option, is_vision_dsapi_model
from qq_personal_bot.settings import AppSettings


def ai_proxy_state(settings: AppSettings, store) -> dict[str, bool]:
    configured = bool(settings.ai_proxy_url)
    enabled = (
        store.get_ai_proxy_enabled(default=configured)
        if hasattr(store, "get_ai_proxy_enabled") else configured
    )
    return {"ai_proxy_configured": configured, "ai_proxy_enabled": configured and enabled}


def resolve_ai_proxy_settings(settings: AppSettings, store) -> AppSettings:
    if settings.ai_proxy_resolved:
        return settings
    if not settings.ai_proxy_url and (
        not hasattr(store, "get_setting") or not store.get_setting("ai_proxy_enabled", "")
    ):
        return settings
    enabled = ai_proxy_state(settings, store)["ai_proxy_enabled"]
    return replace(settings, ai_proxy_url=settings.ai_proxy_url if enabled else "",
                   ai_proxy_resolved=True)


def provider_settings(
    settings: AppSettings, provider: dict, *, managed: bool = True
) -> AppSettings:
    model = provider["default_model"]
    option = next(
        (item for item in provider["models"] if item["id"] == model), catalog_model_option(model)
    )
    return replace(
        settings,
        dsapi_base_url=provider["base_url"],
        dsapi_api_key=provider["api_key"],
        dsapi_model=model,
        dsapi_api_format=provider["api_format"],
        dsapi_tls_mode=provider.get("tls_mode", "auto"),
        dsapi_timeout_seconds=provider["timeout_seconds"],
        dsapi_provider_id=provider["id"],
        dsapi_provider_managed=managed,
        dsapi_vision_enabled=bool(option.get("vision", False)) or is_vision_dsapi_model(model),
    )


def resolve_ai_settings(settings: AppSettings, store) -> AppSettings:
    # A request keeps its provider snapshot, even if the UI switches during generation.
    settings = resolve_ai_proxy_settings(settings, store)
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


def resolve_background_ai_settings(settings: AppSettings, store) -> AppSettings:
    if settings.dsapi_background_resolved:
        return settings
    settings = resolve_ai_proxy_settings(settings, store)
    route = store.get_background_ai_route() if hasattr(store, "get_background_ai_route") else None
    if route:
        # A reply snapshot may be passed in; background work has its own provider and model.
        provider = store.get_ai_provider(route["provider_id"])
        provider["default_model"] = route["model"]
        settings = provider_settings(settings, provider)
    else:
        # Preserve existing installations until a background route is explicitly selected.
        settings = resolve_ai_settings(settings, store)
    return replace(settings, dsapi_background_resolved=True)
