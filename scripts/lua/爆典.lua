-- Command: 爆典
-- Trigger: ~爆典

local function quote_reply(message)
  return {quote = true, reply = message}
end

local function as_number(value)
  return tonumber(value) or 0
end

local function seed_from_event(event)
  local seed = math.floor(as_number(event.timestamp)) +
    math.floor(as_number(event.message_id)) +
    math.floor(as_number(event.user_id))
  return seed % 2147483647
end

function on_command(event, api)
  if event.group_id == nil then
    return quote_reply("这个功能只能在群聊里使用。")
  end

  local relpath = api.pick_classic_image(event.group_id, seed_from_event(event))
  if relpath == nil or relpath == "" then
    return quote_reply("这个群还没有存过典，先发送 ~存典 存一张吧。")
  end

  local image = api.classic_image(relpath)
  if image == nil or image == "" then
    return quote_reply("爆典失败：典图文件不可用。")
  end

  return quote_reply(image)
end
