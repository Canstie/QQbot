from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx


logger = logging.getLogger(__name__)
QUERY_TIMEOUT = 25.0
REQUEST_TIMEOUT = 10.0
FAILURE = "教师查询失败，请稍后再试"
USAGE = "用法：~查老师 <姓名> [课程]，例如 ~查老师 吴晓 或 ~查老师 龙 模拟电子技术"
SEPARATOR = "━━━━━━━━━━━━━━━"


def parse_teacher_command(message: str) -> tuple[str, str] | None:
    parts = message.strip().split(maxsplit=1)
    if not parts or parts[0] != "查老师":
        return None
    args = parts[1].split(maxsplit=1) if len(parts) > 1 else []
    return (args[0], args[1].strip() if len(args) > 1 else "") if args else ("", "")


def _score(value: Any) -> str:
    if isinstance(value, bool):
        return "暂无"
    try:
        number = float(value)
        return f"{number:.1f}" if math.isfinite(number) else "暂无"
    except (ValueError, TypeError):
        return "暂无"


def _comment_date(comment: dict) -> str:
    value = str(comment.get("updatedAt") or "")
    try:
        return datetime.fromisoformat(value).isoformat()
    except ValueError:
        return ""


def format_teacher(data: dict, name: str, course: str = "") -> str:
    if data.get("prof") != name or not isinstance(data.get("scores"), dict):
        raise ValueError("invalid teacher detail")
    if not isinstance(data.get("comments"), list):
        raise ValueError("invalid teacher comments")
    scores = data["scores"]
    comments = [
        row for row in data["comments"]
        if isinstance(row, dict) and isinstance(row.get("comment"), str)
        and row["comment"].strip()
    ]
    newest = sorted(comments, key=_comment_date, reverse=True)[:4]
    count = data.get("reviewCount")
    count_text = str(count) if type(count) is int and count >= 0 else "未知"
    lines = [f"🧬 教师画像：{name}"]
    if course:
        lines.append(f"📚 查询课程：{course}（仅用于查找，以下为教师总体评价）")
    lines.extend([
        SEPARATOR,
        f"⭐ 综合评分：{_score(scores.get('overall'))} / 15",
        "───── 分项指标 (各5分) ─────",
        f"质量:{_score(scores.get('quality'))} | 给分:{_score(scores.get('grading'))}"
        f" | 负荷:{_score(scores.get('load'))}",
        SEPARATOR,
        f"💬 最新评论 (共{count_text}条，展示{len(newest)}条)：",
    ])
    lines.extend(
        f"• [{_score(row.get('overall'))}分] “{row['comment'].strip()}”"
        for row in newest
    )
    if not newest:
        lines.append("暂无评论")
    lines.extend([SEPARATOR, "🌐 完整评价：http://cancir.xyz"])
    return "\n".join(lines)


async def _get_json(client: httpx.AsyncClient, path: str, **kwargs: Any) -> dict:
    response = await asyncio.wait_for(client.get(path, **kwargs), REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("invalid teacher API response")
    return data


async def query_teachers(name: str, course: str = "") -> list[str]:
    if not name:
        return [USAGE]
    deadline = asyncio.get_running_loop().time() + QUERY_TIMEOUT
    async with httpx.AsyncClient(
        base_url="https://cancir.xyz", timeout=REQUEST_TIMEOUT,
        follow_redirects=False,
    ) as client:
        try:
            search = await asyncio.wait_for(_get_json(client, "/api/teachers", params={
                "q_p": name, "q_c": course, "sort": "默认排序", "offset": 0, "limit": 24,
            }), max(0, deadline - asyncio.get_running_loop().time()))
            items, total, more = search.get("items"), search.get("total"), search.get("has_more")
            if (not isinstance(items, list) or type(total) is not int or total < 0
                    or type(more) is not bool):
                raise ValueError("invalid teacher search")
            names = []
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get("prof"), str):
                    raise ValueError("invalid teacher name")
                prof = item["prof"].strip()
                if not prof:
                    raise ValueError("empty teacher name")
                if prof not in names:
                    names.append(prof)
            if not names:
                if total or more:
                    raise ValueError("incomplete teacher search")
                return ["当前老师没有数据"]
            if total > 5 or more or len(names) > 5:
                return [
                    f"匹配到多位老师（接口报告共{total}位），请补全姓名或添加课程后重试。\n"
                    + "候选姓名：" + "、".join(names[:24])
                    + ("\n还有更多结果未列出。" if more or total > len(names) else "")
                ]
        except (httpx.HTTPError, ValueError, asyncio.TimeoutError) as exc:
            logger.warning("Teacher search failed: %s", type(exc).__name__)
            return [FAILURE]

        semaphore = asyncio.Semaphore(3)

        async def detail(prof: str) -> str | None:
            async with semaphore:
                try:
                    data = await _get_json(client, "/api/detail/" + quote(prof, safe=""))
                    return format_teacher(data, prof, course)
                except (httpx.HTTPError, ValueError, asyncio.TimeoutError) as exc:
                    logger.warning("Teacher detail failed for %s: %s", prof, type(exc).__name__)
                    return None

        tasks = [asyncio.create_task(detail(prof)) for prof in names]
        try:
            await asyncio.wait(tasks, timeout=max(0, deadline - asyncio.get_running_loop().time()))
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        messages, failed = [], []
        for prof, task in zip(names, tasks):
            result = None if task.cancelled() else task.result()
            if result is None:
                failed.append(prof)
            else:
                messages.append(result)
        if failed:
            messages.append(FAILURE + "（未能获取：" + "、".join(failed) + "）")
        return messages
