import { useEffect, useState } from "react";
import { ChefHat, ImagePlus, Plus, Save, Sparkles, Trash2 } from "lucide-react";

import { fileToDataUrl, get, post, put, remove } from "../api";
import { Button, Empty, Field, IconButton, PageHeader, Panel, SearchInput, Status, Switch } from "../components/Ui";

const blank = { id: "", title: "", enabled: true, image_url: "" };

export default function MenusPage({ refreshVersion, onChanged }) {
  const [menus, setMenus] = useState([]);
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState(blank);
  const [file, setFile] = useState(null);
  const [notice, setNotice] = useState("正在读取菜单");

  const load = () => get(`/menus?search=${encodeURIComponent(search)}&limit=200`).then((data) => {
    setMenus(data.menus || []); setNotice(`已读取 ${data.menus?.length || 0} 个菜单`);
  }).catch((error) => setNotice(error.message));
  useEffect(() => { const timer = setTimeout(load, 180); return () => clearTimeout(timer); }, [search, refreshVersion]);

  const edit = (menu) => { setEditing(menu); setFile(null); };
  const reset = () => { setEditing(blank); setFile(null); };
  const save = async () => {
    if (!editing.title.trim()) { setNotice("先填写菜名"); return; }
    try {
      const payload = { title: editing.title.trim(), enabled: editing.enabled, image_data_url: await fileToDataUrl(file) };
      if (editing.id) await put(`/menus/${encodeURIComponent(editing.id)}`, payload);
      else await post("/menus", payload);
      reset(); load(); onChanged(); setNotice("菜单已保存");
    } catch (error) { setNotice(error.message); }
  };
  const deleteMenu = async (menu) => {
    if (!window.confirm(`删除菜单“${menu.title}”？`)) return;
    try { await remove(`/menus/${encodeURIComponent(menu.id)}`); if (editing.id === menu.id) reset(); load(); onChanged(); } catch (error) { setNotice(error.message); }
  };
  const prune = async () => {
    if (!window.confirm("清理全部无图 HowToCook 菜单？")) return;
    try { const data = await post("/menus/prune-howtocook-without-images", {}); load(); onChanged(); setNotice(`已清理 ${data.deleted} 条菜单`); } catch (error) { setNotice(error.message); }
  };

  const preview = file ? URL.createObjectURL(file) : editing.image_url;

  return (
    <>
      <PageHeader eyebrow="Food library" title="今日菜单库" description="浏览、搜索和维护群内随机菜单。图片会随条目保存到服务器。" actions={<><Button tone="secondary" icon={Sparkles} onClick={prune}>清理无图数据</Button><Button icon={Plus} onClick={reset}>新建菜单</Button></>} />
      <div className="catalog-layout">
        <Panel className="catalog-list">
          <div className="catalog-toolbar"><SearchInput value={search} onChange={setSearch} placeholder="搜索菜名、分类或标签" /><span>{menus.length} 条</span></div>
          <div className="menu-grid">
            {!menus.length && <Empty title="没有菜单" description="新建一条菜单，或换一个搜索词。" />}
            {menus.map((menu) => (
              <article className={`menu-card ${editing.id === menu.id ? "is-active" : ""}`} key={menu.id} onClick={() => edit(menu)}>
                <div className="menu-card__image">{menu.image_url ? <img src={menu.image_url} alt="" loading="lazy" /> : <ChefHat />}</div>
                <div className="menu-card__copy"><span>{menu.source || "local"}</span><strong>{menu.title}</strong><small>{menu.category || "未分类"} · {menu.enabled ? "启用" : "停用"}</small></div>
                <IconButton label="删除菜单" icon={Trash2} tone="danger" onClick={(e) => { e.stopPropagation(); deleteMenu(menu); }} />
              </article>
            ))}
          </div>
        </Panel>

        <Panel title={editing.id ? "编辑菜单" : "新建菜单"} eyebrow="Recipe editor" className="catalog-editor">
          <div className={`image-drop ${preview ? "has-image" : ""}`}>
            {preview ? <img src={preview} alt="预览" /> : <><ImagePlus /><span>选择菜品图片</span></>}
            <input type="file" accept="image/*" onChange={(e) => setFile(e.target.files?.[0] || null)} />
          </div>
          <Field label="菜名"><input value={editing.title || ""} onChange={(e) => setEditing({ ...editing, title: e.target.value })} placeholder="例如：番茄炒蛋" /></Field>
          <Switch checked={Boolean(editing.enabled)} onChange={(enabled) => setEditing({ ...editing, enabled })} label="允许被今日菜单抽到" />
          <Button icon={Save} onClick={save}>{editing.id ? "保存修改" : "创建菜单"}</Button>
          <Status>{notice}</Status>
        </Panel>
      </div>
    </>
  );
}
