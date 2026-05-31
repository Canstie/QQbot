-- Command: 今日宜忌
-- Trigger: ~今日宜忌

local NAMESPACE = "今日宜忌"

local yi_pool = {
  "摸鱼但不被发现",
  "早点下班",
  "主动喝水",
  "整理桌面",
  "打开任务先做两分钟",
  "夸群友一句",
  "点一杯刚刚好的饮料",
  "把收藏夹里的教程看完一页",
  "保存文件",
  "给自己留十分钟空档",
}

local ji_pool = {
  "嘴硬",
  "凌晨做重大决定",
  "和电梯门比速度",
  "空腹喝冰的",
  "把 bug 说成小问题",
  "连续撤回三次消息",
  "刚醒就开会",
  "在群里立太满的 flag",
  "忘记带钥匙",
  "边走路边回长消息",
}

local sign_pool = {
  "今天主打一个稳中带皮，先把能赢的小局拿下。",
  "别急着证明自己，先证明午饭很好吃。",
  "遇到麻烦先截图，世界会奖励有证据的人。",
  "保持礼貌，但不要把脑子借给别人开车。",
  "今天适合慢慢来，慢慢来也算前进。",
  "情绪不要预支，快乐可以分期。",
}

local function quote_reply(message)
  return {quote = true, reply = message}
end

local function state_key(event)
  return tostring(event.date) .. ":" .. tostring(event.user_id)
end

local function hash_text(text)
  local hash = 0
  for i = 1, #text do
    hash = (hash * 131 + string.byte(text, i)) % 2147483647
  end
  return hash
end

local function copy_table(source)
  local result = {}
  for i = 1, #source do
    result[i] = source[i]
  end
  return result
end

local function pick_unique(source, count)
  local pool = copy_table(source)
  local result = {}
  for _ = 1, math.min(count, #pool) do
    local index = math.random(#pool)
    table.insert(result, table.remove(pool, index))
  end
  return result
end

local function join(items)
  return table.concat(items, "、")
end

local function build_reply(key)
  math.randomseed(hash_text(key))
  local yi = pick_unique(yi_pool, 3)
  local ji = pick_unique(ji_pool, 3)
  local sign = sign_pool[math.random(#sign_pool)]

  return "今日宜：" .. join(yi) ..
    "\n今日忌：" .. join(ji) ..
    "\n今日签语：" .. sign
end

function on_command(event, api)
  local key = state_key(event)
  local saved = api.get_state(key, NAMESPACE)
  if saved ~= nil and saved ~= "" then
    return quote_reply(saved)
  end

  local message = build_reply(key)
  api.set_state(key, message, NAMESPACE)
  return quote_reply(message)
end
