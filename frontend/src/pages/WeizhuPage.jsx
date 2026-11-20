import { useEffect, useState } from "react";

import { get } from "../api";
import { Button, Empty, Loading, PageHeader, Panel, SearchInput } from "../components/Ui";

export default function WeizhuPage({ refreshVersion }) {
  const [rows, setRows] = useState(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let active = true;
    setError("");
    setRows(null);
    get("/weizhu/bindings")
      .then(({ bindings }) => { if (active) setRows(bindings); })
      .catch((reason) => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, [refreshVersion, retry]);

  const term = query.trim().toLowerCase();
  const filtered = rows?.filter(({ qq, nickname }) => qq.includes(term) || nickname.toLowerCase().includes(term));
  return (
    <>
      <PageHeader title="卫戍绑定" />
      <Panel title={rows ? `已绑定角色（${rows.length}）` : "已绑定角色"}>
        {error ? (
          <Empty title="读取绑定失败" description={error}
            action={<Button onClick={() => setRetry((n) => n + 1)}>重试</Button>} />
        ) : !rows ? <Loading label="正在读取绑定" /> : !rows.length ? (
          <Empty title="暂无绑定角色" description="在已启用的群中 @机器人 发送 /卫戍 绑定，使用森空岛 App 扫码后即可在这里查看。" />
        ) : (
          <>
            <SearchInput value={query} onChange={setQuery} placeholder="搜索 QQ 号或角色名#编号" />
            {filtered.length ? (
              <table className="weizhu-bindings" aria-label="QQ 与明日方舟角色绑定">
                <thead><tr><th scope="col">QQ 号</th><th scope="col">明日方舟角色名</th></tr></thead>
                <tbody>{filtered.map(({ qq, nickname }) => {
                  const hash = nickname.lastIndexOf("#");
                  return (
                    <tr key={JSON.stringify([qq, nickname])}>
                      <td><code>{qq}</code></td>
                      <td>{hash < 0 ? nickname : <>{nickname.slice(0, hash)}<code>{nickname.slice(hash)}</code></>}</td>
                    </tr>
                  );
                })}</tbody>
              </table>
            ) : <Empty title="没有匹配的绑定" description="可以按部分 QQ 号、角色名或编号搜索。" />}
          </>
        )}
      </Panel>
    </>
  );
}
