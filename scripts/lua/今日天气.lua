-- Command: 今日天气
-- Trigger: ~今日天气 地点
-- Uses wttr.in public JSON endpoint.

local function first_value(value, fallback)
  if value == nil then
    return fallback
  end
  if type(value) == "table" then
    return value[1] and value[1].value or fallback
  end
  return tostring(value)
end

local function value_at(row, name, fallback)
  if row == nil or row[name] == nil then
    return fallback
  end
  return tostring(row[name])
end

local function quote_reply(message)
  return {quote = true, reply = message}
end

function on_command(event, api)
  local location = event.args
  if location == nil or location == "" then
    return quote_reply("用法：~今日天气 地点，例如 ~今日天气 北京")
  end

  local url = "https://wttr.in/" .. api.url_encode(location) .. "?format=j1&lang=zh"
  local ok, data = pcall(api.http_get_json, url)
  if not ok then
    return quote_reply("天气查询失败，请稍后再试。")
  end

  if data == nil or data.current_condition == nil or data.weather == nil then
    return quote_reply("没有查询到「" .. location .. "」的天气。")
  end

  local current = data.current_condition[1]
  local today = data.weather[1]
  if current == nil or today == nil then
    return quote_reply("没有查询到「" .. location .. "」的天气。")
  end

  local area = location
  if data.nearest_area ~= nil and data.nearest_area[1] ~= nil then
    local nearest = data.nearest_area[1]
    area = first_value(nearest.areaName, area)
  end

  local desc = first_value(current.lang_zh, first_value(current.weatherDesc, "未知"))
  local temp = value_at(current, "temp_C", "?")
  local feels = value_at(current, "FeelsLikeC", "?")
  local humidity = value_at(current, "humidity", "?")
  local wind = value_at(current, "windspeedKmph", "?")
  local min_temp = value_at(today, "mintempC", "?")
  local max_temp = value_at(today, "maxtempC", "?")

  return quote_reply("今日天气｜" .. area ..
    "\n天气：" .. desc ..
    "\n当前：" .. temp .. "℃，体感：" .. feels .. "℃" ..
    "\n今日：" .. min_temp .. "℃ ~ " .. max_temp .. "℃" ..
    "\n湿度：" .. humidity .. "%，风速：" .. wind .. " km/h")
end
