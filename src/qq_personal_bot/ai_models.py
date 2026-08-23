from __future__ import annotations

from typing import Any


DSAPI_MODEL_OPTIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "flash",
        "id": "deepseek-v4-flash",
        "label": "Flash",
        "vision": False,
    },
    {
        "key": "pro",
        "id": "deepseek-v4-pro",
        "label": "Pro",
        "vision": False,
    },
    {
        "key": "vision",
        "id": "deepseek-v4-flash-vision-exp",
        "label": "Flash Vision Exp",
        "vision": True,
    },
)

_MODELS_BY_KEY = {str(item["key"]): item for item in DSAPI_MODEL_OPTIONS}
_MODELS_BY_ID = {str(item["id"]): item for item in DSAPI_MODEL_OPTIONS}


def resolve_dsapi_model(value: str) -> dict[str, Any]:
    normalized = str(value).strip().lower()
    option = _MODELS_BY_KEY.get(normalized) or _MODELS_BY_ID.get(normalized)
    if option is None:
        raise ValueError("model must be flash, pro, or vision")
    return dict(option)


def dsapi_model_option(model_id: str) -> dict[str, Any] | None:
    option = _MODELS_BY_ID.get(str(model_id).strip())
    return dict(option) if option is not None else None


def is_vision_dsapi_model(model_id: str) -> bool:
    option = dsapi_model_option(model_id)
    return bool(option and option["vision"])


def public_dsapi_model_options() -> list[dict[str, Any]]:
    return [dict(option) for option in DSAPI_MODEL_OPTIONS]
