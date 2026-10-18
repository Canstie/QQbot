import { useEffect, useState } from "react";
import { CheckCircle2, Network, Plus, RefreshCw, Save, Search, Trash2, Zap } from "lucide-react";

import { post, put, remove } from "../api";
import { Button, Field, Panel, Status } from "./Ui";

const draftFrom = (provider = {}) => ({
  name: provider.name || "", base_url: provider.base_url || "https://",
  default_model: provider.default_model || "", api_key: "",
  api_format: provider.api_format || "openai", timeout_seconds: provider.timeout_seconds || 60,
  tls_mode: provider.tls_mode || "auto",
});

export default function AiProvidersPanel({ data, onChanged, onProxyChanged }) {
  const [selectedId, setSelectedId] = useState(null);
  const [draft, setDraft] = useState(draftFrom());
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(null);
  const [notice, setNotice] = useState("选择供应商，或添加新的接口。");
  const [proxyNotice, setProxyNotice] = useState("");
  const providers = data?.providers || [];
  const selected = providers.find((item) => item.id === selectedId);
  const active = providers.find((item) => item.active);
  const models = selected?.models || [];
  const visibleModels = models.filter((item) => item.id.toLowerCase().includes(search.toLowerCase()));
  const dirty = !selected || Object.entries(draft).some(([key, value]) => (
    key === "api_key" ? Boolean(value) : String(value) !== String(selected[key])
  ));

  useEffect(() => {
    if (data?.active_provider_id) setSelectedId(data.active_provider_id);
  }, [data?.active_provider_id]);
  useEffect(() => {
    if (selected) { setDraft(draftFrom(selected)); setSearch(""); }
  }, [selected]);

  const update = (key, value) => setDraft((current) => ({ ...current, [key]: value }));
  const run = async (action, work) => {
    setBusy(action);
    try { await work(); }
    catch (error) { setNotice(error.message); }
    finally { setBusy(null); }
  };
  const save = () => run("save", async () => {
    const payload = { ...draft, timeout_seconds: Number(draft.timeout_seconds), api_key: draft.api_key || null };
    const result = selectedId ? await put(`/dsapi/providers/${selectedId}`, payload) : await post("/dsapi/providers", payload);
    setDraft((current) => ({ ...current, api_key: "" }));
    if (result.provider_id) setSelectedId(result.provider_id);
    await onChanged();
    setNotice(selected?.active ? "供应商已保存，下一次 AI 请求立即生效" : "供应商已保存，可获取模型、测试连接或启用");
  });
  const refresh = () => run("models", async () => {
    const result = await post(`/dsapi/providers/${selectedId}/models/refresh`, {});
    await onChanged();
    setNotice(`已保存全部 ${result.count} 个模型，刷新网页后仍可选择`);
  });
  const test = () => run("test", async () => {
    const result = await post(`/dsapi/providers/${selectedId}/test`, { model: draft.default_model });
    setNotice(`连接成功 · ${result.model} · ${result.latency_ms} ms · ${result.reply}`);
  });
  const activate = () => run("activate", async () => {
    await post(`/dsapi/providers/${selectedId}/activate`, {});
    await onChanged();
    setNotice(`已启用 ${selected.name}，所有 AI 功能从下一次请求使用 ${selected.default_model}`);
  });
  const erase = () => run("delete", async () => {
    if (!window.confirm(`删除供应商“${selected.name}”？`)) return;
    await remove(`/dsapi/providers/${selectedId}`);
    setSelectedId(active?.id || null);
    await onChanged();
    setNotice("供应商已删除");
  });
  const toggleProxy = async (enabled) => {
    setBusy("proxy");
    setProxyNotice("");
    try {
      const result = await put("/dsapi/proxy", { enabled });
      if (onProxyChanged) onProxyChanged(result);
      else await onChanged();
      setProxyNotice(enabled ? "已保存，新的 AI 请求使用代理线路。" : "已保存，新的 AI 请求直接连接接口。");
    } catch (error) { setProxyNotice(error.message); }
    finally { setBusy(null); }
  };

  return <Panel title="AI 供应商切换" eyebrow="Provider switch" className="ai-providers" actions={
    <Button tone="secondary" icon={Plus} disabled={Boolean(busy)} onClick={() => {
      setSelectedId(null); setDraft(draftFrom()); setSearch(""); setNotice("填写接口和密钥，保存后获取模型");
    }}>添加供应商</Button>
  }>
    <div className="provider-route"><Network size={18} /><strong>{active?.name || "未配置"}</strong><span>→</span><code>{data?.model || "选择模型"}</code><Status tone={data?.api_configured ? "ok" : "error"}>{data?.api_configured ? "当前使用" : "缺少密钥"}</Status></div>
    <div className="provider-proxy">
      <label><input type="checkbox" checked={Boolean(data?.ai_proxy_enabled)} disabled={Boolean(busy) || !data?.ai_proxy_configured} onChange={(event) => toggleProxy(event.target.checked)} /><span><strong>显式代理</strong><small>{data?.ai_proxy_configured ? "勾选后所有 AI 请求走代理，取消后直连；切换供应商和知识库时保留。" : "服务器尚未配置 AI 代理地址。"}</small></span></label>
      <Status tone={data?.ai_proxy_enabled ? "ok" : "neutral"}>{busy === "proxy" ? "保存中" : data?.ai_proxy_enabled ? "代理线路" : "直连线路"}</Status>
      {proxyNotice && <p role="status" aria-live="polite">{proxyNotice}</p>}
    </div>
    <div className="provider-workspace">
      <div className="provider-list" aria-label="供应商列表">
        {providers.map((item) => <button type="button" key={item.id} disabled={Boolean(busy)} className={`provider-card ${selectedId === item.id ? "is-selected" : ""}`} onClick={() => setSelectedId(item.id)}>
          <span className="provider-card__head"><strong>{item.name}</strong>{item.active && <CheckCircle2 size={16} />}</span>
          <span>{item.base_url}</span><code>{item.default_model}</code>
          <small>{item.models.length} 个模型 · {item.api_key_configured ? "密钥已保存" : "待配置密钥"}{item.active ? " · 已启用" : ""}</small>
        </button>)}
      </div>
      <div className="provider-editor">
        <div className="form-grid form-grid--2">
          <Field label="供应商名称"><input value={draft.name} maxLength={80} disabled={Boolean(busy)} onChange={(event) => update("name", event.target.value)} placeholder="例如：Miku / DeepSeek" /></Field>
          <Field label="接口地址" hint="支持填写域名或 /v1 地址"><input value={draft.base_url} disabled={Boolean(busy)} onChange={(event) => update("base_url", event.target.value)} placeholder="https://api.example.com/v1" /></Field>
          <Field label="API Key" hint={selected?.api_key_configured ? "密钥已保存，留空保留原值" : "密钥仅保存在服务器"}><input type="password" autoComplete="new-password" value={draft.api_key} disabled={Boolean(busy)} onChange={(event) => update("api_key", event.target.value)} placeholder={selected?.api_key_configured ? "已保存 · 留空不修改" : "填写 API Key"} /></Field>
          <Field label="当前供应商模型" hint="所有 AI 功能统一使用此模型"><input list="provider-model-catalog" value={draft.default_model} disabled={Boolean(busy)} onChange={(event) => update("default_model", event.target.value)} placeholder="模型完整名称" /><datalist id="provider-model-catalog">{models.map((item) => <option key={item.id} value={item.id} />)}</datalist></Field>
          <Field label="接口类型"><select value={draft.api_format} disabled={Boolean(busy)} onChange={(event) => update("api_format", event.target.value)}><option value="openai">通用兼容接口（Gemini / 多模型网关）</option><option value="deepseek">DeepSeek（支持 Thinking 开关）</option></select></Field>
          <Field label="请求超时（秒）"><input type="number" min="5" max="600" value={draft.timeout_seconds} disabled={Boolean(busy)} onChange={(event) => update("timeout_seconds", event.target.value)} /></Field>
          <Field label="TLS 协商" hint="连接超时时可尝试兼容模式，仍校验证书"><select value={draft.tls_mode} disabled={Boolean(busy)} onChange={(event) => update("tls_mode", event.target.value)}><option value="auto">自动协商</option><option value="tls12">TLS 1.2 兼容模式</option></select></Field>
        </div>
        <div className="provider-actions">
          <Button icon={Save} disabled={Boolean(busy)} onClick={save}>{busy === "save" ? "保存中" : "保存供应商"}</Button>
          <Button tone="ghost" icon={RefreshCw} disabled={Boolean(busy) || dirty || !selected?.api_key_configured} onClick={refresh}>{busy === "models" ? "获取中" : "获取全部模型"}</Button>
          <Button tone="ghost" icon={Zap} disabled={Boolean(busy) || dirty || !selected?.api_key_configured} onClick={test}>{busy === "test" ? "测试中" : "测试连接"}</Button>
          <Button tone="secondary" icon={CheckCircle2} disabled={Boolean(busy) || dirty || !selected?.api_key_configured || (selected?.active && data?.provider_managed)} onClick={activate}>{busy === "activate" ? "切换中" : selected?.active && data?.provider_managed ? "已启用" : "启用此供应商"}</Button>
          {selected && <Button tone="danger" icon={Trash2} disabled={Boolean(busy) || selected.active} onClick={erase}>删除</Button>}
        </div>
        <p className="provider-notice" role="status" aria-live="polite">{dirty ? "先保存修改，再获取模型、测试连接或启用。" : notice}</p>
        <div className="provider-catalog__head"><strong>可选模型 <small>{models.length}</small></strong><label className="search-input"><Search size={16} /><input aria-label="搜索供应商模型" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索 Gemini、Flash、Qwen…" /></label></div>
        <div className="provider-catalog">{visibleModels.map((item) => <button type="button" disabled={Boolean(busy)} key={item.id} className={`provider-model ${draft.default_model === item.id ? "is-selected" : ""}`} onClick={() => update("default_model", item.id)}><code>{item.id}</code>{item.vision && <small>识图</small>}{item.test_status === "ok" && <small>已测通</small>}{item.test_status === "error" && <small className="provider-model__error">测试失败</small>}{draft.default_model === item.id && <CheckCircle2 size={14} />}</button>)}</div>
        {!models.length && <p className="quiet-note">保存供应商后点击“获取全部模型”；也可以直接填写模型名称。</p>}
        {models.length > 0 && !visibleModels.length && <p className="quiet-note">没有匹配的模型，请修改搜索词。</p>}
      </div>
    </div>
  </Panel>;
}
