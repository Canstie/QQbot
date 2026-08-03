import { useEffect, useState } from "react";
import { BrainCircuit, Eraser, Save, Sparkles } from "lucide-react";

import { formatIds, get, parseIds, post, remove } from "../api";
import { Button, Field, Metric, PageHeader, Panel, Status, Switch } from "../components/Ui";

export default function AiPage({ refreshVersion, onChanged }) {
  const [data, setData] = useState(null);
  const [form, setForm] = useState({ enabledGroups: "", turns: 2, enabled: false, prompt: "", clear: true });
  const [notice, setNotice] = useState("正在读取 AI 配置");

  const load = () => get("/dsapi").then((result) => {
    setData(result);
    setForm({ enabledGroups: formatIds(result.enabled_groups), turns: result.history_turns, enabled: result.knowledge_enabled, prompt: result.knowledge_prompt || "", clear: true });
    setNotice("AI 配置已同步");
  }).catch((error) => setNotice(error.message));

  useEffect(() => {
    void load();
  }, [refreshVersion]);
  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }));

  const save = async () => {
    try {
      const result = await post("/dsapi", { enabled_groups: parseIds(form.enabledGroups), history_turns: Number(form.turns), knowledge_enabled: form.enabled, knowledge_prompt: form.prompt, clear_history: form.clear });
      setData(result);
      setNotice("角色配置已保存，下一条 @bot 消息立即使用新设定");
      setForm((current) => ({ ...current, clear: false }));
      onChanged();
    } catch (error) { setNotice(error.message); }
  };

  const clearHistory = async () => {
    if (!window.confirm("清空所有群的 AI 短期上下文？")) return;
    try {
      const result = await remove("/dsapi/history");
      setData(result);
      setNotice(`已清空 ${result.deleted} 条上下文消息`);
      onChanged();
    } catch (error) { setNotice(error.message); }
  };

  return (
    <>
      <PageHeader eyebrow="Model memory" title="AI 角色与知识" description="为指定群挂载角色设定，并控制每个群独立保留的短期对话。" actions={<Button icon={Save} onClick={save}>保存并生效</Button>} />
      <div className="ai-status-strip">
        <div className="ai-status-strip__model"><BrainCircuit /><div><span>当前模型</span><strong>{data?.model || "读取中"}</strong><small>{data?.base_url || "-"}</small></div></div>
        <Metric label="AI 启用群" value={data?.enabled_groups?.length ?? "-"} tone="blue" />
        <Metric label="上下文消息" value={data?.history_messages ?? "-"} tone="mint" />
        <Metric label="涉及群" value={data?.history_groups ?? "-"} tone="orange" />
        <Status tone={data?.api_configured ? "ok" : "error"}>API {data?.api_configured ? "已配置" : "缺少密钥"}</Status>
      </div>

      <div className="content-grid">
        <Panel title="角色知识提示词" eyebrow="Knowledge layer" className="span-8">
          <div className="knowledge-toolbar"><Switch checked={form.enabled} onChange={(value) => update("enabled", value)} label="挂载角色知识" description="关闭后只使用基础系统提示词" /><span><Sparkles size={15} /> {form.prompt.length.toLocaleString()} 字符</span></div>
          <Field label="知识与角色设定" hint="建议写清身份、语气、世界观、人物关系、事实边界和禁止事项。">
            <textarea className="knowledge-editor" value={form.prompt} onChange={(e) => update("prompt", e.target.value)} placeholder="例如：你是群里的档案管理员。回答时使用简洁中文，只依据下方档案中的事实……" />
          </Field>
        </Panel>

        <div className="stack span-4">
          <Panel title="启用范围" eyebrow="AI groups">
            <Field label="允许调用 AI 的群" hint="独立于总体 Bot 启用群，一行一个群号。"><textarea value={form.enabledGroups} onChange={(e) => update("enabledGroups", e.target.value)} /></Field>
          </Panel>
          <Panel title="短期记忆" eyebrow="Context window">
            <Field label="每群保留轮数"><div className="range-value"><input type="range" min="1" max="20" value={form.turns} onChange={(e) => update("turns", e.target.value)} /><strong>{form.turns} 轮</strong></div></Field>
            <Switch checked={form.clear} onChange={(value) => update("clear", value)} label="保存时清空旧上下文" description="切换角色时建议开启" />
            <Button tone="danger" icon={Eraser} onClick={clearHistory}>立即清空全部上下文</Button>
          </Panel>
        </div>
        <div className="span-12 panel-footer panel-footer--standalone"><Status>{notice}</Status><span className="quiet-note">多模态消息仍会在本地直接丢弃，不调用 API</span></div>
      </div>
    </>
  );
}
