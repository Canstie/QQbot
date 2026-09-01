# QQ Personal Bot

这是一个面向 NTQQ + OneBot v11 桥接方案的个人 QQ Bot 项目，可配合
LLOneBot 或 NapCat Framework 使用。桌面 QQ 客户端可以正常使用，同时 Bot
行为由群聊白名单/黑名单策略控制。

## 已包含功能

- 基于 NoneBot2 的入口和 OneBot v11 适配器。
- 使用 SQLite 保存群聊启用/禁用、黑名单、管理员、触发前缀、限流参数和审计记录。
- 群聊策略管道：群策略判断、触发条件判断、限流和默认回复；本人账号只允许前缀指令触发。
- 管理员命令：
  - `/download`（引用聊天记录，下载其中的全部图片并按内容查重）
  - `/download_overview`（查看 MinIO 图库的图片总数、大小和今日新增）
  - `/bot on [group_id]`
  - `/bot off [group_id]`
  - `/bot aion [group_id]`（开启指定群的 AI；群聊内省略群号则开启当前群）
  - `/bot aioff`（关闭当前群 AI）
  - `/bot aioff all`（关闭全部群 AI）
  - `/bot ai rs`（清空全部 AI 短期上下文）
  - `/bot aim list|flash|pro|vision`（查看或切换当前知识库使用的 AI 模型）
  - `/bot aik list|<序号>`（查看知识库，并按列表序号切换）
  - `/bot mode allowlist|blocklist`
  - `/bot status`
  - `/bot admin add <user_id>`（删除管理员仅允许在 Web 管理页操作）
  - `/bot prefix add|remove|list [prefix]`
- 使用 FastAPI 驱动时，会在 `/qqbot` 挂载本地 Web 管理页和 JSON API；前端文件在 `static/`。
- 可选 Lua 脚本入口，支持通过 OneBot API 获取群列表、群成员、登录信息等。
- 包含针对策略、存储和 OneBot 事件转换的单元测试。

## 安装

项目固定使用 uv 管理的 Python 3.13.15。先安装
[uv](https://docs.astral.sh/uv/getting-started/installation/)，然后同步项目环境：

```powershell
uv sync
Copy-Item .env.example .env
```

编辑 `.env`：

- 将 `ONEBOT_ACCESS_TOKEN` 设置为和 LLOneBot/NapCat 中一致的 token。
- 将 `QQBOT_ADMINS` 设置为你的 QQ 号。
- 除非你明确想使用黑名单模式，否则保持 `QQBOT_POLICY_MODE=allowlist`。

启动：

```powershell
uv run python bot.py
```

生产服务器使用锁文件同步运行依赖，不安装开发依赖：

```bash
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
uv sync --frozen --no-dev
systemctl restart qqbot.service
```

`qqbot.service` 继续直接执行 `/opt/qqbot/.venv/bin/python /opt/qqbot/bot.py`，
因此 uv 只负责创建和同步环境，不参与 Bot 运行时进程管理。

也可以构建一个无终端窗口的启动器 exe：

```powershell
.\tools\build_launcher.ps1
```

构建后双击 `dist\QQBotLauncher.exe` 即可后台启动 Bot。启动器会使用本项目的
`.venv\Scripts\python.exe` 运行 `bot.py`，日志按天写入
`logs\qqbot-YYYY-MM-DD.log`。启动器会在后台驻留，跨天时重启 Bot 切换到新日志，
并清理过期日志；默认保留最近 2 天。若只想保留当天日志，可在启动前设置：

```powershell
$env:QQBOT_LOG_RETENTION_DAYS = "1"
```

如果 `127.0.0.1:8080` 已经被其他进程占用，启动器会提示原因，避免重复启动。

## OneBot 连接

推荐使用反向 WebSocket：

```text
ws://127.0.0.1:8080/onebot/v11/ws
```

在 LLOneBot/NapCat 中配置这个地址，并使用和 `.env` 中一致的 access token。
当前项目保持 OneBot 11；OneBot 12 需要协议端明确支持，LLBot/LLOneBot 当前推荐继续使用 OneBot 11 或其文档中明确支持的协议。

如果希望同一个 QQ 账号在桌面客户端里手动发送 `~指令` 也能触发 Bot，需要在 LLOneBot/LLBot 中开启 `reportSelfMessage`。开启后，本项目只处理本人账号发出的前缀指令，例如 `~今日人品`、`~今日菜单`；本人普通聊天、@ 自己、`吃什么`/`csm` 这类免前缀触发都会保持静默，避免影响正常聊天或形成循环回复。

也支持正向 WebSocket，配置如下：

```env
DRIVER=~fastapi+~websockets
ONEBOT_WS_URLS=["ws://127.0.0.1:3001"]
```

## Web 管理页

打开：

```text
http://127.0.0.1:8080/qqbot/
```

如果设置了 `QQBOT_WEB_TOKEN`，访问管理页会先进入登录页；登录后会写入
HttpOnly 会话 Cookie。脚本或调试请求仍可通过 `?token=...` 或 API 请求头
`X-Admin-Token` 传入同一个 token。

Web 管理页可以直接用表单维护触发前缀、前缀触发回复、免前缀关键词回复。
保存时会校验规则字段和正则表达式；保存成功后下一条消息会使用新规则。
AI 页面支持创建、编辑、删除多个角色知识库，并随时切换当前启用项；升级时已有的
单一知识提示词会自动迁移为“默认知识库”。切换角色时可同步清空短期聊天上下文。
每个知识库可独立绑定 DSAPI 模型、Thinking 模式、回复模式、最大输出 Token、上下文轮数和 Temperature；
新建时通过悬浮配置窗口填写，切换知识库后这些请求参数会同步切换。

前端静态文件位于：

```text
static/index.html
static/styles.css
static/app.js
```

## 自定义回复

回复规则保存在项目根目录的 `replies.json`。推荐在 Web 管理页用表单修改，
也可以直接编辑文件。保存后下一条消息通常就会按新规则回复，不需要改 Python 代码。

示例：

```json
{
  "empty": "Bot 已启用，请在触发词后输入内容。",
  "fallback": "收到：{message}",
  "rules": [
    {
      "type": "exact",
      "pattern": "菜单",
      "reply": "可用命令：菜单、帮助、状态"
    },
    {
      "type": "contains",
      "pattern": "你好",
      "reply": "你好，我是群助手。"
    },
    {
      "type": "prefix",
      "pattern": "复读 ",
      "reply": "{message}"
    },
    {
      "type": "regex",
      "pattern": "^状态$",
      "reply": "Bot 在线。"
    }
  ],
  "direct_rules": []
}
```

`rules` 用于带触发前缀的消息，例如 `~菜单` 会先去掉 `~` 再匹配 `菜单`。
`direct_rules` 用于免前缀关键词回复，例如群里直接出现某个词就回复。
免前缀关键词应答默认按 10% 概率触发，避免群聊太吵；可在 Web 管理页“策略”里调整，或设置
`QQBOT_DIRECT_TRIGGER_PERCENT=10`。这个概率只影响 `direct_rules`，
不影响 `direct_lua_rules`、`~`、`#bot` 等前缀指令，也不影响 @ 触发。

规则从上到下匹配，命中第一条就回复。`type` 支持：

- `exact`：消息完全等于 `pattern`
- `contains`：消息包含 `pattern`
- `prefix`：消息以 `pattern` 开头，`{message}` 会自动去掉这个前缀
- `regex`：按正则表达式匹配

可用变量：

- `{message}`：处理后的消息；`prefix` 规则会去掉前缀
- `{raw_message}`：原始触发内容
- `{pattern}`：当前命中的规则 pattern

## Lua 脚本

Lua 支持多指令脚本，默认目录为：

```text
scripts/lua
```

群里发送 `~指令 参数` 时，会自动执行 `scripts/lua/指令.lua`。例如：

```text
~抽群老婆
```

会执行：

```text
scripts/lua/抽群老婆.lua
```

当前内置脚本示例：

- `~抽群老婆`：每天为当前用户抽取一个固定群成员。
- `~换个老婆`：当天重新抽取一次群老婆。
- `~强娶 @群成员`：指定一个群成员作为今天的群老婆；抽、换、强娶都会避开当天已经成为别人群老婆的人。
- `~今日天气 北京`：通过国内天气接口查询指定地点的中文天气。
- 转发 QQ 小程序到已启用群聊：小红书与小黑盒图片帖会发送全部图片集合；B站视频会把封面、标题、作者名和头像合成为粉色主题卡片；不回复 URL，图片只在发送期间临时缓存。
- `~今日人品`：每天固定生成 0-100 的人品值和一句短评。
- `~今日宜忌`：按当天农历生成统一的“宜/忌/签语”。
- `~今日菜单`：优先使用国内的 极速数据/JisuAPI 菜谱大全接口随机推荐菜单，返回 `pic` 图片时会缓存并附图；未配置 key 或接口失败时回退到本地中文菜谱库。
- `~今日菜单` 后面的文字不再按地区筛选，菜单库也不再维护地区分类；本地回退只按菜名、别名、菜系、分类和标签做内部匹配。
  JisuAPI 需要在 `.env` 设置 `QQBOT_JISU_RECIPE_APPKEY`，并保持 `QQBOT_MENU_PROVIDER=auto` 或 `jisu`。本地数据保存在 SQLite，命中有效本地图片时会附图；启动时会自动清理旧的 `今日菜单:cache:v1/v2` Lua 缓存命名空间。
  默认 seed 在 `data/recipes_seed.jsonl`，如需批量导入本地菜谱可执行 `python tools/import_menu_recipes.py --purge-legacy-cache`；`--seed-only` 可只导入本地 seed。
  也可以直接在群里发送包含 `吃什么` 或 `csm` 的消息触发默认今日菜单，不需要前缀。
- `~添加菜单`：群内两步添加自定义菜单，先发送菜单名，再发送图片；所有已启用群成员可用。
- `~添加饭店`：群内添加饭店，先发送饭店名，再连续发送招牌菜，发送 `完成` 保存，发送 `取消` 退出。
- `~今日饭店`：从当前群已添加并启用的饭店中随机抽取一个。
- `~存典`：引用图片后保存到本群独立的私有 MinIO bucket；按 SHA-256 查重，重复图片会提示“已存在相同的典”。
- `~爆典`：优先从当前群的 MinIO 典藏中发送出现次数最少的图片，同次数时随机选择。
- `~上对称` / `~下对称` / `~左对称` / `~右对称`：引用一张图片后发送，对图片按指定方向生成对称图；图片只临时保存，处理完成后删除。
- `~群排行` 或 `~群排行 摸鱼王`：每天按主题随机生成群成员 TOP3。
- `~群总结`：按今日真实群消息统计总消息数、活跃人数、水群榜、字数榜、发图榜、@ 人榜、最活跃时段、早鸟和夜猫子；只保存统计计数，不保存消息正文。
- `/download`：管理员引用普通图片消息或合并转发聊天记录后使用。Bot 会先提示正在工作，再递归下载记录中的图片，以 SHA-256 内容哈希跨日期查重，并保存到私有 MinIO bucket 的 `YYYYMMDD/` 前缀中。
- `/download_overview`：查看 MinIO 图库的图片总数、总大小和今日新增数量。

Web 管理页按标签页整理为策略、回复规则、Lua、菜单、饭店。策略页可直接修改核心配置 JSON 对应字段，包括模式、启用/屏蔽群、管理员、@ 触发、前缀、免前缀概率、群限流秒数和用户每分钟限流；菜单页可新增/编辑/删除带图菜单并清理无图 HowToCook；饭店页可新增/编辑/删除饭店和招牌菜。

默认启用，相关配置：

```env
QQBOT_LUA_ENABLED=true
QQBOT_LUA_DIR=scripts/lua
QQBOT_LUA_SCRIPT=scripts/main.lua
QQBOT_LUA_TIMEOUT_SECONDS=10
```

推荐脚本定义 `on_command(event, api)`；兼容旧的 `on_message(event, api)`。
返回字符串时会直接作为 bot 回复；返回 `nil` 时继续走 Web 管理页里的 JSON 回复规则。

`event` 中常用字段：

- `event.command`：指令名，例如 `抽群老婆`
- `event.args`：指令后的参数，例如 `北京`
- `event.message`：同 `event.args`
- `event.full_message`：去掉触发前缀后的完整内容，例如 `天气 北京`
- `event.segments`：OneBot 消息段，例如 `at`、`image` 等，可用于读取被 @ 的群成员。

示例：

```lua
function on_command(event, api)
  if event.group_id == nil then
    return "这个功能只能在群聊里使用。"
  end

  local members = api.get_group_member_list(event.group_id)
  return "当前群成员数：" .. tostring(#members)
end
```

常用 `api`：

- `api.get_group_list()`
- `api.get_group_info(group_id)`
- `api.get_group_member_list(group_id)`
- `api.get_group_member_info(group_id, user_id)`
- `api.get_login_info()`
- `api.get_stranger_info(user_id)`
- `api.send_group_message(group_id, message)`
- `api.send_private_message(user_id, message)`
- `api.reply(message)`
- `api.get_state(key, namespace)`：读取 Lua 持久化状态；`namespace` 可省略，默认当前指令名
- `api.set_state(key, value, namespace)`：保存 Lua 持久化状态
- `api.delete_state(key, namespace)`：删除 Lua 持久化状态
- `api.url_encode(value)`：URL 编码文本
- `api.http_get_json(url)`：请求 HTTP/HTTPS JSON 接口，返回 Lua table
- `api.today_lunar()`：返回当天农历信息，包含年月日、中文月日和稳定 key
- `api.json_encode(value)`：把 Lua table 编码为 JSON 字符串，便于保存到状态
- `api.json_decode(value)`：把 JSON 字符串解码为 Lua table
- `api.call(action, params)`：调用其他 OneBot API
- `api.set_pending_command(command)` / `api.clear_pending_command()`：让同一用户下一条群消息继续交给指定 Lua 指令处理
- `api.save_classic_image(group_id, image_source, image_id)`：保存典图到当前群的 MinIO bucket，并返回保存、重复或失败状态
- `api.pick_classic_image(group_id, seed)`：从当前群典藏索引里选一张图片
- `api.classic_image(relpath)`：从 MinIO 读取典图并转成可发送的 CQ 图片
- `api.mirror_referenced_image(direction)`：读取当前消息引用的图片并生成对称图；`direction` 支持 `top`、`bottom`、`left`、`right`，处理过程只使用临时文件

## 测试

```powershell
uv run pytest
```

## 安全说明

个人 QQ 桥接方案属于非官方方案，可能存在账号风控风险。如果合规性比使用同一个
个人 QQ 桌面账号更重要，应改用 QQ 官方机器人 API。
