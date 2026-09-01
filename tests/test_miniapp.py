from __future__ import annotations

import json
from email.message import Message
from urllib.parse import parse_qs, urlparse

import pytest

from qq_personal_bot.miniapp import (
    cache_miniapp_images,
    extract_miniapp_image_source,
)


def test_extracts_xiaohongshu_image_source_from_qq_miniapp():
    payload = {
        "ver": "1.0.0.19",
        "prompt": "[QQ小程序]被治愈的一天",
        "meta": {
            "detail_1": {
                "title": "今天也要好好生活",
                "preview": "https://sns-webpic-qc.xhscdn.com/cover.jpg",
                "qqdocurl": "https://www.xiaohongshu.com/explore/abc123?xsec_token=test",
            }
        },
    }
    segments = ({"type": "json", "data": {"data": json.dumps(payload)}},)

    source = extract_miniapp_image_source(segments)

    assert source is not None
    assert source.source_url == (
        "https://www.xiaohongshu.com/explore/abc123?xsec_token=test"
    )


def test_extracts_prompt_and_unwraps_redirect_url():
    payload = {
        "app": "com.tencent.miniapp_01",
        "prompt": "[QQ小程序] 小红书标题 ",
        "meta": {
            "detail_1": {
                "jumpUrl": (
                    "https://example.qq.com/redirect?"
                    "url=https%3A%2F%2Fxhslink.com%2Fa%2FAbCd"
                )
            }
        },
    }

    source = extract_miniapp_image_source(({"type": "json", "data": payload},))

    assert source is not None
    assert source.source_url == "https://xhslink.com/a/AbCd"


def test_extracts_real_xiaohongshu_tuwen_share():
    payload = {
        "app": "com.tencent.tuwen.lua",
        "bizsrc": "qqconnect.sdkshare",
        "meta": {
            "news": {
                "desc": "姜萍最新近况",
                "jumpUrl": "http://xhslink.com/m/8rpFU0xWGvV",
                "preview": "https://pic.ugcimg.cn/cover/jpg1",
                "tag": "小红书",
                "title": "姜萍最新近况",
            }
        },
        "prompt": "[分享]姜萍最新近况",
        "view": "news",
    }

    source = extract_miniapp_image_source(
        ({"type": "json", "data": {"data": json.dumps(payload, ensure_ascii=False)}},)
    )

    assert source is not None
    assert source.source_url == "http://xhslink.com/m/8rpFU0xWGvV"


def test_ignores_bilibili_share_without_supported_image_parser():
    payload = {
        "ver": "1.0.0.19",
        "prompt": "[QQ小程序]马儿空气动力学",
        "app": "com.tencent.miniapp_01",
        "meta": {
            "detail_1": {
                "appid": "1109937557",
                "title": "哔哩哔哩",
                "desc": "马儿空气动力学",
                "preview": "https://qq.ugcimg.cn/preview",
                "qqdocurl": "https://b23.tv/wrXwLXN?share_source=qq",
            }
        },
    }

    source = extract_miniapp_image_source(
        ({"type": "json", "data": {"data": json.dumps(payload, ensure_ascii=False)}},)
    )

    assert source is None


def test_preserves_xiaohongshu_source_parameters_needed_for_image_fetch():
    payload = {
        "app": "com.tencent.tuwen.lua",
        "meta": {
            "news": {
                "title": "感受姐1能量",
                "jumpUrl": (
                    "https://www.xiaohongshu.com/discovery/item/6a5db5790000000011013ecf"
                    "?app_platform=android&ignoreEngage=true&app_version=9.42.0"
                    "&xsec_source=app_share&type=normal&xsec_token=secret"
                    "&shareRedId=test&share_channel=qq"
                ),
            }
        },
    }

    source = extract_miniapp_image_source(({"type": "json", "data": payload},))

    assert source is not None
    assert "xsec_token=secret" in source.source_url


@pytest.mark.asyncio
async def test_caches_all_xiaohongshu_images_and_cleans_up(monkeypatch):
    payload = {
        "app": "com.tencent.tuwen.lua",
        "meta": {
            "news": {
                "title": "两张图片",
                "jumpUrl": "https://www.xiaohongshu.com/discovery/item/note123?xsec_token=test",
            }
        },
    }
    source = extract_miniapp_image_source(({"type": "json", "data": payload},))
    assert source is not None

    state = {
        "note": {
            "noteDetailMap": {
                "note123": {
                    "note": {
                        "imageList": [
                            {"urlDefault": "https://sns-webpic-qc.xhscdn.com/first"},
                            {"urlDefault": "https://sns-webpic-qc.xhscdn.com/second"},
                        ]
                    }
                }
            }
        }
    }
    page = (
        "<script>window.__INITIAL_STATE__="
        + json.dumps(state, ensure_ascii=False)
        + ";</script>"
    ).encode()

    class FakeHeaders(Message):
        pass

    class FakeResponse:
        def __init__(self, body: bytes, url: str, content_type: str):
            self.body = body
            self.url = url
            self.headers = FakeHeaders()
            self.headers["Content-Type"] = content_type

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size):
            return self.body[:size]

        def geturl(self):
            return self.url

    def fake_urlopen(request, timeout):
        url = request.full_url
        if "discovery/item" in url:
            return FakeResponse(page, url, "text/html")
        if url.endswith("/first"):
            return FakeResponse(b"first-image", url, "image/jpeg")
        if url.endswith("/second"):
            return FakeResponse(b"second-image", url, "image/png")
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr("qq_personal_bot.miniapp.urlopen", fake_urlopen)

    cached = await cache_miniapp_images(source)
    directory = cached.directory

    assert directory is not None and directory.is_dir()
    assert [path.name for path in cached.paths] == ["01.jpg", "02.png"]
    assert [path.read_bytes() for path in cached.paths] == [b"first-image", b"second-image"]

    cached.cleanup()
    assert not directory.exists()


def test_extracts_xiaoheihe_image_source():
    source_url = (
        "https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?"
        "h_camp=link&h_session_id=session&h_src=encoded&link_id=c0687248f6da"
        "&new_post_share_style=true"
    )
    payload = {
        "app": "com.tencent.tuwen.lua",
        "meta": {
            "news": {
                "appid": 1105910806,
                "title": "里昂的变化[cube_喜欢]",
                "jumpUrl": source_url,
                "tag": "小黑盒",
            }
        },
        "prompt": "[分享]里昂的变化[cube_喜欢]",
    }

    source = extract_miniapp_image_source(({"type": "json", "data": payload},))

    assert source is not None
    assert source.source_url == source_url


@pytest.mark.asyncio
async def test_caches_all_xiaoheihe_images_from_signed_detail_api(monkeypatch):
    source_url = (
        "https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?"
        "h_session_id=session&link_id=c0687248f6da"
    )
    source = extract_miniapp_image_source(
        (
            {
                "type": "json",
                "data": {
                    "app": "com.tencent.tuwen.lua",
                    "meta": {"news": {"title": "里昂的变化", "jumpUrl": source_url}},
                },
            },
        )
    )
    assert source is not None

    first_url = "https://imgheybox1.max-c.com/bbs/first/thumb.jpeg?format=jpg"
    second_url = "https://bbsimg.maxjia.com/heybox/second.jpg"
    detail = {
        "status": "ok",
        "result": {
            "link": {
                "text": json.dumps(
                    [
                        {"type": "text", "text": "正文"},
                        {"type": "img", "url": first_url},
                        {"type": "img", "url": first_url},
                        {"type": "img", "url": "https://evil.example/image.jpg"},
                    ]
                ),
                "imgs": [second_url],
            }
        },
    }

    class FakeHeaders(Message):
        pass

    class FakeResponse:
        def __init__(self, body: bytes, url: str, content_type: str):
            self.body = body
            self.url = url
            self.headers = FakeHeaders()
            self.headers["Content-Type"] = content_type

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size):
            return self.body[:size]

        def geturl(self):
            return self.url

    def fake_urlopen(request, timeout):
        parsed = urlparse(request.full_url)
        if parsed.hostname == "api.xiaoheihe.cn":
            query = parse_qs(parsed.query)
            assert parsed.path == "/bbs/app/link/tree"
            assert query["link_id"] == ["c0687248f6da"]
            assert len(query["hkey"][0]) == 7
            assert len(query["nonce"][0]) == 32
            return FakeResponse(
                json.dumps(detail).encode(),
                request.full_url,
                "application/json",
            )
        if request.full_url == first_url:
            return FakeResponse(b"first-image", first_url, "image/jpeg")
        if request.full_url == second_url:
            return FakeResponse(b"second-image", second_url, "image/png")
        raise AssertionError(f"unexpected URL: {request.full_url}")

    monkeypatch.setattr("qq_personal_bot.miniapp.urlopen", fake_urlopen)

    cached = await cache_miniapp_images(source)
    directory = cached.directory

    assert directory is not None and directory.is_dir()
    assert [path.name for path in cached.paths] == ["01.jpg", "02.png"]
    assert [path.read_bytes() for path in cached.paths] == [b"first-image", b"second-image"]

    cached.cleanup()
    assert not directory.exists()


def test_ignores_regular_json_card_and_invalid_scheme():
    regular_card = {
        "app": "com.tencent.map",
        "meta": {"map": {"title": "某地点", "jumpUrl": "https://map.qq.com/a"}},
    }
    invalid_miniapp = {
        "prompt": "[QQ小程序]危险链接",
        "meta": {"detail_1": {"qqdocurl": "javascript:alert(1)"}},
    }

    assert extract_miniapp_image_source(
        ({"type": "json", "data": {"data": json.dumps(regular_card)}},)
    ) is None
    assert extract_miniapp_image_source(
        ({"type": "json", "data": {"data": json.dumps(invalid_miniapp)}},)
    ) is None


def test_ignores_malformed_json_segment():
    assert extract_miniapp_image_source(
        ({"type": "json", "data": {"data": "not-json"}},)
    ) is None
