import { useEffect, useRef, useState } from "react";
import { FileCode2, FileUp, Plus, Save, Trash2 } from "lucide-react";

import { get, post, remove } from "../api";
import { Button, Empty, Field, PageHeader, Panel, SearchInput, Status } from "../components/Ui";

export default function LuaPage({ refreshVersion, onChanged }) {
  const [commands, setCommands] = useState([]);
  const [current, setCurrent] = useState("");
  const [editor, setEditor] = useState("");
  const [filter, setFilter] = useState("");
  const [newCommand, setNewCommand] = useState("");
  const [meta, setMeta] = useState(null);
  const [notice, setNotice] = useState("正在读取 Lua 指令");
  const importRef = useRef(null);

  const openCommand = async (command) => {
    if (!command) return;
    try {
      const data = await get(`/lua/commands/${encodeURIComponent(command)}`);
      setCurrent(data.command);
      setNewCommand(data.command);
      setEditor(data.content || "");
      setMeta(data);
      setNotice(data.using_example ? "这是示例脚本，保存后会创建文件" : "脚本已同步");
    } catch (error) { setNotice(error.message); }
  };

  const load = async (preferred = current) => {
    try {
      const data = await get("/lua/commands");
      setCommands(data.commands || []);
      const next = preferred || data.commands?.[0]?.command || "抽群老婆";
      await openCommand(next);
    } catch (error) { setNotice(error.message); }
  };
  useEffect(() => { load(); }, [refreshVersion]);

  const create = () => openCommand(newCommand.trim());
  const save = async () => {
    if (!current) return;
    try {
      const data = await post(`/lua/commands/${encodeURIComponent(current)}`, { content: editor });
      setNotice("脚本已保存并立即生效");
      await load(data.command);
      onChanged();
    } catch (error) { setNotice(error.message); }
  };
  const deleteCurrent = async () => {
    if (!current || !window.confirm(`删除 Lua 指令“${current}”？`)) return;
    try {
      await remove(`/lua/commands/${encodeURIComponent(current)}`);
      setCurrent(""); setEditor(""); setMeta(null);
      await load(""); onChanged();
      setNotice("脚本已删除");
    } catch (error) { setNotice(error.message); }
  };
  const importFile = (file) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => { setEditor(String(reader.result || "")); setNotice(`已导入 ${file.name}，保存后生效`); };
    reader.onerror = () => setNotice("文件读取失败");
    reader.readAsText(file, "utf-8");
  };

  const visible = commands.filter((item) => !filter || item.command.toLowerCase().includes(filter.toLowerCase()));

  return (
    <>
      <PageHeader eyebrow="Command runtime" title="Lua 指令工作台" description="左侧选择群指令，右侧直接编辑脚本。保存后下一条消息立即使用新版本。" actions={<><Button tone="secondary" icon={FileUp} onClick={() => importRef.current?.click()}>导入文件</Button><Button icon={Save} onClick={save}>保存脚本</Button></>} />
      <input ref={importRef} type="file" accept=".lua,text/plain" hidden onChange={(e) => importFile(e.target.files?.[0])} />
      <div className="lua-workbench">
        <Panel className="lua-browser">
          <div className="lua-create"><Field label="打开或新建指令"><input value={newCommand} onChange={(e) => setNewCommand(e.target.value)} onKeyDown={(e) => e.key === "Enter" && create()} placeholder="指令名" /></Field><Button icon={Plus} onClick={create}>打开</Button></div>
          <SearchInput value={filter} onChange={setFilter} placeholder="筛选脚本" />
          <div className="command-list">
            {!visible.length && <Empty title="没有脚本" description="输入指令名创建一个。" />}
            {visible.map((item) => <button key={item.command} className={current === item.command ? "is-active" : ""} onClick={() => openCommand(item.command)}><FileCode2 size={17} /><span>{item.command}</span><small>{Math.max(1, Math.ceil((item.size || 0) / 1024))} KB</small></button>)}
          </div>
        </Panel>

        <section className="code-workspace">
          <div className="code-workspace__bar"><div><span className="code-dot" /><strong>{current || "未选择脚本"}.lua</strong><small>{meta?.path || ""}</small></div><Button tone="danger" icon={Trash2} onClick={deleteCurrent}>删除</Button></div>
          <textarea className="code-area" spellCheck="false" value={editor} onChange={(e) => setEditor(e.target.value)} placeholder="function on_command(event, api)\n  return 'hello'\nend" />
          <div className="code-status"><Status>{notice}</Status><span>UTF-8 · Lua</span></div>
        </section>
      </div>
    </>
  );
}
