-- Command: help
-- Trigger: ~help

local function quote_reply(message)
  return {quote = true, reply = message}
end

local PUBLIC_SECTIONS = {
  {
    title = "基础与查询",
    commands = {
      "~help 查看这份帮助",
      "~今日天气 <地点> 查询天气",
      "~今日人品 查看今日人品",
      "~今日宜忌 查看今日宜忌",
      "~今日菜单 随机推荐今日吃什么",
    },
  },
  {
    title = "群老婆",
    commands = {
      "~抽群老婆 抽取今日群老婆",
      "~换个老婆 重新抽取一次",
      "~强娶 @群成员 指定今日群老婆",
    },
  },
  {
    title = "图片与典图",
    commands = {
      "~存典 引用图片并保存到本群典图",
      "~爆典 优先发送出现次数最少的本群典图",
      "~左对称 / ~右对称 / ~上对称 / ~下对称",
      "  引用图片后生成对应方向的对称图",
    },
  },
  {
    title = "群数据",
    commands = {
      "~群总结 查看昨天的群消息总结",
    },
  },
  {
    title = "菜单与饭店",
    commands = {
      "~添加菜单 按提示发送菜名和图片",
      "~添加饭店 按提示发送店名和招牌菜",
      "~今日饭店 随机抽一家本群饭店",
      "进行中的添加流程可发送“取消”退出",
    },
  },
  {
    title = "免前缀触发",
    commands = {
      "吃什么 / csm / 今天吃什么 等可直接触发今日菜单",
    },
  },
}

local ADMIN_COMMANDS = {
  "/download 引用聊天记录并下载其中的图片",
  "/download_overview 查看下载图片总量和占用空间",
  "/bot status 查看当前策略",
  "/bot on [group_id] 启用或解除屏蔽群",
  "/bot off [group_id] 停用或屏蔽群",
  "/bot aion [group_id] 开启指定群的 AI（省略则当前群）",
  "/bot mode allowlist|blocklist 切换策略模式",
  "/bot admin add <user_id> 添加管理员",
  "/bot prefix list 查看触发前缀",
  "/bot prefix add|remove <prefix> 增删触发前缀",
  "也可以使用 /qqbot 代替 /bot",
}

local function append_section(lines, title, commands)
  if #lines > 0 then
    table.insert(lines, "")
  end
  table.insert(lines, title)
  for i = 1, #commands do
    table.insert(lines, commands[i])
  end
end

function on_command(event, api)
  local lines = {"QQBot 功能帮助"}

  for i = 1, #PUBLIC_SECTIONS do
    local section = PUBLIC_SECTIONS[i]
    append_section(lines, section.title, section.commands)
  end

  if event.is_admin == true then
    append_section(lines, "管理员命令（仅管理员可用）", ADMIN_COMMANDS)
  end

  return quote_reply(table.concat(lines, "\n"))
end
