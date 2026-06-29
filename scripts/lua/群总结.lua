-- Command: 群总结
-- Trigger: ~群总结

local function quote_reply(message)
  return {quote = true, reply = message}
end

local function display_name(member)
  if member == nil then
    return nil
  end
  if member.card ~= nil and member.card ~= "" then
    return member.card
  end
  if member.nickname ~= nil and member.nickname ~= "" then
    return member.nickname
  end
  return tostring(member.user_id)
end

local function member_names(event, api)
  local names = {}
  local ok, members = pcall(function()
    return api.get_group_member_list(event.group_id)
  end)
  if not ok or members == nil then
    return names
  end

  for i = 1, #members do
    local member = members[i]
    names[tostring(member.user_id)] = display_name(member)
  end
  return names
end

local function name_for(names, user_id)
  local name = names[tostring(user_id)]
  if name ~= nil and name ~= "" then
    return name
  end
  return tostring(user_id)
end

local function hour_label(hour)
  local start_hour = tonumber(hour) or 0
  local end_hour = (start_hour + 1) % 24
  return string.format("%02d:00-%02d:00", start_hour, end_hour)
end

local function append_rank(lines, title, rows, metric, unit, names, empty_text)
  table.insert(lines, title)
  if rows == nil or #rows == 0 then
    table.insert(lines, empty_text or "暂无数据")
    return
  end

  for index = 1, #rows do
    local row = rows[index]
    local value = tonumber(row[metric]) or 0
    table.insert(
      lines,
      tostring(index) .. ". " .. name_for(names, row.user_id) .. "：" .. tostring(value) .. unit
    )
  end
end

function on_command(event, api)
  if event.group_id == nil then
    return quote_reply("这个功能只能在群聊里使用。")
  end

  local summary = api.get_group_daily_summary(event.group_id, event.date, 5)
  if summary == nil or tonumber(summary.total_messages or 0) <= 0 then
    return quote_reply("今天还没有统计到群消息。")
  end

  local names = member_names(event, api)
  local lines = {
    "今日群总结",
    "总消息：" .. tostring(summary.total_messages) .. " 条",
    "参与人数：" .. tostring(summary.active_users) .. " 人",
  }

  if summary.peak_hour ~= nil then
    table.insert(
      lines,
      "最活跃时段：" ..
        hour_label(summary.peak_hour.hour) ..
        "（" .. tostring(summary.peak_hour.message_count) .. " 条）"
    )
  end

  if summary.early_bird ~= nil then
    table.insert(
      lines,
      "早鸟：" ..
        name_for(names, summary.early_bird.user_id) ..
        "（" .. tostring(summary.early_bird.first_time) .. "）"
    )
  end

  if summary.night_owl ~= nil then
    table.insert(
      lines,
      "夜猫子：" ..
        name_for(names, summary.night_owl.user_id) ..
        "（" .. tostring(summary.night_owl.last_time) .. "）"
    )
  end

  table.insert(lines, "")
  append_rank(lines, "水群榜", summary.top_messages, "message_count", " 条", names)
  table.insert(lines, "")
  append_rank(lines, "字数榜", summary.top_text_chars, "text_chars", " 字", names)
  table.insert(lines, "")
  append_rank(lines, "发图榜", summary.top_images, "image_count", " 张", names, "今天还没人发图")
  table.insert(lines, "")
  append_rank(lines, "@人榜", summary.top_mentions, "at_count", " 次", names, "今天还没人@人")

  return quote_reply(table.concat(lines, "\n"))
end
