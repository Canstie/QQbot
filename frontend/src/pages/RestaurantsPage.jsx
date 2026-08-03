import { useEffect, useState } from "react";
import { MapPin, Plus, Save, Store, Trash2 } from "lucide-react";

import { get, post, put, remove } from "../api";
import { Button, Empty, Field, IconButton, PageHeader, Panel, SearchInput, Status, Switch } from "../components/Ui";

const blank = { id: "", group_id: "", name: "", dishes: [], enabled: true };

export default function RestaurantsPage({ refreshVersion, onChanged }) {
  const [restaurants, setRestaurants] = useState([]);
  const [search, setSearch] = useState("");
  const [groupFilter, setGroupFilter] = useState("");
  const [editing, setEditing] = useState(blank);
  const [dishes, setDishes] = useState("");
  const [notice, setNotice] = useState("正在读取饭店");

  const load = () => {
    const params = new URLSearchParams({ search, limit: "200" });
    if (groupFilter.trim()) params.set("group_id", groupFilter.trim());
    get(`/restaurants?${params}`).then((data) => { setRestaurants(data.restaurants || []); setNotice(`已读取 ${data.restaurants?.length || 0} 家饭店`); }).catch((error) => setNotice(error.message));
  };
  useEffect(() => { const timer = setTimeout(load, 180); return () => clearTimeout(timer); }, [search, groupFilter, refreshVersion]);

  const edit = (item) => { setEditing(item); setDishes((item.dishes || []).join("\n")); };
  const reset = () => { setEditing(blank); setDishes(""); };
  const save = async () => {
    const payload = { name: editing.name.trim(), group_id: Number(editing.group_id), dishes: dishes.split("\n").map((item) => item.trim()).filter(Boolean), created_by: 0, enabled: editing.enabled };
    try {
      if (editing.id) await put(`/restaurants/${editing.id}`, payload); else await post("/restaurants", payload);
      reset(); load(); onChanged(); setNotice("饭店已保存");
    } catch (error) { setNotice(error.message); }
  };
  const deleteRestaurant = async (item) => {
    if (!window.confirm(`删除饭店“${item.name}”？`)) return;
    try { await remove(`/restaurants/${item.id}`); if (editing.id === item.id) reset(); load(); onChanged(); } catch (error) { setNotice(error.message); }
  };

  return (
    <>
      <PageHeader eyebrow="Group places" title="群饭店名册" description="维护每个群自己的饭店和招牌菜，供“今日饭店”随机抽取。" actions={<Button icon={Plus} onClick={reset}>添加饭店</Button>} />
      <div className="catalog-layout">
        <Panel className="catalog-list">
          <div className="dual-search"><SearchInput value={search} onChange={setSearch} placeholder="搜索饭店或菜名" /><div className="small-search"><MapPin size={16} /><input value={groupFilter} onChange={(e) => setGroupFilter(e.target.value)} placeholder="筛选群号" /></div></div>
          <div className="restaurant-list">
            {!restaurants.length && <Empty title="没有饭店" description="添加群里的第一家饭店。" />}
            {restaurants.map((item) => (
              <article className={`restaurant-card ${editing.id === item.id ? "is-active" : ""}`} key={item.id} onClick={() => edit(item)}>
                <div className="restaurant-card__mark"><Store /></div>
                <div><span>GROUP {item.group_id}</span><strong>{item.name}</strong><p>{(item.dishes || []).join(" · ") || "还没有招牌菜"}</p></div>
                <Status tone={item.enabled ? "ok" : "neutral"}>{item.enabled ? "启用" : "停用"}</Status>
                <IconButton label="删除饭店" icon={Trash2} tone="danger" onClick={(e) => { e.stopPropagation(); deleteRestaurant(item); }} />
              </article>
            ))}
          </div>
        </Panel>

        <Panel title={editing.id ? "编辑饭店" : "添加饭店"} eyebrow="Place editor" className="catalog-editor">
          <Field label="所属群号"><input inputMode="numeric" value={editing.group_id || ""} disabled={Boolean(editing.id)} onChange={(e) => setEditing({ ...editing, group_id: e.target.value })} placeholder="Group ID" /></Field>
          <Field label="饭店名称"><input value={editing.name || ""} onChange={(e) => setEditing({ ...editing, name: e.target.value })} placeholder="例如：楼下小馆" /></Field>
          <Field label="招牌菜" hint="一行一道菜"><textarea value={dishes} onChange={(e) => setDishes(e.target.value)} placeholder="红烧肉\n酸菜鱼" /></Field>
          <Switch checked={Boolean(editing.enabled)} onChange={(enabled) => setEditing({ ...editing, enabled })} label="允许被今日饭店抽到" />
          <Button icon={Save} onClick={save}>{editing.id ? "保存修改" : "创建饭店"}</Button>
          <Status>{notice}</Status>
        </Panel>
      </div>
    </>
  );
}
