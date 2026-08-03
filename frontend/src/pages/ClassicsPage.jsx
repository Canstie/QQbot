import { useEffect, useState } from "react";
import { Archive, ArrowLeft, Trash2 } from "lucide-react";

import { formatBytes, formatDate, get, remove } from "../api";
import { Button, Empty, IconButton, PageHeader, Panel, SearchInput, Status } from "../components/Ui";

export default function ClassicsPage({ refreshVersion, onChanged }) {
  const [groups, setGroups] = useState([]);
  const [search, setSearch] = useState("");
  const [active, setActive] = useState(null);
  const [notice, setNotice] = useState("正在读取群典藏");

  const loadGroups = () => get(`/classics/groups?search=${encodeURIComponent(search)}&limit=200`).then((data) => {
    setGroups(data.groups || []); setNotice(`已读取 ${data.groups?.length || 0} 个典藏群`);
  }).catch((error) => setNotice(error.message));
  useEffect(() => { const timer = setTimeout(loadGroups, 180); return () => clearTimeout(timer); }, [search, refreshVersion]);

  const openGroup = async (groupId) => {
    try { const data = await get(`/classics/groups/${groupId}`); setActive(data); setNotice(`群 ${groupId} 已加载`); } catch (error) { setNotice(error.message); }
  };
  const deleteGroup = async (groupId) => {
    if (!window.confirm(`删除群 ${groupId} 的全部典图？`)) return;
    try { await remove(`/classics/groups/${groupId}`); if (String(active?.group_id) === String(groupId)) setActive(null); loadGroups(); onChanged(); } catch (error) { setNotice(error.message); }
  };
  const deleteImage = async (filename) => {
    if (!active || !window.confirm(`删除图片“${filename}”？`)) return;
    try { await remove(`/classics/groups/${active.group_id}/images/${encodeURIComponent(filename)}`); await openGroup(active.group_id); loadGroups(); onChanged(); } catch (error) { setNotice(error.message); }
  };

  return (
    <>
      <PageHeader eyebrow="Image archive" title="群典藏" description="按群浏览通过“~存典”保存的图片，支持查看原图和精确删除。" />
      <div className="classics-layout">
        <Panel className="classics-groups">
          <SearchInput value={search} onChange={setSearch} placeholder="搜索群号" />
          <div className="group-archive-list">
            {!groups.length && <Empty title="还没有群典" description="引用图片发送 ~存典 后会出现在这里。" />}
            {groups.map((group) => (
              <button key={group.group_id} className={String(active?.group_id) === String(group.group_id) ? "is-active" : ""} onClick={() => openGroup(group.group_id)}>
                <span className="group-archive-list__icon"><Archive /></span>
                <span><strong>群 {group.group_id}</strong><small>{group.count} 张 · {formatBytes(group.total_bytes)}</small></span>
                <IconButton label="删除整群典藏" icon={Trash2} tone="danger" onClick={(e) => { e.stopPropagation(); deleteGroup(group.group_id); }} />
              </button>
            ))}
          </div>
          <Status>{notice}</Status>
        </Panel>

        <Panel className="classics-gallery-panel" title={active ? `群 ${active.group_id}` : "选择一个群"} eyebrow={active ? `${active.images?.length || 0} images` : "Archive viewer"} actions={active && <Button tone="ghost" icon={ArrowLeft} onClick={() => setActive(null)}>返回群列表</Button>}>
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
