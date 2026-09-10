import { useEffect, useMemo, useState } from "react";
import { CircuitBoard, Search, ZapOff } from "lucide-react";

import { get, put } from "../api";
import { PageHeader, Panel, SearchInput, Status, Switch } from "../components/Ui";

export default function FeaturesPage({ refreshVersion, onChanged }) {
  const [features, setFeatures] = useState([]);
  const [search, setSearch] = useState("");
  const [pending, setPending] = useState("");
  const [notice, setNotice] = useState("正在读取功能线路");

  const load = () => get("/features")
    .then((data) => {
      setFeatures(data.features || []);
      setNotice("所有开关均为即时生效");
    })
    .catch((error) => setNotice(error.message));

  useEffect(() => { void load(); }, [refreshVersion]);

  const grouped = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return features.reduce((result, feature) => {
      const haystack = `${feature.label} ${feature.description} ${feature.id}`.toLowerCase();
      if (needle && !haystack.includes(needle)) return result;
      (result[feature.category] ||= []).push(feature);
      return result;
    }, {});
  }, [features, search]);

  const toggle = async (feature, enabled) => {
    const previous = feature.enabled;
    setPending(feature.id);
    setFeatures((items) => items.map((item) => item.id === feature.id ? { ...item, enabled } : item));
    try {
      const saved = await put(`/features/${encodeURIComponent(feature.id)}`, { enabled });
      setFeatures((items) => items.map((item) => item.id === feature.id ? saved : item));
      setNotice(`${feature.label}已${enabled ? "开启" : "关闭"}`);
      onChanged();
    } catch (error) {
      setFeatures((items) => items.map((item) => item.id === feature.id ? { ...item, enabled: previous } : item));
      setNotice(error.message);
    } finally {
      setPending("");
    }
  };

  const enabledCount = features.filter((feature) => feature.enabled).length;

  return (
    <>
      <PageHeader
        eyebrow="Circuit breakers"
        title="功能中心"
        description="每一条机器人能力都是一条独立线路。拉下开关后，下一次触发会直接静默跳过。"
        actions={<Status tone="ok">{enabledCount}/{features.length} 条线路接通</Status>}
      />

      <div className="feature-console">
        <div className="feature-console__mark"><CircuitBoard /></div>
        <div><span>LIVE CONTROL PLANE</span><strong>改动立即写入运行态</strong><small>{notice}</small></div>
        <SearchInput value={search} onChange={setSearch} placeholder="搜索功能或内部标识" />
      </div>

      <div className="feature-groups">
        {Object.entries(grouped).map(([category, items]) => (
          <Panel key={category} title={category} eyebrow={`${items.filter((item) => item.enabled).length}/${items.length} online`}>
            <div className="feature-switches">
              {items.map((feature) => (
                <div className={`feature-switch ${feature.enabled ? "is-live" : ""}`} key={feature.id}>
                  <span className="feature-switch__signal" />
                  <Switch
                    checked={feature.enabled}
                    onChange={(value) => toggle(feature, value)}
                    label={feature.label}
                    description={feature.description}
                    disabled={pending === feature.id || !feature.available}
                  />
                  <code>{feature.id}</code>
                  {!feature.available && <span className="feature-switch__unavailable"><ZapOff size={13} />缺少环境配置</span>}
                </div>
              ))}
            </div>
          </Panel>
        ))}
        {!Object.keys(grouped).length && (
          <Panel className="span-12"><div className="feature-empty"><Search />没有匹配的功能</div></Panel>
        )}
      </div>
    </>
  );
}
