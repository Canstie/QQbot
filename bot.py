from __future__ import annotations

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter

from qq_personal_bot.activity import close_group_activity
from qq_personal_bot.memory_graph import MemoryGraphWorker
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
memory_worker = MemoryGraphWorker(get_store(), settings)


@driver.on_startup
async def _start_memory_graph() -> None:
    memory_worker.start()


@driver.on_shutdown
async def _flush_activity_on_shutdown() -> None:
    await close_group_activity()
    await memory_worker.stop()

get_store()

nonebot.load_plugin("qq_personal_bot.plugins.self_guard")
nonebot.load_plugin("qq_personal_bot.plugins.control")
nonebot.load_plugin("qq_personal_bot.plugins.download")
nonebot.load_plugin("qq_personal_bot.plugins.chat")
nonebot.load_plugin("qq_personal_bot.plugins.steam")
nonebot.load_plugin("qq_personal_bot.plugins.web_admin")


if __name__ == "__main__":
    nonebot.run()

