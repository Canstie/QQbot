import { useEffect, useState } from "react";
import { Activity, Link2, PlugZap, Plus, Save, Trash2, Unlink, Users } from "lucide-react";

import { formatDate, get, post, put, remove } from "../api";
import { Button, Empty, Field, Metric, PageHeader, Panel, Status, Switch } from "../components/Ui";

const defaultSettings = {
  fixed_poll_interval_seconds: 0,
  smart_poll_intervals: [1, 3, 5, 10, 20, 30],
  retry_times: 3,
  exit_grace_seconds: 180,
  max_group_size: 20,
  price_country: "CN",
  price_currency: "CNY",
  game_filter_mode: "all",
  game_filter_ids: [],
};

export default function SteamPage({ refreshVersion, onChanged }) {
  const [overview, setOverview] = useState({});
  const [groups, setGroups] = useState([]);
  const [players, setPlayers] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [settings, setSettings] = useState(defaultSettings);
  const [features, setFeatures] = useState([]);
  const [selectedGroup, setSelectedGroup] = useState("");
  const [newGroup, setNewGroup] = useState("");
  const [playerForm, setPlayerForm] = useState({ identifier: "", alias: "", qq_user_id: "" });
  const [bindingForm, setBindingForm] = useState({ steam_id: "", qq_user_id: "" });
  const [notice, setNotice] = useState("正在读取 Steam 线路");
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      const [nextOverview, groupData, sessionData, settingData, featureData] = await Promise.all([
        get("/steam/overview"), get("/steam/groups"), get("/steam/sessions?limit=50"),
        get("/steam/settings"), get("/features"),
      ]);
      setOverview(nextOverview);
      setGroups(groupData.groups || []);
      setSessions(sessionData.sessions || []);
      setSettings({ ...defaultSettings, ...settingData });
      setFeatures((featureData.features || []).filter((item) => item.id.startsWith("steam.")));
      const target = selectedGroup || String(groupData.groups?.[0]?.group_id || "");
      if (target) {
        setSelectedGroup(target);
        const data = await get(`/steam/groups/${target}/players`);
        setPlayers(data.players || []);
      } else {
        setPlayers([]);
      }
      setNotice("Steam 数据已同步");
    } catch (error) {
      setNotice(error.message);
    }
  };

  useEffect(() => { void load(); }, [refreshVersion]);
  useEffect(() => {
    if (!selectedGroup) return;
    get(`/steam/groups/${selectedGroup}/players`)
      .then((data) => setPlayers(data.players || []))
      .catch((error) => setNotice(error.message));
  }, [selectedGroup]);

  const master = features.find((item) => item.id === "steam.master");
  const currentGroup = groups.find((item) => String(item.group_id) === String(selectedGroup));

  const toggleFeature = async (feature, enabled) => {
    setSaving(true);
    try {
      const saved = await put(`/features/${encodeURIComponent(feature.id)}`, { enabled });
      setFeatures((items) => items.map((item) => item.id === feature.id ? saved : item));
      setNotice(`${feature.label}已${enabled ? "开启" : "关闭"}`);
      onChanged();
      await load();
    } catch (error) {
      setNotice(error.message);
    } finally {
      setSaving(false);
    }
  };

  const saveGroup = async (groupId, changes = {}) => {
    const base = groups.find((item) => String(item.group_id) === String(groupId)) || {
      monitor_enabled: false, achievement_enabled: true,
    };
    try {
      await put(`/steam/groups/${groupId}`, {
        monitor_enabled: base.monitor_enabled,
        achievement_enabled: base.achievement_enabled,
        ...changes,
      });
      setNotice(`群 ${groupId} 已更新`);
      await load();
    } catch (error) {
      setNotice(error.message);
    }
  };

  const addGroup = async () => {
    if (!/^\d+$/.test(newGroup)) {
      setNotice("群号必须是正整数");
      return;
    }
    await saveGroup(newGroup);
    setSelectedGroup(newGroup);
    setNewGroup("");
  };

  const deleteGroup = async () => {
    if (!selectedGroup) return;
    try {
      await remove(`/steam/groups/${selectedGroup}`);
      setSelectedGroup("");
      setNotice("监控群及其订阅已删除");
      await load();
    } catch (error) {
      setNotice(error.message);
    }
  };

  const addPlayer = async () => {
    if (!selectedGroup || !playerForm.identifier.trim()) return;
    setSaving(true);
    try {
      await post(`/steam/groups/${selectedGroup}/players`, {
        identifier: playerForm.identifier.trim(),
        alias: playerForm.alias.trim(),
        qq_user_id: playerForm.qq_user_id ? Number(playerForm.qq_user_id) : null,
      });
      setPlayerForm({ identifier: "", alias: "", qq_user_id: "" });
      setNotice("玩家已加入监控，QQ 绑定为可选项");
      await load();
    } catch (error) {
      setNotice(error.message);
    } finally {
      setSaving(false);
    }
  };

  const deletePlayer = async (steamId) => {
    try {
      await remove(`/steam/groups/${selectedGroup}/players/${steamId}`);
      setNotice("玩家已移除，关联绑定也已清理");
      await load();
    } catch (error) {
      setNotice(error.message);
    }
  };

  const bindPlayer = async () => {
    if (!selectedGroup || !bindingForm.steam_id || !/^\d+$/.test(bindingForm.qq_user_id)) {
      setNotice("请选择玩家并填写正确的 QQ 号");
      return;
    }
    try {
      await put("/steam/bindings", {
        group_id: Number(selectedGroup),
        qq_user_id: Number(bindingForm.qq_user_id),
        identifier: bindingForm.steam_id,
      });
      setBindingForm({ steam_id: "", qq_user_id: "" });
      setNotice("QQ 与 Steam 玩家已绑定");
      await load();
    } catch (error) {
      setNotice(error.message);
    }
  };

  const unbindPlayer = async (qqUserId) => {
    try {
      await remove(`/steam/bindings/${selectedGroup}/${qqUserId}`);
      setNotice("绑定已解除，玩家仍会继续监控");
      await load();
    } catch (error) {
      setNotice(error.message);
    }
  };

  const saveSettings = async () => {
    setSaving(true);
    try {
      await put("/steam/settings", {
        ...settings,
        smart_poll_intervals: String(settings.smart_poll_intervals).split(/[，,\s]+/).filter(Boolean).map(Number),
        game_filter_ids: String(settings.game_filter_ids).split(/[，,\s]+/).filter(Boolean),
      });
      setNotice("轮询设置已保存并唤醒监控服务");
      await load();
    } catch (error) {
      setNotice(error.message);
    } finally {
      setSaving(false);
    }
  };

  const testConnection = async () => {
    setSaving(true);
    try {
      const result = await post("/steam/connectivity-test", {});
      setNotice(`Steam API 可用，延迟 ${result.latency_ms} ms`);
    } catch (error) {
      setNotice(error.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <PageHeader
        eyebrow="Steam presence"
        title="Steam 监控"
        description="订阅决定谁被监控；QQ 绑定只负责把 Steam 玩家对应到群成员，不影响状态通知。"
        actions={master && <Switch checked={master.enabled} onChange={(value) => toggleFeature(master, value)} label="总开关" description={master.available ? "即时启停后台监控" : "先配置 Steam API Key"} disabled={saving || !master.available} />}
      />

      <section className={`steam-circuit ${overview.enabled ? "is-live" : ""}`}>
        <div className="steam-circuit__pulse"><Activity /></div>
        <div className="steam-circuit__copy"><span>MONITOR BUS / {overview.enabled ? "LIVE" : "OPEN"}</span><strong>{overview.enabled ? "玩家状态线路正在采样" : "Steam 线路目前断开"}</strong><small>{notice}</small></div>
        <div className="steam-circuit__keys"><Status tone={overview.api_configured ? "ok" : "error"}>STEAM KEY</Status><Status tone={overview.sgdb_configured ? "ok" : "neutral"}>SGDB</Status><Status tone={overview.itad_configured ? "ok" : "neutral"}>ITAD</Status></div>
      </section>

      <div className="steam-metrics">
        <Metric label="监控群" value={overview.enabled_group_count ?? 0} suffix="GROUPS" />
        <Metric label="玩家" value={overview.player_count ?? 0} suffix="PLAYERS" tone="mint" />
        <Metric label="进行中" value={overview.active_session_count ?? 0} suffix="SESSIONS" tone="orange" />
        <Metric label="最近成功" value={overview.last_success_at ? formatDate(overview.last_success_at * 1000) : "尚未"} suffix="POLL" tone="ink" />
      </div>

      <div className="content-grid steam-grid">
        <Panel title="监控群" eyebrow="Subscriptions" className="span-4" actions={<div className="inline-add"><input value={newGroup} onChange={(event) => setNewGroup(event.target.value)} placeholder="输入群号" /><Button icon={Plus} onClick={addGroup}>添加</Button></div>}>
          <div className="steam-group-list">
            {groups.map((group) => <button key={group.group_id} className={String(group.group_id) === String(selectedGroup) ? "is-active" : ""} onClick={() => setSelectedGroup(String(group.group_id))}><span>G/{group.group_id}</span><strong>{group.player_count} 位玩家</strong><i className={group.monitor_enabled ? "is-live" : ""} /></button>)}
            {!groups.length && <Empty title="还没有监控群" description="输入群号后添加，监控默认保持关闭。" />}
          </div>
          {currentGroup && <div className="steam-group-switches"><Switch checked={currentGroup.monitor_enabled} onChange={(value) => saveGroup(currentGroup.group_id, { monitor_enabled: value })} label="状态监控" /><Switch checked={currentGroup.achievement_enabled} onChange={(value) => saveGroup(currentGroup.group_id, { achievement_enabled: value })} label="成就通知" /><Button tone="danger" icon={Trash2} onClick={deleteGroup}>移除当前群</Button></div>}
        </Panel>

        <Panel title="群内玩家" eyebrow={selectedGroup ? `G/${selectedGroup}` : "Select group"} className="span-8">
          {selectedGroup && <div className="steam-player-add"><Field label="Steam ID / 好友码 / 主页"><input value={playerForm.identifier} onChange={(event) => setPlayerForm({ ...playerForm, identifier: event.target.value })} /></Field><Field label="备注名（可选）"><input value={playerForm.alias} onChange={(event) => setPlayerForm({ ...playerForm, alias: event.target.value })} /></Field><Field label="绑定 QQ（可选）"><input inputMode="numeric" value={playerForm.qq_user_id} onChange={(event) => setPlayerForm({ ...playerForm, qq_user_id: event.target.value })} /></Field><Button icon={Plus} onClick={addPlayer} disabled={saving}>加入监控</Button></div>}
          <div className="steam-player-list">
            {players.map((player) => <div key={player.steam_id}><div className="steam-avatar">{player.avatar_url ? <img src={player.avatar_url} alt="" /> : <Users />}</div><span><strong>{player.alias || player.persona_name || player.steam_id}</strong><small>{player.game_id ? `正在玩 ${player.game_name || player.game_id}` : "当前未在游戏"}</small><code>{player.steam_id}</code></span><Status tone={player.qq_user_id ? "ok" : "neutral"}>{player.qq_user_id ? `QQ ${player.qq_user_id}` : "未绑定"}</Status>{player.qq_user_id && <button aria-label="解除 QQ 绑定" title="解除 QQ 绑定" onClick={() => unbindPlayer(player.qq_user_id)}><Unlink size={16} /></button>}<button aria-label="移除玩家" title="移除玩家" onClick={() => deletePlayer(player.steam_id)}><Trash2 size={16} /></button></div>)}
            {!players.length && <Empty title="本群还没有玩家" description="无需绑定 QQ，添加 Steam 玩家后即可接收状态通知。" />}
          </div>
          {!!players.length && <div className="steam-binding-bar"><Link2 size={17} /><select value={bindingForm.steam_id} onChange={(event) => setBindingForm({ ...bindingForm, steam_id: event.target.value })}><option value="">选择要绑定的 Steam 玩家</option>{players.map((player) => <option key={player.steam_id} value={player.steam_id}>{player.alias || player.persona_name || player.steam_id}</option>)}</select><input inputMode="numeric" placeholder="QQ 号" value={bindingForm.qq_user_id} onChange={(event) => setBindingForm({ ...bindingForm, qq_user_id: event.target.value })} /><Button tone="secondary" onClick={bindPlayer}>绑定</Button></div>}
        </Panel>

        <Panel title="通知线路" eyebrow="Delivery gates" className="span-7">
          <div className="steam-feature-grid">{features.filter((item) => item.id.startsWith("steam.notify.") || item.id === "steam.achievements").map((feature) => <Switch key={feature.id} checked={feature.enabled} onChange={(value) => toggleFeature(feature, value)} label={feature.label} description={feature.description} disabled={saving} />)}</div>
        </Panel>

        <Panel title="轮询参数" eyebrow="Polling profile" className="span-5" actions={<Button icon={Save} onClick={saveSettings} disabled={saving}>保存参数</Button>}>
          <div className="form-grid form-grid--2"><Field label="固定间隔" hint="0 表示智能轮询，单位秒"><input type="number" min="0" value={settings.fixed_poll_interval_seconds} onChange={(event) => setSettings({ ...settings, fixed_poll_interval_seconds: Number(event.target.value) })} /></Field><Field label="退出确认"><div className="input-suffix"><input type="number" min="0" value={settings.exit_grace_seconds} onChange={(event) => setSettings({ ...settings, exit_grace_seconds: Number(event.target.value) })} /><span>秒</span></div></Field><Field label="智能间隔" hint="游戏中、在线、最近离线依次填写"><input value={settings.smart_poll_intervals} onChange={(event) => setSettings({ ...settings, smart_poll_intervals: event.target.value })} /></Field><Field label="单群人数上限"><input type="number" min="1" max="200" value={settings.max_group_size} onChange={(event) => setSettings({ ...settings, max_group_size: Number(event.target.value) })} /></Field><Field label="失败重试次数"><input type="number" min="0" max="10" value={settings.retry_times} onChange={(event) => setSettings({ ...settings, retry_times: Number(event.target.value) })} /></Field><Field label="商店地区 / 货币"><div className="paired-inputs"><input maxLength="2" value={settings.price_country} onChange={(event) => setSettings({ ...settings, price_country: event.target.value.toUpperCase() })} /><input maxLength="3" value={settings.price_currency} onChange={(event) => setSettings({ ...settings, price_currency: event.target.value.toUpperCase() })} /></div></Field><Field label="游戏通知过滤"><select value={settings.game_filter_mode} onChange={(event) => setSettings({ ...settings, game_filter_mode: event.target.value })}><option value="all">全部游戏</option><option value="allow">仅允许清单</option><option value="block">排除清单</option></select></Field><Field label="游戏 AppID 清单" hint="逗号分隔"><input value={settings.game_filter_ids} onChange={(event) => setSettings({ ...settings, game_filter_ids: event.target.value })} /></Field></div>
          <div className="panel-footer"><Button tone="ghost" icon={PlugZap} onClick={testConnection} disabled={saving}>测试连接</Button><Status tone={overview.last_error ? "error" : "ok"}>{overview.last_error || "轮询服务就绪"}</Status></div>
        </Panel>

        <Panel title="最近游玩会话" eyebrow="Session ledger" className="span-12">
          <div className="steam-session-list">{sessions.slice(0, 12).map((session) => <div key={session.session_key}><span className={`session-state session-state--${session.state}`} /><code>{session.steam_id}</code><strong>{session.game_name || session.game_id}</strong><small>{formatDate(session.started_at * 1000)} → {session.ended_at ? formatDate(session.ended_at * 1000) : session.state === "confirming_exit" ? "确认退出中" : "进行中"}</small></div>)}</div>
          {!sessions.length && <Empty title="还没有游玩会话" description="开启群监控后，第一次轮询只建立基线；后续状态变化会记录在这里。" />}
        </Panel>
      </div>
    </>
  );
}
