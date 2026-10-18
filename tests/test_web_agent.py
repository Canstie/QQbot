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
async def test_insufficient_without_a_next_action_reads_existing_candidates(monkeypatch, caplog):
    calls, reads = [], []
    source = result(snippet="摘要尚未包含答案。")
    quote = "正文说明项目甲在周六上午九点开放。"

    def complete(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps({"action": "search", "question": "开放安排", "time_scope": "timeless",
                               "queries": ["项目甲 开放安排"]})
        if len(calls) == 2:
            return json.dumps({"status": "insufficient", "next_query": "", "read_sources": []})
        assert json.loads(messages[-1]["content"])["sources"][0]["text"] == quote
        return json.dumps({"status": "supported", "answer": "周六上午九点开放。",
                           "evidence": [{"source": 1, "quote": quote}]})

    def read(items):
        reads.extend(item.url for item in items)
        return [quote for _ in items]

    monkeypatch.setattr("qq_personal_bot.web_search.search_web", lambda *a, **kw: [source])
    monkeypatch.setattr(agent, "read_web_pages", read)
    with caplog.at_level("INFO", logger=agent.__name__):
        reply = await agent.generate_web_reply(complete, "角色", [], "项目甲开放安排")
    assert reply == "周六上午九点开放。"
    assert reads == [source.url]
    assert "action=fallback_read" in caplog.text and "evidence=accepted" in caplog.text


@pytest.mark.asyncio
async def test_automatic_reads_are_bounded_when_all_pages_fail(monkeypatch, caplog):
    responses = iter([
        {"action": "search", "question": "项目安排", "time_scope": "timeless", "queries": ["项目 安排"]},
        *[{"status": "insufficient"}] * 3,
    ])
    sources = [WebSearchResult(f"页面{i}", f"https://example.test/{i}", "摘要缺少所需信息。") for i in range(8)]
    reads = []

    def read(items):
        reads.extend(item.url for item in items)
        return ["" for _ in items]

    monkeypatch.setattr("qq_personal_bot.web_search.search_web", lambda *a, **kw: sources)
    monkeypatch.setattr(agent, "read_web_pages", read)
    with caplog.at_level("INFO", logger=agent.__name__):
        reply = await agent.generate_web_reply(lambda _: json.dumps(next(responses)), "角色", [], "项目安排")
    assert reply == agent.UNVERIFIED_WEB_REPLY
    assert len(reads) == len(set(reads)) == 6
    assert "outcome=unverified" in caplog.text and "reason=model_insufficient" in caplog.text


@pytest.mark.asyncio
async def test_plain_or_malformed_answer_cannot_bypass_verification(monkeypatch):
    monkeypatch.setattr("qq_personal_bot.web_search.search_web", lambda *args: pytest.fail("invalid plan"))
    reply = await agent.generate_web_reply(lambda messages: "这个产品尚未推出。", "角色", [], "现在怎么样")
    assert reply == agent.WEB_PROTOCOL_ERROR_REPLY


@pytest.mark.asyncio
async def test_external_fact_cannot_be_a_direct_answer_basis():
    reply = await agent.generate_web_reply(
        lambda messages: json.dumps({"action":"answer", "basis":"training_knowledge", "answer":"旧知识中的现状。"}),
        "角色", [], "核实现在的情况",
    )
    assert reply == agent.WEB_PROTOCOL_ERROR_REPLY


@pytest.mark.asyncio
async def test_failed_grounding_protocol_is_not_reported_as_no_search_results(monkeypatch, caplog):
    responses = iter([
        json.dumps({"action": "search", "question": "项目安排", "time_scope": "timeless",
                    "queries": ["项目 安排"]}),
        "这不是有效的结构化核验结果。",
        "再次输出了自然语言而非JSON。",
    ])
    monkeypatch.setattr("qq_personal_bot.web_search.search_web", lambda *a, **kw: [result()])
    with caplog.at_level("INFO", logger=agent.__name__):
        reply = await agent.generate_web_reply(lambda _: next(responses), "角色", [], "项目安排")
    assert reply == agent.WEB_PROTOCOL_ERROR_REPLY
    assert "stage=ground outcome=invalid_response" in caplog.text


@pytest.mark.asyncio
async def test_small_talk_and_reasoning_do_not_need_a_search(monkeypatch):
    monkeypatch.setattr("qq_personal_bot.web_search.search_web", lambda *args: pytest.fail("unneeded search"))
    reply = await agent.generate_web_reply(
        lambda messages: json.dumps({"action":"answer", "basis":"conversation", "answer":"晚上好呀"}), "角色", [], "晚上好",
    )
    assert reply == "晚上好呀"


@pytest.mark.asyncio
@pytest.mark.parametrize("current", ["像素方块的硬核才是王道", "那它为什么这么流行？"])
async def test_previous_questions_are_background_not_pending_requests(current):
    history = [
        {"role": "user", "content": "群聊背景\nQQ 123 当前对你说：奶蛙是什么梗"},
        {"role": "assistant", "content": "旧助手的事实结论不应用作本轮证据。"},
    ]
    context = [{"user_id": 789, "content": "群里在讨论另一个话题"}]

    def complete(messages):
        assert [item["role"] for item in messages] == ["system", "user"]
        data = json.loads(messages[-1]["content"])
        assert data["current_request"] == current
        assert data["current_user_id"] == 123
        assert data["previous_user_questions"] == ["奶蛙是什么梗"]
        assert data["recent_group_messages"] == context
        assert "旧助手的事实结论" not in str(messages)
        return json.dumps({"action": "clarify", "answer": "你想聊这句话的意思还是出处？"})

    assert await agent.generate_web_reply(
        complete, "角色", history, current, recent_context=context, current_user_id=123,
    ) == "你想聊这句话的意思还是出处？"


@pytest.mark.asyncio
async def test_grounding_keeps_original_request_when_planner_drifts_to_old_topic(monkeypatch):
    current = "搜一下本周展览的开放时间"
    old = result(title="旧话题资料", snippet="旧话题是此前已经回答过的社区用语。")
    fresh = WebSearchResult("展览公告", "https://example.test/exhibition", "本周展览每天上午九点开放。")
    calls, searches = [], []

    def search(query, **kwargs):
        searches.append(query)
        return [old] if len(searches) == 1 else [fresh]

    def complete(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps({"action": "search", "question": "旧话题的含义", "time_scope": "timeless",
                               "queries": ["旧话题"]})
        data = json.loads(messages[-1]["content"])
        assert data["current_request"] == current
        assert data["question"] == "旧话题的含义"
        assert data["previous_user_questions"] == ["旧话题是什么"]
        if len(calls) == 2:
            return json.dumps({"status": "insufficient", "next_query": "本周展览 开放时间"})
        return json.dumps({"status": "supported", "answer": "每天上午九点开放。",
                           "evidence": [{"source": 2, "quote": fresh.snippet}]})

    monkeypatch.setattr("qq_personal_bot.web_search.search_web", search)
    assert await agent.generate_web_reply(
        complete, "角色", [{"role": "user", "content": "旧话题是什么"}], current,
    ) == "每天上午九点开放。"
    assert searches == ["旧话题", "本周展览 开放时间"]


@pytest.mark.asyncio
async def test_structured_current_request_preserves_image_blocks():
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}}
    prompt = [{"type": "text", "text": "看这张图"}, image]

    def complete(messages):
        content = messages[-1]["content"]
        assert json.loads(content[0]["text"])["current_request"] == "看这张图"
        assert content[1:] == [image]
        return json.dumps({"action": "clarify", "answer": "你想了解图中哪一部分？"})

    assert await agent.generate_web_reply(
        complete, "角色", [], prompt,
    ) == "你想了解图中哪一部分？"
    assert prompt[0]["text"] == "看这张图"


@pytest.mark.asyncio
@pytest.mark.parametrize("term", ["绝妖星", "铅笔芯计划", "MSR"])
async def test_unfamiliar_terms_keep_the_original_name_without_a_date(monkeypatch, term):
    queries = []
    source = result(snippet=f"{term}是社区使用的简称，指向该项目的协作活动。")
    responses = iter([
        {"action":"search", "question":f"{term}是什么", "time_scope":"timeless", "focus":term,
         "queries":[f"{term} 是什么"]},
        {"status":"supported", "answer":"该项目协作活动的简称。",
         "evidence":[{"source":1,"quote":source.snippet}]},
    ])

    def search(query, **kwargs):
        queries.append(query)
        assert kwargs["required_terms"] == [[term]]
        return [source]

    monkeypatch.setattr("qq_personal_bot.web_search.search_web", search)
    assert await agent.generate_web_reply(
        lambda messages: json.dumps(next(responses)), "角色", [], f"{term}是什么",
    ) == "该项目协作活动的简称。"
    assert queries[0] == term and all("年" not in query for query in queries)


@pytest.mark.asyncio
async def test_one_malformed_structured_response_can_be_repaired():
    calls = []

    def complete(messages):
        calls.append(messages)
        return ('{broken JSON' if len(calls) == 1 else
                json.dumps({"action":"answer", "basis":"reasoning", "answer":"49"}))

    assert await agent.generate_web_reply(complete, "角色", [], "7的平方") == "49"
    assert len(calls) == 2 and "合法JSON" in calls[-1][-1]["content"]


@pytest.mark.asyncio
async def test_inexact_quote_receives_feedback_and_can_be_corrected(monkeypatch):
    calls = []
    source = result(snippet="原始文档明确说明该项目属于协作活动。")

    def complete(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps({"action":"search", "question":"项目含义", "time_scope":"timeless", "queries":["项目"]})
        if len(calls) == 3:
            assert json.loads(messages[-1]["content"])["verification_feedback"]
        return json.dumps({"status":"supported", "answer":"该项目属于协作活动。", "evidence":[
            {"source":1, "quote":"引用并不在原文中，不能发送。" if len(calls) == 2 else source.snippet}]})

    monkeypatch.setattr("qq_personal_bot.web_search.search_web", lambda *a, **kw: [source])
    assert await agent.generate_web_reply(complete, "角色", [], "项目含义") == "该项目属于协作活动。"
    assert len(calls) == 3


def test_evidence_tolerates_layout_spaces_but_not_different_facts():
    sources = [{"title":"资料", "snippet":"某活动 是一种 社区 协作活动，代号 ＡＢＣ。"}]
    decision = {"status":"supported", "answer":"已核实。", "evidence":[
        {"source":1,"quote":"某活动是一种社区协作活动，代号ABC。"}]}
    assert agent._supported_answer(decision, sources, False) == "已核实。"
    decision["evidence"][0]["quote"] = "某活动是一种社区竞技活动，代号ABC。"
    assert agent._supported_answer(decision, sources, False) is None


@pytest.mark.asyncio
async def test_reading_a_blocked_page_does_not_discard_a_requested_search(monkeypatch):
    calls, searches = [], []
    blocked = WebSearchResult("社区用法", "https://example.test/blocked", "只有简称，缺少说明。")
    definition = WebSearchResult("完整说明", "https://example.test/definition", "该简称表示一种公开的协作活动。")

    def complete(messages):
        calls.append(messages)
        if len(calls) == 1:
            return json.dumps({"action":"search", "question":"简称含义", "time_scope":"timeless", "queries":["简称"]})
        if len(calls) == 2:
            return json.dumps({"status":"insufficient", "read_sources":[1], "next_query":"简称 完整说明"})
        assert json.loads(messages[-1]["content"])["sources"][0]["read_status"] == "unavailable"
        return json.dumps({"status":"supported", "answer":"一种协作活动的简称。", "evidence":[
            {"source":2,"quote":definition.snippet}]})

    def search(query, **kwargs):
        searches.append(query)
        return [blocked] if len(searches) == 1 else [definition]

    monkeypatch.setattr("qq_personal_bot.web_search.search_web", search)
    assert await agent.generate_web_reply(complete, "角色", [], "简称含义") == "一种协作活动的简称。"
    assert searches == ["简称", "简称 完整说明"] and len(calls) == 3
