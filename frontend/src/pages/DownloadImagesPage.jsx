import { useEffect, useRef, useState } from "react";
import { Cloud, Image, RefreshCw, Search, Trash2, Upload } from "lucide-react";

import { api, formatBytes, formatDate, get, remove } from "../api";
import { Button, Empty, IconButton, Metric, PageHeader, Panel, Status } from "../components/Ui";

const PAGE_SIZE = 60;

function dateLabel(value) {
  if (!value || value.length !== 8) return value || "未知日期";
  return `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`;
}

export default function DownloadImagesPage({ refreshVersion, onChanged }) {
  const [overview, setOverview] = useState({ total: 0, total_bytes: 0, today_count: 0 });
  const [images, setImages] = useState([]);
  const [dates, setDates] = useState([]);
  const [selectedDate, setSelectedDate] = useState("");
  const [total, setTotal] = useState(0);
  const [notice, setNotice] = useState("正在连接对象图库");
  const [loading, setLoading] = useState(false);
  const [hashResult, setHashResult] = useState(null);
  const [hashError, setHashError] = useState("");
  const [hashLoading, setHashLoading] = useState(false);
  const hashRequest = useRef(0);
  const skipRefresh = useRef(null);

  const loadOverview = () => get("/download-images/overview").then(setOverview);
  const loadImages = async ({ append = false } = {}) => {
    setLoading(true);
    const offset = append ? images.length : 0;
    const query = new URLSearchParams({ offset: String(offset), limit: String(PAGE_SIZE) });
    if (selectedDate) query.set("date", selectedDate);
    try {
      const data = await get(`/download-images?${query}`);
      setImages((current) => append ? [...current, ...(data.images || [])] : (data.images || []));
      setDates(data.dates || []);
      setTotal(data.total || 0);
      setNotice(`已读取 ${append ? Math.min(offset + (data.images?.length || 0), data.total || 0) : (data.images?.length || 0)} / ${data.total || 0} 张`);
    } catch (error) {
      setNotice(error.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadOverview().catch((error) => setNotice(error.message)); }, [refreshVersion]);
  useEffect(() => {
    const skipped = skipRefresh.current;
    if (skipped?.date === selectedDate && refreshVersion > skipped.version) {
      skipRefresh.current = null;
      return;
    }
    if (skipped && skipped.date !== selectedDate) skipRefresh.current = null;
    loadImages();
  }, [selectedDate, refreshVersion]);

  const deleteImage = async (item) => {
    if (!window.confirm(`从对象图库删除这张图片？\n${item.sha256}`)) return;
    try {
      await remove(`/download-images/${item.id}`);
      setImages((current) => current.filter((image) => image.id !== item.id));
      setTotal((current) => Math.max(0, current - 1));
      setDates((current) => current
        .map((entry) => entry.date === item.downloaded_date
          ? { ...entry, count: Math.max(0, entry.count - 1) }
          : entry)
        .filter((entry) => entry.count > 0 || entry.date === selectedDate));
      setNotice("图片已删除");
      setHashResult((current) => current?.image?.id === item.id ? { ...current, matched: false, image: null } : current);
      skipRefresh.current = { date: selectedDate, version: refreshVersion };
      onChanged();
    } catch (error) {
      setNotice(error.message);
    }
  };

  const matchImage = async (file) => {
    if (!file) return;
    const requestId = ++hashRequest.current;
    setHashResult(null);
    setHashError("");
    setHashLoading(true);
    try {
      if (file.size > 50 * 1024 * 1024) throw new Error("图片不能超过 50 MB");
      const result = await api("/download-images/match", {
        method: "POST",
        body: file,
        headers: { "Content-Type": file.type || "application/octet-stream" },
      });
      if (requestId === hashRequest.current) setHashResult({ ...result, fileName: file.name });
    } catch (error) {
      if (requestId === hashRequest.current) setHashError(error.message);
    } finally {
      if (requestId === hashRequest.current) setHashLoading(false);
    }
  };

  const refresh = () => Promise.all([loadOverview(), loadImages()]);

  return (
    <>
      <PageHeader
        eyebrow="Object contact sheet"
        title="下载图片"
        description="按首次收录日期浏览聊天记录图片。内容哈希相同的图片只保留一份。"
        actions={<Button tone="secondary" icon={RefreshCw} onClick={refresh} disabled={loading}>刷新图库</Button>}
      />

      <div className="download-metrics">
        <Metric label="图库总量" value={overview.total || 0} suffix="UNIQUE IMAGES" />
        <Metric label="占用空间" value={formatBytes(overview.total_bytes)} suffix="MINIO OBJECT DATA" tone="mint" />
        <Metric label="今日新增" value={overview.today_count || 0} suffix={dateLabel(overview.today)} tone="orange" />
        <div className={`storage-beacon ${overview.storage_available === false ? "is-offline" : ""}`}>
          <Cloud />
          <span><strong>{overview.storage_available === false ? "MinIO 离线" : "MinIO 已连接"}</strong><small>PRIVATE / QQBOT-DOWNLOADS</small></span>
        </div>
      </div>

      <Panel className="download-hash-panel" title="按图片哈希查找" eyebrow="Exact SHA-256 match">
        <div className="download-hash-layout">
          <label className="download-hash-picker">
            <Upload size={23} />
            <strong>选择本地图片</strong>
            <span>上传后计算 SHA-256，只匹配内容完全相同的 OSS 图片；最多 50 MB。</span>
            <input type="file" accept="image/*" disabled={hashLoading} onChange={(event) => { matchImage(event.target.files?.[0]); event.target.value = ""; }} />
          </label>
          <div className="download-hash-result" aria-live="polite">
            {hashLoading && <Status icon={Search}>正在计算哈希并查找对象图库…</Status>}
            {hashError && <Status tone="error">{hashError}</Status>}
            {!hashLoading && !hashError && !hashResult && <p>选择图片后显示完整哈希和精确匹配结果。</p>}
            {hashResult && <>
              <small>{hashResult.fileName}</small>
              <code>{hashResult.sha256}</code>
              {hashResult.matched ? <>
                <Status tone="ok">找到唯一匹配的 OSS 图片</Status>
                <div className="download-hash-match">
                  <a href={hashResult.image.image_url} target="_blank" rel="noreferrer"><img src={hashResult.image.image_url} alt="匹配的图库图片" /></a>
                  <div><span>{formatBytes(hashResult.image.size_bytes)} · {dateLabel(hashResult.image.downloaded_date)}</span><Button tone="danger" icon={Trash2} onClick={() => deleteImage(hashResult.image)}>删除匹配图片</Button></div>
                </div>
              </> : <Status>图库中没有相同哈希的 OSS 图片。</Status>}
            </>}
          </div>
        </div>
      </Panel>

      <Panel className="download-date-panel" title="收录日期" eyebrow="Contact strips">
        <div className="date-contact-strip" role="list" aria-label="按收录日期筛选">
          <button className={!selectedDate ? "is-active" : ""} onClick={() => setSelectedDate("")}>
            <span>全部</span><strong>{overview.total || 0}</strong>
          </button>
          {dates.map((item) => (
            <button key={item.date} className={selectedDate === item.date ? "is-active" : ""} onClick={() => setSelectedDate(item.date)}>
              <span>{dateLabel(item.date)}</span><strong>{item.count}</strong>
            </button>
          ))}
        </div>
      </Panel>

      <Panel className="download-gallery-panel" title={selectedDate ? dateLabel(selectedDate) : "全部图片"} eyebrow={`${total} images`}>
        {!images.length && !loading && <Empty title="图库中还没有图片" description="管理员引用聊天记录发送 /d 后，图片会出现在这里。" />}
        <div className="masonry-gallery download-gallery">
          {images.map((item) => (
            <figure key={item.id}>
              <a href={item.image_url} target="_blank" rel="noreferrer"><img src={item.image_url} alt="" loading="lazy" /></a>
              <figcaption>
                <div><strong>{item.sha256.slice(0, 12)}</strong><small>{formatBytes(item.size_bytes)} · {formatDate(item.created_at)}</small></div>
                <IconButton label="删除图片" icon={Trash2} tone="danger" disabled={loading} onClick={() => deleteImage(item)} />
              </figcaption>
            </figure>
          ))}
        </div>
        {images.length < total && <div className="gallery-more"><Button icon={Image} onClick={() => loadImages({ append: true })} disabled={loading}>{loading ? "读取中" : "继续加载"}</Button></div>}
        <Status tone={overview.storage_available === false ? "error" : "neutral"}>{notice}</Status>
      </Panel>
    </>
  );
}
