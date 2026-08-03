import { useEffect, useState } from "react";
import { Plus, Save, Trash2 } from "lucide-react";

import { get, post } from "../api";
import { Button, Empty, Field, IconButton, PageHeader, Panel, SearchInput, Status } from "../components/Ui";

const ruleTypes = [
  ["exact", "完全匹配"], ["contains", "包含关键词"], ["prefix", "前缀匹配"], ["regex", "正则匹配"],
];

function RuleSection({ title, eyebrow, rules, onChange, onAdd, onRemove }) {
  const [filter, setFilter] = useState("");
  const visible = rules.map((rule, index) => ({ rule, index })).filter(({ rule }) =>
    !filter || [rule.type, rule.pattern, rule.reply].some((value) => String(value || "").toLowerCase().includes(filter.toLowerCase())),
  );

  return (
    <Panel title={title} eyebrow={eyebrow} actions={<Button tone="secondary" icon={Plus} onClick={onAdd}>添加规则</Button>}>
      <div className="list-toolbar"><SearchInput value={filter} onChange={setFilter} placeholder="搜索触发内容或回复" /><span>{visible.length} / {rules.length} 条</span></div>
      <div className="rule-stack">
        {!visible.length && <Empty title="没有匹配的规则" description={rules.length ? "换一个搜索词。" : "添加第一条回复规则。"} />}
        {visible.map(({ rule, index }) => (
          <article className="rule-editor" key={`${index}-${rule.pattern}`}>
            <div className="rule-editor__number">{String(index + 1).padStart(2, "0")}</div>
            <Field label="匹配方式"><select value={rule.type} onChange={(e) => onChange(index, "type", e.target.value)}>{ruleTypes.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></Field>
            <Field label="触发内容"><input value={rule.pattern} onChange={(e) => onChange(index, "pattern", e.target.value)} placeholder="用户发送的内容" /></Field>
            <Field label="机器人回复" className="rule-editor__reply"><textarea value={rule.reply} onChange={(e) => onChange(index, "reply", e.target.value)} placeholder="机器人返回的文本" /></Field>
            <IconButton label="删除规则" icon={Trash2} tone="danger" onClick={() => onRemove(index)} />
          </article>
        ))}
      </div>
    </Panel>
  );
}

export default function RepliesPage({ refreshVersion, onChanged }) {
  const [config, setConfig] = useState({ empty: "", fallback: "", rules: [], direct_rules: [] });
  const [notice, setNotice] = useState("正在读取回复规则");

  const load = () => get("/replies").then((data) => {
    setConfig({ empty: "", fallback: "", rules: [], direct_rules: [], ...(data.config || {}) });
    setNotice(data.valid ? "回复规则已同步" : data.error);
  }).catch((error) => setNotice(error.message));
  useEffect(load, [refreshVersion]);

  const updateRule = (kind, index, field, value) => setConfig((current) => ({
    ...current,
    [kind]: current[kind].map((rule, ruleIndex) => ruleIndex === index ? { ...rule, [field]: value } : rule),
  }));
  const addRule = (kind) => setConfig((current) => ({ ...current, [kind]: [{ type: "contains", pattern: "", reply: "" }, ...current[kind]] }));
  const removeRule = (kind, index) => setConfig((current) => ({ ...current, [kind]: current[kind].filter((_, ruleIndex) => ruleIndex !== index) }));
  const save = async () => {
    try {
      const data = await post("/replies", config);
      setConfig(data.config);
      setNotice("回复规则已保存并立即生效");
      onChanged();
    } catch (error) { setNotice(error.message); }
  };

  return (
    <>
      <PageHeader eyebrow="Text responses" title="回复规则" description="管理前缀触发与群聊关键词回复。规则自上而下匹配，命中第一条后停止。" actions={<Button icon={Save} onClick={save}>保存全部规则</Button>} />
      <Panel title="默认应答" eyebrow="Fallback copy" className="reply-defaults">
        <div className="form-grid form-grid--2">
          <Field label="空内容回复"><input value={config.empty || ""} onChange={(e) => setConfig({ ...config, empty: e.target.value })} /></Field>
          <Field label="未命中回复" hint="可使用 {message} 插入原消息。"><input value={config.fallback || ""} onChange={(e) => setConfig({ ...config, fallback: e.target.value })} /></Field>
        </div>
      </Panel>
      <div className="stack">
        <RuleSection title="前缀触发规则" eyebrow="Prefixed" rules={config.rules || []} onChange={(...args) => updateRule("rules", ...args)} onAdd={() => addRule("rules")} onRemove={(index) => removeRule("rules", index)} />
        <RuleSection title="免前缀关键词" eyebrow="Direct" rules={config.direct_rules || []} onChange={(...args) => updateRule("direct_rules", ...args)} onAdd={() => addRule("direct_rules")} onRemove={(index) => removeRule("direct_rules", index)} />
      </div>
      <div className="page-notice"><Status>{notice}</Status></div>
    </>
  );
}
