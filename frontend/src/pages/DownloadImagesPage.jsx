import { useEffect, useState } from "react";
import { Cloud, Image, RefreshCw, Trash2 } from "lucide-react";

import { formatBytes, formatDate, get, remove } from "../api";
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
  useEffect(() => { loadImages(); }, [selectedDate, refreshVersion]);

  const deleteImage = async (item) => {
    if (!window.confirm(`从对象图库删除这张图片？\n${item.sha256}`)) return;
    try {
      await remove(`/download-images/${item.id}`);
      setNotice("图片已删除");
      await Promise.all([loadOverview(), loadImages()]);
      onChanged();
    } catch (error) {
      setNotice(error.message);
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
        {!images.length && !loading && <Empty title="图库中还没有图片" description="管理员引用聊天记录发送 /download 后，图片会出现在这里。" />}
        <div className="masonry-gallery download-gallery">
          {images.map((item) => (
            <figure key={item.id}>
              <a href={item.image_url} target="_blank" rel="noreferrer"><img src={item.image_url} alt="" loading="lazy" /></a>
              <figcaption>
                <div><strong>{item.sha256.slice(0, 12)}</strong><small>{formatBytes(item.size_bytes)} · {formatDate(item.created_at)}</small></div>
                <IconButton label="删除图片" icon={Trash2} tone="danger" onClick={() => deleteImage(item)} />
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
