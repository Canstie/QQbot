-- Command: 存典
-- Trigger: ~存典

local NAMESPACE = "存典"

local function flow_key(event)
  return tostring(event.group_id) .. ":" .. tostring(event.user_id)
end

local function quote_reply(message)
  return {quote = true, reply = message}
end

local function first_image_source(event)
  if event.segments == nil then
    return nil
  end

  for i = 1, #event.segments do
    local segment = event.segments[i]
    if segment ~= nil and segment.type == "image" and segment.data ~= nil then
      local url = segment.data.url
      if url ~= nil and tostring(url) ~= "" then
        return tostring(url)
      end
      local file = segment.data.file
      if file ~= nil and tostring(file) ~= "" then
        return tostring(file)
      end
    end
  end

  return nil
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

  local key = flow_key(event)
  local raw_message = tostring(event.raw_message or ""):gsub("^%s+", ""):gsub("%s+$", "")
  if raw_message == "取消" then
    api.delete_state(key, NAMESPACE)
    api.clear_pending_command()
    return quote_reply("已取消。")
  end

  local waiting = api.get_state(key, NAMESPACE)
  local image_source = first_image_source(event)
  if waiting ~= nil then
    if image_source == nil then
      return quote_reply("没有读取到图片，请直接发送一张典图，或发送“取消”退出。")
    end

    local relpath = api.save_classic_image(event.group_id, image_source, image_id(event))
    if relpath == nil or relpath == "" then
      return quote_reply("存典失败：没有读取到可支持的图片，请重新发送图片，或发送“取消”退出。")
    end

    api.delete_state(key, NAMESPACE)
    api.clear_pending_command()
    return quote_reply("存典成功")
  end

  api.set_state(key, "waiting", NAMESPACE)
  api.set_pending_command("存典")
  return quote_reply("请发出你要存的典或发送'取消'以取消")
end
