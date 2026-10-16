from __future__ import annotations

from email.message import Message
from types import SimpleNamespace

import pytest

from qq_personal_bot import web_reader as reader


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://user:secret@example.test/", "http://example.test:8080/"])
def test_non_public_page_urls_are_rejected(url):
    with pytest.raises(ValueError):
        reader._public_url(url)


def test_private_resolution_and_redirects_cannot_be_read(monkeypatch):
    monkeypatch.setattr(reader.socket, "getaddrinfo", lambda *args, **kwargs: [
        (2, 1, 6, "", ("127.0.0.1", 443)),
    ])
    monkeypatch.setattr(reader, "build_opener", lambda *args: pytest.fail("private address must not be opened"))
    assert reader.read_web_page("https://example.test/") == ""
    with pytest.raises(ValueError):
        reader._PublicRedirects().redirect_request(None, None, 302, "", {}, "http://localhost/")


def test_page_reader_extracts_text_without_executing_scripts(monkeypatch):
    html = "<html><nav>导航</nav><script>secret script</script><article><h1>公开公告</h1><p>正文&amp;日期</p></article><footer>页脚</footer></html>"
    headers = Message()
    headers["Content-Type"] = "text/html; charset=utf-8"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            return html.encode()[:size]

    response = Response()
    response.headers = headers
    monkeypatch.setattr(reader, "_public_url", lambda url: None)
    monkeypatch.setattr(reader, "build_opener", lambda *args: SimpleNamespace(open=lambda *a, **kw: response))
    assert reader.read_web_page("https://example.test/notice") == "公开公告\n正文&日期"


def test_structured_article_and_metadata_are_read_as_data():
    parser = reader._PageText()
    parser.feed('<meta property="og:description" content="公告摘要">'
                '<script type="application/ld+json">{"articleBody":"新的公开正文",'
                '"datePublished":"2026-10-16", "unrelated":"忽略字段"}</script>'
                '<script>window.evil="不得执行"</script><img alt="活动有效期图片说明">')
    text = "".join(parser.parts)
    assert all(part in text for part in ("公告摘要", "新的公开正文", "2026-10-16", "活动有效期图片说明"))
    assert "忽略字段" not in text and "不得执行" not in text
