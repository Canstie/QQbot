from __future__ import annotations

import json

import pytest

from qq_personal_bot import web_agent as agent
from qq_personal_bot.web_search import WebSearchResult


def result(title="原始公告", snippet="当期安排已公布，正在执行新的公开方案。"):
    return WebSearchResult(title, "https://example.test/source", snippet)


@pytest.fixture(autouse=True)
def no_page_network(monkeypatch):
    monkeypatch.setattr(agent, "read_web_pages", lambda items: ["" for _ in items])


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "明日方舟终末地现在的卡池是谁的", "这家服务商目前有什么套餐", "本周有哪些展览", "你搜索一下",
])
async def test_domains_share_the_same_semantic_search_and_evidence_flow(monkeypatch, question):
    calls, searches = [], []
    source = result()

    def complete(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps({"action":"search", "question":question, "time_scope":"current",
                               "queries":["独立主题 当前安排"]})
        return json.dumps({"status":"supported", "time_valid":True, "answer":"核实后的结论。",
                           "evidence":[{"source":1,"quote":source.snippet}]})

    def search(query, **kwargs):
        searches.append(query)
        assert kwargs["semantic"] is True
        return [source]

    monkeypatch.setattr("qq_personal_bot.web_search.search_web", search)
    reply = await agent.generate_web_reply(complete, "角色设定", [], question)
    assert reply == "核实后的结论。" and len(searches) == 1 and len(calls) == 2


@pytest.mark.asyncio
async def test_model_supplied_current_month_is_not_duplicated(monkeypatch):
    source = result()
    month = f"{agent.beijing_now():%Y年%m月}"
    query = f"主题 安排 {month}"
    responses = iter([
        {"action":"search", "question":"当前安排", "time_scope":"current", "queries":[query]},
        {"status":"supported", "time_valid":True, "answer":"已核实。",
         "evidence":[{"source":1,"quote":source.snippet}]},
    ])

    def search(value, **kwargs):
        assert value == query
        return [source]

    monkeypatch.setattr("qq_personal_bot.web_search.search_web", search)
    assert await agent.generate_web_reply(
        lambda messages: json.dumps(next(responses)), "角色", [], "当前安排",
    ) == "已核实。"


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["invented_quote", "wrong_source", "expired", "empty_evidence"])
async def test_unverified_or_expired_claim_cannot_be_sent(monkeypatch, corruption):
    calls = []
    source = result()

    def complete(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps({"action":"search", "question":"当前情况", "time_scope":"current",
                               "queries":["所问主题 当前情况"]})
        decision = {"status":"supported", "time_valid":True, "answer":"凭旧知识编出的答案。",
                    "evidence":[{"source":1,"quote":source.snippet}]}
        if corruption == "invented_quote":
            decision["evidence"][0]["quote"] = "这段文字实际上不存在于任何资料中。"
        elif corruption == "wrong_source":
            decision["evidence"][0]["source"] = 99
        elif corruption == "expired":
            decision["time_valid"] = False
        else:
            decision["evidence"] = []
        return json.dumps(decision)

    monkeypatch.setattr("qq_personal_bot.web_search.search_web", lambda *args, **kwargs: [source])
    assert await agent.generate_web_reply(complete, "角色", [], "当前情况") == agent.UNVERIFIED_WEB_REPLY


@pytest.mark.asyncio
async def test_insufficient_evidence_drives_a_new_query(monkeypatch):
    calls, searches = [], []
    fresh = WebSearchResult("新公告", "https://example.test/new", "有效期覆盖当前日期，最新安排已经明确公布。")

    def search(query, **kwargs):
        searches.append(query)
        return [result()] if len(searches) == 1 else [fresh]

    def complete(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps({"action":"search", "question":"核实当前状态", "time_scope":"current",
                               "queries":["宽泛主题"]})
        if len(calls) == 2:
            return json.dumps({"status":"insufficient", "next_query":"更准确的独立事件 原始公告"})
        return json.dumps({"status":"supported", "time_valid":True, "answer":"新公告中的已核实结论。",
                           "evidence":[{"source":2,"quote":fresh.snippet}]})

    monkeypatch.setattr("qq_personal_bot.web_search.search_web", search)
    reply = await agent.generate_web_reply(complete, "角色", [], "核实当前状态")
    assert reply == "新公告中的已核实结论。"
    assert len(searches) == 2 and "更准确的独立事件" in searches[-1]


@pytest.mark.asyncio
async def test_initial_queries_are_both_kept_before_refinement(monkeypatch):
    searches, reads, calls = [], [], []
    stale = [WebSearchResult(f"旧页面{i}", f"https://example.test/old{i}", "过期资料无法证实当前情况。",
                            "2020-01-01（页面日期线索）") for i in range(3)]
    fresh = WebSearchResult("当期公告", "https://example.test/new", "当期正式公告说明有效的安排。",
                            "2026-10-16（页面日期线索）")

    def search(query, **kwargs):
        searches.append(query)
        return stale if len(searches) < 3 else [fresh]

    def read(items):
        reads.append([item.url for item in items])
        return [fresh.snippet if item == fresh else "" for item in items]

    def complete(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps({"action":"search", "question":"当前安排", "time_scope":"current",
                               "queries":["主题 当前安排", "主题 另一种名称"]})
        if len(calls) == 2:
            return json.dumps({"status":"insufficient", "next_query":"主题 原始发布方"})
        return json.dumps({"status":"supported", "time_valid":True, "answer":"已核实的安排。",
                           "evidence":[{"source":4,"quote":fresh.snippet}]})

    monkeypatch.setattr("qq_personal_bot.web_search.search_web", search)
    monkeypatch.setattr(agent, "read_web_pages", read)
    assert await agent.generate_web_reply(complete, "角色", [], "当前安排") == "已核实的安排。"
    assert searches == [f"主题 当前安排 {agent.beijing_now():%Y年%m月}", "主题 另一种名称", "主题 原始发布方"]
    assert reads == []  # The snippet already supports the answer; no unnecessary page reads.


@pytest.mark.asyncio
async def test_related_homepage_can_lead_to_a_detail_link_but_not_an_invented_url(monkeypatch):
    calls, reads = [], []
    homepage = WebSearchResult("官方主页", "https://example.test/", "发布方的活动信息。")
    detail_url = "https://example.test/news/123"
    quote = "本期正式安排的有效期覆盖当前日期。"

    def read(items):
        reads.extend(item.url for item in items)
        return [f"当期公告 ({detail_url})" if item.url == homepage.url else quote for item in items]

    def complete(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps({"action":"search", "question":"当前安排", "time_scope":"current",
                               "queries":["主题 安排"]})
        if len(calls) == 2:
            return json.dumps({"status":"insufficient", "read_sources":[1]})
        if len(calls) == 3:
            return json.dumps({"status":"insufficient", "read_urls":[
                "https://example.test/invented", detail_url]})
        return json.dumps({"status":"supported", "time_valid":True, "answer":"当期安排已核实。",
                           "evidence":[{"source":2,"quote":quote}]})

    monkeypatch.setattr("qq_personal_bot.web_search.search_web", lambda *a, **kw: [homepage])
    monkeypatch.setattr(agent, "read_web_pages", read)
    assert await agent.generate_web_reply(complete, "角色", [], "当前安排") == "当期安排已核实。"
    assert reads == [homepage.url, detail_url]


@pytest.mark.asyncio
async def test_failed_reads_do_not_consume_the_successful_page_budget(monkeypatch):
    calls, reads = [], []
    sources = [WebSearchResult(f"页面{i}", f"https://example.test/{i}", "摘要不足以确认。") for i in range(4)]
    quote = "详细的原始公告证实了所问安排。"

    def read(items):
        reads.append([item.url for item in items])
        return [quote if item == sources[3] else "" for item in items]

    def complete(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps({"action":"search", "question":"安排", "time_scope":"timeless", "queries":["主题 安排"]})
        if len(calls) < 4:
            return json.dumps({"status":"insufficient", "read_sources":[1,2,3] if len(calls) == 2 else [4]})
        return json.dumps({"status":"supported", "answer":"已核实。", "evidence":[{"source":4,"quote":quote}]})

    monkeypatch.setattr("qq_personal_bot.web_search.search_web", lambda *a, **kw: sources)
    monkeypatch.setattr(agent, "read_web_pages", read)
    assert await agent.generate_web_reply(complete, "角色", [], "安排") == "已核实。"
    assert reads == [[item.url for item in sources[:3]], [sources[3].url]]


@pytest.mark.asyncio
async def test_plain_or_malformed_answer_cannot_bypass_verification(monkeypatch):
    monkeypatch.setattr("qq_personal_bot.web_search.search_web", lambda *args: pytest.fail("invalid plan"))
    reply = await agent.generate_web_reply(lambda messages: "这个产品尚未推出。", "角色", [], "现在怎么样")
    assert reply == agent.UNVERIFIED_WEB_REPLY


@pytest.mark.asyncio
async def test_external_fact_cannot_be_a_direct_answer_basis():
    reply = await agent.generate_web_reply(
        lambda messages: json.dumps({"action":"answer", "basis":"training_knowledge", "answer":"旧知识中的现状。"}),
        "角色", [], "核实现在的情况",
    )
    assert reply == agent.UNVERIFIED_WEB_REPLY


@pytest.mark.asyncio
async def test_small_talk_and_reasoning_do_not_need_a_search(monkeypatch):
    monkeypatch.setattr("qq_personal_bot.web_search.search_web", lambda *args: pytest.fail("unneeded search"))
    reply = await agent.generate_web_reply(
        lambda messages: json.dumps({"action":"answer", "basis":"conversation", "answer":"晚上好呀"}), "角色", [], "晚上好",
    )
    assert reply == "晚上好呀"
