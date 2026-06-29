const ruleTypes = [
  ["exact", "完全匹配"],
  ["contains", "包含关键词"],
  ["prefix", "前缀匹配"],
  ["regex", "正则匹配"],
];

const validViews = new Set([
  "overview",
  "policy",
  "replies",
  "lua",
  "menus",
  "restaurants",
  "classics",
]);

const state = {
  replyConfig: { empty: "", fallback: "Received: {message}", rules: [], direct_rules: [] },
  luaCommands: [],
  currentLuaCommand: "",
  menus: [],
  restaurants: [],
  classicsGroups: [],
  currentClassicGroup: null,
  currentClassicImages: [],
  ruleFilters: { rules: "", direct_rules: "" },
};

const byId = (id) => document.getElementById(id);

const headers = () => {
  const tokenInput = byId("token");
  const token = tokenInput ? tokenInput.value.trim() : "";
  return token
    ? { "Content-Type": "application/json", "X-Admin-Token": token }
    : { "Content-Type": "application/json" };
};

function setNotice(id, message, kind = "") {
  const node = byId(id);
  node.textContent = message;
  node.className = kind ? `notice ${kind}` : "notice";
}

function setConnectionStatus(message) {
  byId("connectionStatus").textContent = message;
}

function setGlobalStatus(message, kind = "") {
  setNotice("globalNotice", message, kind);
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

async function logout() {
  await fetch("./logout", { method: "POST" });
  window.location.href = "./login";
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
    .split(/[\s,，]+/)
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => Number(item))
    .filter((item) => Number.isFinite(item));
}

function formatIdList(values) {
  return (values || []).join("\n");
}

function formatDateTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString("zh-CN", { hour12: false });
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const amount = bytes / 1024 ** index;
  return `${amount.toFixed(amount >= 10 || index === 0 ? 0 : 1)} ${units[index]}`;
}

function showView(name) {
  document.querySelectorAll(".nav-link").forEach((node) => {
    node.classList.toggle("active", node.dataset.view === name);
  });
  document.querySelectorAll(".view").forEach((node) => {
    node.classList.toggle("active", node.dataset.viewPanel === name);
  });
}

function parseRoute() {
  const hash = location.hash.replace(/^#/, "").trim();
  if (!hash) return { view: "overview", groupId: null };

  const [rawView, rawGroupId] = hash.split("/");
  const view = validViews.has(rawView) ? rawView : "overview";
  const groupId = view === "classics" && rawGroupId ? rawGroupId.trim() : null;
  return { view, groupId };
}

function setRoute(view, groupId = null, replace = false) {
  const nextHash = groupId ? `#${view}/${groupId}` : `#${view}`;
  if (location.hash === nextHash) {
    applyRoute();
    return;
  }
  if (replace) {
    history.replaceState(null, "", nextHash);
    applyRoute();
    return;
  }
  location.hash = nextHash;
}

function renderOverviewArchiveStrip() {
  const list = byId("overviewArchiveStrip");
  if (!state.classicsGroups.length) {
    list.innerHTML = '<div class="empty-list">还没有任何群存过典图。</div>';
    return;
  }

  list.innerHTML = state.classicsGroups.slice(0, 4).map((group) => `
    <article class="archive-card">
      <p class="panel-kicker">Group</p>
      <strong>${escapeHtml(group.group_id)}</strong>
      <p>${group.count} 张典图 · ${formatDateTime(group.updated_at)}</p>
      <div class="button-row">
        <button class="secondary" data-classics-open="${group.group_id}">进入群号</button>
      </div>
    </article>
  `).join("");
}

function renderRules(kind) {
  const listId = kind === "rules" ? "rulesList" : "directRulesList";
  const metaId = kind === "rules" ? "rulesMeta" : "directRulesMeta";
  const list = byId(listId);
  const rules = state.replyConfig[kind] || [];
  const filter = (state.ruleFilters[kind] || "").trim().toLowerCase();
  const visibleRules = rules
    .map((rule, index) => ({ rule, index }))
    .filter(({ rule }) => {
      if (!filter) return true;
      return [rule.type, rule.pattern, rule.reply]
        .some((value) => String(value || "").toLowerCase().includes(filter));
    });

  byId(metaId).textContent = filter
    ? `显示 ${visibleRules.length} / ${rules.length} 条`
    : `${rules.length} 条`;

  if (!rules.length) {
    list.innerHTML = '<div class="empty-list">暂无规则</div>';
    return;
  }

  if (!visibleRules.length) {
    list.innerHTML = '<div class="empty-list">没有匹配的规则</div>';
    return;
  }

  list.innerHTML = visibleRules.map(({ rule, index }) => `
    <div class="rule-card">
      <div class="rule-index">#${index + 1}</div>
      <label>匹配方式
        <select data-kind="${kind}" data-index="${index}" data-field="type">
          ${ruleTypes.map(([value, label]) => `
            <option value="${value}" ${rule.type === value ? "selected" : ""}>${label}</option>
          `).join("")}
        </select>
      </label>
      <label>触发内容
        <input data-kind="${kind}" data-index="${index}" data-field="pattern" value="${escapeHtml(rule.pattern)}" />
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
  state.replyConfig[node.dataset.kind][Number(node.dataset.index)][node.dataset.field] = node.value;
}

function addRule(kind) {
  state.replyConfig[kind] = state.replyConfig[kind] || [];
  state.ruleFilters[kind] = "";
  const filterId = kind === "rules" ? "rulesFilter" : "directRulesFilter";
  byId(filterId).value = "";
  state.replyConfig[kind].unshift({ type: "contains", pattern: "", reply: "" });
  renderRules(kind);
}

function removeRule(kind, index) {
  state.replyConfig[kind].splice(index, 1);
  renderRules(kind);
}

async function loadPolicy() {
  try {
    const data = await requestJson("./api/policy", { headers: headers() });
    byId("state").textContent = JSON.stringify(data, null, 2);
    byId("overviewMode").textContent = data.mode || "-";
    byId("enabledCount").textContent = data.enabled_groups?.length ?? 0;
    byId("blockedCount").textContent = data.blocked_groups?.length ?? 0;
    byId("adminCount").textContent = data.admins?.length ?? 0;

    if (data.mode) byId("mode").value = data.mode;
    byId("prefixesInput").value = (data.trigger?.prefixes || ["~"]).join(",");
    byId("mentionTrigger").checked = Boolean(data.trigger?.mention);
    byId("directTriggerPercent").value = data.trigger?.direct_trigger_percent ?? 10;
    byId("perGroupSeconds").value = data.limits?.per_group_seconds ?? 5;
    byId("perUserPerMinute").value = data.limits?.per_user_per_minute ?? 5;
    byId("enabledGroupsInput").value = formatIdList(data.enabled_groups);
    byId("blockedGroupsInput").value = formatIdList(data.blocked_groups);
    byId("adminsInput").value = formatIdList(data.admins);

    setConnectionStatus("在线");
    setNotice("configNotice", "核心配置已加载", "ok");
    return data;
  } catch (error) {
    setConnectionStatus("异常");
    byId("state").textContent = error.message;
    setNotice("configNotice", error.message, "error");
    throw error;
  }
}

async function saveCoreConfig() {
  const prefixes = byId("prefixesInput").value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

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
  await loadPolicy();
}

async function groupAction(action) {
  const groupId = byId("groupId").value.trim();
  if (!groupId) return;
  await requestJson(`./api/groups/${groupId}/${action}`, {
    method: "POST",
    headers: headers(),
  });
  await loadPolicy();
}

async function loadReplies() {
  try {
    const data = await requestJson("./api/replies", { headers: headers() });
    state.replyConfig = data.config || state.replyConfig;
    state.replyConfig.rules = state.replyConfig.rules || [];
    state.replyConfig.direct_rules = state.replyConfig.direct_rules || [];
    byId("emptyReply").value = state.replyConfig.empty || "";
    byId("fallbackReply").value = state.replyConfig.fallback || "";
    renderRules("rules");
    renderRules("direct_rules");
    setNotice("editorNotice", data.valid ? "回复规则已加载" : data.error, data.valid ? "ok" : "error");
    return data;
  } catch (error) {
    setNotice("editorNotice", error.message, "error");
    throw error;
  }
}

async function saveReplies() {
  state.replyConfig.empty = byId("emptyReply").value;
  state.replyConfig.fallback = byId("fallbackReply").value;

  const data = await requestJson("./api/replies", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(state.replyConfig),
  });

  state.replyConfig = data.config || state.replyConfig;
  renderRules("rules");
  renderRules("direct_rules");
  setNotice("editorNotice", "回复规则已保存", "ok");
}

const encodedCommand = (command) => encodeURIComponent(command);

function renderLuaCommandList() {
  const list = byId("luaCommandList");
  if (!state.luaCommands.length) {
    list.innerHTML = '<div class="empty-list">暂无脚本</div>';
    return;
  }

  list.innerHTML = state.luaCommands.map((item) => `
    <button class="command-item ${item.command === state.currentLuaCommand ? "active" : ""}" data-lua-open="${escapeHtml(item.command)}">
      <span>${escapeHtml(item.command)}</span>
      <small>${Math.max(1, Math.ceil((item.size || 0) / 1024))} KB</small>
    </button>
  `).join("");
}

async function loadLuaCommands(preferredCommand = state.currentLuaCommand) {
  const data = await requestJson("./api/lua/commands", { headers: headers() });
  state.luaCommands = data.commands || [];
  byId("overviewLuaCount").textContent = state.luaCommands.length;
  byId("luaListMeta").textContent =
    `${data.lua_dir} | ${data.enabled ? "已启用" : "未启用"} | ${state.luaCommands.length} 个脚本`;
  renderLuaCommandList();

  const nextCommand = preferredCommand || state.luaCommands[0]?.command || "抽群老婆";
  await openLuaCommand(nextCommand);
  return data;
}

async function openLuaCommand(command) {
  const value = String(command || "").trim();
  if (!value) return;

  const data = await requestJson(`./api/lua/commands/${encodedCommand(value)}`, { headers: headers() });
  state.currentLuaCommand = data.command;
  byId("luaCommandInput").value = data.command;
  byId("luaCurrentCommand").value = data.command;
  byId("luaEditor").value = data.content || "";
  byId("luaMeta").textContent = `${data.path}${data.using_example ? " | 示例脚本" : ""}`;
  setNotice("luaNotice", data.using_example ? "当前展示的是示例脚本，保存后会生成文件" : "Lua 脚本已加载", "ok");
  renderLuaCommandList();
}

async function saveLua() {
  if (!state.currentLuaCommand) return;

  const data = await requestJson(`./api/lua/commands/${encodedCommand(state.currentLuaCommand)}`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ content: byId("luaEditor").value }),
  });

  await loadLuaCommands(data.command);
  setNotice("luaNotice", "脚本已保存", "ok");
}

async function deleteLua() {
  if (!state.currentLuaCommand) return;
  if (!confirm(`删除 Lua 指令“${state.currentLuaCommand}”？`)) return;

  await requestJson(`./api/lua/commands/${encodedCommand(state.currentLuaCommand)}`, {
    method: "DELETE",
    headers: headers(),
  });

  state.currentLuaCommand = "";
  await loadLuaCommands();
  setNotice("luaNotice", "脚本已删除", "ok");
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
  if (!state.menus.length) {
    list.innerHTML = '<div class="empty-list">暂无菜单</div>';
    return;
  }

  list.innerHTML = state.menus.map((menu) => `
    <article class="item-card">
      ${menu.image_url ? `<img src="${escapeHtml(menu.image_url)}" alt="${escapeHtml(menu.title)}" />` : '<div class="image-placeholder">无图</div>'}
      <div>
        <strong>${escapeHtml(menu.title)}</strong>
        <p>${escapeHtml(menu.category)} · ${escapeHtml(menu.source)} · ${menu.enabled ? "启用" : "停用"}</p>
      </div>
      <div class="button-row">
        <button class="secondary" data-edit-menu="${escapeHtml(menu.id)}">编辑</button>
        <button class="danger" data-delete-menu="${escapeHtml(menu.id)}">删除</button>
      </div>
    </article>
  `).join("");
}

async function loadMenus() {
  const query = encodeURIComponent(byId("menuSearch").value.trim());
  const data = await requestJson(`./api/menus?search=${query}`, { headers: headers() });
  state.menus = data.menus || [];
  byId("overviewMenuCount").textContent = state.menus.length;
  renderMenus();
  setNotice("menuNotice", `已加载 ${state.menus.length} 个菜单`, "ok");
  return data;
}

function newMenu() {
  byId("menuCurrentId").value = "";
  byId("menuTitle").value = "";
  byId("menuEnabled").checked = true;
  byId("menuImage").value = "";
}

function editMenu(id) {
  const menu = state.menus.find((item) => item.id === id);
  if (!menu) return;
  byId("menuCurrentId").value = menu.id;
  byId("menuTitle").value = menu.title;
  byId("menuEnabled").checked = Boolean(menu.enabled);
  byId("menuImage").value = "";
  setNotice("menuNotice", `正在编辑：${menu.title}`, "ok");
}

async function saveMenu() {
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
}

async function deleteMenu(id) {
  if (!confirm("删除这个菜单？")) return;
  await requestJson(`./api/menus/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: headers(),
  });
  await loadMenus();
}

async function pruneMenus() {
  const data = await requestJson("./api/menus/prune-howtocook-without-images", {
    method: "POST",
    headers: headers(),
  });
  await loadMenus();
  setNotice("menuNotice", `已清理 ${data.deleted} 条无图 HowToCook 菜单`, "ok");
}

function renderRestaurants() {
  const list = byId("restaurantList");
  if (!state.restaurants.length) {
    list.innerHTML = '<div class="empty-list">暂无饭店</div>';
    return;
  }

  list.innerHTML = state.restaurants.map((restaurant) => `
    <article class="item-card">
      <div class="image-placeholder">群 ${restaurant.group_id}</div>
      <div>
        <strong>${escapeHtml(restaurant.name)}</strong>
        <p>${restaurant.enabled ? "启用" : "停用"} · ${escapeHtml((restaurant.dishes || []).join("、"))}</p>
      </div>
      <div class="button-row">
        <button class="secondary" data-edit-restaurant="${restaurant.id}">编辑</button>
        <button class="danger" data-delete-restaurant="${restaurant.id}">删除</button>
      </div>
    </article>
  `).join("");
}

async function loadRestaurants() {
  const groupId = byId("restaurantFilterGroupId").value.trim();
  const query = groupId ? `?group_id=${encodeURIComponent(groupId)}` : "";
  const data = await requestJson(`./api/restaurants${query}`, { headers: headers() });
  state.restaurants = data.restaurants || [];
  renderRestaurants();
  setNotice("restaurantNotice", `已加载 ${state.restaurants.length} 个饭店`, "ok");
  return data;
}

function newRestaurant() {
  byId("restaurantCurrentId").value = "";
  byId("restaurantGroupId").value = "";
  byId("restaurantName").value = "";
  byId("restaurantDishes").value = "";
  byId("restaurantEnabled").checked = true;
}

function editRestaurant(id) {
  const restaurant = state.restaurants.find((item) => String(item.id) === String(id));
  if (!restaurant) return;
  byId("restaurantCurrentId").value = restaurant.id;
  byId("restaurantGroupId").value = restaurant.group_id;
  byId("restaurantName").value = restaurant.name;
  byId("restaurantDishes").value = (restaurant.dishes || []).join("\n");
  byId("restaurantEnabled").checked = Boolean(restaurant.enabled);
  setNotice("restaurantNotice", `正在编辑：${restaurant.name}`, "ok");
}

async function saveRestaurant() {
  const id = byId("restaurantCurrentId").value;
  const payload = {
    group_id: Number(byId("restaurantGroupId").value),
    name: byId("restaurantName").value.trim(),
    dishes: byId("restaurantDishes").value
      .split("\n")
      .map((item) => item.trim())
      .filter(Boolean),
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
}

async function deleteRestaurant(id) {
  if (!confirm("删除这个饭店？")) return;
  await requestJson(`./api/restaurants/${id}`, {
    method: "DELETE",
    headers: headers(),
  });
  await loadRestaurants();
}

function renderClassicsGroups() {
  const list = byId("classicsGroupsList");
  const search = byId("classicsGroupSearch").value.trim();
  byId("classicsGroupsMeta").textContent = search
    ? `匹配到 ${state.classicsGroups.length} 个群`
    : `共 ${state.classicsGroups.length} 个群有典图`;

  if (!state.classicsGroups.length) {
    list.innerHTML = '<div class="empty-list">没有找到符合条件的群典藏。</div>';
    return;
  }

  list.innerHTML = state.classicsGroups.map((group) => `
    <article class="archive-item">
      ${group.cover_url ? `<img src="${escapeHtml(group.cover_url)}" alt="群 ${group.group_id} 封面" />` : ""}
      <div>
        <p class="panel-kicker">Group</p>
        <strong>${escapeHtml(group.group_id)}</strong>
        <p>${group.count} 张典图 · ${formatBytes(group.total_bytes)}</p>
        <p>最近更新：${formatDateTime(group.updated_at)}</p>
      </div>
      <div class="button-row">
        <button class="secondary" data-classics-open="${group.group_id}">进入群号</button>
        <button class="danger" data-delete-classics-group="${group.group_id}">删除整群</button>
      </div>
    </article>
  `).join("");
}

function renderClassicDetailPlaceholder(message = "群内典图会在这里显示。") {
  byId("classicsActiveGroup").textContent = "选择一个群查看存的典";
  byId("classicsDetailMeta").textContent = message;
  byId("classicsGallery").innerHTML = '<div class="empty-list">先从左侧选择一个群号。</div>';
}

function renderClassicGroupDetail() {
  if (!state.currentClassicGroup) {
    renderClassicDetailPlaceholder();
    return;
  }

  byId("classicsActiveGroup").textContent = `群 ${state.currentClassicGroup} 的典图`;
  byId("classicsDetailMeta").textContent =
    `共 ${state.currentClassicImages.length} 张 · 点击图片可在新窗口打开原图`;

  if (!state.currentClassicImages.length) {
    byId("classicsGallery").innerHTML = '<div class="empty-list">这个群目前还没有可显示的典图。</div>';
    return;
  }

  byId("classicsGallery").innerHTML = state.currentClassicImages.map((image) => `
    <figure class="gallery-tile">
      <a href="${escapeHtml(image.image_url)}" target="_blank" rel="noreferrer">
        <img src="${escapeHtml(image.image_url)}" alt="${escapeHtml(image.filename)}" loading="lazy" />
      </a>
      <figcaption>
        ${escapeHtml(image.filename)}<br />
        ${formatBytes(image.size)} · ${formatDateTime(image.modified_at)}
        <div class="button-row">
          <button class="danger" data-delete-classic-image="${escapeHtml(image.filename)}">删除这张</button>
        </div>
      </figcaption>
    </figure>
  `).join("");
}

async function loadClassicGroups() {
  const search = encodeURIComponent(byId("classicsGroupSearch").value.trim());
  const data = await requestJson(`./api/classics/groups?search=${search}`, { headers: headers() });
  state.classicsGroups = data.groups || [];
  byId("overviewClassicGroupCount").textContent = state.classicsGroups.length;
  renderOverviewArchiveStrip();
  renderClassicsGroups();
  if (!state.currentClassicGroup) {
    renderClassicDetailPlaceholder();
  }
  setNotice("classicsNotice", `已加载 ${state.classicsGroups.length} 个群典藏`, "ok");
  return data;
}

async function loadClassicGroup(groupId) {
  const value = String(groupId || "").trim();
  if (!value) {
    state.currentClassicGroup = null;
    state.currentClassicImages = [];
    renderClassicDetailPlaceholder();
    return;
  }

  const data = await requestJson(`./api/classics/groups/${encodeURIComponent(value)}`, { headers: headers() });
  state.currentClassicGroup = String(data.group_id);
  state.currentClassicImages = data.images || [];
  renderClassicGroupDetail();
  setNotice("classicsNotice", `群 ${state.currentClassicGroup} 已加载`, "ok");
}

async function clearClassicGroup() {
  state.currentClassicGroup = null;
  state.currentClassicImages = [];
  renderClassicDetailPlaceholder();
  setRoute("classics");
}

async function deleteClassicGroup(groupId = state.currentClassicGroup) {
  const value = String(groupId || "").trim();
  if (!value) return;
  if (!confirm(`删除群 ${value} 的全部典图？`)) return;

  await requestJson(`./api/classics/groups/${encodeURIComponent(value)}`, {
    method: "DELETE",
    headers: headers(),
  });

  if (String(state.currentClassicGroup) === value) {
    state.currentClassicGroup = null;
    state.currentClassicImages = [];
    setRoute("classics", null, true);
    renderClassicDetailPlaceholder("已删除这个群的全部典图。");
  }
  await loadClassicGroups();
  setNotice("classicsNotice", `群 ${value} 的典图已删除`, "ok");
}

async function deleteClassicImage(filename) {
  if (!state.currentClassicGroup || !filename) return;
  if (!confirm(`删除这张典图：${filename}？`)) return;

  await requestJson(
    `./api/classics/groups/${encodeURIComponent(state.currentClassicGroup)}/images/${encodeURIComponent(filename)}`,
    {
      method: "DELETE",
      headers: headers(),
    },
  );

  await loadClassicGroup(state.currentClassicGroup);
  await loadClassicGroups();
  setNotice("classicsNotice", `已删除：${filename}`, "ok");
}

function applyRoute() {
  const { view, groupId } = parseRoute();
  showView(view);
  if (view !== "classics") return;
  if (!groupId) {
    if (state.currentClassicGroup) {
      renderClassicGroupDetail();
    } else {
      renderClassicDetailPlaceholder();
    }
    return;
  }

  loadClassicGroup(groupId).catch((error) => {
    setNotice("classicsNotice", error.message, "error");
  });
}

async function refreshAll() {
  setConnectionStatus("同步中");
  setGlobalStatus("正在同步控制室...");

  const tasks = [
    loadPolicy(),
    loadReplies(),
    loadLuaCommands(),
    loadMenus(),
    loadRestaurants(),
    loadClassicGroups(),
  ];

  const results = await Promise.allSettled(tasks);
  const failures = results.filter((item) => item.status === "rejected");

  if (failures.length) {
    setConnectionStatus("部分异常");
    setGlobalStatus(`同步完成，但有 ${failures.length} 项加载失败`, "error");
  } else {
    setConnectionStatus("在线");
    setGlobalStatus("控制室已同步", "ok");
  }
}

document.addEventListener("click", (event) => {
  const target = event.target.closest(
    "button, [data-add-rule], [data-group-action], [data-lua-open], [data-edit-menu], [data-delete-menu], [data-edit-restaurant], [data-delete-restaurant], [data-classics-open], [data-delete-classics-group], [data-delete-classic-image]",
  );
  if (!target) return;

  if (target.dataset.view) {
    setRoute(target.dataset.view);
  }

  if (target.dataset.groupAction) {
    groupAction(target.dataset.groupAction).catch((error) => {
      byId("state").textContent = error.message;
    });
  }

  if (target.dataset.addRule) {
    addRule(target.dataset.addRule);
  }

  if (target.dataset.luaOpen) {
    openLuaCommand(target.dataset.luaOpen).catch((error) => {
      setNotice("luaNotice", error.message, "error");
    });
  }

  if (target.dataset.editMenu) {
    editMenu(target.dataset.editMenu);
  }

  if (target.dataset.deleteMenu) {
    deleteMenu(target.dataset.deleteMenu).catch((error) => {
      setNotice("menuNotice", error.message, "error");
    });
  }

  if (target.dataset.editRestaurant) {
    editRestaurant(target.dataset.editRestaurant);
  }

  if (target.dataset.deleteRestaurant) {
    deleteRestaurant(target.dataset.deleteRestaurant).catch((error) => {
      setNotice("restaurantNotice", error.message, "error");
    });
  }

  if (target.dataset.classicsOpen) {
    setRoute("classics", target.dataset.classicsOpen);
  }

  if (target.dataset.deleteClassicsGroup) {
    deleteClassicGroup(target.dataset.deleteClassicsGroup).catch((error) => {
      setNotice("classicsNotice", error.message, "error");
    });
  }

  if (target.dataset.deleteClassicImage) {
    deleteClassicImage(target.dataset.deleteClassicImage).catch((error) => {
      setNotice("classicsNotice", error.message, "error");
    });
  }

  const action = target.dataset.action;
  if (!action) return;

  const actions = {
    refresh: refreshAll,
    logout,
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
    "load-classics-groups": loadClassicGroups,
    "clear-classics-group": clearClassicGroup,
    "delete-current-classics-group": deleteClassicGroup,
    "reload-classics-group": () => (
      state.currentClassicGroup ? loadClassicGroup(state.currentClassicGroup) : loadClassicGroups()
    ),
  };

  if (actions[action]) {
    actions[action]().catch((error) => {
      setGlobalStatus(error.message, "error");
    });
  }
});

byId("luaImport").addEventListener("change", (event) => {
  importLuaFile(event.target.files[0]);
});

byId("menuSearch").addEventListener("input", () => {
  loadMenus().catch((error) => setNotice("menuNotice", error.message, "error"));
});

byId("restaurantFilterGroupId").addEventListener("input", () => {
  loadRestaurants().catch((error) => setNotice("restaurantNotice", error.message, "error"));
});

byId("classicsGroupSearch").addEventListener("input", () => {
  loadClassicGroups().catch((error) => setNotice("classicsNotice", error.message, "error"));
});

document.querySelectorAll("[data-rule-filter]").forEach((node) => {
  node.addEventListener("input", () => {
    state.ruleFilters[node.dataset.ruleFilter] = node.value;
    renderRules(node.dataset.ruleFilter);
  });
});

window.addEventListener("hashchange", applyRoute);

if (!location.hash) {
  history.replaceState(null, "", "#overview");
}

applyRoute();
refreshAll().catch((error) => {
  setConnectionStatus("异常");
  setGlobalStatus(error.message, "error");
});
