"""卫戍协议卡片渲染：赛季统计卡 / 单局详情卡 / 战绩列表卡（卫戍视觉风格）。

资源与图标来自森空岛 garrison resource 接口：
- 赛季背景/LOGO（bbs.hycdn.cn）
- 盟约图标（白色单色，按金色着色）
- 分队头像（干员立绘）
"""
import hashlib
import json
import os
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps

from qq_personal_bot.drawing import wrap_text
from qq_personal_bot.image_utils import validate_dimensions

# Keep the Chinese glyphs available in headless Linux containers as well as Windows.
FONT_PATH = Path(__file__).resolve().parent / "fonts" / "NotoSansSC.ttf"
GAME_TIMEZONE = timezone(timedelta(hours=8))

# 卫戍协议视觉风格配色（深绿黑 + 黄铜金）
BG = (11, 14, 13)
BG2 = (17, 22, 20)
PANEL = (20, 26, 24)
PANEL2 = (26, 33, 30)
BORDER = (43, 54, 49)
BORDER_HI = (92, 82, 56)
TEXT = (232, 236, 233)
DIM = (143, 160, 154)
GOLD = (201, 179, 126)
GOLD_HI = (230, 211, 163)
GREEN = (88, 199, 133)
RED = (226, 96, 90)
TEAL = (96, 190, 178)

MODE_LABELS = {"LOCAL": "教学模式", "SINGLE": "单人模拟", "MULTI": "协同模拟"}
DIFF_LABELS = {
    "TRAINING": "入门协议",
    "FUNNY": "标准模拟",
    "NORMAL": "险境模拟",
    "HARD": "绝境模拟",
    "ABYSS": "终极模拟",
    "FAST": "极速模拟",
}
SOLO_RANGE = {"FUNNY": "1–4", "NORMAL": "5–8", "HARD": "9–14", "ABYSS": "15–20"}


@lru_cache(maxsize=64)
def font(size, bold=False):
    try:
        face = ImageFont.truetype(str(FONT_PATH), size)
    except OSError as exc:
        raise OSError("无法加载卫戍中文字体，请检查安装包中的 fonts/NotoSansSC.ttf") from exc
    face.set_variation_by_axes([700 if bold else 400])
    return face


def text_w(draw, text, f):
    box = draw.textbbox((0, 0), str(text), font=f)
    return box[2] - box[0]


def cut(draw, text, f, max_w):
    text = str(text)
    if text_w(draw, text, f) <= max_w:
        return text
    while text and text_w(draw, text + "…", f) > max_w:
        text = text[:-1]
    return text + "…"


def fmt_date(ts, fmt="%m-%d %H:%M"):
    try:
        v = int(str(ts))
        if v > 10**12:
            v //= 1000
        return datetime.fromtimestamp(v, GAME_TIMEZONE).strftime(fmt)
    except Exception:  # noqa: BLE001
        return "未知"


def duration_sec(record):
    try:
        return int(record["gameEndTs"]) - int(record["gameStartTs"])
    except Exception:  # noqa: BLE001
        return 0


def fmt_dur(sec):
    m, s = divmod(max(0, int(sec)), 60)
    if m < 60:
        return f"{m}分{s:02d}秒"
    return f"{m // 60}时{m % 60}分"


def _cover(img, w, h):
    scale = max(w / img.width, h / img.height)
    nw, nh = int(img.width * scale) + 1, int(img.height * scale) + 1
    img2 = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - w) // 2
    top = (nh - h) // 2
    return img2.crop((left, top, left + w, top + h))


def _circle(img, size):
    img = img.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _rounded(img, size, radius=8):
    img = img.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size, size), radius, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _tint(img, color):
    solid = Image.new("RGBA", img.size, color + (255,))
    solid.putalpha(img.getchannel("A"))
    return solid


@lru_cache(maxsize=1)
def _boss_names():
    return json.loads(Path(__file__).with_name("boss_names.json").read_text(encoding="utf-8"))


def _png(image):
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class WeizhuRenderer:
    def __init__(self, cache_dir: Path, client: httpx.Client, resource: dict):
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = client
        self.download_deadline = time.monotonic() + 20
        self.images = {}
        self.boss_names = _boss_names()
        self.set_resource(resource)

    # ---------- 资源映射 ----------
    def set_resource(self, resource: dict):
        self.chars = {c.get("charId"): c for c in resource.get("chars") or []}
        self.items = {i.get("itemId"): i for i in resource.get("items") or []}
        self.strategies = {s.get("strategyId"): s for s in resource.get("strategies") or []}
        self.bonds = {b.get("bondId"): b for b in resource.get("bonds") or []}
        self.version = resource.get("versionInfo") or {}
        self.season_bg_url = self.season_logo_url = ""
        imgs = resource.get("image") or []
        if imgs:
            img = imgs[0]
            self.season_bg_url = img.get("backgroundPc") or img.get("backgroundMobile") or ""
            self.season_logo_url = img.get("logoPc") or img.get("logoMobile") or ""

    def season_label(self) -> str:
        v = self.version
        return f"{v.get('phaseName') or '序列'} · {v.get('versionId') or ''}".strip(" ·")

    def mode_label(self, record) -> str:
        return MODE_LABELS.get(record.get("modeType"), record.get("modeType") or "未知")

    def diff_label(self, record) -> str:
        code = record.get("modeDifficulty") or ""
        if record.get("modeType") == "SINGLE" and self.version.get("seasonId") == "act3autochess":
            rng = SOLO_RANGE.get(code)
            if rng:
                return f"规程等级 {rng}"
        return DIFF_LABELS.get(code, code or "未知")

    def band_label(self, band_id) -> str:
        s = self.strategies.get(band_id) or {}
        init = s.get("initiatorName") or band_id or "未知分队"
        name = s.get("name") or ""
        return f"{init} · {name}" if name else init

    def bond_name(self, bond_id) -> str:
        return (self.bonds.get(bond_id) or {}).get("name") or str(bond_id)

    def is_success(self, record) -> bool:
        return bool(record.get("completeState"))

    def trophy_delta(self, record) -> int:
        try:
            return int(record.get("trophyAfter") or 0) - int(record.get("trophyBefore") or 0)
        except Exception:  # noqa: BLE001
            return 0

    def squad(self, record) -> list:
        return record.get("chessSquad") or []

    def squad_members(self, record) -> list:
        out = []
        for q in self.squad(record):
            info = q.get("chessInfo") or {}
            cid = info.get("charId") or q.get("charId") or ""
            char = self.chars.get(cid) or {}
            equips = []
            for eq in q.get("equips") or []:
                item_id = eq
                if isinstance(eq, dict):
                    item_id = eq.get("equipId") or eq.get("itemId") or eq.get("id")
                if isinstance(item_id, str) and item_id:
                    item = self.items.get(item_id) or {}
                    equips.append((item.get("name") or item_id, item.get("iconUrl")))
            # 卫戍的干员等级/精英化以「合成等级(mergeLevel)」对应的 traits 为准（官方卡片逻辑）
            try:
                merge_level = int(q.get("mergeLevel") or 0)
            except (TypeError, ValueError):
                merge_level = 0
            traits = char.get("traits") or []
            trait = None
            for tr in traits:
                try:
                    if int((tr or {}).get("mergeLevel") or 0) == merge_level:
                        trait = tr
                        break
                except (TypeError, ValueError):
                    continue
            if trait is None and char.get("isDiyChar") and not traits and merge_level in (0, 1):
                trait = {"evolvePhase": 2, "level": 1 if merge_level == 0 else 60}
            level = trait.get("level") if trait else info.get("charLevel")
            evolve = trait.get("evolvePhase") if trait else info.get("evolvePhase")
            try:
                tier = int(info.get("chessLevel") or char.get("shopLevel") or 0)
            except (TypeError, ValueError):
                tier = 0
            out.append(
                {
                    "char_id": cid,
                    "name": char.get("name") or info.get("charName") or "未知",
                    "level": level,
                    "evolve": evolve,
                    "tier": tier,
                    "merge_level": merge_level,
                    "equips": equips,
                }
            )
        return out

    def active_bonds(self, record) -> list:
        rows = []
        for b in record.get("bondInfo") or []:
            if not b.get("activate"):
                continue
            layer = int(b.get("layer") or 0)
            if layer <= 0:
                continue
            rows.append((b.get("bondId"), self.bond_name(b.get("bondId")), layer))
        return sorted(rows, key=lambda x: -x[2])

    def _asset(self, url):
        if not url or not url.startswith("https://"):
            return None
        if url in self.images:
            return self.images[url]
        path = self.cache_dir / hashlib.sha256(url.encode()).hexdigest()
        image = None
        try:
            if path.exists():
                with Image.open(path) as cached:
                    validate_dimensions(cached)
                    image = cached.convert("RGBA")
            elif time.monotonic() < self.download_deadline:
                with self.client.stream("GET", url) as response:
                    response.raise_for_status()
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 12 * 1024 * 1024:
                            raise ValueError("图片过大")
                with Image.open(BytesIO(body)) as downloaded:
                    validate_dimensions(downloaded)
                    image = downloaded.convert("RGBA")
                # Concurrent cards share only complete, validated CDN resources.
                fd, name = tempfile.mkstemp(dir=self.cache_dir)
                try:
                    with os.fdopen(fd, "wb") as tmp:
                        tmp.write(body)
                    os.replace(name, path)
                finally:
                    Path(name).unlink(missing_ok=True)
        except (httpx.HTTPError, OSError, ValueError, Image.DecompressionBombError):
            pass
        self.images[url] = image
        return image

    def _avatar(self, char_id, size):
        image = self._asset((self.chars.get(char_id) or {}).get("avatarUrl"))
        return _circle(image, size) if image is not None else None

    def _bond_icon(self, bond_id, size, color=GOLD_HI):
        img = self._asset((self.bonds.get(bond_id) or {}).get("iconUrl"))
        if img is None:
            return None
        return _tint(_rounded(img, size, radius=int(size * 0.22)), color)

    def _band_icon(self, band_id, size):
        img = self._asset((self.strategies.get(band_id) or {}).get("iconUrl"))
        if img is None:
            return None
        return _rounded(img, size, radius=int(size * 0.22))

    # ---------- 页眉 / 页脚 ----------
    def _header(self, img, draw, subtitle="", badge_text="", badge_color=GOLD, height=172):
        bg = self._asset(self.season_bg_url)
        if bg is not None:
            img.paste(_cover(bg, img.width, height), (0, 0))
            overlay = Image.new("RGBA", (img.width, height), (5, 8, 7, 168))
            img.paste(overlay, (0, 0), overlay)
        draw.rectangle((0, 0, img.width, 3), fill=GOLD)
        logo = self._asset(self.season_logo_url)
        if logo is not None:
            lw = int(logo.width * 58 / logo.height)
            logo_s = logo.resize((lw, 58), Image.LANCZOS)
            img.paste(logo_s, (48, 40), logo_s)
        else:
            draw.text((48, 40), "卫戍协议 · 规程", font=font(40, True), fill=GOLD_HI)
        if subtitle:
            draw.text((50, 118), subtitle, font=font(16), fill=DIM)
        if badge_text:
            f = font(19, True)
            w = text_w(draw, badge_text, f) + 40
            draw.rounded_rectangle(
                (img.width - 48 - w, 56, img.width - 48, 106), 8,
                fill=(12, 16, 14), outline=badge_color, width=2,
            )
            draw.text((img.width - 48 - w + 20, 68), badge_text, font=f, fill=badge_color)

    def _player_line(self, img, draw, y, player, extra="", width=None):
        width = width or img.width
        draw.rounded_rectangle((40, y, width - 40, y + 84), 12, fill=PANEL, outline=BORDER, width=1)
        draw.rectangle((40, y, 46, y + 84), fill=GOLD)
        avatar = self._asset(player.get("avatar_url"))
        p_av = _circle(avatar, 54) if avatar is not None else None
        if p_av is not None:
            img.paste(p_av, (62, y + 15), p_av)
            draw.ellipse((61, y + 14, 117, y + 70), outline=GOLD, width=2)
        else:
            av = Image.new("RGBA", (54, 54), (0, 0, 0, 0))
            d = ImageDraw.Draw(av)
            d.ellipse((0, 0, 54, 54), fill=(34, 42, 39), outline=GOLD, width=2)
            d.text((16, 8), (player.get("nickname") or "博")[:1], font=font(24, True), fill=GOLD_HI)
            img.paste(av, (62, y + 15), av)
        draw.text((134, y + 12), player.get("nickname") or "博士", font=font(25, True), fill=TEXT)
        draw.text(
            (136, y + 50),
            f"UID {player.get('uid', '')} · {player.get('channel', '官服')}"
            + (f" · {extra}" if extra else ""),
            font=font(16),
            fill=DIM,
        )

    def _section(self, draw, y, text, right=""):
        draw.rectangle((48, y + 8, 56, y + 26), fill=GOLD)
        draw.text((66, y), text, font=font(24, True), fill=TEXT)
        if right:
            draw.text(
                (66 + text_w(draw, text, font(24, True)) + 16, y + 8),
                right,
                font=font(16),
                fill=DIM,
            )

    def _footer(self, draw, img, y):
        draw.line((40, y, img.width - 40, y), fill=BORDER, width=1)
        draw.text((48, y + 16), "数据来源：森空岛 · 卫戍协议战绩接口", font=font(15), fill=DIM)
        draw.text(
            (img.width - 360, y + 16),
            f"生成于 {datetime.now(GAME_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}",
            font=font(14),
            fill=DIM,
        )

    # ---------- 统计卡 ----------
    def render_stats_card(self, player, records) -> bytes:
        total = len(records)
        success = sum(1 for r in records if self.is_success(r))
        fail = total - success
        win_rate = success / total * 100 if total else 0
        durs = [duration_sec(r) for r in records]
        kills = [int(r.get("killCnt") or 0) for r in records]
        avg_dur = sum(durs) / len(durs) if durs else 0
        max_dur = max(durs) if durs else 0
        avg_kill = sum(kills) / len(kills) if kills else 0
        max_kill = max(kills) if kills else 0
        trophy_now = max((int(r.get("trophyAfter") or 0) for r in records), default=0)
        trophy_min = min((int(r.get("trophyAfter") or 0) for r in records), default=0)
        band_cnt = Counter(r.get("bandId") for r in records)
        band_rows = []
        for bid, cnt in band_cnt.most_common(5):
            wins = sum(1 for r in records if r.get("bandId") == bid and self.is_success(r))
            band_rows.append((bid, self.band_label(bid), cnt, wins / cnt * 100 if cnt else 0))
        bond_layers = defaultdict(list)
        for r in records:
            for b in r.get("bondInfo") or []:
                if b.get("activate") and int(b.get("layer") or 0) > 0:
                    bond_layers[b.get("bondId")].append(int(b.get("layer") or 0))
        bond_rows = sorted(
            ((bid, self.bond_name(bid), sum(v) / len(v), len(v)) for bid, v in bond_layers.items()),
            key=lambda x: -x[2],
        )[:6]
        diff_cnt = Counter(self.diff_label(r) for r in records)
        mode_cnt = Counter(self.mode_label(r) for r in records)
        boss_killed = sum(1 for r in records for b in r.get("acBossInfo") or [] if b.get("isKilled"))
        boss_total = sum(1 for r in records for b in r.get("acBossInfo") or [])
        rows = records[:9]

        width = 1200
        band_h = 46 + 5 * 52 + 18
        bond_h = 46 + 6 * 50 + 14
        table_h = 46 + (len(rows) + 1) * 50 + 12
        height = 172 + 104 + 176 + 132 + band_h + 22 + bond_h + 22 + table_h + 70
        img = Image.new("RGB", (width, height), BG)
        draw = ImageDraw.Draw(img)
        self._header(img, draw, f"STRONGHOLD PROTOCOL · {self.season_label()}", self.season_label(), GOLD)
        y = 172 + 18
        self._player_line(img, draw, y, player, f"当前奖杯 {trophy_now}", width)
        y += 104

        stats = [
            ("总场次", str(total), "协同 + 单人模拟", TEAL),
            ("成功 / 中断", f"{success} / {fail}", f"胜率 {win_rate:.1f}%", GREEN),
            ("平均时长", f"{avg_dur / 60:.1f} 分", f"最长 {max_dur / 60:.0f} 分", GOLD),
            ("平均击杀", f"{avg_kill:.0f}", f"最高 {max_kill}", RED),
        ]
        card_w = (width - 80 - 3 * 14) / 4
        for i, (label, value, sub, color) in enumerate(stats):
            x0 = 40 + i * (card_w + 14)
            draw.rounded_rectangle((x0, y, x0 + card_w, y + 156), 12, fill=PANEL, outline=BORDER, width=1)
            draw.rectangle((x0, y, x0 + card_w, y + 4), fill=color)
            draw.text((x0 + 20, y + 20), label, font=font(18), fill=DIM)
            draw.text((x0 + 20, y + 52), value, font=font(40, True), fill=GOLD_HI if color == GOLD else TEXT)
            draw.text((x0 + 20, y + 112), sub, font=font(16), fill=color)
        y += 176

        draw.rounded_rectangle((40, y, width - 40, y + 112), 12, fill=PANEL, outline=BORDER, width=1)
        draw.text((62, y + 14), "难度分布", font=font(15), fill=DIM)
        draw.text(
            (62, y + 40),
            cut(draw, " · ".join(f"{k} {v}" for k, v in diff_cnt.most_common(5)), font(17), 620),
            font=font(17),
            fill=TEXT,
        )
        draw.text((720, y + 14), "模式分布", font=font(15), fill=DIM)
        draw.text(
            (720, y + 40),
            cut(draw, " · ".join(f"{k} {v}" for k, v in mode_cnt.most_common()), font(17), 400),
            font=font(17),
            fill=TEXT,
        )
        draw.text(
            (62, y + 76),
            f"Boss 击杀累计 {boss_killed} / {boss_total} · 总击杀 {sum(kills)} · 奖杯区间 {trophy_min} → {trophy_now}",
            font=font(16),
            fill=DIM,
        )
        y += 132

        self._section(draw, y, "常用分队", "TOP 5")
        y += 46
        max_cnt = max((c for _, _, c, _ in band_rows), default=1)
        for i, (bid, name, cnt, wr) in enumerate(band_rows):
            ry = y + i * 52
            draw.rounded_rectangle((40, ry, width - 40, ry + 46), 10, fill=PANEL, outline=BORDER, width=1)
            icon = self._band_icon(bid, 40)
            if icon is not None:
                img.paste(icon, (52, ry + 3), icon)
                tx = 104
            else:
                tx = 62
            draw.text((tx, ry + 11), cut(draw, name, font(19, True), 330), font=font(19, True), fill=TEXT)
            bar_w = int(340 * cnt / max_cnt) if max_cnt else 0
            draw.rectangle((tx + 360, ry + 18, tx + 360 + max(bar_w, 6), ry + 28), fill=GOLD)
            info = f"{cnt} 场 · 胜率 {wr:.0f}%"
            draw.text((width - 60 - text_w(draw, info, font(17)), ry + 12), info, font=font(17), fill=GOLD_HI)
        y += band_h - 46

        self._section(draw, y, "常用盟约", "平均叠层")
        y += 46
        max_layer = max((v for _, _, v, _ in bond_rows), default=1)
        for i, (bid, name, avg, cnt) in enumerate(bond_rows):
            ry = y + i * 50
            draw.rounded_rectangle((40, ry, width - 40, ry + 44), 10, fill=PANEL, outline=BORDER, width=1)
            icon = self._bond_icon(bid, 34)
            if icon is not None:
                img.paste(icon, (52, ry + 5), icon)
                tx = 100
            else:
                tx = 62
            draw.text((tx, ry + 10), cut(draw, name, font(19, True), 190), font=font(19, True), fill=TEXT)
            bar_w = int(540 * avg / max_layer) if max_layer else 0
            draw.rectangle((tx + 230, ry + 17, tx + 230 + max(bar_w, 6), ry + 27), fill=TEAL)
            info = f"层数 {avg:.0f} · {cnt} 局"
            draw.text((width - 60 - text_w(draw, info, font(17)), ry + 11), info, font=font(17), fill=GOLD_HI)
        y += bond_h - 46

        self._section(draw, y, "最近对局", f"最近 {len(rows)} 局 / 共 {total} 局")
        y += 46
        draw.rounded_rectangle((40, y, width - 40, y + (len(rows) + 1) * 50 + 8), 12, fill=PANEL, outline=BORDER, width=1)
        heads = ["时间", "模式", "难度", "结果", "分队", "击杀", "奖杯", "时长"]
        xs = [58, 175, 290, 450, 570, 770, 870, 965]
        for i, h in enumerate(heads):
            draw.text((xs[i], y + 14), h, font=font(16, True), fill=DIM)
        for i, r in enumerate(rows):
            ry = y + 50 * (i + 1)
            draw.line((58, ry - 4, width - 58, ry - 4), fill=(32, 40, 36), width=1)
            ok = self.is_success(r)
            delta = self.trophy_delta(r)
            vals = [
                fmt_date(r.get("gameEndTs")),
                self.mode_label(r),
                self.diff_label(r),
                "成功" if ok else "中断",
                self.band_label(r.get("bandId")),
                str(r.get("killCnt") or 0),
                ("+" if delta >= 0 else "") + str(delta),
                fmt_dur(duration_sec(r)),
            ]
            for j, v in enumerate(vals):
                color = TEXT
                if j == 3:
                    color = GREEN if ok else RED
                elif j == 6:
                    color = GREEN if delta >= 0 else RED
                elif j == 0:
                    color = DIM
                draw.text((xs[j], ry + 8), cut(draw, v, font(16), 190 if j == 4 else 120), font=font(16), fill=color)

        self._footer(draw, img, height - 62)
        return _png(img)

    # ---------- 单局详情卡 ----------
    def render_match_card(self, player, record) -> bytes:
        members = self.squad_members(record)
        bonds = self.active_bonds(record)
        bosses = record.get("acBossInfo") or []
        events = record.get("events") or []
        ok = self.is_success(record)
        delta = self.trophy_delta(record)
        equipment_font = font(14)
        equipment_line_height = 18
        equipment_width = 100
        icon_size = 42
        equipment_label_top = icon_size + 5
        measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        equipment_layouts = []
        for member in members:
            tiles = []
            layout_height = 0
            items = member["equips"]
            for start in range(0, len(items), 2):
                row = items[start : start + 2]
                labels = [
                    wrap_text(measure, name, equipment_font, equipment_width - 8)
                    for name, _ in row
                ]
                for column, ((_, url), lines) in enumerate(zip(row, labels, strict=True)):
                    tiles.append((column * (equipment_width + 8), layout_height, url, lines))
                layout_height += equipment_label_top + max(map(len, labels)) * equipment_line_height + 8
            equipment_layouts.append((tiles, layout_height))
        row_offsets = []
        squad_height = 0
        for i in range(0, len(members), 5):
            row_offsets.append(squad_height)
            row_height = max(height for _, height in equipment_layouts[i : i + 5])
            squad_height += max(150, 104 + row_height)
        if not members:
            squad_height = 64
        width = 1200
        bond_rows_n = ((len(bonds) + 3) // 4) if bonds else 1
        height = 172 + 100 + 22 + 2 * 104 + 22
        height += 44 + squad_height + 10
        height += 44 + 48 * bond_rows_n + 14
        if bosses:
            height += 44 + 20 + 40 * min(len(bosses), 10)
        if events:
            height += 44 + 28 * min(len(events), 8)
        height += 80
        img = Image.new("RGB", (width, height), BG)
        draw = ImageDraw.Draw(img)
        self._header(
            img,
            draw,
            f"{fmt_date(record.get('gameEndTs'), '%Y-%m-%d %H:%M')} · {self.season_label()}",
            "模拟成功" if ok else "模拟中断",
            GREEN if ok else RED,
        )
        y = 172 + 14
        self._player_line(img, draw, y, player)
        y += 100

        info = [
            ("难度", self.diff_label(record), GOLD_HI),
            ("模式", self.mode_label(record), TEXT),
            ("分队", self.band_label(record.get("bandId")), TEXT),
            ("奖杯变化", f"{record.get('trophyBefore')} → {record.get('trophyAfter')}（{'+' if delta >= 0 else ''}{delta}）", GOLD if delta >= 0 else RED),
            ("击杀", str(record.get("killCnt") or 0), TEXT),
            ("同步次数", str(record.get("syncCnt") or 0), TEXT),
            ("金币消耗", str(record.get("coinCost") or 0), TEXT),
            ("回合", str(record.get("passRound") or 0), TEXT),
            ("时长", fmt_dur(duration_sec(record)), TEXT),
            ("Boss 击杀", f"{sum(1 for b in bosses if b.get('isKilled'))} / {len(bosses)}", TEXT),
        ]
        cell_w = (width - 80 - 4 * 14) / 5
        for i, (k, v, color) in enumerate(info):
            col = i % 5
            row = i // 5
            x0 = int(40 + col * (cell_w + 14))
            y0 = int(y + row * 104)
            draw.rounded_rectangle((x0, y0, x0 + cell_w, y0 + 92), 12, fill=PANEL, outline=BORDER, width=1)
            draw.rectangle((x0, y0, x0 + 3, y0 + 92), fill=GOLD if row == 0 else BORDER_HI)
            draw.text((x0 + 16, y0 + 12), k, font=font(14), fill=DIM)
            if k == "分队":
                band_icon = self._band_icon(record.get("bandId"), 26)
                vx = x0 + 16
                if band_icon is not None:
                    img.paste(band_icon, (x0 + 14, y0 + 36), band_icon)
                    vx = x0 + 48
                s = self.strategies.get(record.get("bandId")) or {}
                init = s.get("initiatorName") or v
                name = s.get("name") or ""
                draw.text((vx, y0 + 32), cut(draw, init, font(15, True), cell_w - (vx - x0) - 16), font=font(15, True), fill=color)
                if name:
                    draw.text((vx, y0 + 58), cut(draw, name, font(13), cell_w - (vx - x0) - 16), font=font(13), fill=DIM)
            else:
                draw.text((x0 + 16, y0 + 40), cut(draw, v, font(18, True), cell_w - 28), font=font(18, True), fill=color)
        y += 2 * 104 + 22

        self._section(draw, y, "本局阵容", f"{len(members)} 名干员")
        y += 44
        draw.rounded_rectangle((40, y, width - 40, y + squad_height + 6), 12, fill=PANEL, outline=BORDER, width=1)
        if not members:
            draw.text((64, y + 24), "这局没有记录到阵容数据", font=font(17), fill=DIM)
        for i, m in enumerate(members):
            col = i % 5
            row = i // 5
            cx = 62 + col * 224
            cy = y + 16 + row_offsets[row]
            av = self._avatar(m["char_id"], 70)
            if av is not None:
                img.paste(av, (cx, cy), av)
                draw.ellipse((cx, cy, cx + 70, cy + 70), outline=GOLD, width=2)
            else:
                draw.ellipse((cx, cy, cx + 70, cy + 70), fill=(34, 42, 39), outline=GOLD, width=2)
                draw.text((cx + 22, cy + 18), m["name"][:1], font=font(26, True), fill=GOLD_HI)
            ev = m.get("evolve")
            ev_txt = ""
            if ev is not None:
                try:
                    ev_txt = ["精0", "精1", "精2"][min(2, max(0, int(ev)))]
                except (TypeError, ValueError):
                    ev_txt = ""
            draw.text((cx + 84, cy + 2), cut(draw, m["name"], font(18, True), 124), font=font(18, True), fill=TEXT)
            lv = f"Lv{m['level']}" if m.get("level") is not None else ""
            extra = ""
            if int(m.get("merge_level") or 0) >= 1:
                extra = " · 合体"
            elif ev_txt:
                extra = f" · {ev_txt}"
            draw.text((cx + 84, cy + 28), cut(draw, lv + extra, font(13), 138), font=font(13), fill=GOLD_HI)
            tier = int(m.get("tier") or 0)
            if tier > 0:
                stars = "★" * min(6, tier)
                draw.text((cx + 84, cy + 46), stars, font=font(12), fill=GOLD)
            for left, top, url, lines in equipment_layouts[i][0]:
                center_x = cx + left + equipment_width // 2
                tile_y = cy + 78 + top
                icon = self._asset(url)
                if icon is not None:
                    icon_x = center_x - icon_size // 2
                    draw.rounded_rectangle(
                        (icon_x, tile_y, icon_x + icon_size, tile_y + icon_size),
                        radius=6,
                        fill=PANEL2,
                        outline=BORDER,
                    )
                    icon = ImageOps.contain(icon, (icon_size - 6, icon_size - 6), Image.Resampling.LANCZOS)
                    icon_pos = (center_x - icon.width // 2, tile_y + (icon_size - icon.height) // 2)
                    img.paste(icon, icon_pos, icon)
                label_y = tile_y + (equipment_label_top if icon is not None else 12)
                for line_index, line in enumerate(lines):
                    draw.text(
                        (center_x, label_y + line_index * equipment_line_height),
                        line,
                        anchor="mt",
                        font=equipment_font,
                        fill=TEAL,
                    )
        y += squad_height + 10

        self._section(draw, y, "本局盟约", f"{len(bonds)} 个激活")
        y += 44
        if bonds:
            cx = 40
            for bid, name, layer in bonds[:12]:
                txt = f"{name}  {layer}"
                w = text_w(draw, txt, font(18, True)) + 76
                if cx + w > width - 40:
                    cx = 40
                    y += 48 + 4
                draw.rounded_rectangle((cx, y, cx + w, y + 42), 10, fill=PANEL2, outline=BORDER_HI, width=1)
                icon = self._bond_icon(bid, 30)
                if icon is not None:
                    img.paste(icon, (cx + 8, y + 6), icon)
                else:
                    draw.ellipse((cx + 10, y + 9, cx + 36, y + 35), fill=(42, 50, 45))
                draw.text((cx + 46, y + 8), txt, font=font(18, True), fill=GOLD_HI)
                cx += w + 12
            y += 48 + 14
        else:
            draw.text((64, y), "本局没有激活的盟约（对局中断或盟约未成形时常见）", font=font(17), fill=DIM)
            y += 48 + 14

        if bosses:
            self._section(draw, y, "Boss 击杀明细", f"{sum(1 for b in bosses if b.get('isKilled'))} / {len(bosses)}")
            y += 44
            draw.rounded_rectangle((40, y, width - 40, y + 20 + 40 * min(len(bosses), 10)), 12, fill=PANEL, outline=BORDER, width=1)
            for i, b in enumerate(bosses[:10]):
                ry = y + 14 + i * 40
                bid = str(b.get("bossId") or "?")
                binfo = self.boss_names.get(bid) or {}
                bname = binfo.get("name") or bid
                draw.text((64, ry), cut(draw, bname, font(17, True), 300), font=font(17, True), fill=TEXT)
                color = GREEN if b.get("isKilled") else RED
                draw.text((400, ry), "已击杀" if b.get("isKilled") else "未击杀", font=font(17, True), fill=color)
                hidden = b.get("isHidden") or binfo.get("hidden")
                if hidden:
                    draw.text((520, ry), "隐藏 Boss", font=font(15), fill=DIM)
            y += 20 + 40 * min(len(bosses), 10) + 16

        if events:
            self._section(draw, y, "对局事件")
            y += 44
            for ev in events[:8]:
                draw.text((64, y), cut(draw, str(ev), font(15), width - 120), font=font(15), fill=DIM)
                y += 28
            y += 8

        self._footer(draw, img, height - 62)
        return _png(img)

    # ---------- 同局多人组队战报 ----------
    def render_multi_match_card(self, players_records) -> bytes:
        records = [record for _, record in players_records]
        width = 1400
        meta_h = 96
        panel_h = 330
        first_rec = records[0]
        killed_names = []
        for b in first_rec.get("acBossInfo") or []:
            if b.get("isKilled"):
                binfo = self.boss_names.get(str(b.get("bossId") or "")) or {}
                killed_names.append(binfo.get("name") or str(b.get("bossId") or ""))
        boss_strip_h = 40 if killed_names else 0
        height = 172 + meta_h + boss_strip_h + panel_h * len(players_records) + 76
        img = Image.new("RGB", (width, height), BG)
        draw = ImageDraw.Draw(img)
        ok_all = all(self.is_success(r) for r in records)
        ok_any = any(self.is_success(r) for r in records)
        badge = "组队成功" if ok_all else ("组队中断" if not ok_any else "部分成功")
        badge_color = GREEN if ok_all else (RED if not ok_any else GOLD)
        first = records[0]
        self._header(
            img,
            draw,
            f"{fmt_date(first.get('gameEndTs'), '%Y-%m-%d %H:%M')} · {self.season_label()} · {len(records)} 人同局",
            badge,
            badge_color,
        )

        y = 172
        # 公共信息条
        draw.rounded_rectangle((40, y, width - 40, y + meta_h - 16), 12, fill=PANEL, outline=BORDER, width=1)
        draw.rectangle((40, y, 46, y + meta_h - 16), fill=GOLD)
        boss_killed = sum(1 for b in first.get("acBossInfo") or [] if b.get("isKilled"))
        boss_total = len(first.get("acBossInfo") or [])
        meta = [
            ("难度", self.diff_label(first)),
            ("模式", self.mode_label(first)),
            ("时长", fmt_dur(duration_sec(first))),
            ("Boss 击杀", f"{boss_killed} / {boss_total}"),
            ("对局ID", str(first.get("sceneId") or "-")[:14]),
        ]
        cx = 64
        for label, value in meta:
            draw.text((cx, y + 14), label, font=font(14), fill=DIM)
            draw.text((cx, y + 40), cut(draw, value, font(19, True), 150), font=font(19, True), fill=TEXT)
            cx += 186
        y += meta_h
        if killed_names:
            draw.rounded_rectangle((40, y, width - 40, y + 34), 10, fill=PANEL2, outline=BORDER_HI, width=1)
            draw.text(
                (64, y + 7),
                cut(draw, "击杀 Boss：" + "、".join(killed_names), font(16), width - 160),
                font=font(16),
                fill=GOLD_HI,
            )
            y += boss_strip_h

        # 每人一块
        for idx, (player, record) in enumerate(players_records):
            top = y + idx * panel_h
            ok = self.is_success(record)
            delta = self.trophy_delta(record)
            draw.rounded_rectangle((40, top, width - 40, top + panel_h - 16), 14, fill=PANEL, outline=BORDER, width=1)
            draw.rectangle((40, top, 46, top + panel_h - 16), fill=GREEN if ok else RED)
            # 头像
            avatar = self._asset(player.get("avatar_url"))
            p_av = _circle(avatar, 76) if avatar is not None else None
            px, py = 64, top + 18
            if p_av is not None:
                img.paste(p_av, (px, py), p_av)
                draw.ellipse((px - 1, py - 1, px + 77, py + 77), outline=GOLD, width=2)
            else:
                draw.ellipse((px, py, px + 76, py + 76), fill=(34, 42, 39), outline=GOLD, width=2)
                draw.text((px + 24, py + 18), (player.get("nickname") or "博")[:1], font=font(30, True), fill=GOLD_HI)
            draw.text((px + 92, py + 2), cut(draw, player.get("nickname") or "博士", font(24, True), 300), font=font(24, True), fill=TEXT)
            draw.text((px + 94, py + 34), f"UID {player.get('uid', '')} · {player.get('channel', '官服')}", font=font(15), fill=DIM)
            draw.text((px + 94, py + 56), cut(draw, self.band_label(record.get("bandId")), font(17), 330), font=font(17), fill=GOLD_HI)
            # 结果和奖杯
            draw.text((px + 470, py + 2), "模拟成功" if ok else "模拟中断", font=font(22, True), fill=GREEN if ok else RED)
            draw.text(
                (px + 470, py + 36),
                f"奖杯 {record.get('trophyBefore')} → {record.get('trophyAfter')}（{'+' if delta >= 0 else ''}{delta}）",
                font=font(17),
                fill=GOLD if delta >= 0 else RED,
            )
            # 数据
            stats = [
                ("击杀", str(record.get("killCnt") or 0)),
                ("同步", str(record.get("syncCnt") or 0)),
                ("金币", str(record.get("coinCost") or 0)),
                ("回合", str(record.get("passRound") or 0)),
                ("时长", fmt_dur(duration_sec(record))),
            ]
            sx = px + 470
            sy = py + 76
            for label, value in stats:
                draw.text((sx, sy), label, font=font(13), fill=DIM)
                draw.text((sx, sy + 20), value, font=font(17, True), fill=TEXT)
                sx += 120

            # 阵容（紧凑头像行）
            members = self.squad_members(record)
            mx, my = 64, top + 146
            draw.text((mx, top + 118), f"阵容 {len(members)}", font=font(14), fill=DIM)
            for i, m in enumerate(members[:10]):
                cell = mx + i * 128
                avi = self._avatar(m["char_id"], 48)
                if avi is not None:
                    img.paste(avi, (cell, my), avi)
                    draw.ellipse((cell, my, cell + 48, my + 48), outline=GOLD, width=1)
                else:
                    draw.ellipse((cell, my, cell + 48, my + 48), fill=(34, 42, 39), outline=GOLD, width=1)
                draw.text((cell, my + 54), cut(draw, m["name"], font(12), 100), font=font(12), fill=TEXT)
                lv = f"Lv{m['level']}" if m.get("level") is not None else ""
                if int(m.get("merge_level") or 0) >= 1:
                    lv += " 合体"
                draw.text((cell, my + 70), lv, font=font(11), fill=GOLD_HI)

            # 盟约
            bonds = self.active_bonds(record)
            bx, by = 64, top + 244
            draw.text((bx, by), f"盟约 {len(bonds)}", font=font(14), fill=DIM)
            by += 26
            for bid, name, layer in bonds[:8]:
                txt = f"{name} {layer}"
                w = text_w(draw, txt, font(15)) + 56
                if bx + w > width - 64:
                    break
                draw.rounded_rectangle((bx, by, bx + w, by + 34), 10, fill=PANEL2, outline=BORDER_HI, width=1)
                icon = self._bond_icon(bid, 24)
                if icon is not None:
                    img.paste(icon, (bx + 8, by + 5), icon)
                draw.text((bx + 38, by + 8), txt, font=font(15), fill=GOLD_HI)
                bx += w + 10

        self._footer(draw, img, height - 62)
        return _png(img)

    # ---------- 战绩列表卡 ----------
    def render_list_card(self, player, records, limit=10) -> bytes:
        rows = records[:limit]
        passed = sum(1 for r in rows if self.is_success(r))
        failed = len(rows) - passed
        pass_rate = passed / len(rows) * 100 if rows else 0
        width = 1200
        height = 172 + 100 + 76 + 40 + (len(rows) + 1) * 54 + 70
        img = Image.new("RGB", (width, height), BG)
        draw = ImageDraw.Draw(img)
        self._header(
            img,
            draw,
            f"共 {len(records)} 局 · 显示最近 {len(rows)} 局",
            self.season_label(),
        )
        y = 172 + 14
        self._player_line(img, draw, y, player)
        y += 100
        # 通过次数汇总
        draw.rounded_rectangle((40, y, width - 40, y + 62), 12, fill=PANEL, outline=BORDER, width=1)
        draw.rectangle((40, y, 46, y + 62), fill=GOLD)
        draw.text((64, y + 10), f"最近 {len(rows)} 局", font=font(16), fill=DIM)
        draw.text((64, y + 32), f"通过 {passed} 次", font=font(22, True), fill=GREEN)
        draw.text((220, y + 34), f"失败 {failed} 次", font=font(18), fill=RED)
        draw.text((360, y + 34), f"通过率 {pass_rate:.0f}%", font=font(18), fill=GOLD_HI)
        draw.text(
            (560, y + 34),
            f"总击杀 {sum(int(r.get('killCnt') or 0) for r in rows)}",
            font=font(18),
            fill=DIM,
        )
        y += 76
        draw.rounded_rectangle((40, y, width - 40, y + (len(rows) + 1) * 54 + 8), 12, fill=PANEL, outline=BORDER, width=1)
        heads = ["时间", "模式", "难度", "结果", "分队", "击杀", "奖杯", "时长"]
        xs = [58, 175, 290, 450, 570, 770, 870, 965]
        for i, h in enumerate(heads):
            draw.text((xs[i], y + 16), h, font=font(16, True), fill=DIM)
        for i, r in enumerate(rows):
            ry = y + 54 * (i + 1)
            draw.line((58, ry - 4, width - 58, ry - 4), fill=(32, 40, 36), width=1)
            ok = self.is_success(r)
            delta = self.trophy_delta(r)
            vals = [
                fmt_date(r.get("gameEndTs")),
                self.mode_label(r),
                self.diff_label(r),
                "成功" if ok else "中断",
                self.band_label(r.get("bandId")),
                str(r.get("killCnt") or 0),
                ("+" if delta >= 0 else "") + str(delta),
                fmt_dur(duration_sec(r)),
            ]
            for j, v in enumerate(vals):
                color = TEXT
                if j == 3:
                    color = GREEN if ok else RED
                elif j == 6:
                    color = GREEN if delta >= 0 else RED
                elif j == 0:
                    color = DIM
                draw.text((xs[j], ry + 10), cut(draw, v, font(16), 190 if j == 4 else 120), font=font(16), fill=color)
        self._footer(draw, img, height - 62)
        return _png(img)
