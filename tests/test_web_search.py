from __future__ import annotations

from qq_personal_bot.web_search import format_web_results, search_web, should_search_web


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
