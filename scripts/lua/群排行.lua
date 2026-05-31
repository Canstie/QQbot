-- Command: 群排行
-- Trigger: ~群排行 [主题]

local NAMESPACE = "群排行"

local themes = {
  "摸鱼王",
  "干饭积极分子",
  "夜猫子",
  "表情包火力",
  "今日好运担当",
  "群聊气氛组",
  "准点下班代言人",
  "隐身冠军",
}

local comments = {
  "实力稳定，榜上有名并不意外。",
  "今天气场很足，建议先接受掌声。",
  "数据来自娱乐宇宙，认真你就赢了。",
  "排名仅供快乐参考，禁止据此开会。",
}

local function quote_reply(message)
  return {quote = true, reply = message}
end

local function trim(value)
  return tostring(value or ""):match("^%s*(.-)%s*$")
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

local function hash_text(text)
  local hash = 0
  for i = 1, #text do
    hash = (hash * 131 + string.byte(text, i)) % 2147483647
  end
  return hash
end

local function state_key(event, topic)
  return tostring(event.date) .. ":" .. tostring(event.group_id) .. ":" .. topic
end

local function collect_candidates(event, api)
  local members = api.get_group_member_list(event.group_id)
  if members == nil then
    return {}
  end

  local login = api.get_login_info()
  local self_id = tostring(login.user_id)
  local candidates = {}
  for i = 1, #members do
    if tostring(members[i].user_id) ~= self_id then
      table.insert(candidates, members[i])
    end
  end
  return candidates
end

local function pick_topic(key, requested)
  if requested ~= "" then
    return requested
  end
  math.randomseed(hash_text(key .. ":topic"))
  return themes[math.random(#themes)]
end

local function build_reply(key, topic, candidates)
  math.randomseed(hash_text(key .. ":rank"))
  local pool = {}
  for i = 1, #candidates do
    pool[i] = candidates[i]
  end

  local lines = {"今日「" .. topic .. "」排行榜"}
  local count = math.min(3, #pool)
  for rank = 1, count do
    local index = math.random(#pool)
    local member = table.remove(pool, index)
    table.insert(lines, tostring(rank) .. ". " .. display_name(member))
  end
  table.insert(lines, comments[math.random(#comments)])
  return table.concat(lines, "\n")
end

function on_command(event, api)
  if event.group_id == nil then
    return quote_reply("这个功能只能在群聊里使用。")
  end

  local requested = trim(event.args)
  local topic_key = requested ~= "" and requested or "默认"
  local key = state_key(event, topic_key)
  local saved = api.get_state(key, NAMESPACE)
  if saved ~= nil and saved ~= "" then
    return quote_reply(saved)
  end

  local candidates = collect_candidates(event, api)
  if #candidates == 0 then
    return quote_reply("没有可排行的群成员。")
  end

  local topic = pick_topic(key, requested)
  local message = build_reply(key, topic, candidates)
  api.set_state(key, message, NAMESPACE)
  return quote_reply(message)
end
