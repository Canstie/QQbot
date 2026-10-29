from __future__ import annotations

import json
import shutil
from email.message import Message
from io import BytesIO
from urllib.error import HTTPError

import pytest
from PIL import Image, ImageDraw

from qq_personal_bot import bilibili_card
from qq_personal_bot.bilibili_card import (
    BilibiliCardRequest,
    BilibiliVideoMetadata,
)


def _image_bytes(color=(30, 60, 90), size=(640, 360), image_format="JPEG") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format=image_format)
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, body: bytes, url: str, content_type: str = "application/json"):
        self.body = body
        self.url = url
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size=None):
        return self.body if size is None else self.body[:size]

    def geturl(self):
        return self.url


def test_resolves_b23_short_link_to_bvid(monkeypatch):
    final_url = "https://www.bilibili.com/video/BV1Gyu36LEfL?p=1"
    monkeypatch.setattr(
        bilibili_card,
        "_open_restricted",
        lambda request, allowed_url, timeout: FakeResponse(b"", final_url, "text/html"),
    )

    resolved = bilibili_card._resolve_bilibili_video_url("https://b23.tv/wrXwLXN")

    assert resolved == final_url
    assert bilibili_card._extract_bvid(resolved) == "BV1Gyu36LEfL"


def test_rejects_b23_redirect_to_untrusted_host(monkeypatch):
    monkeypatch.setattr(
        bilibili_card,
        "_open_restricted",
        lambda request, allowed_url, timeout: FakeResponse(
            b"", "https://evil.example/video/BV1Gyu36LEfL", "text/html"
        ),
    )

    with pytest.raises(ValueError, match="did not resolve"):
        bilibili_card._resolve_bilibili_video_url("https://b23.tv/wrXwLXN")


def test_accepts_bilibili_video_url_from_final_http_412(monkeypatch):
    final_url = "https://www.bilibili.com/video/BV1Gyu36LEfL?p=1"

    def fail_after_redirect(request, allowed_url, timeout):
        raise HTTPError(final_url, 412, "Precondition Failed", Message(), None)

    monkeypatch.setattr(bilibili_card, "_open_restricted", fail_after_redirect)

    resolved = bilibili_card._resolve_bilibili_video_url("https://b23.tv/wrXwLXN")

    assert resolved == final_url


def test_fetches_bilibili_video_metadata(monkeypatch):
    api_url = "https://api.bilibili.com/x/web-interface/view?bvid=BV1Gyu36LEfL"
    payload = {
        "code": 0,
        "data": {
            "title": "马儿空气动力学",
            "pic": "http://i1.hdslb.com/bfs/archive/cover.jpg",
            "owner": {
                "name": "东海爱马仕Fix",
                "face": "https://i2.hdslb.com/bfs/face/avatar.jpg",
            },
        },
    }
    monkeypatch.setattr(
        bilibili_card,
        "_open_restricted",
        lambda request, allowed_url, timeout: FakeResponse(
            json.dumps(payload, ensure_ascii=False).encode(), api_url
        ),
    )

    metadata = bilibili_card._fetch_bilibili_metadata("BV1Gyu36LEfL")

    assert metadata.title == "马儿空气动力学"
    assert metadata.cover_url == "https://i1.hdslb.com/bfs/archive/cover.jpg"
    assert metadata.author_name == "东海爱马仕Fix"
    assert metadata.author_avatar_url == "https://i2.hdslb.com/bfs/face/avatar.jpg"


def _mobile_page(state: dict) -> bytes:
    return (
        "<html><script>window.__INITIAL_STATE__ = \n"
        + json.dumps(state, ensure_ascii=False)
        + ";(function(){})();</script></html>"
    ).encode()


def _mobile_state() -> dict:
    return {
        "video": {
            "error": 0,
            "viewInfo": {
                "bvid": "BV1Gyu36LEfL",
                "title": "马儿空气动力学",
                "pic": "http://i1.hdslb.com/bfs/archive/cover.jpg",
                "owner": {"name": "东海爱马仕", "face": "https://i2.hdslb.com/bfs/face/avatar.jpg"},
            },
        }
    }


@pytest.mark.parametrize("api_failure", ["http412", "invalid_json", "incomplete"])
def test_fetches_original_metadata_from_mobile_when_api_fails(monkeypatch, api_failure):
    calls = []

    def fake_open(request, allowed_url, timeout):
        calls.append(request.full_url)
        assert allowed_url(request.full_url)
        if len(calls) == 1:
            if api_failure == "http412":
                raise HTTPError(request.full_url, 412, "Precondition Failed", Message(), None)
            body = b"not json" if api_failure == "invalid_json" else b'{"code":0,"data":{}}'
            return FakeResponse(body, request.full_url)
        assert request.full_url == "https://m.bilibili.com/video/BV1Gyu36LEfL"
        assert "iPhone" in request.get_header("User-agent")
        assert not allowed_url("https://evil.example/video/BV1Gyu36LEfL")
        assert not allowed_url("https://m.bilibili.com/video/BV1xx411c7mD")
        return FakeResponse(_mobile_page(_mobile_state()), request.full_url, "text/html")

    monkeypatch.setattr(bilibili_card, "_open_restricted", fake_open)

    metadata = bilibili_card._fetch_bilibili_metadata("BV1Gyu36LEfL")

    assert len(calls) == 2
    assert metadata.cover_url == "https://i1.hdslb.com/bfs/archive/cover.jpg"
    assert metadata.author_name == "东海爱马仕"
    assert metadata.author_avatar_url == "https://i2.hdslb.com/bfs/face/avatar.jpg"


@pytest.mark.parametrize("invalid", ["unavailable", "other_video", "preview", "missing_author"])
def test_rejects_unavailable_or_incorrect_mobile_metadata(monkeypatch, invalid):
    state = _mobile_state()
    view = state["video"]["viewInfo"]
    if invalid == "unavailable":
        state["video"]["error"] = -404
    elif invalid == "other_video":
        view["bvid"] = "BV1xx411c7mD"
    elif invalid == "preview":
        view["pic"] = "https://qq.ugcimg.cn/share-preview.jpg"
    else:
        view["owner"] = {}
    monkeypatch.setattr(
        bilibili_card,
        "_open_restricted",
        lambda request, allowed_url, timeout: FakeResponse(
            _mobile_page(state), request.full_url, "text/html"
        ),
    )

    with pytest.raises(ValueError):
        bilibili_card._fetch_bilibili_mobile_metadata("BV1Gyu36LEfL")


def test_rejects_mobile_redirect_to_other_host(monkeypatch):
    monkeypatch.setattr(
        bilibili_card,
        "_open_restricted",
        lambda request, allowed_url, timeout: FakeResponse(
            _mobile_page(_mobile_state()), "https://evil.example/video/BV1Gyu36LEfL"
        ),
    )

    with pytest.raises(ValueError, match="redirected"):
        bilibili_card._fetch_bilibili_mobile_metadata("BV1Gyu36LEfL")


def test_generates_complete_pink_bilibili_card(monkeypatch):
    metadata = BilibiliVideoMetadata(
        title="这是一个用于验证两行截断效果的超长B站视频标题" * 5,
        cover_url="https://i1.hdslb.com/bfs/archive/cover.jpg",
        author_name="测试作者",
        author_avatar_url="https://i2.hdslb.com/bfs/face/avatar.jpg",
    )
    monkeypatch.setattr(
        bilibili_card,
        "_resolve_bilibili_video_url",
        lambda url: "https://www.bilibili.com/video/BV1Gyu36LEfL",
    )
    monkeypatch.setattr(bilibili_card, "_fetch_bilibili_metadata", lambda bvid: metadata)
    monkeypatch.setattr(
        bilibili_card,
        "_download_image_bytes",
        lambda url, max_bytes: (
            _image_bytes((20, 80, 160), (1280, 720))
            if "archive" in url
            else _image_bytes((220, 180, 120), (200, 200))
        ),
    )

    path = bilibili_card.generate_bilibili_card(
        BilibiliCardRequest(source_url="https://b23.tv/wrXwLXN")
    )

    assert path is not None and path.is_file()
    try:
        with Image.open(path) as card:
            assert card.format == "JPEG"
            assert card.size == (1200, 920)
            pixel = card.convert("RGB").getpixel((2, 2))
            assert pixel[0] > 240 and 90 < pixel[1] < 140 and 130 < pixel[2] < 180
            # The cover and avatar must be separate original images.
            assert card.getpixel((600, 300)) == pytest.approx((20, 80, 160), abs=5)
            assert card.getpixel((95, 851)) == pytest.approx((220, 180, 120), abs=5)
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def test_metadata_failure_skips_card_instead_of_using_qq_preview(monkeypatch):
    downloads = []
    monkeypatch.setattr(
        bilibili_card,
        "_resolve_bilibili_video_url",
        lambda url: (_ for _ in ()).throw(OSError("offline")),
    )
    monkeypatch.setattr(
        bilibili_card,
        "_download_image_bytes",
        lambda url, max_bytes: downloads.append(url),
    )

    path = bilibili_card.generate_bilibili_card(
        BilibiliCardRequest(
            source_url="https://b23.tv/wrXwLXN",
            fallback_title="QQ卡片标题",
        )
    )

    assert path is None
    assert downloads == []


def test_avatar_failure_uses_text_placeholder(monkeypatch):
    cover_body = _image_bytes()
    captured = {}
    metadata = BilibiliVideoMetadata(
        title="视频标题",
        cover_url="https://i1.hdslb.com/bfs/archive/cover.jpg",
        author_name="作者名字",
        author_avatar_url="https://i2.hdslb.com/bfs/face/avatar.jpg",
    )
    monkeypatch.setattr(
        bilibili_card,
        "_resolve_bilibili_video_url",
        lambda url: "https://www.bilibili.com/video/BV1Gyu36LEfL",
    )
    monkeypatch.setattr(bilibili_card, "_fetch_bilibili_metadata", lambda bvid: metadata)

    def fake_download(url, max_bytes):
        if "avatar" in url:
            raise OSError("avatar unavailable")
        return cover_body

    monkeypatch.setattr(bilibili_card, "_download_image_bytes", fake_download)

    def fake_render(**kwargs):
        captured.update(kwargs)
        kwargs["output"].write_bytes(b"card")

    monkeypatch.setattr(bilibili_card, "_render_bilibili_card", fake_render)

    path = bilibili_card.generate_bilibili_card(
        BilibiliCardRequest(source_url="https://b23.tv/wrXwLXN")
    )

    assert path is not None
    try:
        assert captured["author_name"] == "作者名字"
        assert captured["avatar_body"] is None
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


def test_cover_failure_returns_no_card(monkeypatch):
    monkeypatch.setattr(
        bilibili_card,
        "_resolve_bilibili_video_url",
        lambda url: url,
    )
    monkeypatch.setattr(
        bilibili_card,
        "_fetch_bilibili_metadata",
        lambda bvid: BilibiliVideoMetadata(
            title="标题",
            cover_url="https://i1.hdslb.com/bfs/archive/cover.jpg",
            author_name="作者",
            author_avatar_url="https://i2.hdslb.com/bfs/face/avatar.jpg",
        ),
    )
    monkeypatch.setattr(
        bilibili_card,
        "_download_image_bytes",
        lambda url, max_bytes: (_ for _ in ()).throw(OSError("missing")),
    )

    path = bilibili_card.generate_bilibili_card(
        BilibiliCardRequest(
            source_url="https://www.bilibili.com/video/BV1Gyu36LEfL",
            fallback_title="标题",
        )
    )

    assert path is None


def test_long_title_is_limited_to_two_lines():
    image = Image.new("RGB", (1200, 920), "white")
    draw = ImageDraw.Draw(image)
    font = bilibili_card._load_font(46, bold=True)

    lines = bilibili_card._wrap_text(
        draw,
        "很长的视频标题" * 40,
        font,
        max_width=1072,
        max_lines=2,
    )

    assert len(lines) == 2
    assert lines[-1].endswith("…")


def test_title_wrap_and_truncation_keep_complete_emoji_clusters(monkeypatch):
    from qq_personal_bot.drawing import grapheme_clusters

    font = object()
    monkeypatch.setattr(bilibili_card, "_text_width", lambda draw, text, font: len(grapheme_clusters(text)) * 10)
    monkeypatch.setattr("qq_personal_bot.drawing.text_width_with_fallback",
                        lambda draw, text, font: len(grapheme_clusters(text)) * 10)
    text = "甲👩‍💻🇨🇳👍🏽乙丙"
    lines = bilibili_card._wrap_text(None, text, font, max_width=20, max_lines=2)
    assert lines == ["甲👩‍💻", "🇨🇳…"]


@pytest.mark.parametrize("text,limit,expected", [
    ("甲👩‍💻乙", 2, "甲"),
    ("甲🇨🇳乙", 2, "甲"),
    ("甲👍🏽乙", 2, "甲"),
    ("甲🌸乙", 2, "甲🌸"),
])
def test_metadata_length_limit_never_cuts_an_emoji(text, limit, expected):
    assert bilibili_card._clean_text(text, limit) == expected


def test_card_title_and_author_use_fallback_renderer(tmp_path, monkeypatch):
    calls = []
    original = bilibili_card._draw_text

    def track(draw, position, text, **kwargs):
        calls.append(text)
        return original(draw, position, text, **kwargs)

    monkeypatch.setattr(bilibili_card, "_draw_text", track)
    bilibili_card._render_bilibili_card(
        cover_body=_image_bytes((20, 80, 160), (1280, 720)), title="测试 😊🌸",
        author_name="UP 主🎮", avatar_body=None, output=tmp_path / "emoji.jpg",
    )
    assert "测试 😊🌸" in calls
    assert "UP 主🎮" in calls
