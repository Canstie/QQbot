# QQ Personal Bot

这是一个面向 NTQQ + OneBot v11 桥接方案的个人 QQ Bot 项目，可配合
LLOneBot 或 NapCat Framework 使用。桌面 QQ 客户端可以正常使用，同时 Bot
行为由群聊白名单/黑名单策略控制。

## 已包含功能

- 基于 NoneBot2 的入口和 OneBot v11 适配器。
- 使用 SQLite 保存群聊启用/禁用、黑名单、管理员、触发前缀、限流参数和审计记录。
- 群聊策略管道：忽略自身消息、群策略判断、触发条件判断、限流和默认回复。
- 管理员命令：
  - `/bot on [group_id]`
  - `/bot off [group_id]`
  - `/bot mode allowlist|blocklist`
  - `/bot status`
  - `/bot admin add|remove <user_id>`
  - `/bot prefix add|remove|list [prefix]`
- 使用 FastAPI 驱动时，会在 `/qqbot` 挂载本地 Web 管理页和 JSON API；前端文件在 `static/`。
- 可选 Lua 脚本入口，支持通过 OneBot API 获取群列表、群成员、登录信息等。
- 包含针对策略、存储和 OneBot 事件转换的单元测试。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

编辑 `.env`：

- 将 `ONEBOT_ACCESS_TOKEN` 设置为和 LLOneBot/NapCat 中一致的 token。
- 将 `QQBOT_ADMINS` 设置为你的 QQ 号。
- 除非你明确想使用黑名单模式，否则保持 `QQBOT_POLICY_MODE=allowlist`。

启动：

```powershell
python bot.py
```

## OneBot 连接

推荐使用反向 WebSocket：

```text
ws://127.0.0.1:8080/onebot/v11/ws
```

在 LLOneBot/NapCat 中配置这个地址，并使用和 `.env` 中一致的 access token。

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

如果设置了 `QQBOT_WEB_TOKEN`，可在页面 token 输入框中填写，也可以通过
`?token=...` 或 API 请求头 `X-Admin-Token` 传入。

Web 管理页可以直接用表单维护触发前缀、前缀触发回复、免前缀关键词回复。
保存时会校验规则字段和正则表达式；保存成功后下一条消息会使用新规则。

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

Lua 脚本位于：

```text
scripts/main.lua
```

默认启用，相关配置：

```env
QQBOT_LUA_ENABLED=true
QQBOT_LUA_SCRIPT=scripts/main.lua
QQBOT_LUA_TIMEOUT_SECONDS=3
```

脚本需要定义 `on_message(event, api)`。返回字符串时会直接作为 bot 回复；返回
`nil` 时继续走 Web 管理页里的回复规则。

示例：

```lua
function on_message(event, api)
  if event.message == "群人数" and event.group_id ~= nil then
    local members = api.get_group_member_list(event.group_id)
    return "当前群成员数：" .. tostring(#members)
  end

  return nil
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
- `api.call(action, params)`：调用其他 OneBot API

## 测试

```powershell
python -m pytest
```

## 安全说明

个人 QQ 桥接方案属于非官方方案，可能存在账号风控风险。如果合规性比使用同一个
个人 QQ 桌面账号更重要，应改用 QQ 官方机器人 API。
