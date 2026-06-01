-- Command: 今日菜单
-- Trigger: ~今日菜单 [地点/口味]
-- Uses a domestic recipe source when configured, then falls back to the local Chinese recipe database.

local reasons = {
  "理由：它今天和你的运气比较合拍，先吃了再说。",
  "理由：菜单轮盘刚好停在这里，厨房建议不要反悔。",
  "理由：这道菜出场很稳，适合今天直接开饭。",
  "理由：它在菜单池里存在感很强，今天该它上桌。",
  "理由：随机数已经替你拍板，剩下的交给筷子。",
  "理由：这道菜今天状态不错，适合负责你的快乐。",
}

local function quote_reply(message)
  return {quote = true, reply = message}
end

local function trim(value)
  return tostring(value or ""):match("^%s*(.-)%s*$")
end

local function seed_from_event(event)
  local timestamp = tonumber(event.timestamp) or 0
  local message_id = tonumber(event.message_id) or 0
  local user_id = tonumber(event.user_id) or 0
  return math.floor(timestamp + message_id + user_id) % 2147483647
end

local function title_for_target(target)
  if target == "" then
    return "今日菜单"
  end
  return "今日菜单｜" .. target
end

function on_command(event, api)
  local target = trim(event.args)
  local seed = seed_from_event(event)
  local recipe = api.pick_menu_recipe(target, seed)
  if recipe == nil then
    return quote_reply("今日菜单\n本地菜谱库还是空的，先导入一点菜单再来抽吧。")
  end

  math.randomseed(seed)
  local lines = {
    title_for_target(target),
    "推荐：" .. tostring(recipe.title or "神秘料理"),
    reasons[math.random(#reasons)],
  }

  local image = api.local_image(recipe.image_relpath)
  if image == nil then
    local image_url = trim(recipe.image_url)
    if image_url:sub(1, 7) == "http://" or image_url:sub(1, 8) == "https://" then
      image = "[CQ:image,file=" .. image_url .. "]"
    end
  end
  if image ~= nil then
    table.insert(lines, image)
  end

  return quote_reply(table.concat(lines, "\n"))
end
