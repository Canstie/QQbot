-- Command: 今日菜单
-- Trigger: ~今日菜单 [地点/口味]
-- Uses cached TheMealDB data and MyMemory translation when available.

local API_BASE = "https://www.themealdb.com/api/json/v1/1/"
local TRANSLATE_BASE = "https://api.mymemory.translated.net/get"
local CACHE_NAMESPACE = "今日菜单:cache:v1"

local area_aliases = {
  ["中餐"] = "Chinese",
  ["中国"] = "Chinese",
  ["中国菜"] = "Chinese",
  ["粤菜"] = "Chinese",
  ["广州"] = "Chinese",
  ["广东"] = "Chinese",
  ["深圳"] = "Chinese",
  ["北京"] = "Chinese",
  ["上海"] = "Chinese",
  ["成都"] = "Chinese",
  ["重庆"] = "Chinese",
  ["日料"] = "Japanese",
  ["日本"] = "Japanese",
  ["日本菜"] = "Japanese",
  ["泰餐"] = "Thai",
  ["泰国"] = "Thai",
  ["越南"] = "Vietnamese",
  ["马来"] = "Malaysian",
  ["印度"] = "Indian",
  ["意大利"] = "Italian",
  ["墨西哥"] = "Mexican",
  ["法国"] = "French",
  ["美国"] = "American",
  ["英国"] = "British",
  ["土耳其"] = "Turkish",
  ["西班牙"] = "Spanish",
  ["希腊"] = "Greek",
}

local category_aliases = {
  ["鸡"] = "Chicken",
  ["鸡肉"] = "Chicken",
  ["炸鸡"] = "Chicken",
  ["牛"] = "Beef",
  ["牛肉"] = "Beef",
  ["猪"] = "Pork",
  ["猪肉"] = "Pork",
  ["羊"] = "Lamb",
  ["羊肉"] = "Lamb",
  ["海鲜"] = "Seafood",
  ["鱼"] = "Seafood",
  ["虾"] = "Seafood",
  ["素"] = "Vegetarian",
  ["素食"] = "Vegetarian",
  ["甜点"] = "Dessert",
  ["甜品"] = "Dessert",
  ["意面"] = "Pasta",
  ["面"] = "Pasta",
  ["早餐"] = "Breakfast",
}

local category_zh = {
  Beef = "牛肉",
  Breakfast = "早餐",
  Chicken = "鸡肉",
  Dessert = "甜品",
  Goat = "羊肉",
  Lamb = "羊肉",
  Miscellaneous = "其他",
  Pasta = "意面",
  Pork = "猪肉",
  Seafood = "海鲜",
  Side = "配菜",
  Starter = "前菜",
  Vegan = "纯素",
  Vegetarian = "素食",
}

local fallback_staples = {
  "牛肉粉",
  "烧鹅饭",
  "番茄鸡蛋面",
  "麻辣烫",
  "猪脚饭",
  "云吞面",
  "咖喱鸡饭",
  "酸菜鱼",
  "砂锅粥",
  "鸡腿堡套餐",
  "煲仔饭",
  "兰州牛肉面",
}

local fallback_sides = {
  "加一份青菜",
  "配荷包蛋",
  "来点炸物",
  "加豆腐",
  "配小份水果",
  "加一份叉烧",
  "来碗例汤",
  "配凉拌黄瓜",
}

local fallback_drinks = {
  "冻柠茶",
  "无糖乌龙",
  "热豆浆",
  "冰美式",
  "柠檬水",
  "椰子水",
  "酸梅汤",
  "奶茶少糖",
}

local reasons = {
  "理由：今天需要一点稳定碳水，先把心情垫起来。",
  "理由：这顿吃完，下午的世界会稍微好沟通一点。",
  "理由：不一定改变人生，但能改变接下来两小时的表情。",
  "理由：今天不适合纠结，适合把选择权交给菜单。",
  "理由：这组合有点离谱，但离谱得很有精神。",
  "理由：吃饱再说，很多烦恼只是血糖在报警。",
}

local function quote_reply(message)
  return {quote = true, reply = message}
end

local function trim(value)
  return tostring(value or ""):match("^%s*(.-)%s*$")
end

local function as_number(value)
  return tonumber(value) or 0
end

local function seed_from_event(event)
  local seed = math.floor(as_number(event.timestamp)) +
    math.floor(as_number(event.message_id)) +
    math.floor(as_number(event.user_id))
  return seed % 2147483647
end

local function target_from_args(event)
  local value = trim(event.args)
  if value == "" then
    return "默认"
  end
  return value
end

local function list_key(target)
  return "list:" .. target
end

local function meal_key(id)
  return "meal:" .. tostring(id)
end

local function pick(pool)
  return pool[math.random(#pool)]
end

local function choose_meal(meals)
  if meals == nil or #meals == 0 then
    return nil
  end
  return meals[math.random(#meals)]
end

local function safe_json_decode(api, value)
  if value == nil or value == "" then
    return nil
  end
  local ok, decoded = pcall(api.json_decode, value)
  if ok then
    return decoded
  end
  return nil
end

local function save_json(api, key, value)
  local ok, encoded = pcall(api.json_encode, value)
  if ok then
    api.set_state(key, encoded, CACHE_NAMESPACE)
  end
end

local function endpoint_for_target(target, api)
  if target == "默认" then
    return API_BASE .. "filter.php?a=Chinese"
  end

  local area = area_aliases[target]
  if area ~= nil then
    return API_BASE .. "filter.php?a=" .. api.url_encode(area)
  end

  local category = category_aliases[target]
  if category ~= nil then
    return API_BASE .. "filter.php?c=" .. api.url_encode(category)
  end

  return API_BASE .. "search.php?s=" .. api.url_encode(target)
end

local function normalize_meal(row)
  if row == nil or row.idMeal == nil or row.strMeal == nil then
    return nil
  end
  return {
    id = tostring(row.idMeal),
    name = tostring(row.strMeal),
    thumb = tostring(row.strMealThumb or ""),
    category = tostring(row.strCategory or ""),
  }
end

local function normalize_meal_list(data)
  local result = {}
  if data == nil or data.meals == nil then
    return result
  end

  for i = 1, #data.meals do
    local meal = normalize_meal(data.meals[i])
    if meal ~= nil then
      table.insert(result, meal)
    end
  end
  return result
end

local function load_cached_list(api, target)
  local cached = safe_json_decode(api, api.get_state(list_key(target), CACHE_NAMESPACE))
  if cached ~= nil and #cached > 0 then
    return cached
  end
  return nil
end

local function fetch_list(api, target)
  local endpoint = endpoint_for_target(target, api)
  local ok, data = pcall(api.http_get_json, endpoint)
  local meals = ok and normalize_meal_list(data) or {}
  if #meals == 0 and target ~= "默认" then
    local fallback_ok, fallback_data =
      pcall(api.http_get_json, API_BASE .. "filter.php?a=Chinese")
    meals = fallback_ok and normalize_meal_list(fallback_data) or {}
  end
  if #meals > 0 then
    save_json(api, list_key(target), meals)
    return meals
  end
  return nil
end

local function load_or_fetch_list(api, target)
  return load_cached_list(api, target) or fetch_list(api, target)
end

local function contains_chinese(value)
  return tostring(value or ""):find("[\228-\233]") ~= nil
end

local function translate_text(api, value)
  local text = trim(value)
  if text == "" or contains_chinese(text) then
    return text
  end

  local url = TRANSLATE_BASE ..
    "?q=" .. api.url_encode(text) ..
    "&langpair=" .. api.url_encode("en|zh-CN")
  local ok, data = pcall(api.http_get_json, url)
  if ok and data ~= nil and data.responseData ~= nil then
    local translated = trim(data.responseData.translatedText)
    if translated ~= "" and contains_chinese(translated) then
      return translated
    end
    if data.matches ~= nil then
      for i = 1, #data.matches do
        local match_translation = trim(data.matches[i].translation)
        if match_translation ~= "" and contains_chinese(match_translation) then
          return match_translation
        end
      end
    end
    if translated ~= "" and translated ~= text then
      return translated
    end
  end
  return text
end

local function translated_category(category)
  local value = trim(category)
  if value == "" then
    return ""
  end
  return category_zh[value] or value
end

local function load_cached_meal(api, id)
  return safe_json_decode(api, api.get_state(meal_key(id), CACHE_NAMESPACE))
end

local function build_meal_cache(api, meal)
  local cached = {
    id = tostring(meal.id),
    name = translate_text(api, meal.name),
    thumb = tostring(meal.thumb or ""),
    category = translated_category(meal.category),
  }
  save_json(api, meal_key(cached.id), cached)
  return cached
end

local function load_or_build_meal(api, meal)
  return load_cached_meal(api, meal.id) or build_meal_cache(api, meal)
end

local function append_line(lines, label, value)
  if value ~= nil and value ~= "" then
    table.insert(lines, label .. tostring(value))
  end
end

local function image_message(url)
  if url == nil or url == "" then
    return nil
  end
  return "[CQ:image,file=" .. tostring(url) .. "]"
end

local function build_api_reply(target, meal)
  local title = "今日菜单"
  if target ~= "默认" then
    title = title .. "｜" .. target
  end

  local lines = {
    title,
    "推荐：" .. tostring(meal.name or "神秘料理"),
  }
  append_line(lines, "分类：", meal.category)
  table.insert(lines, pick(reasons))

  local image = image_message(meal.thumb)
  if image ~= nil then
    table.insert(lines, image)
  end

  return table.concat(lines, "\n")
end

local function try_api_menu(event, api, target)
  math.randomseed(seed_from_event(event))

  local meals = load_or_fetch_list(api, target)
  local picked = choose_meal(meals)
  if picked == nil then
    return nil
  end

  local meal = load_or_build_meal(api, picked)
  return build_api_reply(target, meal)
end

local function build_fallback_reply(event, target)
  math.randomseed(seed_from_event(event))
  local title = "今日菜单"
  if target ~= "默认" then
    title = title .. "｜" .. target
  end

  return title ..
    "\n主食：" .. pick(fallback_staples) ..
    "\n搭配：" .. pick(fallback_sides) ..
    "\n饮品/小食：" .. pick(fallback_drinks) ..
    "\n" .. pick(reasons) ..
    "\n外部菜单接口暂时不可用，先用本地菜单顶上。"
end

function on_command(event, api)
  local target = target_from_args(event)
  local message = try_api_menu(event, api, target)
  if message == nil then
    message = build_fallback_reply(event, target)
  end

  return quote_reply(message)
end
