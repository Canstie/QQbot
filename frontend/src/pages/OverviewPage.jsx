import { Archive, BrainCircuit, ChefHat, Code2, MessageSquareText, Settings2 } from "lucide-react";

import { Empty, LinkRow, Metric, PageHeader, Panel, Status } from "../components/Ui";

export default function OverviewPage({ summary, onNavigate }) {
  const policy = summary?.policy;
  const ai = summary?.ai;
  const groups = summary?.classics?.groups || [];

  return (
    <>
      <PageHeader
        eyebrow="Live operations"
        title="机器人信号台"
        description="先看链路，再动配置。这里汇总当前群策略、AI 状态和内容资产。"
      />

      <section className="overview-console">
        <div className="bot-core">
          <div className="bot-core__orb"><span /><i /><b>Q</b></div>
          <div className="bot-core__copy">
            <span className="mono-label">BOT / ACTIVE</span>
            <h2>消息链路已接通</h2>
            <p>OneBot 正在接收群事件，策略层决定消息进入 Lua 指令还是 AI 对话。</p>
            <div className="inline-statuses">
              <Status tone="ok">OneBot 在线</Status>
              <Status tone={ai?.api_configured ? "ok" : "error"}>DSAPI {ai?.api_configured ? "可用" : "未配置"}</Status>
              <Status>{policy?.mode || "读取中"}</Status>
            </div>
          </div>
        </div>

        <div className="overview-metrics">
          <Metric label="启用群" value={policy?.enabled_groups?.length ?? "-"} suffix="GROUPS" />
          <Metric label="AI 群" value={ai?.enabled_groups?.length ?? "-"} suffix="GROUPS" tone="mint" />
          <Metric label="短期消息" value={ai?.history_messages ?? "-"} suffix="MESSAGES" tone="orange" />
          <Metric label="典藏群" value={groups.length || "-"} suffix="ARCHIVES" tone="ink" />
        </div>
      </section>

      <div className="content-grid content-grid--overview">
        <Panel title="常用工作区" eyebrow="Quick routes">
          <div className="link-stack">
            <LinkRow icon={Settings2} title="调整群策略" description="启用群、触发方式与频率限制" meta={`${policy?.enabled_groups?.length ?? 0} 群`} onClick={() => onNavigate("policy")} />
            <LinkRow icon={BrainCircuit} title="切换 AI 角色" description="知识提示词与短期上下文" meta={ai?.model || "DSAPI"} onClick={() => onNavigate("ai")} />
            <LinkRow icon={Code2} title="维护 Lua 指令" description="编辑群功能脚本并即时生效" meta={`${summary?.lua?.commands?.length ?? 0} 个`} onClick={() => onNavigate("lua")} />
            <LinkRow icon={MessageSquareText} title="管理固定回复" description="前缀与免前缀触发规则" onClick={() => onNavigate("replies")} />
          </div>
        </Panel>

        <Panel title="内容库存" eyebrow="Content stores">
          <div className="asset-tally">
            <button onClick={() => onNavigate("menus")}><ChefHat /><span><strong>{summary?.menus?.menus?.length ?? 0}</strong>菜单条目</span></button>
            <button onClick={() => onNavigate("classics")}><Archive /><span><strong>{groups.reduce((sum, group) => sum + group.count, 0)}</strong>群典图片</span></button>
          </div>
          <div className="recent-archive">
            <div className="section-caption"><span>最近更新的典藏群</span><button onClick={() => onNavigate("classics")}>查看全部</button></div>
            {groups.length ? groups.slice(0, 3).map((group) => (
              <div className="archive-signal" key={group.group_id}>
                <span>G/{group.group_id}</span><i /><strong>{group.count} 张</strong>
              </div>
            )) : <Empty title="还没有群典" description="在群内引用图片发送 ~存典 后，这里会出现记录。" />}
          </div>
        </Panel>
      </div>
    </>
  );
}
