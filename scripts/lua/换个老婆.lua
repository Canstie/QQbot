-- Command: 换个老婆
-- Trigger: ~换个老婆

local NAMESPACE = "群老婆"

local function as_number(value)
  return tonumber(value) or 0
end

local function seed_from_event(event)
  local seed = math.floor(as_number(event.timestamp)) +
    math.floor(as_number(event.message_id)) +
    math.floor(as_number(event.user_id))
  return seed % 2147483647
end

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

local function wife_reply(member)
  return quote_reply("你今天亲爱的群老婆是\n" .. avatar_message(member.user_id) .. "\n" .. display_name(member))
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

local function claim_owner(claims, user_id)
  return claims[tostring(user_id)]
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

local function candidate_members(event, api, claims, exclude_user_id)
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
    local owner_id = claim_owner(claims, member_id)
    local partner_id = api.get_state(state_key(event, member_id), NAMESPACE)
    if member_id ~= self_id and
        member_id ~= caller_id and
        member_id ~= exclude_id and
        (owner_id == nil or tostring(owner_id) == caller_id) and
        (partner_id == nil or tostring(partner_id) == caller_id) then
      table.insert(candidates, members[i])
    end
  end

  return candidates
end

function on_command(event, api)
  if event.group_id == nil then
    return quote_reply("这个功能只能在群聊里使用。")
  end

  local key = state_key(event, event.user_id)
  local old_user_id = api.get_state(key, NAMESPACE)
  local claims = load_claims(event, api)
  local caller_id = tostring(event.user_id)
  if old_user_id == nil or old_user_id == "" then
    old_user_id = claim_owner(claims, caller_id)
  end
  dissolve_pair(event, api, claims, caller_id, old_user_id)

  local candidates = candidate_members(event, api, claims, old_user_id)

  if #candidates == 0 then
    save_claims(event, api, claims)
    return quote_reply("没有可重新抽取的群老婆。")
  end

  math.randomseed(seed_from_event(event))
  local picked = candidates[math.random(#candidates)]
  local login = api.get_login_info()
  assign_pair(event, api, claims, caller_id, picked.user_id, login and login.user_id or nil)
  save_claims(event, api, claims)
  return wife_reply(picked)
end
