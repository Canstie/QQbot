import asyncio

import httpx
import pytest

from qq_personal_bot import teachers


def detail(name, comments=None):
    return {"prof": name, "scores": {"overall": 14.9, "quality": 5, "grading": 4.9,
                                   "load": 5}, "reviewCount": 7, "comments": comments or []}


def install_client(monkeypatch, handler):
    factory = httpx.AsyncClient
    monkeypatch.setattr(teachers.httpx, "AsyncClient", lambda **kw: factory(
        **kw, transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize("text,expected", [
    ("查老师", ("", "")), ("查老师 吴晓", ("吴晓", "")),
    ("查老师 龙 模拟 电子技术", ("龙", "模拟 电子技术")),
    ("查老师   龙   模拟电子技术  ", ("龙", "模拟电子技术")),
    ("查老师吴晓", None), ("其他指令", None),
])
def test_parse(text, expected):
    assert teachers.parse_teacher_command(text) == expected


def test_format_general_scores_latest_comments_and_missing_values():
    comments = [{"comment": str(i), "overall": 15, "updatedAt": f"2025-01-{i:02}"}
                for i in range(1, 6)]
    comments += [{"comment": "same day", "updatedAt": "2025-01-05", "overall": 12},
                 {"comment": "   ", "updatedAt": "2026-01-01"}]
    text = teachers.format_teacher(detail("何宣", comments), "何宣", "程序设计")
    assert "14.9 / 15" in text
    assert "质量:5.0 | 给分:4.9 | 负荷:5.0" in text
    assert "共7条，展示4条" in text
    assert text.index('“5”') < text.index('“same day”') < text.index('“4”')
    assert '“1”' not in text
    assert "以下为教师总体评价" in text
    empty = detail("何宣")
    empty["scores"] = {}
    assert "质量:暂无" in teachers.format_teacher(empty, "何宣")
    assert "暂无评论" in teachers.format_teacher(empty, "何宣")


@pytest.mark.asyncio
@pytest.mark.parametrize("course", ["", "模拟 电子技术"])
@pytest.mark.parametrize("query", ["吴", "吴晓"])
async def test_search_three_details_order_dedup_and_parameters(monkeypatch, course, query):
    names = ["吴晓", "吴晓青", "吴晓雄"]
    requests = []

    async def handler(request):
        requests.append(request)
        if request.url.path == "/api/teachers":
            assert dict(request.url.params) == {
                "q_p": query, "q_c": course, "sort": "默认排序", "offset": "0", "limit": "24"}
            return httpx.Response(200, json={"items": [{"prof": n} for n in names + names[:1]],
                                             "total": 3, "has_more": False})
        name = request.url.path.rsplit("/", 1)[1]
        if name == "吴晓":
            await asyncio.sleep(0.01)
        return httpx.Response(200, json=detail(name))

    install_client(monkeypatch, handler)
    result = await teachers.query_teachers(query, course)
    expected_names = ["吴晓"] if query == "吴晓" else names
    assert [text.splitlines()[0] for text in result] == [
        f"🧬 教师画像：{n}" for n in expected_names]
    assert len(requests) == len(expected_names) + 1
    assert b"%E5%90%B4%E6%99%93" in requests[1].url.raw_path


@pytest.mark.asyncio
async def test_exact_name_takes_priority_over_many_results(monkeypatch):
    def handler(request):
        if request.url.path == "/api/teachers":
            return httpx.Response(200, json={
                "items": [{"prof": n} for n in ["吴晓青", "吴晓", "吴晓雄"]],
                "total": 30, "has_more": True,
            })
        assert request.url.path == "/api/detail/吴晓"
        return httpx.Response(200, json=detail("吴晓"))
    install_client(monkeypatch, handler)
    result = await teachers.query_teachers("吴晓")
    assert len(result) == 1
    assert result[0].startswith("🧬 教师画像：吴晓\n")


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,expected", [
    ({"items": [], "total": 0, "has_more": False}, "当前老师没有数据"),
    ({"items": [{"prof": str(i)} for i in range(6)], "total": 30, "has_more": True},
     "请补全姓名或添加课程"),
    ({"items": [], "total": 3, "has_more": False}, teachers.FAILURE),
    ({"oops": True}, teachers.FAILURE),
])
async def test_search_empty_many_and_invalid(monkeypatch, payload, expected):
    def handler(request):
        assert request.url.path == "/api/teachers"
        return httpx.Response(200, json=payload)
    install_client(monkeypatch, handler)
    assert expected in (await teachers.query_teachers("龙"))[0]


@pytest.mark.asyncio
async def test_missing_name_and_search_network_failure(monkeypatch):
    assert await teachers.query_teachers("") == [teachers.USAGE]
    def handler(request):
        raise httpx.ReadTimeout("timeout")
    install_client(monkeypatch, handler)
    assert await teachers.query_teachers("吴晓") == [teachers.FAILURE]


@pytest.mark.asyncio
@pytest.mark.parametrize("expire", [False, True])
async def test_partial_failure_deadline_and_concurrency(monkeypatch, expire):
    active = 0
    peak = 0
    async def handler(request):
        nonlocal active, peak
        if request.url.path == "/api/teachers":
            return httpx.Response(200, json={"items": [{"prof": str(i)} for i in range(5)],
                                             "total": 5, "has_more": False})
        name = request.url.path.rsplit("/", 1)[1]
        active += 1
        peak = max(peak, active)
        try:
            if name == "1":
                if expire:
                    await asyncio.Event().wait()
                return httpx.Response(503)
            await asyncio.sleep(0.001)
            return httpx.Response(200, json=detail(name))
        finally:
            active -= 1
    install_client(monkeypatch, handler)
    if expire:
        monkeypatch.setattr(teachers, "QUERY_TIMEOUT", 0.1)
    result = await teachers.query_teachers("龙")
    assert peak == 3 and active == 0
    assert len(result) == 5
    assert "未能获取：1" in result[-1]
    assert [text.splitlines()[0] for text in result[:-1]] == [
        f"🧬 教师画像：{n}" for n in ("0", "2", "3", "4")]
