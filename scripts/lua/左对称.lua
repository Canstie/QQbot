-- Command: 左对称
-- Trigger: ~左对称

local function quote_reply(message)
  return {quote = true, reply = message}
end

function on_command(event, api)
  local image = api.mirror_referenced_image("left")
  if image == nil or image == "" then
    return quote_reply("请引用一张图片再发送 ~左对称。")
  end

  return quote_reply(image)
end
