import { startTransition, useEffect, useState } from "react";
import {
  Archive,
  Bot,
  BrainCircuit,
  ChefHat,
  Code2,
  LayoutDashboard,
  Images,
  LogOut,
  Menu,
  MessageSquareText,
  RefreshCw,
  Settings2,
  Store,
  X,
} from "lucide-react";

import { get } from "./api";
import { IconButton, Status } from "./components/Ui";
import AiPage from "./pages/AiPage";
import ClassicsPage from "./pages/ClassicsPage";
import DownloadImagesPage from "./pages/DownloadImagesPage";
import LuaPage from "./pages/LuaPage";
import MenusPage from "./pages/MenusPage";
import OverviewPage from "./pages/OverviewPage";
import PolicyPage from "./pages/PolicyPage";
import RepliesPage from "./pages/RepliesPage";
import RestaurantsPage from "./pages/RestaurantsPage";

const navigation = [
  { id: "overview", label: "运行总览", short: "总览", icon: LayoutDashboard },
  { id: "policy", label: "群与策略", short: "策略", icon: Settings2 },
  { id: "ai", label: "AI 角色", short: "AI", icon: BrainCircuit },
  { id: "replies", label: "回复规则", short: "回复", icon: MessageSquareText },
  { id: "lua", label: "Lua 指令", short: "Lua", icon: Code2 },
  { id: "menus", label: "今日菜单", short: "菜单", icon: ChefHat },
  { id: "restaurants", label: "群饭店", short: "饭店", icon: Store },
  { id: "classics", label: "群典藏", short: "典藏", icon: Archive },
  { id: "downloads", label: "下载图片", short: "图库", icon: Images },
];

const pageMap = {
  overview: OverviewPage,
  policy: PolicyPage,
  ai: AiPage,
  replies: RepliesPage,
  lua: LuaPage,
  menus: MenusPage,
  restaurants: RestaurantsPage,
  classics: ClassicsPage,
  downloads: DownloadImagesPage,
};

function routeFromHash() {
  const route = window.location.hash.replace(/^#\/?/, "").split("/")[0];
  return pageMap[route] ? route : "overview";
}

export default function App() {
  const [route, setRoute] = useState(routeFromHash);
  const [mobileNav, setMobileNav] = useState(false);
  const [summary, setSummary] = useState(null);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState("");

  const navigate = (next) => {
    window.location.hash = next;
    setMobileNav(false);
  };

  const refresh = () => {
    setRefreshVersion((value) => value + 1);
  };

  useEffect(() => {
    const onHashChange = () => startTransition(() => setRoute(routeFromHash()));
    window.addEventListener("hashchange", onHashChange);
    if (!window.location.hash) window.history.replaceState(null, "", "#overview");
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    let active = true;
    setSyncing(true);
    setError("");
    Promise.all([
      get("/policy"),
      get("/dsapi"),
      get("/lua/commands"),
      get("/menus?limit=200"),
      get("/restaurants?limit=200"),
      get("/classics/groups?limit=200"),
      get("/download-images/overview"),
    ])
      .then(([policy, ai, lua, menus, restaurants, classics, downloads]) => {
        if (!active) return;
        setSummary({ policy, ai, lua, menus, restaurants, classics, downloads });
      })
      .catch((reason) => active && setError(reason.message))
      .finally(() => active && setSyncing(false));
    return () => {
      active = false;
    };
  }, [refreshVersion]);

  const CurrentPage = pageMap[route];
  const currentNav = navigation.find((item) => item.id === route);

  return (
    <div className="app-shell">
      <aside className={`signal-dock ${mobileNav ? "is-open" : ""}`}>
        <div className="dock-brand">
          <div className="dock-brand__mark"><Bot size={24} /></div>
          <div><strong>QQ BOT</strong><span>Signal Desk</span></div>
          <IconButton className="dock-close" label="关闭导航" icon={X} onClick={() => setMobileNav(false)} />
        </div>

        <nav className="dock-nav" aria-label="管理模块">
          {navigation.map(({ id, label, icon: Icon }) => (
            <button key={id} className={route === id ? "is-active" : ""} onClick={() => navigate(id)}>
              <Icon size={19} />
              <span>{label}</span>
              {route === id && <i />}
            </button>
          ))}
        </nav>

        <div className="dock-foot">
          <div className="dock-foot__status"><span className="live-dot" /><div><strong>OneBot 在线</strong><small>实时信号已接通</small></div></div>
          <button onClick={() => fetch("./logout", { method: "POST" }).then(() => { window.location.href = "./login"; })}>
            <LogOut size={17} />退出管理
          </button>
        </div>
      </aside>

      {mobileNav && <button className="nav-scrim" aria-label="关闭导航" onClick={() => setMobileNav(false)} />}

      <main className="workspace">
        <header className="signal-bar">
          <div className="signal-bar__title">
            <IconButton className="mobile-menu" label="打开导航" icon={Menu} onClick={() => setMobileNav(true)} />
            <div><span>CONTROL / {route.toUpperCase()}</span><strong>{currentNav?.label}</strong></div>
          </div>

          <div className="packet-route" aria-label="消息处理链路">
            {["OneBot", "Policy", "Lua", "AI"].map((item, index) => (
              <div className={index < 2 || summary?.ai?.api_configured ? "is-live" : ""} key={item}>
                <span>{item}</span>{index < 3 && <i />}
              </div>
            ))}
          </div>

          <div className="signal-bar__actions">
            <Status tone={error ? "error" : "ok"}>{error ? "同步异常" : syncing ? "同步中" : "运行正常"}</Status>
            <IconButton label="同步全部数据" icon={RefreshCw} onClick={refresh} className={syncing ? "spin-icon" : ""} />
          </div>
        </header>

        {error && <div className="global-alert">{error}<button onClick={refresh}>重新同步</button></div>}

        <div className="page-stage" key={route}>
          <CurrentPage
            summary={summary}
            refreshVersion={refreshVersion}
            onNavigate={navigate}
            onChanged={refresh}
          />
        </div>
      </main>

      <nav className="mobile-tabs" aria-label="移动端快捷导航">
        {navigation.slice(0, 5).map(({ id, short, icon: Icon }) => (
          <button key={id} className={route === id ? "is-active" : ""} onClick={() => navigate(id)}>
            <Icon size={19} /><span>{short}</span>
          </button>
        ))}
      </nav>
    </div>
  );
}
