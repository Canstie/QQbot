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

function RouteSelector({ title, hint, route, value, providers, busy, onSave }) {
  const [providerId, setProviderId] = useState(value?.provider_id || "");
  const [model, setModel] = useState(value?.model || "");
  useEffect(() => {
    setProviderId(value?.provider_id || "");
    setModel(value?.model || "");
  }, [value?.provider_id, value?.model]);
  const provider = providers.find((item) => item.id === Number(providerId));
  const saved = providerId === value?.provider_id && model === value?.model;
  return <div className="provider-chain">
    <div className="provider-chain__heading"><Network size={18} /><strong>{title}</strong><Status tone={value ? "ok" : "neutral"}>{value ? "已配置" : "暂随消息链路"}</Status></div>
    <p className="quiet-note">{hint}</p>
    <div className="provider-chain__fields">
      <Field label={`${title}供应商`}><select value={providerId} disabled={Boolean(busy)} onChange={(event) => {
        const id = Number(event.target.value);
        setProviderId(id);
        setModel(providers.find((item) => item.id === id)?.default_model || "");
      }}><option value="" disabled>选择供应商</option>{providers.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>
      <Field label={`${title}模型`}><input list={`${route}-model-catalog`} value={model} disabled={Boolean(busy)} onChange={(event) => setModel(event.target.value)} placeholder="选择或输入模型名称" /><datalist id={`${route}-model-catalog`}>{provider?.models.map((item) => <option key={item.id} value={item.id} />)}</datalist></Field>
      <Button icon={Save} disabled={Boolean(busy) || !provider?.api_key_configured || !model.trim() || saved} onClick={() => onSave(route, Number(providerId), model)}>{busy === route ? "保存中" : saved ? "已保存" : `保存${title}`}</Button>
    </div>
  </div>;
}

export default function AiProvidersPanel({ data, onChanged, onProxyChanged }) {
  const [selectedId, setSelectedId] = useState(null);
  const [draft, setDraft] = useState(draftFrom());
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(null);
  const [notice, setNotice] = useState("选择供应商，或添加新的接口。");
  const [proxyNotice, setProxyNotice] = useState("");
  const [routeNotice, setRouteNotice] = useState("");
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
    catch (error) {
      if (action === "message" || action === "background") setRouteNotice(error.message);
      else setNotice(error.message);
    }
    finally { setBusy(null); }
  };
  const save = () => run("save", async () => {
    const payload = { ...draft, timeout_seconds: Number(draft.timeout_seconds), api_key: draft.api_key || null };
    const result = selectedId ? await put(`/dsapi/providers/${selectedId}`, payload) : await post("/dsapi/providers", payload);
    setDraft((current) => ({ ...current, api_key: "" }));
    if (result.provider_id) setSelectedId(result.provider_id);
    await onChanged();
    setNotice("供应商已保存，可获取模型、测试连接或在上方选择链路。");
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
  const saveRoute = (route, providerId, model) => run(route, async () => {
    setRouteNotice("");
    await put(`/dsapi/routes/${route}`, { provider_id: providerId, model });
    await onChanged();
    setRouteNotice(`${route === "message" ? "消息" : "后台"}链路已保存，后续请求使用 ${providers.find((item) => item.id === providerId)?.name} / ${model}。`);
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

  return <Panel title="AI 链路与供应商" eyebrow="AI routes" className="ai-providers" actions={
    <Button tone="secondary" icon={Plus} disabled={Boolean(busy)} onClick={() => {
      setSelectedId(null); setDraft(draftFrom()); setSearch(""); setNotice("填写接口和密钥，保存后获取模型");
    }}>添加供应商</Button>
  }>
    <RouteSelector title="消息链路" hint="@ 回复、识图、随机插话、人物评价、群聊总结和海龟汤；联网规划、核验和回答也使用此链路。" route="message" value={data?.active_provider_id ? { provider_id: data.active_provider_id, model: data.model } : null} providers={providers} busy={busy} onSave={saveRoute} />
    <RouteSelector title="后台链路" hint="知识图谱提取、关系记忆整理。独立保存供应商和模型，切换消息链路不会改变这里。" route="background" value={data?.background_route} providers={providers} busy={busy} onSave={saveRoute} />
    {routeNotice && <p className="provider-notice" role="status" aria-live="polite">{routeNotice}</p>}
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
          <small>{item.models.length} 个模型 · {item.api_key_configured ? "密钥已保存" : "待配置密钥"}{item.active ? " · 消息链路" : ""}{item.id === data?.background_route?.provider_id ? " · 后台链路" : ""}</small>
        </button>)}
      </div>
      <div className="provider-editor">
        <div className="form-grid form-grid--2">
          <Field label="供应商名称"><input value={draft.name} maxLength={80} disabled={Boolean(busy)} onChange={(event) => update("name", event.target.value)} placeholder="例如：Miku / DeepSeek" /></Field>
          <Field label="接口地址" hint="支持填写域名或 /v1 地址"><input value={draft.base_url} disabled={Boolean(busy)} onChange={(event) => update("base_url", event.target.value)} placeholder="https://api.example.com/v1" /></Field>
          <Field label="API Key" hint={selected?.api_key_configured ? "密钥已保存，留空保留原值" : "密钥仅保存在服务器"}><input type="password" autoComplete="new-password" value={draft.api_key} disabled={Boolean(busy)} onChange={(event) => update("api_key", event.target.value)} placeholder={selected?.api_key_configured ? "已保存 · 留空不修改" : "填写 API Key"} /></Field>
          <Field label="供应商默认模型" hint="用于连接测试；保存当前消息供应商时同步更新消息模型，后台模型独立保存。"><input list="provider-model-catalog" value={draft.default_model} disabled={Boolean(busy)} onChange={(event) => update("default_model", event.target.value)} placeholder="模型完整名称" /><datalist id="provider-model-catalog">{models.map((item) => <option key={item.id} value={item.id} />)}</datalist></Field>
          <Field label="接口类型"><select value={draft.api_format} disabled={Boolean(busy)} onChange={(event) => update("api_format", event.target.value)}><option value="openai">通用兼容接口（Gemini / 多模型网关）</option><option value="deepseek">DeepSeek（支持 Thinking 开关）</option></select></Field>
          <Field label="请求超时（秒）"><input type="number" min="5" max="600" value={draft.timeout_seconds} disabled={Boolean(busy)} onChange={(event) => update("timeout_seconds", event.target.value)} /></Field>
          <Field label="TLS 协商" hint="连接超时时可尝试兼容模式，仍校验证书"><select value={draft.tls_mode} disabled={Boolean(busy)} onChange={(event) => update("tls_mode", event.target.value)}><option value="auto">自动协商</option><option value="tls12">TLS 1.2 兼容模式</option></select></Field>
        </div>
        <div className="provider-actions">
          <Button icon={Save} disabled={Boolean(busy)} onClick={save}>{busy === "save" ? "保存中" : "保存供应商"}</Button>
          <Button tone="ghost" icon={RefreshCw} disabled={Boolean(busy) || dirty || !selected?.api_key_configured} onClick={refresh}>{busy === "models" ? "获取中" : "获取全部模型"}</Button>
          <Button tone="ghost" icon={Zap} disabled={Boolean(busy) || dirty || !selected?.api_key_configured} onClick={test}>{busy === "test" ? "测试中" : "测试连接"}</Button>
          {selected && <Button tone="danger" icon={Trash2} disabled={Boolean(busy) || selected.active || selected.id === data?.background_route?.provider_id} onClick={erase}>删除</Button>}
        </div>
        <p className="provider-notice" role="status" aria-live="polite">{dirty ? "先保存供应商修改，再获取模型或测试连接。" : notice}</p>
        <div className="provider-catalog__head"><strong>可选模型 <small>{models.length}</small></strong><label className="search-input"><Search size={16} /><input aria-label="搜索供应商模型" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索 Gemini、Flash、Qwen…" /></label></div>
        <div className="provider-catalog">{visibleModels.map((item) => <button type="button" disabled={Boolean(busy)} key={item.id} className={`provider-model ${draft.default_model === item.id ? "is-selected" : ""}`} onClick={() => update("default_model", item.id)}><code>{item.id}</code>{item.vision && <small>识图</small>}{item.test_status === "ok" && <small>已测通</small>}{item.test_status === "error" && <small className="provider-model__error">测试失败</small>}{draft.default_model === item.id && <CheckCircle2 size={14} />}</button>)}</div>
        {!models.length && <p className="quiet-note">保存供应商后点击“获取全部模型”；也可以直接填写模型名称。</p>}
        {models.length > 0 && !visibleModels.length && <p className="quiet-note">没有匹配的模型，请修改搜索词。</p>}
      </div>
    </div>
  </Panel>;
}
