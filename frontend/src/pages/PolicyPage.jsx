import { useEffect, useState } from "react";
import { Radio, Save, ShieldCheck, Zap } from "lucide-react";

import { formatIds, get, parseIds, post } from "../api";
import { Button, Field, PageHeader, Panel, Status, Switch } from "../components/Ui";

const emptyForm = {
  mode: "allowlist", prefixes: "~,#bot", mention: true, directPercent: 10,
  groupSeconds: 5, userMinute: 5, enabled: "", blocked: "", admins: "",
};

export default function PolicyPage({ refreshVersion, onChanged }) {
  const [form, setForm] = useState(emptyForm);
  const [groupId, setGroupId] = useState("");
  const [notice, setNotice] = useState("正在读取策略");
  const [saving, setSaving] = useState(false);

  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }));

  const load = () => get("/policy").then((data) => {
    setForm({
      mode: data.mode,
      prefixes: (data.trigger?.prefixes || []).join(","),
      mention: Boolean(data.trigger?.mention),
      directPercent: data.trigger?.direct_trigger_percent ?? 10,
      groupSeconds: data.limits?.per_group_seconds ?? 5,
      userMinute: data.limits?.per_user_per_minute ?? 5,
      enabled: formatIds(data.enabled_groups),
      blocked: formatIds(data.blocked_groups),
      admins: formatIds(data.admins),
    });
    setNotice("配置已同步");
  }).catch((error) => setNotice(error.message));

  useEffect(load, [refreshVersion]);

  const save = async () => {
    setSaving(true);
    try {
      await post("/policy/core", {
        mode: form.mode,
        enabled_groups: parseIds(form.enabled),
        blocked_groups: parseIds(form.blocked),
        admins: parseIds(form.admins),
        trigger: {
          mention: form.mention,
          prefixes: form.prefixes.split(",").map((item) => item.trim()).filter(Boolean),
          direct_trigger_percent: Number(form.directPercent),
        },
        limits: {
          per_group_seconds: Number(form.groupSeconds),
          per_user_per_minute: Number(form.userMinute),
        },
      });
      setNotice("策略已保存并立即生效");
      onChanged();
      load();
    } catch (error) {
      setNotice(error.message);
    } finally {
      setSaving(false);
    }
  };

  const groupAction = async (action) => {
    if (!groupId.trim()) return;
    try {
      await post(`/groups/${encodeURIComponent(groupId.trim())}/${action}`, {});
      setNotice(`群 ${groupId} 状态已更新`);
      load();
      onChanged();
    } catch (error) {
      setNotice(error.message);
    }
  };

  return (
    <>
      <PageHeader eyebrow="Message gate" title="群与策略" description="决定哪些群可以触发机器人，以及消息进入处理链路的条件。" actions={<Button icon={Save} onClick={save} disabled={saving}>{saving ? "保存中" : "保存全部"}</Button>} />
      <div className="content-grid">
        <Panel title="核心路由" eyebrow="Policy mode" className="span-7">
          <div className="form-grid form-grid--2">
            <Field label="运行模式" hint="allowlist 只服务启用群；blocklist 服务未被屏蔽的群。">
              <select value={form.mode} onChange={(e) => update("mode", e.target.value)}><option value="allowlist">允许列表</option><option value="blocklist">屏蔽列表</option></select>
            </Field>
            <Field label="触发前缀" hint="使用英文逗号分隔。">
              <input value={form.prefixes} onChange={(e) => update("prefixes", e.target.value)} />
            </Field>
            <Switch checked={form.mention} onChange={(value) => update("mention", value)} label="允许 @bot 触发" description="AI 群仍需在 AI 页面单独启用" />
            <Field label="免前缀触发概率"><div className="input-suffix"><input type="number" min="0" max="100" value={form.directPercent} onChange={(e) => update("directPercent", e.target.value)} /><span>%</span></div></Field>
            <Field label="群冷却时间"><div className="input-suffix"><input type="number" min="0" step="0.5" value={form.groupSeconds} onChange={(e) => update("groupSeconds", e.target.value)} /><span>秒</span></div></Field>
            <Field label="单用户每分钟上限"><div className="input-suffix"><input type="number" min="0" value={form.userMinute} onChange={(e) => update("userMinute", e.target.value)} /><span>次</span></div></Field>
          </div>
        </Panel>

        <Panel title="快速控制单群" eyebrow="Group switch" className="span-5">
          <div className="group-switcher">
            <div className="group-switcher__icon"><Radio /></div>
            <Field label="群号"><input inputMode="numeric" value={groupId} onChange={(e) => setGroupId(e.target.value)} placeholder="输入 Group ID" /></Field>
            <div className="button-cluster"><Button tone="primary" onClick={() => groupAction("on")}>启用</Button><Button tone="secondary" onClick={() => groupAction("off")}>关闭</Button><Button tone="danger" onClick={() => groupAction("block")}>屏蔽</Button><Button tone="ghost" onClick={() => groupAction("unblock")}>解除屏蔽</Button></div>
          </div>
        </Panel>

        <Panel title="群与管理员清单" eyebrow="Access lists" className="span-12">
          <div className="form-grid form-grid--3">
            <Field label="启用群" hint="一行一个群号"><textarea value={form.enabled} onChange={(e) => update("enabled", e.target.value)} /></Field>
            <Field label="屏蔽群" hint="一行一个群号"><textarea value={form.blocked} onChange={(e) => update("blocked", e.target.value)} /></Field>
            <Field label="管理员 QQ" hint="可使用 /bot 管理命令"><textarea value={form.admins} onChange={(e) => update("admins", e.target.value)} /></Field>
          </div>
          <div className="panel-footer"><Status icon={ShieldCheck}>{notice}</Status><span className="quiet-note"><Zap size={14} /> 保存后无需重启</span></div>
        </Panel>
      </div>
    </>
  );
}
