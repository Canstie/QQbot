from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from qq_personal_bot import web_search as web
from qq_personal_bot.web_search import (
    WebSearchResult,
    WebSearchSession,
    format_web_results,
    hide_web_sources,
    search_web,
)


@pytest.fixture(autouse=True)
def reset_cache(monkeypatch):
    web._search_cache.clear()
    monkeypatch.setattr(web, "_HTML_SEARCH_URLS", ())
    yield
    web._search_cache.clear()


class Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, size):
        return self.body[:size]


@pytest.fixture
def current_time(monkeypatch):
    now = datetime(2026, 10, 16, 20, tzinfo=timezone(timedelta(hours=8)))
    monkeypatch.setattr(web, "beijing_now", lambda: now)
    return now


def test_news_dates_exclude_stale_future_and_undated_results(monkeypatch, current_time):
    body = '''<rss><channel>
    <item><title>科技 Current</title><link>https://example.test/2026/10/16/current</link>
      <pubDate>周五, 16 10月 2026 10:00:00 GMT</pubDate></item>
    <item><title>Old</title><link>https://example.test/old</link>
      <pubDate>Thu, 01 Oct 2026 10:00:00 GMT</pubDate></item>
    <item><title>Future</title><link>https://example.test/future</link>
      <pubDate>Sat, 17 Oct 2026 10:00:00 GMT</pubDate></item>
    <item><title>Unknown</title><link>https://example.test/unknown</link></item>
    </channel></rss>'''.encode()
    requests = []

    def fetch(request, timeout):
        requests.append(request.full_url)
        return Response(body)

    monkeypatch.setattr(web, "urlopen", fetch)
    results = search_web("今天科技新闻")
    assert [item.title for item in results] == ["科技 Current"]
    assert results[0].published == "2026-10-16（页面日期线索）"
    assert parse_qs(urlparse(requests[0]).query)["q"] == ["今天科技新闻"]
    assert "2026" in web.web_search_guidance()
    assert "2026-10-16 20:00" in web.web_search_guidance()


def test_historical_news_is_not_forced_into_recent_window(monkeypatch, current_time):
    body = b'''<rss><channel><item><title>Historical</title>
    <link>https://example.test/2024</link><pubDate>Fri, 01 Mar 2024 10:00:00 GMT</pubDate>
    </item></channel></rss>'''
    monkeypatch.setattr(web, "urlopen", lambda *args, **kwargs: Response(body))
    assert search_web("评价2024年的新闻")[0].title == "Historical"


def test_broken_search_endpoint_uses_fallback_and_caches_results(monkeypatch):
    attempts = []

    def fetch(request, timeout):
        attempts.append(request.full_url)
        if len(attempts) == 1:
            return Response(b"<html>not an RSS feed</html>")
        return Response(b'''<rss><channel><item><title>Found</title>
          <link>https://example.test/verified</link></item></channel></rss>''')

    monkeypatch.setattr(web, "urlopen", fetch)
    assert search_web("Found")[0].title == "Found"
    assert search_web("Found")[0].title == "Found"
    assert len(attempts) == 2
    assert "cn.bing.com" in attempts[1]


def test_tool_session_has_real_source_ids_and_bounded_queries(monkeypatch):
    monkeypatch.setattr(web, "search_web", lambda *args, **kwargs: [
        WebSearchResult("Official", "https://example.test/official", "Verified excerpt"),
    ])
    session = WebSearchSession()
    first = session.run_tool("web_search", json.dumps({"query": "some topic"}))
    second = session.run_tool("web_search", json.dumps({"query": "another topic"}))
    assert first["results"][0]["index"] == second["results"][0]["index"] == 1
    assert len(session.results) == 1
    assert "error" in session.run_tool("web_search", '{"query":"third topic"}')


@pytest.mark.parametrize("name,arguments", [
    ("run_shell", '{"query":"ignored"}'), ("web_search", "invalid"),
    ("web_search", '{"query":123}'), ("web_search", '{"query":"q","freshness_days":true}'),
])
def test_invalid_or_unknown_tool_cannot_trigger_search(monkeypatch, name, arguments):
    monkeypatch.setattr(web, "search_web", lambda *args, **kwargs: pytest.fail("unexpected network call"))
    assert "error" in WebSearchSession().run_tool(name, arguments)


@pytest.mark.parametrize("footer", [
    "检索来源：\n[1] 官方消息\nhttps://example.test/official",
    "### 参考资料\n[1] 官方消息\nhttps://example.test/official",
    "**搜索资料**：官方消息；摘要：公开信息；来源：https://example.test/official",
    "Sources:\n[1] https://example.test/official",
])
def test_search_material_is_hidden_but_answer_is_preserved(footer):
    reply = hide_web_sources(f"事实已核实[1]。我的看法是还需观察。\n\n{footer}")
    assert reply == "事实已核实。我的看法是还需观察。"


def test_inline_citations_are_hidden_without_removing_requested_links():
    reply = hide_web_sources(
        "消息已发布[1](https://example.test/official)[2, 3]【4】[^5]。\n"
        "你要的官网：https://example.test/"
    )
    assert reply == "消息已发布。\n你要的官网：https://example.test/"


def test_all_queries_reject_dictionary_and_homepage_results(monkeypatch):
    body = '''<rss><channel>
    <item><title>梁（汉字）</title><link>https://example.test/character</link><description>梁的意思</description></item>
    <item><title>DeepSeek 官方首页</title><link>https://example.test/home</link></item>
    <item><title>DeepSeek API 补偿7天消费金额</title><link>https://example.test/compensation</link>
      <description>因服务故障，补偿以赠金返还。</description></item>
    </channel></rss>'''.encode()
    monkeypatch.setattr(web, "urlopen", lambda *args, **kwargs: Response(body))
    results = search_web("DeepSeek API 补偿 7天", freshness_days=0)
    assert [item.url for item in results] == ["https://example.test/compensation"]


def test_html_search_uses_source_cards_not_generated_summary_or_other_models(monkeypatch):
    body = '''<html>
    <li class="res-list"><h3><a href="https://news.so.com/search">尊界 V800 刹车 支架 断裂</a></h3>AI生成摘要</li>
    <li class="res-list"><h3><a href="https://image.so.com/search">尊界 V800 刹车 支架 断裂</a></h3>图片搜索页</li>
    <li class="res-list"><h3><a data-mdurl="https://example.test/s800" href="https://www.so.com/link?a=1">尊界S800</a></h3>刹车支架断裂</li>
    <li class="res-list"><h3><a data-mdurl="https://example.test/v800" href="https://www.so.com/link?a=2">尊界V800刹车踏板支架断裂</a></h3>
      <p>2026年10月15日，回应制动部件问题。</p></li>
    </html>'''.encode()
    monkeypatch.setattr(web, "_HTML_SEARCH_URLS", (("https://www.so.com/s", "q"),))
    requests = []
    monkeypatch.setattr(web, "urlopen", lambda request, **kwargs: (
        requests.append(request.full_url) or Response(body)
    ))
    results = search_web("尊界 V800 刹车 支架 断裂", freshness_days=0)
    assert len(requests) == 1
    assert [item.url for item in results] == ["https://example.test/v800"]
    assert "2026-10-15" in results[0].published


def test_sogou_fallback_reads_original_source_url(current_time):
    body = '''<div class="vrwrap"><h3 class="vr-title"><a href="/link?url=redirect">DeepSeek API 补偿</a></h3>
    <p>补偿按近7天消费返还。</p><div data-url="https://example.test/notice"></div></div>'''
    results = web._parse_html_results(body, "https://www.sogou.com/web", 5, 0, current_time, "DeepSeek API 补偿")
    assert results[0].url == "https://example.test/notice"
    assert "消费返还" in results[0].snippet


def test_semantic_candidate_search_combines_independent_engines(monkeypatch):
    monkeypatch.setattr(web, "_HTML_SEARCH_URLS", (
        ("https://www.so.com/s", "q"), ("https://www.sogou.com/web", "query"),
    ))
    requests = []

    def fetch(request, **kwargs):
        requests.append(request.full_url)
        name = "first" if "so.com/s?" in request.full_url else "second"
        body = "".join(f'<li class="res-list"><h3><a href="https://example.test/{name}/{i}">{name} {i}</a></h3>'
                       f'<p class="res-desc">candidate text</p></li>' for i in range(2))
        return Response(body.encode())

    monkeypatch.setattr(web, "urlopen", fetch)
    results = search_web("任意主题", semantic=True, freshness_days=0)
    assert len(requests) == 2
    assert [item.title for item in results] == ["first 0", "second 0", "first 1", "second 1"]


def test_entity_background_without_the_event_cannot_be_used_as_evidence(current_time):
    candidates = [
        WebSearchResult("梁文锋 深度求索 创始人", "https://example.test/background", "人物经历"),
        WebSearchResult("别家平台赠送额度", "https://example.test/unrelated", "其他平台的活动。" * 40 + "相关搜索：DeepSeek 补偿"),
        WebSearchResult("梁文锋 深度求索 补偿通知", "https://example.test/notice", "发放赠金"),
    ]
    results = web._select_results(candidates, 5, 0, current_time, "梁文锋 深度求索", [
        ["DeepSeek", "深度求索", "梁文锋"], ["补偿", "返还", "赠金"],
    ])
    assert [item.url for item in results] == ["https://example.test/notice"]


def test_search_web_parses_bing_rss(monkeypatch):
    body = b"""<?xml version='1.0' encoding='utf-8'?>
    <rss><channel>
      <item><title>Example One</title><link>https://example.com/one</link>
      <description>&lt;b&gt;Fresh&lt;/b&gt; result</description><pubDate>today</pubDate></item>
      <item><title>Bad</title><link>javascript:alert(1)</link></item>
    </channel></rss>"""

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, size):
            return body[:size]

    monkeypatch.setattr("qq_personal_bot.web_search.urlopen", lambda request, timeout: FakeResponse())

    results = search_web("Example fresh", limit=5)
    formatted = format_web_results(results)

    assert len(results) == 1
    assert results[0].url == "https://example.com/one"
    assert results[0].snippet == "Fresh result"
    assert "https://example.com/one" in formatted
