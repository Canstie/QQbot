import { useEffect, useState } from "react";
import { BookOpen, BrainCircuit, CheckCircle2, Eraser, ImagePlus, Plus, Save, Sparkles, Trash2, Upload, X } from "lucide-react";

import { fileToDataUrl, formatBytes, formatIds, get, parseIds, post, put, remove } from "../api";
import { Button, Empty, Field, IconButton, Metric, PageHeader, Panel, Status, Switch } from "../components/Ui";

const knowledgeDraftFrom = (knowledge = {}, defaults = {}) => ({
  name: knowledge.name || "",
  prompt: knowledge.prompt || "",
  model: knowledge.model || defaults.model || defaults.default_model || "deepseek-v4-flash",
  thinking_enabled: knowledge.thinking_enabled ?? false,
  max_tokens: knowledge.max_tokens ?? defaults.max_tokens ?? defaults.default_max_tokens ?? 80,
  temperature: knowledge.temperature ?? "",
});

const knowledgePayload = (draft) => ({
  ...draft,
  max_tokens: Number(draft.max_tokens),
  temperature: draft.temperature === "" ? null : Number(draft.temperature),
});

export default function AiPage({ refreshVersion, onChanged }) {
  const [data, setData] = useState(null);
  const [form, setForm] = useState({ enabledGroups: "", turns: 2, randomPercent: 2, stickerPercent: 20, aiEnabled: true, knowledgeEnabled: false, clear: true });
  const [selectedKnowledgeId, setSelectedKnowledgeId] = useState(null);
  const [knowledgeDraft, setKnowledgeDraft] = useState(knowledgeDraftFrom());
  const [createDraft, setCreateDraft] = useState(knowledgeDraftFrom());
  const [createOpen, setCreateOpen] = useState(false);
  const [notice, setNotice] = useState("正在读取 AI 配置");
  const [stickers, setStickers] = useState([]);
  const [stickerFile, setStickerFile] = useState(null);

  const applyConfig = (result, preferredId = null) => {
    setData(result);
    setForm((current) => ({ enabledGroups: formatIds(result.enabled_groups), turns: result.history_turns, randomPercent: result.random_reply_percent ?? 2, stickerPercent: result.random_sticker_percent ?? 20, aiEnabled: result.enabled ?? true, knowledgeEnabled: result.knowledge_enabled, clear: current.clear ?? true }));
    const bases = result.knowledge_bases || [];
    const nextId = preferredId ?? result.active_knowledge_id ?? bases[0]?.id ?? null;
    const selected = bases.find((item) => item.id === nextId) || bases[0] || null;
    setSelectedKnowledgeId(selected?.id ?? null);
    setKnowledgeDraft(knowledgeDraftFrom(selected || {}, result));
  };
  const load = () => get("/dsapi").then((result) => {
    applyConfig(result);
    setNotice("AI 配置已同步");
  }).catch((error) => setNotice(error.message));
  const loadStickers = () => get("/stickers").then((result) => {
    setStickers(result.stickers || []);
  }).catch((error) => setNotice(error.message));

  useEffect(() => {
    void load();
    void loadStickers();
  }, [refreshVersion]);
  useEffect(() => {
    if (!createOpen) return undefined;
    const closeOnEscape = (event) => {
      if (event.key === "Escape") setCreateOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [createOpen]);
  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }));

  const selectKnowledge = (knowledge) => {
    setSelectedKnowledgeId(knowledge.id);
    setKnowledgeDraft(knowledgeDraftFrom(knowledge, data || {}));
  };

  const saveKnowledge = async () => {
    if (!selectedKnowledgeId) throw new Error("请先新建知识库");
    const result = await put(`/dsapi/knowledge/${selectedKnowledgeId}`, knowledgePayload(knowledgeDraft));
    applyConfig(result, selectedKnowledgeId);
    return result;
  };

  const save = async () => {
    try {
      if (selectedKnowledgeId) await saveKnowledge();
      const result = await post("/dsapi", { enabled: form.aiEnabled, enabled_groups: parseIds(form.enabledGroups), history_turns: Number(form.turns), random_reply_percent: Number(form.randomPercent), random_sticker_percent: Number(form.stickerPercent), knowledge_enabled: form.knowledgeEnabled, active_knowledge_id: selectedKnowledgeId, clear_history: form.clear });
      applyConfig(result, selectedKnowledgeId);
      setNotice("AI 配置已保存，选中的知识库已生效");
      setForm((current) => ({ ...current, clear: false }));
      onChanged();
    } catch (error) { setNotice(error.message); }
  };

  const openCreateKnowledge = () => {
    const names = new Set((data?.knowledge_bases || []).map((item) => item.name));
    let index = (data?.knowledge_bases?.length || 0) + 1;
    while (names.has(`新知识库 ${index}`)) index += 1;
    setCreateDraft(knowledgeDraftFrom({ name: `新知识库 ${index}` }, data || {}));
    setCreateOpen(true);
  };

  const createKnowledge = async () => {
    try {
      const result = await post("/dsapi/knowledge", knowledgePayload(createDraft));
      applyConfig(result, result.knowledge_base.id);
      setCreateOpen(false);
      setNotice("知识库及 DSAPI 参数已创建，可切换启用");
      onChanged();
    } catch (error) { setNotice(error.message); }
  };

  const persistKnowledge = async () => {
    try {
      await saveKnowledge();
      setNotice("知识库内容已保存");
      onChanged();
    } catch (error) { setNotice(error.message); }
  };

  const activateKnowledge = async () => {
    if (!selectedKnowledgeId) { setNotice("请先选择知识库"); return; }
    try {
      await saveKnowledge();
      const result = await post(`/dsapi/knowledge/${selectedKnowledgeId}/activate`, { clear_history: form.clear });
      applyConfig(result, selectedKnowledgeId);
      setNotice(`已切换到“${result.active_knowledge_name}”`);
      setForm((current) => ({ ...current, clear: false }));
      onChanged();
    } catch (error) { setNotice(error.message); }
  };

  const deleteKnowledge = async () => {
    if (!selectedKnowledgeId || !window.confirm(`删除知识库“${knowledgeDraft.name}”？`)) return;
    try {
      const result = await remove(`/dsapi/knowledge/${selectedKnowledgeId}`);
      applyConfig(result);
      setNotice("知识库已删除");
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

  const uploadSticker = async () => {
    if (!stickerFile) { setNotice("请先选择表情包图片"); return; }
    try {
      await post("/stickers", {
        filename: stickerFile.name,
        image_data_url: await fileToDataUrl(stickerFile),
      });
      setStickerFile(null);
      await loadStickers();
      setNotice("表情包已上传并加入随机插话库");
    } catch (error) { setNotice(error.message); }
  };

  const deleteSticker = async (filename) => {
    if (!window.confirm(`删除表情包“${filename}”？`)) return;
    try {
      await remove(`/stickers/${encodeURIComponent(filename)}`);
      await loadStickers();
      setNotice("表情包已删除");
    } catch (error) { setNotice(error.message); }
  };

  return (
    <>
      <PageHeader eyebrow="Model memory" title="AI 角色与知识" description="维护多个角色知识库，随时切换当前设定，并控制每个群的短期对话。" actions={<Button icon={Save} onClick={save}>保存并生效</Button>} />
      <div className="ai-status-strip">
        <div className="ai-status-strip__model"><BrainCircuit /><div><span>当前模型</span><strong>{data?.model || "读取中"}</strong><small>{data?.thinking_enabled ? "Thinking" : "Non-thinking"} · {data?.max_tokens || "-"} tokens · {data?.base_url || "-"}</small></div></div>
        <Metric label="AI 启用群" value={data?.enabled_groups?.length ?? "-"} tone="blue" />
        <Metric label="上下文消息" value={data?.history_messages ?? "-"} tone="mint" />
        <Metric label="涉及群" value={data?.history_groups ?? "-"} tone="orange" />
        <Status tone={!data?.api_configured ? "error" : data?.enabled ? "ok" : "neutral"}>AI {!data?.api_configured ? "缺少密钥" : data?.enabled ? "已启用" : "已关闭"}</Status>
      </div>

      <div className="content-grid">
        <Panel title="角色知识库" eyebrow="Knowledge library" className="span-8" actions={<Button tone="secondary" icon={Plus} onClick={openCreateKnowledge}>新建</Button>}>
          <div className="knowledge-toolbar"><Switch checked={form.knowledgeEnabled} onChange={(value) => update("knowledgeEnabled", value)} label="挂载角色知识" description="关闭后只使用基础系统提示词" /><span><Sparkles size={15} /> {(data?.knowledge_bases?.length || 0)} 个知识库</span></div>
          <div className="knowledge-workspace">
            <aside className="knowledge-library">
              {(data?.knowledge_bases || []).map((item) => (
                <button type="button" key={item.id} className={`knowledge-card ${selectedKnowledgeId === item.id ? "is-selected" : ""} ${item.active ? "is-active" : ""}`} onClick={() => selectKnowledge(item)}>
                  <BookOpen size={17} />
                  <span><strong>{item.name}</strong><small>{item.model} · {item.thinking_enabled ? "Thinking" : "Fast"} · {item.prompt_chars.toLocaleString()} 字符</small></span>
                  {item.active && <b><CheckCircle2 size={12} />当前</b>}
                </button>
              ))}
              {!data?.knowledge_bases?.length && <Empty title="还没有知识库" description="新建后可分别保存不同角色与资料。" action={<Button tone="secondary" icon={Plus} onClick={openCreateKnowledge}>新建第一个</Button>} />}
            </aside>
            <div className="knowledge-detail">
              {selectedKnowledgeId ? <>
                <div className="knowledge-detail__head">
                  <Field label="知识库名称"><input value={knowledgeDraft.name} maxLength={80} onChange={(event) => setKnowledgeDraft((current) => ({ ...current, name: event.target.value }))} /></Field>
                  <span><Sparkles size={14} /> {knowledgeDraft.prompt.length.toLocaleString()} 字符</span>
                </div>
                <div className="knowledge-runtime-grid">
                  <Field label="模型"><input value={knowledgeDraft.model} maxLength={200} onChange={(event) => setKnowledgeDraft((current) => ({ ...current, model: event.target.value }))} /></Field>
                  <Field label="最大输出 Token"><input type="number" min="1" max="32768" value={knowledgeDraft.max_tokens} onChange={(event) => setKnowledgeDraft((current) => ({ ...current, max_tokens: event.target.value }))} /></Field>
                  <Field label="Temperature" hint="留空使用服务商默认值"><input type="number" min="0" max="2" step="0.1" value={knowledgeDraft.temperature} onChange={(event) => setKnowledgeDraft((current) => ({ ...current, temperature: event.target.value }))} /></Field>
                  <Switch checked={knowledgeDraft.thinking_enabled} onChange={(value) => setKnowledgeDraft((current) => ({ ...current, thinking_enabled: value }))} label="Thinking 模式" description={knowledgeDraft.thinking_enabled ? "请求模型进行思考" : "Non-thinking，直接简短回复"} />
                </div>
                <Field label="知识与角色设定" hint="写清身份、语气、世界观、人物关系、事实边界和禁止事项。">
                  <textarea className="knowledge-editor" value={knowledgeDraft.prompt} onChange={(event) => setKnowledgeDraft((current) => ({ ...current, prompt: event.target.value }))} placeholder="例如：你是群里的档案管理员。回答时使用简洁中文，只依据下方档案中的事实……" />
                </Field>
                <div className="knowledge-actions">
                  <Button tone="danger" icon={Trash2} onClick={deleteKnowledge}>删除</Button>
                  <Button tone="ghost" icon={Save} onClick={persistKnowledge}>保存知识库</Button>
                  <Button icon={CheckCircle2} onClick={activateKnowledge}>{data?.active_knowledge_id === selectedKnowledgeId ? "保存并保持启用" : "切换到此知识库"}</Button>
                </div>
              </> : <Empty title="选择一个知识库" description="从左侧选择，或创建新的角色知识库。" />}
            </div>
          </div>
        </Panel>

        <div className="stack span-4">
          <Panel title="启用范围" eyebrow="AI groups">
            <Switch checked={form.aiEnabled} onChange={(value) => update("aiEnabled", value)} label="启用 AI 功能" description="关闭后停止 @bot、随机文字和随机表情包回复" />
            <Field label="允许调用 AI 的群" hint="独立于总体 Bot 启用群，一行一个群号。"><textarea value={form.enabledGroups} onChange={(e) => update("enabledGroups", e.target.value)} /></Field>
          </Panel>
          <Panel title="短期记忆" eyebrow="Context window">
            <Field label="每群保留轮数"><div className="range-value"><input type="range" min="1" max="20" value={form.turns} onChange={(e) => update("turns", e.target.value)} /><strong>{form.turns} 轮</strong></div></Field>
            <Field label="随机插话概率" hint="仅监听 AI 启用群的普通纯文本消息；0% 表示关闭。"><div className="range-value"><input type="range" min="0" max="100" step="0.5" value={form.randomPercent} onChange={(e) => update("randomPercent", e.target.value)} /><strong>{form.randomPercent}%</strong></div></Field>
            <Field label="表情包占插话比例" hint="从独立表情包库随机选择；无图时自动回退为文字。"><div className="range-value"><input type="range" min="0" max="100" step="1" value={form.stickerPercent} onChange={(e) => update("stickerPercent", e.target.value)} /><strong>{form.stickerPercent}%</strong></div></Field>
            <Switch checked={form.clear} onChange={(value) => update("clear", value)} label="保存时清空旧上下文" description="切换角色时建议开启" />
            <p className="quiet-note">随机插话会读取当前消息之前最多 10 句群聊；连续 {Math.round((data?.history_idle_seconds || 1200) / 60)} 分钟无人对话后自动清空。</p>
            <Button tone="danger" icon={Eraser} onClick={clearHistory}>立即清空全部上下文</Button>
          </Panel>
        </div>
        <Panel title="随机表情包库" eyebrow="Sticker pool" className="span-12" actions={<Button icon={Upload} onClick={uploadSticker}>上传表情包</Button>}>
          <div className="sticker-upload">
            <label className="image-drop">
              <ImagePlus /><span>{stickerFile ? stickerFile.name : "选择 JPG、PNG、GIF 或 WebP，最大 10 MB"}</span>
              <input type="file" accept="image/jpeg,image/png,image/gif,image/webp" onChange={(e) => setStickerFile(e.target.files?.[0] || null)} />
            </label>
            <span className="quiet-note">共 {stickers.length} 张；随机插话不会读取群典藏图片。</span>
          </div>
          {!stickers.length && <Empty title="表情包库为空" description="上传图片后，随机插话才可能发送表情包。" />}
          <div className="sticker-grid">
            {stickers.map((item) => (
              <figure key={item.filename}>
                <img src={item.image_url} alt={item.filename} loading="lazy" />
                <figcaption><span>{formatBytes(item.size)}</span><IconButton label="删除表情包" icon={Trash2} tone="danger" onClick={() => deleteSticker(item.filename)} /></figcaption>
              </figure>
            ))}
          </div>
        </Panel>
        <div className="span-12 panel-footer panel-footer--standalone"><Status>{notice}</Status><span className="quiet-note">多模态消息仍会在本地直接丢弃，不调用 API</span></div>
      </div>
      {createOpen && (
        <div className="modal-scrim" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setCreateOpen(false); }}>
          <section className="knowledge-modal" role="dialog" aria-modal="true" aria-labelledby="create-knowledge-title">
            <header className="knowledge-modal__head">
              <div><span>New knowledge profile</span><h2 id="create-knowledge-title">新建知识库</h2><p>角色知识与模型参数将绑定保存，切换知识库时同步生效。</p></div>
              <IconButton label="关闭" icon={X} onClick={() => setCreateOpen(false)} />
            </header>
            <div className="knowledge-modal__body">
              <div className="form-grid form-grid--2">
                <Field label="知识库名称"><input autoFocus value={createDraft.name} maxLength={80} onChange={(event) => setCreateDraft((current) => ({ ...current, name: event.target.value }))} /></Field>
                <Field label="模型"><input value={createDraft.model} maxLength={200} onChange={(event) => setCreateDraft((current) => ({ ...current, model: event.target.value }))} /></Field>
                <Field label="最大输出 Token"><input type="number" min="1" max="32768" value={createDraft.max_tokens} onChange={(event) => setCreateDraft((current) => ({ ...current, max_tokens: event.target.value }))} /></Field>
                <Field label="Temperature" hint="留空使用服务商默认值"><input type="number" min="0" max="2" step="0.1" value={createDraft.temperature} onChange={(event) => setCreateDraft((current) => ({ ...current, temperature: event.target.value }))} /></Field>
              </div>
              <Switch checked={createDraft.thinking_enabled} onChange={(value) => setCreateDraft((current) => ({ ...current, thinking_enabled: value }))} label="开启 Thinking 模式" description={createDraft.thinking_enabled ? "随该知识库启用深度思考" : "保持 Non-thinking，优先快速短回复"} />
              <Field label="知识与角色设定" hint="可留空，创建后仍可继续编辑。"><textarea value={createDraft.prompt} onChange={(event) => setCreateDraft((current) => ({ ...current, prompt: event.target.value }))} placeholder="输入身份、语气、世界观、人物关系与事实资料……" /></Field>
            </div>
            <footer className="knowledge-modal__actions"><Button tone="ghost" onClick={() => setCreateOpen(false)}>取消</Button><Button icon={Plus} onClick={createKnowledge}>创建知识库</Button></footer>
          </section>
        </div>
      )}
    </>
  );
}
