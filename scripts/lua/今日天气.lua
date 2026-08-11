-- Command: 今日天气
-- Trigger: ~今日天气 地点
-- Uses the domestic UAPI weather endpoint, which returns Chinese data.

local function value_at(row, name, fallback, suffix)
  if row == nil or row[name] == nil then
    return fallback
  end
  local value = row[name]
  if type(value) == "number" then
    value = string.format("%g", value)
  else
    value = tostring(value)
  end
  return value .. (suffix or "")
end

local function quote_reply(message)
  return {quote = true, reply = message}
end

local function weather_text(today)
  local daytime = value_at(today, "weather_day", "未知")
  local nighttime = value_at(today, "weather_night", "未知")
  if daytime == nighttime then
    return daytime
  end
  return "白天" .. daytime .. "，夜间" .. nighttime
end

function on_command(event, api)
  local location = event.args
  if location == nil or location == "" then
    return quote_reply("用法：~今日天气 地点，例如 ~今日天气 北京")
  end

  local url = "https://uapis.cn/api/v1/misc/weather?city=" .. api.url_encode(location) ..
    "&extended=true&forecast=true"
  local ok, data = pcall(api.http_get_json, url)
  if not ok then
    return quote_reply("天气查询失败，请稍后再试。")
  end

  if data == nil or data.city == nil or data.temperature == nil then
    return quote_reply("没有查询到「" .. location .. "」的天气。")
  end

  local today = data.forecast and data.forecast[1]
  local description = value_at(data, "weather", "未知")
  if today ~= nil then
    description = weather_text(today)
  end

  local area = value_at(data, "district", "")
  if area == "" then
    area = value_at(data, "city", location)
  end
  local temp = value_at(data, "temperature", "?", "℃")
  local feels = value_at(data, "feels_like", "?", "℃")
  local humidity = value_at(data, "humidity", "?", "%")
  local min_temp = value_at(data, "temp_min", "?", "℃")
  local max_temp = value_at(data, "temp_max", "?", "℃")
  local wind_direction = value_at(data, "wind_direction", "风向未知")
  local wind_scale = value_at(data, "wind_power", "风力未知")
  local update_time = value_at(data, "report_time", "未知")

  return quote_reply("今日天气｜" .. area ..
    "\n天气：" .. description ..
    "\n当前：" .. temp .. "，体感：" .. feels ..
    "\n今日：" .. min_temp .. " ~ " .. max_temp ..
    "\n湿度：" .. humidity .. "，" .. wind_direction .. " " .. wind_scale ..
    "\n发布：" .. update_time)
end
