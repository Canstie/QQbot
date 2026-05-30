let replyConfig = { empty: "", fallback: "Received: {message}", rules: [], direct_rules: [] };

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
      <div class="rule-fields">
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
  const kind = node.dataset.kind;
  const index = Number(node.dataset.index);
  const field = node.dataset.field;
  replyConfig[kind][index][field] = node.value;
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

function syncGlobalReplyFields() {
  replyConfig.empty = byId("emptyReply").value;
  replyConfig.fallback = byId("fallbackReply").value;
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
  } catch (error) {
    byId("connectionStatus").textContent = "异常";
    byId("state").textContent = error.message;
  }
}

async function setMode() {
  try {
    await requestJson("./api/policy/mode", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ mode: byId("mode").value }),
    });
    await refresh();
  } catch (error) {
    byId("state").textContent = error.message;
  }
}

async function savePrefixes() {
  try {
    const prefixes = byId("prefixesInput").value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
    await requestJson("./api/policy/prefixes", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ prefixes }),
    });
    await refresh();
  } catch (error) {
    byId("state").textContent = error.message;
  }
}

async function groupAction(action) {
  const groupId = byId("groupId").value.trim();
  if (!groupId) return;
  try {
    await requestJson(`./api/groups/${groupId}/${action}`, { method: "POST", headers: headers() });
    await refresh();
  } catch (error) {
    byId("state").textContent = error.message;
  }
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
  syncGlobalReplyFields();
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

async function loadLua() {
  try {
    const data = await requestJson("./api/lua", { headers: headers() });
    byId("luaEditor").value = data.content || "";
    byId("luaMeta").textContent = `${data.script_path} · ${data.enabled ? "已启用" : "未启用"}${data.using_example ? " · 当前显示示例" : ""}`;
    setNotice("luaNotice", data.using_example ? "脚本为空，已显示示例" : "Lua 脚本已加载", "ok");
  } catch (error) {
    setNotice("luaNotice", error.message, "error");
  }
}

async function saveLua() {
  try {
    const data = await requestJson("./api/lua", {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ content: byId("luaEditor").value }),
    });
    byId("luaEditor").value = data.content || "";
    byId("luaMeta").textContent = `${data.script_path} · ${data.enabled ? "已启用" : "未启用"}`;
    setNotice("luaNotice", "保存成功", "ok");
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

document.addEventListener("click", (event) => {
  const action = event.target.dataset.action;
  if (action === "refresh") refresh();
  if (action === "set-mode") setMode();
  if (action === "save-prefixes") savePrefixes();
  if (action === "load-replies") loadReplies();
  if (action === "save-replies") saveReplies();
  if (action === "load-lua") loadLua();
  if (action === "save-lua") saveLua();

  const groupActionName = event.target.dataset.groupAction;
  if (groupActionName) groupAction(groupActionName);

  const addRuleKind = event.target.dataset.addRule;
  if (addRuleKind) addRule(addRuleKind);
});

byId("luaImport").addEventListener("change", (event) => importLuaFile(event.target.files?.[0]));

refresh();
loadReplies();
loadLua();
