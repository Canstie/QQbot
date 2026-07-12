-- Command: help
-- Trigger: ~help

local function quote_reply(message)
  return {quote = true, reply = message}
end

function on_command(event, api)
  local lines = {
    "可用功能",
    "",
    "基础命令",
    "~help 查看这份帮助",
    "~今日天气 地点 查询天气",
    "~今日人品 查看今日人品",
    "~今日宜忌 查看今日宜忌",
    "~今日菜单 随机推荐今日吃什么",
    "",
    "群老婆",
    "~抽群老婆 抽今日群老婆",
    "~换个老婆 重新抽取一次",
    "~强娶 @群成员 指定今日群老婆",
    "",
    "图片与典图",
    "~存典 引用一张图片后保存到本群典图",
    "~爆典 随机发送一张已保存典图",
    "~左对称 / ~右对称 / ~上对称 / ~下对称 引用图片后生成对称图",
    "",
    "群数据",
    "~群总结 查看昨天的群消息总结",
    "",
    "菜单与饭店",
    "~添加菜单 先发菜名，再发图片",
    "~添加饭店 先发饭店名，再逐条发送招牌菜，发送 完成 保存",
    "~今日饭店 随机抽一家已添加的饭店",
    "",
    "免前缀触发",
    "吃什么 / csm / 今天吃什么 等会直接触发今日菜单",
    "进行中的 添加菜单 / 添加饭店 流程可发送 取消 退出",
    "",
    "管理员命令",
    "/bot status",
    "/bot on [group_id]",
    "/bot off [group_id]",
    "/bot mode allowlist|blocklist",
    "/bot admin add|remove <user_id>",
    "/bot prefix add|remove|list [prefix]",
  }

  return quote_reply(table.concat(lines, "\n"))
end
