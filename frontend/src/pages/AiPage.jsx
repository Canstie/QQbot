import { useEffect, useState } from "react";
import { BrainCircuit, Eraser, ImagePlus, Save, Sparkles, Trash2, Upload } from "lucide-react";

import { fileToDataUrl, formatBytes, formatIds, get, parseIds, post, remove } from "../api";
import { Button, Empty, Field, IconButton, Metric, PageHeader, Panel, Status, Switch } from "../components/Ui";

export default function AiPage({ refreshVersion, onChanged }) {
  const [data, setData] = useState(null);
  const [form, setForm] = useState({ enabledGroups: "", turns: 2, randomPercent: 2, stickerPercent: 20, enabled: false, prompt: "", clear: true });
  const [notice, setNotice] = useState("正在读取 AI 配置");
  const [stickers, setStickers] = useState([]);
  const [stickerFile, setStickerFile] = useState(null);

  const load = () => get("/dsapi").then((result) => {
    setData(result);
    setForm({ enabledGroups: formatIds(result.enabled_groups), turns: result.history_turns, randomPercent: result.random_reply_percent ?? 2, stickerPercent: result.random_sticker_percent ?? 20, enabled: result.knowledge_enabled, prompt: result.knowledge_prompt || "", clear: true });
    setNotice("AI 配置已同步");
  }).catch((error) => setNotice(error.message));
  const loadStickers = () => get("/stickers").then((result) => {
    setStickers(result.stickers || []);
  }).catch((error) => setNotice(error.message));

  useEffect(() => {
    void load();
    void loadStickers();
  }, [refreshVersion]);
  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }));

  const save = async () => {
    try {
      const result = await post("/dsapi", { enabled_groups: parseIds(form.enabledGroups), history_turns: Number(form.turns), random_reply_percent: Number(form.randomPercent), random_sticker_percent: Number(form.stickerPercent), knowledge_enabled: form.enabled, knowledge_prompt: form.prompt, clear_history: form.clear });
      setData(result);
      setNotice("AI 配置已保存，下一条群消息立即使用新设定");
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
            <Field label="随机插话概率" hint="仅监听 AI 启用群的普通纯文本消息；0% 表示关闭。"><div className="range-value"><input type="range" min="0" max="100" step="0.5" value={form.randomPercent} onChange={(e) => update("randomPercent", e.target.value)} /><strong>{form.randomPercent}%</strong></div></Field>
            <Field label="表情包占插话比例" hint="从独立表情包库随机选择；无图时自动回退为文字。"><div className="range-value"><input type="range" min="0" max="100" step="1" value={form.stickerPercent} onChange={(e) => update("stickerPercent", e.target.value)} /><strong>{form.stickerPercent}%</strong></div></Field>
            <Switch checked={form.clear} onChange={(value) => update("clear", value)} label="保存时清空旧上下文" description="切换角色时建议开启" />
            <p className="quiet-note">连续 {Math.round((data?.history_idle_seconds || 1200) / 60)} 分钟无人对话后，该群上下文自动清空。</p>
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
    </>
  );
}
