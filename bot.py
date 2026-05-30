from __future__ import annotations

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

from qq_personal_bot.runtime import get_settings, get_store


settings = get_settings()

nonebot.init(
    driver=settings.nonebot_driver,
    host=settings.host,
    port=settings.port,
    command_start={"/"},
    superusers={str(admin) for admin in settings.admins},
)

driver = nonebot.get_driver()
driver.register_adapter(OneBotV11Adapter)

get_store()

nonebot.load_plugin("qq_personal_bot.plugins.control")
nonebot.load_plugin("qq_personal_bot.plugins.chat")
nonebot.load_plugin("qq_personal_bot.plugins.web_admin")


if __name__ == "__main__":
    nonebot.run()

