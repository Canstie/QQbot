-- Command: 右对称
-- Trigger: ~右对称

local function quote_reply(message)
  return {quote = true, reply = message}
end

function on_command(event, api)
  local image = api.mirror_referenced_image("right")
  if image == nil or image == "" then
    return quote_reply("请引用一张图片再发送 ~右对称。")
  end

  return quote_reply(image)
end
