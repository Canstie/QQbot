-- Command: 加速
-- Trigger: ~加速 [倍数]

local function quote_reply(message)
  return {quote = true, reply = message}
end

function on_command(event, api)
  local factor = 2
  if event.args ~= nil and event.args ~= "" then
    factor = tonumber(event.args)
    if factor == nil or factor < 1 or factor > 10 then
      return quote_reply("倍数需要是 1 到 10 之间的数字，例如：~加速 2")
    end
  end

  local result = api.speed_up_referenced_gif(factor)
  if result.status == "missing" then
    return quote_reply("请引用一个 GIF 再发送 ~加速，默认加速 2 倍。")
  end
  if result.status == "not_gif" then
    return quote_reply("引用的文件不是 GIF，暂时只支持 GIF 加速。")
  end
  if result.status == "static_gif" then
    return quote_reply("这个 GIF 只有一帧，无法加速。")
  end
  if result.status == "invalid_factor" then
    return quote_reply("倍数需要是 1 到 10 之间的数字，例如：~加速 2")
  end
  if result.status ~= "ok" or result.image == nil then
    return quote_reply("GIF 加速失败，可以换一个小一点的 GIF 再试。")
  end

  return quote_reply(result.image)
end
