-- 随机抽取一名群成员
-- 触发方式：~抽群友 / ~随机抽人 / ~抽人

local function display_name(member)
  if member.card ~= nil and member.card ~= "" then
    return member.card
  end
  if member.nickname ~= nil and member.nickname ~= "" then
    return member.nickname
  end
  return tostring(member.user_id)
end

function on_message(event, api)
  local text = event.message or ""

  if text ~= "抽群友" and text ~= "随机抽人" and text ~= "抽人" then
    return nil
  end

  if event.group_id == nil then
    return "这个功能只能在群聊里使用。"
  end

  local members = api.get_group_member_list(event.group_id)
  if members == nil or #members == 0 then
    return "没有获取到群成员列表。"
  end

  local login = api.get_login_info()
  local self_id = tostring(login.user_id)

  local candidates = {}
  for i = 1, #members do
    local member = members[i]
    if tostring(member.user_id) ~= self_id then
      table.insert(candidates, member)
    end
  end

  if #candidates == 0 then
    return "没有可抽取的群成员。"
  end

  math.randomseed(tonumber(event.timestamp) + tonumber(event.message_id or 0))
  local picked = candidates[math.random(#candidates)]

  return "随机抽取结果：" .. display_name(picked) .. "（" .. tostring(picked.user_id) .. "）"
end