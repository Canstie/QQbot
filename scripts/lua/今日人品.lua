-- Command: 今日人品
-- Trigger: ~今日人品

local NAMESPACE = "今日人品"

local function state_key(event)
  return tostring(event.date) .. ":" .. tostring(event.user_id)
end

local function deterministic_score(event)
  local text = tostring(event.date) .. ":" .. tostring(event.user_id)
  local hash = 0
  for i = 1, #text do
    hash = (hash * 131 + string.byte(text, i)) % 2147483647
  end
  return hash % 101
end

local function score_text(value)
  if value >= 95 then
    return "今天人品闪闪发光。适合大胆一点，连路过自动门都像在为你鼓掌。"
  end
  if value >= 85 then
    return "今天人品相当能打。适合推进计划、发起聊天、抽卡十连，至少气势已经到位。"
  end
  if value >= 70 then
    return "今天小顺风。红灯少一点，消息回得快一点，摸鱼都更像合理休息。"
  end
  if value >= 55 then
    return "今天平稳偏好。没有天降大奖，但也不太会被生活偷袭，适合把小事慢慢做完。"
  end
  if value >= 40 then
    return "今天普通模式。别硬碰硬，稳一点就行。奶茶少冰，人生也少一点波动。"
  end
  if value >= 25 then
    return "今天有点逆风。重要决定建议多看两眼，发消息前检查错别字。"
  end
  if value >= 10 then
    return "今天人品在低电量模式。适合低调行事、早睡保命，遇事先深呼吸三秒。"
  end
  return "今天不太适宜出门硬刚世界。建议贴墙走、少嘴硬、多喝水，把大事留给明天的自己。"
end

local function quote_reply(message)
  return {quote = true, reply = message}
end

local function reply(value)
  local score = tonumber(value) or 0
  return quote_reply("你今天的人品是：" .. tostring(score) .. "\n" .. score_text(score))
end

function on_command(event, api)
  local key = state_key(event)
  local saved = api.get_state(key, NAMESPACE)
  if saved ~= nil and saved ~= "" then
    return reply(saved)
  end

  local value = tostring(deterministic_score(event))
  api.set_state(key, value, NAMESPACE)
  return reply(value)
end
