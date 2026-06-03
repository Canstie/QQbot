let replyConfig = { empty: "", fallback: "Received: {message}", rules: [], direct_rules: [] };
let luaCommands = [];
let currentLuaCommand = "";
let menus = [];
let restaurants = [];

const ruleTypes = [
  ["exact", "完全匹配"],
  ["contains", "包含关键词"],
  ["prefix", "前缀匹配"],
  ["regex", "正则匹配"],
];

const byId = (id) => document.getElementById(id);

const headers = () => {
  const token = byId("token").value;
  return token
    ? { "X-Admin-Token": token, "Content-Type": "application/json" }
    : { "Content-Type": "application/json" };
};

function setNotice(id, message, kind = "") {
  const node = byId(id);
  node.textContent = message;
  node.className = kind ? `notice ${kind}` : "notice";
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
  }
  if (!response.ok) {
    const detail = data.detail || `HTTP ${response.status}`;
    throw new Error(Array.isArray(detail) ? JSON.stringify(detail) : detail);
  }
  return data;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function parseIdList(value) {
  return String(value || "")
    .split(/[,\s，、]+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => Number(item));
}

function formatIdList(values) {
  return (values || []).join("\n");
}

function showTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === name);
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.panel === name);
  });
}

function renderRules(kind) {
  const listId = kind === "rules" ? "rulesList" : "directRulesList";
  const list = byId(listId);
  const rules = replyConfig[kind] || [];
  if (!rules.length) {
    list.innerHTML = '<div class="empty-list">暂无规则</div>';
    return;
  }
  list.innerHTML = rules.map((rule, index) => `
    <div class="rule-card">
      <label>匹配方式
        <select data-kind="${kind}" data-index="${index}" data-field="type">
          ${ruleTypes.map(([value, label]) => `<option value="${value}" ${rule.type === value ? "selected" : ""}>${label}</option>`).join("")}
        </select>
      </label>
      <label>触发内容
        <input data-kind="${kind}" data-index="${index}" data-field="pattern" value="${escapeHtml(rule.pattern)}">
      </label>
      <label>回复内容
        <textarea data-kind="${kind}" data-index="${index}" data-field="reply">${escapeHtml(rule.reply)}</textarea>
      </label>
      <button class="danger" data-remove-rule="${kind}" data-index="${index}">删除</button>
    </div>
  `).join("");
  list.querySelectorAll("[data-kind]").forEach((node) => {
    node.addEventListener("input", updateRuleFromInput);
    node.addEventListener("change", updateRuleFromInput);
  });
  list.querySelectorAll("[data-remove-rule]").forEach((node) => {
    node.addEventListener("click", () => removeRule(node.dataset.removeRule, Number(node.dataset.index)));
  });
}

function updateRuleFromInput(event) {
  const node = event.target;
  replyConfig[node.dataset.kind][Number(node.dataset.index)][node.dataset.field] = node.value;
}

function addRule(kind) {
  replyConfig[kind] = replyConfig[kind] || [];
  replyConfig[kind].push({ type: "contains", pattern: "", reply: "" });
  renderRules(kind);
}

function removeRule(kind, index) {
  replyConfig[kind].splice(index, 1);
  renderRules(kind);
}

async function refresh() {
  try {
    const data = await requestJson("./api/policy", { headers: headers() });
    byId("state").textContent = JSON.stringify(data, null, 2);
    byId("enabledCount").textContent = data.enabled_groups?.length ?? 0;
    byId("blockedCount").textContent = data.blocked_groups?.length ?? 0;
    byId("adminCount").textContent = data.admins?.length ?? 0;
    byId("connectionStatus").textContent = "在线";
    if (data.mode) byId("mode").value = data.mode;
    byId("prefixesInput").value = (data.trigger?.prefixes || ["~"]).join(",");
    byId("mentionTrigger").checked = Boolean(data.trigger?.mention);
    byId("directTriggerPercent").value = data.trigger?.direct_trigger_percent ?? 10;
    byId("perGroupSeconds").value = data.limits?.per_group_seconds ?? 5;
    byId("perUserPerMinute").value = data.limits?.per_user_per_minute ?? 5;
    byId("enabledGroupsInput").value = formatIdList(data.enabled_groups);
    byId("blockedGroupsInput").value = formatIdList(data.blocked_groups);
    byId("adminsInput").value = formatIdList(data.admins);
    setNotice("configNotice", "核心配置已加载", "ok");
  } catch (error) {
    byId("connectionStatus").textContent = "异常";
    byId("state").textContent = error.message;
    setNotice("configNotice", error.message, "error");
  }
}

async function setMode() {
  await requestJson("./api/policy/mode", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ mode: byId("mode").value }),
  });
  await refresh();
}

async function savePrefixes() {
  const prefixes = byId("prefixesInput").value.split(",").map((item) => item.trim()).filter(Boolean);
  await requestJson("./api/policy/prefixes", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ prefixes }),
  });
  await refresh();
}

async function saveDirectTriggerPercent() {
  const percent = Number(byId("directTriggerPercent").value);
  await requestJson("./api/policy/direct-trigger-percent", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ percent }),
  });
  await refresh();
}

async function saveCoreConfig() {
  const prefixes = byId("prefixesInput").value.split(",").map((item) => item.trim()).filter(Boolean);
  const payload = {
    mode: byId("mode").value,
    enabled_groups: parseIdList(byId("enabledGroupsInput").value),
    blocked_groups: parseIdList(byId("blockedGroupsInput").value),
    admins: parseIdList(byId("adminsInput").value),
    trigger: {
      mention: byId("mentionTrigger").checked,
      prefixes,
      direct_trigger_percent: Number(byId("directTriggerPercent").value),
    },
    limits: {
      per_group_seconds: Number(byId("perGroupSeconds").value),
      per_user_per_minute: Number(byId("perUserPerMinute").value),
    },
  };
  const data = await requestJson("./api/policy/core", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(payload),
  });
  byId("state").textContent = JSON.stringify(data, null, 2);
  setNotice("configNotice", "核心配置已保存", "ok");
  await refresh();
}

async function groupAction(action) {
  const groupId = byId("groupId").value.trim();
  if (!groupId) return;
  await requestJson(`./api/groups/${groupId}/${action}`, { method: "POST", headers: headers() });
  await refresh();
}

async function loadReplies() {
  try {
    const data = await requestJson("./api/replies", { headers: headers() });
    replyConfig = data.config || replyConfig;
    replyConfig.rules = replyConfig.rules || [];
    replyConfig.direct_rules = replyConfig.direct_rules || [];
    byId("emptyReply").value = replyConfig.empty || "";
    byId("fallbackReply").value = replyConfig.fallback || "";
    renderRules("rules");
    renderRules("direct_rules");
    setNotice("editorNotice", data.valid ? "回复规则已加载" : data.error, data.valid ? "ok" : "error");
  } catch (error) {
    setNotice("editorNotice", error.message, "error");
  }
}

async function saveReplies() {
  replyConfig.empty = byId("emptyReply").value;
  replyConfig.fallback = byId("fallbackReply").value;
  try {
    const data = await requestJson("./api/replies", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify(replyConfig),
    });
    replyConfig = data.config || replyConfig;
    renderRules("rules");
    renderRules("direct_rules");
    setNotice("editorNotice", "保存成功", "ok");
  } catch (error) {
    setNotice("editorNotice", error.message, "error");
  }
}

const encodedCommand = (command) => encodeURIComponent(command);

function renderLuaCommandList() {
  const list = byId("luaCommandList");
  if (!luaCommands.length) {
    list.innerHTML = '<div class="empty-list">暂无脚本</div>';
    return;
  }
  list.innerHTML = luaCommands.map((item) => `
    <button class="lua-command-item ${item.command === currentLuaCommand ? "active" : ""}" data-lua-open="${escapeHtml(item.command)}">
      <span>${escapeHtml(item.command)}</span><small>${Math.max(1, Math.ceil((item.size || 0) / 1024))} KB</small>
    </button>
  `).join("");
}

async function loadLuaCommands(preferredCommand = currentLuaCommand) {
  try {
    const data = await requestJson("./api/lua/commands", { headers: headers() });
    luaCommands = data.commands || [];
    byId("luaListMeta").textContent = `${data.lua_dir} | ${data.enabled ? "已启用" : "未启用"} | ${luaCommands.length} 个脚本`;
    renderLuaCommandList();
    const nextCommand = preferredCommand || luaCommands[0]?.command || "抽群老婆";
    await openLuaCommand(nextCommand);
  } catch (error) {
    setNotice("luaNotice", error.message, "error");
  }
}

async function openLuaCommand(command) {
  const value = String(command || "").trim();
  if (!value) return;
  try {
    const data = await requestJson(`./api/lua/commands/${encodedCommand(value)}`, { headers: headers() });
    currentLuaCommand = data.command;
    byId("luaCommandInput").value = data.command;
    byId("luaCurrentCommand").value = data.command;
    byId("luaEditor").value = data.content || "";
    byId("luaMeta").textContent = `${data.path}${data.using_example ? " | 示例" : ""}`;
    setNotice("luaNotice", data.using_example ? "当前显示示例，保存后生效" : "Lua 脚本已加载", "ok");
    renderLuaCommandList();
  } catch (error) {
    setNotice("luaNotice", error.message, "error");
  }
}

async function saveLua() {
  if (!currentLuaCommand) return;
  try {
    const data = await requestJson(`./api/lua/commands/${encodedCommand(currentLuaCommand)}`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ content: byId("luaEditor").value }),
    });
    await loadLuaCommands(data.command);
    setNotice("luaNotice", "保存成功", "ok");
  } catch (error) {
    setNotice("luaNotice", error.message, "error");
  }
}

async function deleteLua() {
  if (!currentLuaCommand || !confirm(`删除 Lua 指令“${currentLuaCommand}”？`)) return;
  try {
    await requestJson(`./api/lua/commands/${encodedCommand(currentLuaCommand)}`, {
      method: "DELETE",
      headers: headers(),
    });
    currentLuaCommand = "";
    await loadLuaCommands();
    setNotice("luaNotice", "已删除", "ok");
  } catch (error) {
    setNotice("luaNotice", error.message, "error");
  }
}

function importLuaFile(file) {
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    byId("luaEditor").value = String(reader.result || "");
    setNotice("luaNotice", `已导入 ${file.name}，保存后生效`, "ok");
  };
  reader.onerror = () => setNotice("luaNotice", "导入失败", "error");
  reader.readAsText(file, "utf-8");
}

async function fileToDataUrl(file) {
  if (!file) return null;
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function renderMenus() {
  const list = byId("menuList");
  if (!menus.length) {
    list.innerHTML = '<div class="empty-list">暂无菜单</div>';
    return;
  }
  list.innerHTML = menus.map((menu) => `
    <article class="item-card">
      ${menu.image_url ? `<img src="${escapeHtml(menu.image_url)}" alt="">` : '<div class="image-placeholder">无图</div>'}
      <div>
        <strong>${escapeHtml(menu.title)}</strong>
        <p>${escapeHtml(menu.category)} | ${escapeHtml(menu.source)} | ${menu.enabled ? "启用" : "停用"}</p>
      </div>
      <div class="button-row">
        <button class="secondary" data-edit-menu="${escapeHtml(menu.id)}">编辑</button>
        <button class="danger" data-delete-menu="${escapeHtml(menu.id)}">删除</button>
      </div>
    </article>
  `).join("");
}

async function loadMenus() {
  try {
    const query = encodeURIComponent(byId("menuSearch").value.trim());
    const data = await requestJson(`./api/menus?search=${query}`, { headers: headers() });
    menus = data.menus || [];
    renderMenus();
    setNotice("menuNotice", `已加载 ${menus.length} 个菜单`, "ok");
  } catch (error) {
    setNotice("menuNotice", error.message, "error");
  }
}

function newMenu() {
  byId("menuCurrentId").value = "";
  byId("menuTitle").value = "";
  byId("menuEnabled").checked = true;
  byId("menuImage").value = "";
}

function editMenu(id) {
  const menu = menus.find((item) => item.id === id);
  if (!menu) return;
  byId("menuCurrentId").value = menu.id;
  byId("menuTitle").value = menu.title;
  byId("menuEnabled").checked = Boolean(menu.enabled);
  byId("menuImage").value = "";
  setNotice("menuNotice", `正在编辑：${menu.title}`, "ok");
}

async function saveMenu() {
  try {
    const id = byId("menuCurrentId").value;
    const imageDataUrl = await fileToDataUrl(byId("menuImage").files[0]);
    const payload = {
      title: byId("menuTitle").value.trim(),
      enabled: byId("menuEnabled").checked,
      image_data_url: imageDataUrl,
    };
    const url = id ? `./api/menus/${encodeURIComponent(id)}` : "./api/menus";
    await requestJson(url, {
      method: id ? "PUT" : "POST",
      headers: headers(),
      body: JSON.stringify(payload),
    });
    newMenu();
    await loadMenus();
    setNotice("menuNotice", "菜单已保存", "ok");
  } catch (error) {
    setNotice("menuNotice", error.message, "error");
  }
}

async function deleteMenu(id) {
  if (!confirm("删除这个菜单？")) return;
  await requestJson(`./api/menus/${encodeURIComponent(id)}`, { method: "DELETE", headers: headers() });
  await loadMenus();
}

async function pruneMenus() {
  try {
    const data = await requestJson("./api/menus/prune-howtocook-without-images", {
      method: "POST",
      headers: headers(),
    });
    await loadMenus();
    setNotice("menuNotice", `已清理 ${data.deleted} 条无图 HowToCook 菜单`, "ok");
  } catch (error) {
    setNotice("menuNotice", error.message, "error");
  }
}

function renderRestaurants() {
  const list = byId("restaurantList");
  if (!restaurants.length) {
    list.innerHTML = '<div class="empty-list">暂无饭店</div>';
    return;
  }
  list.innerHTML = restaurants.map((restaurant) => `
    <article class="item-card">
      <div>
        <strong>${escapeHtml(restaurant.name)}</strong>
        <p>群 ${restaurant.group_id} | ${restaurant.enabled ? "启用" : "停用"}</p>
        <p>${escapeHtml((restaurant.dishes || []).join("、"))}</p>
      </div>
      <div class="button-row">
        <button class="secondary" data-edit-restaurant="${restaurant.id}">编辑</button>
        <button class="danger" data-delete-restaurant="${restaurant.id}">删除</button>
      </div>
    </article>
  `).join("");
}

async function loadRestaurants() {
  try {
    const groupId = byId("restaurantGroupId").value.trim();
    const query = groupId ? `?group_id=${encodeURIComponent(groupId)}` : "";
    const data = await requestJson(`./api/restaurants${query}`, { headers: headers() });
    restaurants = data.restaurants || [];
    renderRestaurants();
    setNotice("restaurantNotice", `已加载 ${restaurants.length} 个饭店`, "ok");
  } catch (error) {
    setNotice("restaurantNotice", error.message, "error");
  }
}

function newRestaurant() {
  byId("restaurantCurrentId").value = "";
  byId("restaurantName").value = "";
  byId("restaurantDishes").value = "";
  byId("restaurantEnabled").checked = true;
}

function editRestaurant(id) {
  const restaurant = restaurants.find((item) => String(item.id) === String(id));
  if (!restaurant) return;
  byId("restaurantCurrentId").value = restaurant.id;
  byId("restaurantGroupId").value = restaurant.group_id;
  byId("restaurantName").value = restaurant.name;
  byId("restaurantDishes").value = (restaurant.dishes || []).join("\n");
  byId("restaurantEnabled").checked = Boolean(restaurant.enabled);
  setNotice("restaurantNotice", `正在编辑：${restaurant.name}`, "ok");
}

async function saveRestaurant() {
  try {
    const id = byId("restaurantCurrentId").value;
    const payload = {
      group_id: Number(byId("restaurantGroupId").value),
      name: byId("restaurantName").value.trim(),
      dishes: byId("restaurantDishes").value.split("\n").map((item) => item.trim()).filter(Boolean),
      enabled: byId("restaurantEnabled").checked,
    };
    const url = id ? `./api/restaurants/${id}` : "./api/restaurants";
    await requestJson(url, {
      method: id ? "PUT" : "POST",
      headers: headers(),
      body: JSON.stringify(payload),
    });
    newRestaurant();
    await loadRestaurants();
    setNotice("restaurantNotice", "饭店已保存", "ok");
  } catch (error) {
    setNotice("restaurantNotice", error.message, "error");
  }
}

async function deleteRestaurant(id) {
  if (!confirm("删除这个饭店？")) return;
  await requestJson(`./api/restaurants/${id}`, { method: "DELETE", headers: headers() });
  await loadRestaurants();
}

document.addEventListener("click", async (event) => {
  const target = event.target.closest("button, [data-add-rule], [data-group-action], [data-lua-open], [data-edit-menu], [data-delete-menu], [data-edit-restaurant], [data-delete-restaurant]");
  if (!target) return;
  if (target.matches(".tab")) showTab(target.dataset.tab);
  if (target.dataset.groupAction) groupAction(target.dataset.groupAction).catch((error) => byId("state").textContent = error.message);
  if (target.dataset.addRule) addRule(target.dataset.addRule);
  if (target.dataset.luaOpen) openLuaCommand(target.dataset.luaOpen);
  if (target.dataset.editMenu) editMenu(target.dataset.editMenu);
  if (target.dataset.deleteMenu) deleteMenu(target.dataset.deleteMenu).catch((error) => setNotice("menuNotice", error.message, "error"));
  if (target.dataset.editRestaurant) editRestaurant(target.dataset.editRestaurant);
  if (target.dataset.deleteRestaurant) deleteRestaurant(target.dataset.deleteRestaurant).catch((error) => setNotice("restaurantNotice", error.message, "error"));

  const action = target.dataset.action;
  if (!action) return;
  const actions = {
    refresh,
    "set-mode": setMode,
    "save-prefixes": savePrefixes,
    "save-direct-trigger-percent": saveDirectTriggerPercent,
    "save-core-config": saveCoreConfig,
    "load-replies": loadReplies,
    "save-replies": saveReplies,
    "load-lua": loadLuaCommands,
    "create-lua-command": () => openLuaCommand(byId("luaCommandInput").value || "抽群老婆"),
    "save-lua": saveLua,
    "delete-lua": deleteLua,
    "load-menus": loadMenus,
    "save-menu": saveMenu,
    "new-menu": newMenu,
    "prune-menus": pruneMenus,
    "load-restaurants": loadRestaurants,
    "save-restaurant": saveRestaurant,
    "new-restaurant": newRestaurant,
  };
  if (actions[action]) {
    actions[action]().catch((error) => {
      byId("state").textContent = error.message;
    });
  }
});

byId("luaImport").addEventListener("change", (event) => importLuaFile(event.target.files[0]));
byId("menuSearch").addEventListener("input", () => loadMenus());

refresh();
loadReplies();
loadLuaCommands();
loadMenus();
loadRestaurants();
