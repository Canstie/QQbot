-- Command: 浠婃棩瀹滃繉
-- Trigger: ~浠婃棩瀹滃繉

local NAMESPACE = "浠婃棩瀹滃繉"

local yi_pool = {
  "鎽搁奔浣嗕笉琚彂鐜?",
  "鏃╃偣涓嬬彮",
  "涓诲姩鍠濇按",
  "鏁寸悊妗岄潰",
  "鎵撳紑浠诲姟鍏堝仛涓ゅ垎閽?",
  "澶哥兢鍙嬩竴鍙?",
  "鐐逛竴鏉垰鍒氬ソ鐨勯ギ鏂?",
  "鎶婃敹钘忓す閲岀殑鏁欑▼鐪嬪畬涓€椤?",
  "淇濆瓨鏂囦欢",
  "缁欒嚜宸辩暀鍗佸垎閽熺┖妗?",
}

local ji_pool = {
  "鍢寸‖",
  "鍑屾櫒鍋氶噸澶у喅瀹?",
  "鍜岀數姊棬姣旈€熷害",
  "绌鸿吂鍠濆啺鐨?",
  "鎶?bug 璇存垚灏忛棶棰?",
  "杩炵画鎾ゅ洖涓夋娑堟伅",
  "鍒氶啋灏卞紑浼?",
  "鍦ㄧ兢閲岀珛澶弧鐨?flag",
  "蹇樿甯﹂挜鍖?",
  "杈硅蛋璺竟鍥為暱娑堟伅",
}

local sign_pool = {
  "浠婂ぉ涓绘墦涓€涓ǔ涓甫鐨紝鍏堟妸鑳借耽鐨勫皬灞€鎷夸笅銆?",
  "鍒€ョ潃璇佹槑鑷繁锛屽厛璇佹槑鍗堥キ寰堝ソ鍚冦€?",
  "閬囧埌楹荤儲鍏堟埅鍥撅紝涓栫晫浼氬鍔辨湁璇佹嵁鐨勪汉銆?",
  "淇濇寔绀艰矊锛屼絾涓嶈鎶婅剳瀛愬€锺?",
  "浠婂ぉ閫傚悎鎱㈡參鏉ワ紝鎱㈡參鏉ヤ篃绠楀墠杩涖€?",
  "鎯呯华涓嶈棰勬敮锛屽揩涔?",
}

local function quote_reply(message)
  return {quote = true, reply = message}
end

local function state_key(event)
  return tostring(event.date) .. ":" .. tostring(event.user_id)
end

local function hash_text(text)
  local hash = 0
  for i = 1, #text do
    hash = (hash * 131 + string.byte(text, i)) % 2147483647
  end
  return hash
end

local function copy_table(source)
  local result = {}
  for i = 1, #source do
    result[i] = source[i]
  end
  return result
end

local function pick_unique(source, count)
  local pool = copy_table(source)
  local result = {}
  for _ = 1, math.min(count, #pool) do
    local index = math.random(#pool)
    table.insert(result, table.remove(pool, index))
  end
  return result
end

local function join(items)
  return table.concat(items, "，")
end

local function get_lunar_info(event, api)
  local date_str = os.date("%Y-%m-%d", event.timestamp)
  local url = "https://www.sojson.com/open/api/lunar/json.shtml?date=" .. api.url_encode(date_str)
  local ok, data = pcall(api.http_get_json, url)
  if not ok or type(data) ~= "table" then
    return nil
  end
  local payload = data.data or data
  return { month = payload.lunarMonth or payload.moonMonth, day = payload.lunarDay or payload.moonDay }
end

local function build_reply(event, api, key)
  math.randomseed(hash_text(key))
  local yi = pick_unique(yi_pool, 3)
  local ji = pick_unique(ji_pool, 3)
  local sign = sign_pool[math.random(#sign_pool)]

  local lunar = get_lunar_info(event, api)
  local lunar_desc = ""
  if lunar then
    lunar_desc = "\n农历" .. lunar.month .. "月" .. lunar.day .. "日"
  end

  return "浠婃棩瀹滐細" .. join(yi) ..
    "\n浠婃棩蹇岋細" .. join(ji) ..
    "\n浠婃棩绛捐锛?" .. sign .. lunar_desc
end

function on_command(event, api)
  local key = state_key(event)
  local saved = api.get_state(key, NAMESPACE)
  if saved ~= nil and saved ~= "" then
    return quote_reply(saved)
  end

  local message = build_reply(event, api, key)
  api.set_state(key, message, NAMESPACE)
  return quote_reply(message)
end
