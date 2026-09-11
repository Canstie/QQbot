-- Command: 强娶
-- Trigger: ~强娶 @群成员

local NAMESPACE = "群老婆"

local function display_name(member)
  if member.card ~= nil and member.card ~= "" then
    return member.card
  end
  if member.nickname ~= nil and member.nickname ~= "" then
    return member.nickname
  end
  return "未知成员"
end

local function avatar_message(user_id)
  local url = "https://q1.qlogo.cn/g?b=qq&amp;nk=" .. tostring(user_id) .. "&amp;s=640"
  return "[CQ:image,file=" .. url .. "]"
end

local function quote_reply(message)
  return {quote = true, reply = message}
end

local function wife_reply(member, extra_line)
  local message = "强娶成功!\n你今天亲爱的群老婆是\n" .. avatar_message(member.user_id) .. "\n" .. display_name(member)
  if extra_line ~= nil and extra_line ~= "" then
    message = message .. "\n" .. extra_line
  end
  return quote_reply(message)
end

local function state_key(event, user_id)
  local target_id = user_id or event.user_id
  return tostring(event.date) .. ":" .. tostring(event.group_id) .. ":" .. tostring(target_id)
end

local function claim_key(event)
  return tostring(event.date) .. ":" .. tostring(event.group_id) .. ":claims"
end

local function load_claims(event, api)
  local raw = api.get_state(claim_key(event), NAMESPACE)
  if raw == nil or raw == "" then
    return {}
  end

  local ok, decoded = pcall(function()
    return api.json_decode(raw)
  end)
  if ok and decoded ~= nil then
    return decoded
  end

  return {}
end

local function save_claims(event, api, claims)
  api.set_state(claim_key(event), api.json_encode(claims), NAMESPACE)
end

local function dissolve_pair(event, api, claims, user_id, partner_id)
  local left_id = tostring(user_id)
  local right_id = partner_id and tostring(partner_id) or nil
  api.delete_state(state_key(event, left_id), NAMESPACE)
  if right_id == nil or right_id == "" then
    return
  end

  local reverse_id = api.get_state(state_key(event, right_id), NAMESPACE)
  if reverse_id ~= nil and tostring(reverse_id) == left_id then
    api.delete_state(state_key(event, right_id), NAMESPACE)
  end
  if tostring(claims[right_id] or "") == left_id then
    claims[right_id] = nil
  end
  if tostring(claims[left_id] or "") == right_id then
    claims[left_id] = nil
  end
end

local function assign_pair(event, api, claims, left_id, right_id, bot_id)
  local left = tostring(left_id)
  local right = tostring(right_id)
  api.set_state(state_key(event, left), right, NAMESPACE)
  if right ~= tostring(bot_id or "") then
    api.set_state(state_key(event, right), left, NAMESPACE)
  end
  claims[right] = left
  claims[left] = right
end

local function find_member(members, user_id)
  local target = tostring(user_id)
  for i = 1, #members do
    if tostring(members[i].user_id) == target then
      return members[i]
    end
  end
  return nil
end

local function bot_member_from_login(login)
  if login == nil or login.user_id == nil then
    return nil
  end
  return {
    user_id = login.user_id,
    nickname = login.nickname or "Bot",
    card = "",
  }
end

local function extract_target_id(text)
  if text == nil then
    return nil
  end
  local value = tostring(text)
  return string.match(value, "%[CQ:at,qq=(%d+)") or
    string.match(value, "%[at:qq=(%d+)")
end

local function target_from_at(event)
  if event.segments == nil then
    return nil
  end

  for i = 1, #event.segments do
    local segment = event.segments[i]
    if segment ~= nil then
      local segment_type = tostring(segment.type or "")
      local segment_data = segment.data
      if segment_type == "at" and segment_data ~= nil then
        local qq = segment_data.qq
        if qq ~= nil and tostring(qq) ~= "all" then
          return tostring(qq)
        end
      end

      if segment_data ~= nil then
        local from_text = extract_target_id(segment_data.text) or extract_target_id(segment_data.raw)
        if from_text ~= nil then
          return from_text
        end
      end
    end
  end

  return nil
end

local function target_from_source_message(event)
  return extract_target_id(event.platform_raw_message) or extract_target_id(event.raw_message)
end

local function target_from_args(event)
  if event.args == nil then
    return nil
  end
  return string.match(tostring(event.args), "(%d+)")
end

function on_command(event, api)
  if event.group_id == nil then
    return quote_reply("这个功能只能在群聊里使用。")
  end

  local target_id = target_from_at(event) or target_from_source_message(event) or target_from_args(event)
  if target_id == nil or target_id == "" then
    return quote_reply("请@要强娶的群成员，或输入 QQ 号。")
  end

  local members = api.get_group_member_list(event.group_id)
  if members == nil then
    members = {}
  end

  local login = api.get_login_info()
  local target_member = find_member(members, target_id)
  if target_member == nil and login ~= nil and tostring(target_id) == tostring(login.user_id) then
    target_member = bot_member_from_login(login)
  end
  if target_member == nil then
    return quote_reply("没有在群成员列表里找到这个人。")
  end

  local bot_warning = nil

  local caller_id = tostring(event.user_id)
  if tostring(target_id) == caller_id then
    return quote_reply("不能强娶自己。")
  end

  local claims = load_claims(event, api)
  local current_owner = claims[tostring(target_id)]
  if current_owner ~= nil and tostring(current_owner) ~= caller_id then
    return quote_reply("ta已经是别人的群老婆")
  end

  local target_partner = api.get_state(state_key(event, target_id), NAMESPACE)
  if target_partner ~= nil and tostring(target_partner) ~= caller_id then
    return quote_reply("ta已经是别人的群老婆")
  end

  local key = state_key(event, event.user_id)
  local old_user_id = api.get_state(key, NAMESPACE)
  if old_user_id == nil or old_user_id == "" then
    old_user_id = claims[caller_id]
  end
  dissolve_pair(event, api, claims, caller_id, old_user_id)

  assign_pair(event, api, claims, caller_id, target_id, login and login.user_id or nil)
  save_claims(event, api, claims)

  if tostring(target_id) == tostring(login.user_id) then
    bot_warning = "和我是没有好结果的哟"
  end

  return wife_reply(target_member, bot_warning)
end
