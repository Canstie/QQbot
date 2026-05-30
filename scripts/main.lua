-- Optional QQ bot script.
-- Return a string to override replies.json, return nil to keep using JSON replies.

function on_message(event, api)
  if event.message == "群人数" and event.group_id ~= nil then
    local members = api.get_group_member_list(event.group_id)
    return "当前群成员数：" .. tostring(#members)
  end

  if event.message == "登录信息" then
    local info = api.get_login_info()
    return "当前账号：" .. tostring(info.nickname or info.user_id)
  end

  return nil
end
