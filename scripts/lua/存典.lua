-- Command: 存典
-- Trigger: ~存典

local function quote_reply(message)
  return {quote = true, reply = message}
end

local function image_id(event)
  local raw = tostring(event.timestamp or "0") .. "_" .. tostring(event.user_id) .. "_" .. tostring(event.message_id or "0")
  local safe = string.gsub(raw, "[^%w_%-]", "_")
  return safe
end

function on_command(event, api)
  if event.group_id == nil then
    return quote_reply("这个功能只能在群聊里使用。")
  end

  local result = api.save_referenced_classic_image(event.group_id, image_id(event))
  if result == nil or result == "" then
    return quote_reply("请引用一张图片后再发送~存典。")
  end
  if result == "exists" then
    return quote_reply("已存在相同的典")
  end
  if result == "invalid" then
    return quote_reply("存典失败：图片格式不支持。")
  end
  if result == "storage_error" then
    return quote_reply("存典失败：对象存储暂不可用。")
  end

  return quote_reply("存典成功")
end
