-- Command: 今日宜忌
-- Trigger: ~今日宜忌

local NAMESPACE = "今日宜忌"

local yi_pool = {
  "清理待办",
  "推进小改动",
  "补测试",
  "整理桌面",
  "早点收工",
  "备份重要文件",
  "复盘旧问题",
  "给朋友回消息",
  "喝水散步",
  "把拖延的事先做五分钟",
  "整理聊天记录",
  "把临时想法写下来",
}

local ji_pool = {
  "深夜改配置",
  "空腹猛灌咖啡",
  "带着情绪回消息",
  "临上线前再大改",
  "没看日志就下结论",
  "连续撤回三次消息",
  "把小 bug 说成没事",
  "答应过多临时需求",
  "边走路边看群消息",
  "忘记保存文件",
  "起床就开会",
  "把锅甩给网络环境",
}

local sign_pool = {
  "先把手上的小事做稳，今天自然越走越顺。",
  "今天适合慢一点，但别停下来。",
  "先整理，再推进，很多麻烦会自己缩小。",
  "不必硬拼速度，稳定输出比冲一波更赚。",
  "今天贵在少折腾，做完比做满更重要。",
  "把该补的坑补掉，晚点会轻松很多。",
}

local function quote_reply(message)
  return { quote = true, reply = message }
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
  return table.concat(items, "，")
end

local function get_lunar_info(api)
  local ok, lunar = pcall(api.today_lunar)
  if not ok or type(lunar) ~= "table" then
    return nil
  end
  if lunar.display == nil or lunar.key == nil then
    return nil
  end
  return lunar
end

local function build_reply(event, api)
  local lunar = get_lunar_info(api)
  local seed_key = tostring(event.date)
  local title = "今日宜忌"

  if lunar ~= nil then
    seed_key = lunar.key
    title = "今日宜忌（农历" .. lunar.display .. "）"
  end

  math.randomseed(hash_text(seed_key))
  local yi = pick_unique(yi_pool, 3)
  local ji = pick_unique(ji_pool, 3)
  local sign = sign_pool[math.random(#sign_pool)]

  return title ..
    "\n今日宜：" .. join(yi) ..
    "\n今日忌：" .. join(ji) ..
    "\n今日签语：" .. sign
end

function on_command(event, api)
  local lunar = get_lunar_info(api)
  local key = lunar and lunar.key or tostring(event.date)
  local saved = api.get_state(key, NAMESPACE)
  if saved ~= nil and saved ~= "" then
    return quote_reply(saved)
  end

  local message = build_reply(event, api)
  api.set_state(key, message, NAMESPACE)
  return quote_reply(message)
end
