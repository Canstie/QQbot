from __future__ import annotations

from nonebot import get_driver, logger

from qq_personal_bot.web import create_app


driver = get_driver()

try:
    from nonebot.drivers.fastapi import Driver as FastAPIDriver

    if isinstance(driver, FastAPIDriver):
        driver.server_app.mount("/qqbot", create_app())
    else:
        logger.warning("QQBot web admin requires the FastAPI driver.")
except Exception as exc:  # pragma: no cover - depends on the selected NoneBot driver
    logger.warning(f"QQBot web admin was not mounted: {exc}")

