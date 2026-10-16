from __future__ import annotations

import ipaddress
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from qq_personal_bot.web_search import WebSearchResult

_MAX_PAGE_BYTES = 1_500_000


def _public_url(url: str) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.port not in (None, 80, 443)):
        raise ValueError("invalid public page URL")
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("page URL is not public")


class _PublicRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg", "nav", "footer"}:
            self.skip += 1
        if not self.skip and tag in {"p", "div", "br", "li", "h1", "h2", "h3", "article"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg", "nav", "footer"} and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def read_web_page(url: str) -> str:
    try:
        _public_url(url)
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 QQBot/0.1", "Accept": "text/html"})
        with build_opener(_PublicRedirects()).open(request, timeout=5) as response:
            if "html" not in response.headers.get_content_type():
                return ""
            raw = response.read(_MAX_PAGE_BYTES + 1)
            if len(raw) > _MAX_PAGE_BYTES:
                return ""
            encoding = response.headers.get_content_charset() or "utf-8"
        parser = _PageText()
        parser.feed(raw.decode(encoding, errors="replace"))
        lines = [re.sub(r"\s+", " ", part).strip() for part in "".join(parser.parts).splitlines()]
        return "\n".join(part for part in lines if part)[:10000]
    except (OSError, ValueError, LookupError):
        return ""


def read_web_pages(results: list[WebSearchResult]) -> list[str]:
    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(lambda item: read_web_page(item.url), results))
