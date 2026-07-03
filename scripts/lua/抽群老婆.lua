-- Command: 抽群老婆
-- Trigger: ~抽群老婆

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

local function state_key(event)
  return tostring(event.date) .. ":" .. tostring(event.group_id) .. ":" .. tostring(event.user_id)
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

local function assign_claim(claims, wife_id, owner_id)
  claims[tostring(wife_id)] = tostring(owner_id)
end

local function load_members(event, api)
  local members = api.get_group_member_list(event.group_id)
  if members == nil then
    return {}
  end
  return members
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

local function candidate_members(event, api, claims, exclude_user_id)
  local members = load_members(event, api)
  local login = api.get_login_info()
  local self_id = tostring(login.user_id)
  local caller_id = tostring(event.user_id)
  local exclude_id = exclude_user_id and tostring(exclude_user_id) or nil
  local candidates = {}

  for i = 1, #members do
    local member_id = tostring(members[i].user_id)
    local owner_id = claim_owner(claims, member_id)
    if member_id ~= self_id and
        member_id ~= caller_id and
        member_id ~= exclude_id and
        (owner_id == nil or tostring(owner_id) == caller_id) then
      table.insert(candidates, members[i])
    end
  end

  return candidates
end

local function pick_wife(event, api, claims, exclude_user_id)
  local candidates = candidate_members(event, api, claims, exclude_user_id)
  if #candidates == 0 then
    return nil
  end

  math.randomseed(seed_from_event(event))
  return candidates[math.random(#candidates)]
end

function on_command(event, api)
  if event.group_id == nil then
    return quote_reply("这个功能只能在群聊里使用。")
  end

  local key = state_key(event)
  local saved_user_id = api.get_state(key, NAMESPACE)
  local members = load_members(event, api)
  local claims = load_claims(event, api)
  local caller_id = tostring(event.user_id)
  local login = api.get_login_info()
  local self_id = login and tostring(login.user_id) or nil

  if saved_user_id ~= nil and saved_user_id ~= "" then
    local saved_member = find_member(members, saved_user_id)
    if saved_member ~= nil and tostring(saved_user_id) ~= self_id then
      local owner_id = claim_owner(claims, saved_user_id)
      if owner_id == nil or tostring(owner_id) == caller_id then
        assign_claim(claims, saved_user_id, caller_id)
        save_claims(event, api, claims)
        return wife_reply(saved_member)
      end
    end
    api.delete_state(key, NAMESPACE)
  end

  local picked = pick_wife(event, api, claims, nil)
  if picked == nil then
    return quote_reply("没有可抽取的群老婆。")
  end

  api.set_state(key, tostring(picked.user_id), NAMESPACE)
  assign_claim(claims, picked.user_id, caller_id)
  save_claims(event, api, claims)
  return wife_reply(picked)
end
