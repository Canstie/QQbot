-- Command: 换个老婆
-- Trigger: ~换个老婆

local NAMESPACE = "群老婆"

local function as_number(value)
  return tonumber(value) or 0
end

local function display_name(member)
  if member.card ~= nil and member.card ~= "" then
    return member.card
  end
  if member.nickname ~= nil and member.nickname ~= "" then
    return member.nickname
  end
  return tostring(member.user_id)
end

local function quote_reply(message)
  return {quote = true, reply = message}
end

local function state_key(event)
  return tostring(event.date) .. ":" .. tostring(event.group_id) .. ":" .. tostring(event.user_id)
end

local function candidate_members(event, api, exclude_user_id)
  local members = api.get_group_member_list(event.group_id)
  if members == nil then
    return {}
  end

  local login = api.get_login_info()
  local self_id = tostring(login.user_id)
  local caller_id = tostring(event.user_id)
  local exclude_id = exclude_user_id and tostring(exclude_user_id) or nil
  local candidates = {}

  for i = 1, #members do
    local member_id = tostring(members[i].user_id)
    if member_id ~= self_id and member_id ~= caller_id and member_id ~= exclude_id then
      table.insert(candidates, members[i])
    end
  end

  return candidates
end

function on_command(event, api)
  if event.group_id == nil then
    return quote_reply("这个功能只能在群聊里使用。")
  end

  local key = state_key(event)
  local old_user_id = api.get_state(key, NAMESPACE)
  local candidates = candidate_members(event, api, old_user_id)

  if #candidates == 0 then
    return quote_reply("没有可重新抽取的群老婆。")
  end

  math.randomseed(as_number(event.timestamp) + as_number(event.message_id) + as_number(event.user_id))
  local picked = candidates[math.random(#candidates)]
  api.set_state(key, tostring(picked.user_id), NAMESPACE)

  if old_user_id == nil or old_user_id == "" then
    return quote_reply("你之前还没有群老婆，已为你抽取：" .. display_name(picked) .. "（" .. tostring(picked.user_id) .. "）")
  end

  return quote_reply("已重新抽取！你的新群老婆是：" .. display_name(picked) .. "（" .. tostring(picked.user_id) .. "）")
end
