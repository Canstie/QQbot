from __future__ import annotations

import io
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, unquote, urlparse
from urllib.request import Request, urlopen


_MENU_USER_AGENT = "qq-personal-bot/0.1"
_DEFAULT_IMAGE_SUFFIX = ".jpg"
_ALLOWED_IMAGE_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
_HOWTOCOOK_ZIP_URL = "https://github.com/Anduin2017/HowToCook/archive/refs/heads/master.zip"
_HOWTOCOOK_MEDIA_BASE_URL = "https://media.githubusercontent.com/media/Anduin2017/HowToCook/master/"
_JISU_RECIPE_SEARCH_URL = "https://api.jisuapi.com/recipe/search"
_JISU_DEFAULT_KEYWORDS = (
    "家常菜",
    "下饭菜",
    "鸡肉",
    "牛肉",
    "豆腐",
    "白菜",
    "鱼",
    "火锅",
    "面条",
    "早餐",
)

_HOWTOCOOK_CATEGORY_MAP = {
    "aquatic": "水产",
    "breakfast": "早餐",
    "condiment": "酱料",
    "dessert": "甜品",
    "drink": "饮品",
    "meat_dish": "荤菜",
    "semi-finished": "半成品",
    "soup": "汤羹",
    "staple": "主食",
    "vegetable_dish": "素菜",
}

_CUISINE_RULES = (
    (("粤", "广式", "白切", "烧腊", "煲仔"), "粤菜"),
    (("麻婆", "水煮", "回锅", "鱼香", "口水鸡"), "川菜"),
    (("湘", "湖南", "剁椒", "小炒", "腊味"), "湘菜"),
    (("本帮", "葱油"), "本帮菜"),
    (("老北京", "烤鸭", "炸酱"), "京菜"),
    (("东北", "锅包肉", "地三鲜", "杀猪菜"), "东北菜"),
    (("陕西", "肉夹馍", "油泼", "biang"), "陕西菜"),
    (("佛跳墙", "沙茶"), "闽菜"),
    (("淮扬", "狮子头"), "淮扬菜"),
    (("热干面",), "湖北菜"),
    (("过桥", "汽锅"), "滇菜"),
    (("新疆", "大盘鸡", "馕", "孜然羊"), "西北菜"),
    (("冬阴功", "咖喱"), "泰式"),
    (("日式", "寿喜", "照烧"), "日式"),
)

_TOOLS_KEYWORDS = {
    "刀",
    "锅",
    "铲",
    "勺",
    "筷子",
    "碗",
    "盘",
    "量杯",
    "烤箱",
    "空气炸锅",
    "微波炉",
    "料理机",
    "厨师机",
}


def normalize_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} cannot be empty")
    return text


def normalize_text_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        raise ValueError(f"{field_name} cannot be empty")

    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ValueError(f"{field_name} must be a string or list")

    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)

    if not result:
        raise ValueError(f"{field_name} cannot be empty")
    return result


def optional_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise ValueError("value must be a string or list")

    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def encode_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def decode_json_list(value: str) -> list[str]:
    decoded = json.loads(value)
    if not isinstance(decoded, list):
        raise ValueError("expected a JSON list")
    return [str(item).strip() for item in decoded if str(item).strip()]


def load_seed_records(seed_path: Path, image_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not seed_path.is_file():
        return records

    for line_number, raw_line in enumerate(seed_path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        raw = json.loads(line)
        if not isinstance(raw, dict):
            raise ValueError(f"seed record at line {line_number} must be an object")
        records.append(normalize_seed_record(raw, image_dir=image_dir))
    return records


def fetch_jisu_recipe(appkey: str, target: str, seed: int, timeout_seconds: float) -> dict[str, Any] | None:
    key = str(appkey or "").strip()
    if not key:
        return None

    keyword = str(target or "").strip()
    if not keyword:
        keyword = _JISU_DEFAULT_KEYWORDS[abs(int(seed)) % len(_JISU_DEFAULT_KEYWORDS)]

    params = {
        "appkey": key,
        "keyword": keyword,
        "num": "10",
        "start": str(abs(int(seed)) % 30),
    }
    request = Request(
        _JISU_RECIPE_SEARCH_URL + "?" + urlencode(params),
        headers={"User-Agent": _MENU_USER_AGENT},
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read(2_000_000).decode("utf-8"))

    if str(payload.get("status")) != "0":
        return None

    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    items = result.get("list")
    if not isinstance(items, list) or not items:
        return None

    recipes = [_normalize_jisu_recipe(item) for item in items if isinstance(item, dict)]
    recipes = [recipe for recipe in recipes if recipe is not None]
    if not recipes:
        return None
    with_images = [recipe for recipe in recipes if recipe.get("image_url")]
    if with_images:
        recipes = with_images
    return recipes[abs(int(seed)) % len(recipes)]


def _normalize_jisu_recipe(raw: dict[str, Any]) -> dict[str, Any] | None:
    title = str(raw.get("name") or "").strip()
    if not title:
        return None

    image_url = str(raw.get("pic") or "").strip()
    if image_url and not image_url.startswith(("http://", "https://")):
        image_url = ""

    tags = optional_text_list(str(raw.get("tag") or "").replace("，", ",").split(","))
    ingredients = []
    material = raw.get("material")
    if isinstance(material, list):
        for item in material:
            if not isinstance(item, dict):
                continue
            name = str(item.get("mname") or "").strip()
            amount = str(item.get("amount") or "").strip()
            if name and amount:
                ingredients.append(f"{name} {amount}")
            elif name:
                ingredients.append(name)
    if not ingredients:
        ingredients = ["见菜谱详情"]

    steps = []
    process = raw.get("process")
    if isinstance(process, list):
        for item in process:
            if isinstance(item, dict):
                step = str(item.get("pcontent") or "").strip()
                if step:
                    steps.append(re.sub(r"<[^>]+>", "", step))
    if not steps:
        content = str(raw.get("content") or "").strip()
        steps = [re.sub(r"<[^>]+>", "", content)] if content else ["按菜谱步骤制作"]

    return {
        "id": "jisu-" + str(raw.get("id") or title),
        "title": title,
        "aliases": [],
        "cuisine": "国内菜谱",
        "region": "",
        "category": str(raw.get("classid") or "菜谱"),
        "tags": tags,
        "ingredients": ingredients,
        "steps": steps,
        "image_relpath": "",
        "image_url": image_url,
        "enabled": True,
        "source": "jisu",
    }


def load_howtocook_records(image_dir: Path, limit: int | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="qqbot-howtocook-") as temp_dir:
        root = _download_howtocook_repo(Path(temp_dir))
        markdown_files = sorted((root / "dishes").rglob("*.md"))
        if limit is not None:
            markdown_files = markdown_files[: max(limit, 0)]
        for markdown_path in markdown_files:
            record = parse_howtocook_markdown(markdown_path, image_dir=image_dir)
            if record is not None:
                records.append(record)
    return records


def normalize_seed_record(raw: dict[str, Any], *, image_dir: Path) -> dict[str, Any]:
    recipe_id = normalize_text(raw.get("id"), "id")
    try:
        image_relpath = cache_image(raw.get("image_url"), recipe_id=recipe_id, image_dir=image_dir)
    except (OSError, ValueError):
        image_relpath = ""
    return {
        "id": recipe_id,
        "title": normalize_text(raw.get("title"), "title"),
        "aliases_json": encode_json(optional_text_list(raw.get("aliases"))),
        "cuisine": normalize_text(raw.get("cuisine"), "cuisine"),
        "region": "",
        "category": normalize_text(raw.get("category"), "category"),
        "tags_json": encode_json(optional_text_list(raw.get("tags"))),
        "ingredients_json": encode_json(normalize_text_list(raw.get("ingredients"), "ingredients")),
        "steps_json": encode_json(normalize_text_list(raw.get("steps"), "steps")),
        "image_relpath": image_relpath,
        "enabled": 1 if bool(raw.get("enabled", True)) else 0,
        "source": str(raw.get("source", "local") or "local").strip() or "local",
    }


def parse_howtocook_markdown(markdown_path: Path, *, image_dir: Path) -> dict[str, Any] | None:
    text = markdown_path.read_text(encoding="utf-8", errors="ignore")
    title = _parse_howtocook_title(text, markdown_path)
    if not title:
        return None

    relative_parts = markdown_path.relative_to(markdown_path.parents[2]).parts
    category_key = relative_parts[1] if len(relative_parts) > 1 else "staple"
    category = _HOWTOCOOK_CATEGORY_MAP.get(category_key, "家常菜")
    cuisine = detect_cuisine(title, text, category)
    ingredients = _extract_howtocook_ingredients(text)
    steps = _extract_howtocook_steps(text)
    if not ingredients or not steps:
        return None

    image_url = _extract_howtocook_image(markdown_path, text)
    recipe_key = "/".join(relative_parts[:-1] + (markdown_path.stem,))
    recipe_id = "howtocook-" + recipe_key.encode("utf-8").hex()[:56]
    aliases = []
    stem = markdown_path.stem.strip()
    if stem and stem != title:
        aliases.append(stem)

    tags = [category, "HowToCook"]
    if cuisine not in tags:
        tags.append(cuisine)
    image_relpath = ""
    if image_url:
        try:
            image_relpath = cache_image(image_url, recipe_id=recipe_id, image_dir=image_dir)
        except OSError:
            image_relpath = ""
        except ValueError:
            image_relpath = ""

    return {
        "id": recipe_id,
        "title": title,
        "aliases_json": encode_json(aliases),
        "cuisine": cuisine,
        "region": "",
        "category": category,
        "tags_json": encode_json(tags),
        "ingredients_json": encode_json(ingredients),
        "steps_json": encode_json(steps),
        "image_relpath": image_relpath,
        "enabled": 1,
        "source": "howtocook",
    }


def cache_image(image_url: Any, *, recipe_id: str, image_dir: Path) -> str:
    source = str(image_url or "").strip()
    if not source:
        return ""

    suffix = detect_image_suffix(source)
    relpath = f"{recipe_id}{suffix}"
    image_dir.mkdir(parents=True, exist_ok=True)
    target_path = image_dir / relpath
    parsed = urlparse(source)

    if parsed.scheme in {"http", "https"}:
        request = Request(source, headers={"User-Agent": _MENU_USER_AGENT})
        with urlopen(request, timeout=15) as response:
            target_path.write_bytes(response.read(5_000_000))
        if not is_supported_image_file(target_path):
            target_path.unlink(missing_ok=True)
            return ""
        return relpath

    local_source = resolve_local_source(source, parsed=parsed)
    source_path = local_source.resolve(strict=True)
    if not is_supported_image_file(source_path):
        return ""
    target_resolved = target_path.resolve(strict=False)
    if source_path != target_resolved:
        shutil.copyfile(source_path, target_path)
    return relpath


def cache_image_bytes(body: bytes, *, recipe_id: str, image_dir: Path, suffix: str = ".jpg") -> str:
    if not body:
        return ""
    normalized_suffix = suffix.lower().strip()
    if normalized_suffix not in _ALLOWED_IMAGE_SUFFIXES:
        normalized_suffix = _DEFAULT_IMAGE_SUFFIX
    relpath = f"{recipe_id}{normalized_suffix}"
    image_dir.mkdir(parents=True, exist_ok=True)
    target_path = image_dir / relpath
    target_path.write_bytes(body)
    if not is_supported_image_file(target_path):
        target_path.unlink(missing_ok=True)
        return ""
    return relpath


def is_supported_image_file(path: Path) -> bool:
    try:
        header = path.read_bytes()[:16]
    except OSError:
        return False

    if header.startswith(b"\xff\xd8\xff"):
        return True
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if header.startswith((b"GIF87a", b"GIF89a")):
        return True
    return len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"


def detect_image_suffix(source: str) -> str:
    parsed = urlparse(source)
    suffix = Path(unquote(parsed.path)).suffix.lower()
    if suffix in _ALLOWED_IMAGE_SUFFIXES:
        return suffix
    return _DEFAULT_IMAGE_SUFFIX


def resolve_local_source(source: str, *, parsed: Any | None = None) -> Path:
    parsed = parsed or urlparse(source)
    if parsed.scheme == "file":
        path = unquote(parsed.path)
        if path.startswith("/") and len(path) >= 3 and path[2] == ":":
            path = path[1:]
        if parsed.netloc:
            path = f"//{parsed.netloc}{path}"
        return Path(path)

    local_path = Path(source)
    if local_path.is_absolute():
        return local_path
    return (Path.cwd() / local_path).resolve(strict=False)


def detect_cuisine(title: str, text: str, category: str) -> str:
    haystack = f"{title}\n{text}".casefold()
    for keywords, cuisine in _CUISINE_RULES:
        if any(keyword.casefold() in haystack for keyword in keywords):
            return cuisine
    return category


def _download_howtocook_repo(temp_dir: Path) -> Path:
    request = Request(_HOWTOCOOK_ZIP_URL, headers={"User-Agent": _MENU_USER_AGENT})
    with urlopen(request, timeout=60) as response:
        body = response.read(100_000_000)
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        archive.extractall(temp_dir)
    extracted = next(temp_dir.iterdir())
    return extracted


def _parse_howtocook_title(text: str, markdown_path: Path) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            title = re.sub(r"的做法$", "", title)
            return title
    return markdown_path.stem.strip()


def _extract_howtocook_image(markdown_path: Path, text: str) -> str:
    match = re.search(r"!\[[^\]]*\]\(([^)]+)\)", text)
    if match is None:
        return ""
    image_ref = match.group(1).strip().strip("<>")
    if " " in image_ref and not image_ref.startswith("http"):
        image_ref = image_ref.split(" ", 1)[0].strip()
    if not image_ref:
        return ""
    parsed = urlparse(image_ref)
    if parsed.scheme in {"http", "https", "file"}:
        return image_ref
    local_image_path = (markdown_path.parent / image_ref).resolve(strict=False)
    try:
        repo_root = next(
            parent for parent in (markdown_path, *markdown_path.parents) if parent.name.startswith("HowToCook-")
        )
        repo_relative_path = local_image_path.relative_to(repo_root.resolve(strict=False)).as_posix()
    except (StopIteration, ValueError):
        return str(local_image_path)
    return _HOWTOCOOK_MEDIA_BASE_URL + quote(repo_relative_path, safe="/")


def _extract_howtocook_ingredients(text: str) -> list[str]:
    measured = _extract_markdown_list(text, ("## 计算", "## 用料", "## 材料"))
    if measured:
        return measured

    base = _extract_markdown_list(text, ("## 必备原料和工具", "## 原料", "## 食材"))
    if base:
        return [item for item in base if not _looks_like_tool(item)]

    fallback = _extract_section_lines(text, ("## 必备原料和工具", "## 原料", "## 食材", "## 计算"))
    return [item for item in fallback if not _looks_like_tool(item)]


def _extract_howtocook_steps(text: str) -> list[str]:
    steps = _extract_markdown_list(text, ("## 操作", "## 步骤", "## 做法"), numbered=True)
    if steps:
        return steps
    return _extract_section_lines(text, ("## 操作", "## 步骤", "## 做法"))


def _extract_markdown_list(text: str, headings: tuple[str, ...], numbered: bool = False) -> list[str]:
    lines = text.splitlines()
    for heading in headings:
        for index, line in enumerate(lines):
            if line.strip().startswith(heading):
                items: list[str] = []
                for item_line in lines[index + 1 :]:
                    stripped = item_line.strip()
                    if not stripped:
                        continue
                    if stripped.startswith("#"):
                        break
                    if numbered:
                        match = re.match(r"^\d+\.\s+(.*)$", stripped)
                        if match:
                            items.append(_normalize_markdown_text(match.group(1)))
                    elif stripped.startswith("- "):
                        items.append(_normalize_markdown_text(stripped[2:]))
                if items:
                    return items
    return []


def _normalize_markdown_text(value: str) -> str:
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = value.replace("**", "").replace("*", "")
    return value.strip(" \t-")


def _looks_like_tool(item: str) -> bool:
    normalized = item.strip()
    return any(keyword in normalized for keyword in _TOOLS_KEYWORDS)


def _extract_section_lines(text: str, headings: tuple[str, ...]) -> list[str]:
    lines = text.splitlines()
    for heading in headings:
        for index, line in enumerate(lines):
            if line.strip().startswith(heading):
                items: list[str] = []
                for item_line in lines[index + 1 :]:
                    stripped = item_line.strip()
                    if not stripped:
                        continue
                    if stripped.startswith("#"):
                        break
                    stripped = re.sub(r"^\d+\.\s+", "", stripped)
                    stripped = re.sub(r"^-+\s*", "", stripped)
                    normalized = _normalize_markdown_text(stripped)
                    if normalized:
                        items.append(normalized)
                if items:
                    return items
    return []
