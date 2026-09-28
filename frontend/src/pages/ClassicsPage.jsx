import { useEffect, useState } from "react";
import { Archive, ArrowLeft, Link2, Trash2, Unlink } from "lucide-react";

import { formatBytes, formatDate, get, post, remove } from "../api";
import { Button, Empty, Field, IconButton, PageHeader, Panel, SearchInput, Status } from "../components/Ui";

export default function ClassicsPage({ refreshVersion, onChanged }) {
  const [groups, setGroups] = useState([]);
  const [bindings, setBindings] = useState([]);
  const [masterId, setMasterId] = useState("");
  const [memberId, setMemberId] = useState("");
  const [busy, setBusy] = useState(false);
  const [search, setSearch] = useState("");
  const [active, setActive] = useState(null);
  const [notice, setNotice] = useState("正在读取群典藏");

  const loadGroups = () => Promise.all([
    get(`/classics/groups?search=${encodeURIComponent(search)}&limit=200`),
    get("/classics/bindings"),
  ]).then(([data, bindingData]) => {
    setGroups(data.groups || []); setBindings(bindingData.bindings || []);
    setNotice(`已读取 ${data.groups?.length || 0} 个典藏群`);
  }).catch((error) => setNotice(error.message));
  useEffect(() => { const timer = setTimeout(loadGroups, 180); return () => clearTimeout(timer); }, [search, refreshVersion]);

  const openGroup = async (groupId) => {
    try { const data = await get(`/classics/groups/${groupId}`); setActive(data); setNotice(`群 ${groupId} 已加载`); } catch (error) { setNotice(error.message); }
  };
  const deleteGroup = async (groupId) => {
    if (bindings.some((item) => item.master_group_id === groupId || item.group_ids.includes(groupId))) {
      setNotice("共享典藏请先解散，再删除整群典图。"); return;
    }
    if (!window.confirm(`删除群 ${groupId} 的全部典图？`)) return;
    try { await remove(`/classics/groups/${groupId}`); if (String(active?.group_id) === String(groupId)) setActive(null); loadGroups(); onChanged(); } catch (error) { setNotice(error.message); }
  };
  const deleteImage = async (filename) => {
    if (!active || !window.confirm(`删除图片“${filename}”？绑定群共享时会从所有群删除。`)) return;
    try { await remove(`/classics/groups/${active.group_id}/images/${encodeURIComponent(filename)}`); await openGroup(active.group_id); loadGroups(); onChanged(); } catch (error) { setNotice(error.message); }
  };

  const bindGroup = async () => {
    const master = Number(masterId);
    const member = Number(memberId);
    if (!Number.isSafeInteger(master) || master <= 0 || !Number.isSafeInteger(member) || member <= 0 || master === member) {
      setNotice("请输入两个不同的正整数群号。"); return;
    }
    setBusy(true); setNotice("正在合并副群已有典图，请稍候……");
    try {
      const result = await post("/classics/bindings", { master_group_id: master, group_id: member });
      await loadGroups();
      if (active) await openGroup(active.group_id);
      onChanged();
      setNotice(`群 ${member} 已绑定主群 ${master}，合并 ${result.merged_count} 张，去重 ${result.duplicate_count} 张${result.cleanup_pending ? "；原副群 bucket 清理待重试" : ""}。`);
      setMemberId("");
    } catch (error) { setNotice(error.message); }
    finally { setBusy(false); }
  };

  const dissolveGroup = async (family) => {
    if (!window.confirm(`解散主群 ${family.master_group_id} 的共享典藏？将全量复制到 ${family.group_ids.length} 个副群，校验完成后各群独立。`)) return;
    setBusy(true); setNotice("正在复制并校验每个副群的典图，请稍候……");
    try {
      const result = await remove(`/classics/bindings/${family.master_group_id}`);
      await loadGroups();
      if (active) await openGroup(active.group_id);
      onChanged();
      setNotice(`已解散共享典藏；每群 ${result.images_per_group} 张，共完成 ${result.copied_count} 次复制。`);
    } catch (error) { setNotice(error.message); }
    finally { setBusy(false); }
  };

  return (
    <>
      <PageHeader eyebrow="Image archive" title="群典藏" description="绑定群共享 ~存典、~爆典和 ~爆典all；解散时全量复制为各群独立典藏。" />
      <Panel title="共享典藏" eyebrow="Group bindings" className="classics-binding-panel">
        <div className="form-grid form-grid--2">
          <Field label="主群号"><input type="number" min="1" value={masterId} onChange={(event) => setMasterId(event.target.value)} placeholder="主群 QQ 号" /></Field>
          <Field label="副群号"><input type="number" min="1" value={memberId} onChange={(event) => setMemberId(event.target.value)} placeholder="要绑定的群 QQ 号" /></Field>
        </div>
        <div className="classics-binding-actions"><Button icon={Link2} onClick={bindGroup} disabled={busy}>绑定并合并已有典图</Button></div>
        <div className="classics-binding-list">
          {bindings.length === 0 && <span>当前没有共享典藏。</span>}
          {bindings.map((family) => <div className="classics-binding-row" key={family.master_group_id}>
            <span>主群 {family.master_group_id} → 副群 {family.group_ids.join("、")}</span>
            <Button tone="ghost" icon={Unlink} disabled={busy} onClick={() => dissolveGroup(family)}>解散并全量复制</Button>
          </div>)}
        </div>
      </Panel>
      <div className="classics-layout">
        <Panel className="classics-groups">
          <SearchInput value={search} onChange={setSearch} placeholder="搜索群号" />
          <div className="group-archive-list">
            {!groups.length && <Empty title="还没有群典" description="引用图片发送 ~存典 后会出现在这里。" />}
            {groups.map((group) => (
              <button key={group.group_id} className={String(active?.group_id) === String(group.group_id) ? "is-active" : ""} onClick={() => openGroup(group.group_id)}>
                <span className="group-archive-list__icon"><Archive /></span>
                <span><strong>群 {group.group_id}{group.owner_group_id !== group.group_id ? ` · 共享主群 ${group.owner_group_id}` : ""}</strong><small>{group.count} 张 · {formatBytes(group.total_bytes)}</small></span>
                <IconButton label="删除整群典藏" icon={Trash2} tone="danger" disabled={busy || bindings.some((item) => item.master_group_id === group.group_id || item.group_ids.includes(group.group_id))} onClick={(e) => { e.stopPropagation(); deleteGroup(group.group_id); }} />
              </button>
            ))}
          </div>
          <Status>{notice}</Status>
        </Panel>

        <Panel className="classics-gallery-panel" title={active ? `群 ${active.group_id}${active.owner_group_id !== active.group_id ? ` · 共享主群 ${active.owner_group_id}` : ""}` : "选择一个群"} eyebrow={active ? `${active.count || 0} images` : "Archive viewer"} actions={active && <Button tone="ghost" icon={ArrowLeft} onClick={() => setActive(null)}>返回群列表</Button>}>
          {!active && <Empty title="选择左侧群号" description="典图将在这里以原始比例预览。" />}
          {active && !(active.images || []).length && <Empty title="这个群没有可显示的图片" />}
          {active && <div className="masonry-gallery">
            {(active.images || []).map((item) => (
              <figure key={item.filename}>
                <a href={item.image_url} target="_blank" rel="noreferrer"><img src={item.image_url} alt={item.filename} loading="lazy" /></a>
                <figcaption><div><strong>{item.filename}</strong><small>{formatBytes(item.size)} · {formatDate(item.modified_at)}</small></div><IconButton label="删除图片" icon={Trash2} tone="danger" onClick={() => deleteImage(item.filename)} /></figcaption>
              </figure>
            ))}
          </div>}
        </Panel>
      </div>
    </>
  );
}
