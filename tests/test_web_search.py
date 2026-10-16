from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from qq_personal_bot import web_search as web
from qq_personal_bot.web_search import (
    WebSearchResult,
    WebSearchSession,
    attach_web_sources,
    format_web_results,
    search_web,
    should_search_web,
)


@pytest.fixture(autouse=True)
def reset_cache():
    web._search_cache.clear()
    yield
    web._search_cache.clear()


@pytest.mark.parametrize("text", [
    "如何看待最近的人工智能监管动态", "点评今天的科技新闻", "评价台海局势",
    "最近科技圈有什么大事", "DeepSeek 近况怎么样", "国际新闻你怎么看",
])
def test_current_event_and_commentary_intents_are_automatically_searched(text):
    assert should_search_web(text)


@pytest.mark.parametrize("text", [
    "随便聊聊", "你现在在干嘛", "评价一下我的性格", "点评这段代码", "我今天有点累",
])
def test_casual_and_personal_topics_do_not_force_public_search(text):
    assert not should_search_web(text)


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
    assert "2026" in requests[0]
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
    assert search_web("fallback query")[0].title == "Found"
    assert search_web("fallback query")[0].title == "Found"
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


def test_source_footer_uses_only_returned_sources():
    results = [WebSearchResult("Official", "https://example.test/official", "snippet")]
    reply = attach_web_sources("事实已核实[1]。https://invented.test/fake [99]", results)
    assert "https://example.test/official" in reply
    assert "https://invented.test" not in reply
    assert "[99]" not in reply
    assert "检索来源" in reply


def test_web_search_only_triggers_for_fresh_or_explicit_queries():
    assert should_search_web("帮我查一下 DeepSeek 最新消息") is True
    assert should_search_web("今天有什么新闻") is True
    assert should_search_web("你现在在干嘛") is False
    assert should_search_web("随便聊聊") is False


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

    results = search_web("unique test query", limit=5)
    formatted = format_web_results(results)

    assert len(results) == 1
    assert results[0].url == "https://example.com/one"
    assert results[0].snippet == "Fresh result"
    assert "https://example.com/one" in formatted
