from __future__ import annotations

import json
from email.message import Message

import pytest

from qq_personal_bot.miniapp import (
    cache_miniapp_images,
    extract_miniapp_link,
    format_miniapp_link,
)


def test_extracts_title_and_xiaohongshu_url_from_qq_miniapp():
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

    link = extract_miniapp_link(segments)

    assert link is not None
    assert link.title == "今天也要好好生活"
    assert link.url == "https://www.xiaohongshu.com/explore/abc123"
    assert format_miniapp_link(link) == (
        "标题：今天也要好好生活\n"
        "链接：https://www.xiaohongshu.com/explore/abc123"
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

    link = extract_miniapp_link(({"type": "json", "data": payload},))

    assert link is not None
    assert link.title == "小红书标题"
    assert link.url == "https://xhslink.com/a/AbCd"


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

    link = extract_miniapp_link(
        ({"type": "json", "data": {"data": json.dumps(payload, ensure_ascii=False)}},)
    )

    assert link is not None
    assert link.title == "姜萍最新近况"
    assert link.url == "http://xhslink.com/m/8rpFU0xWGvV"


def test_bilibili_share_uses_video_title_instead_of_platform_name():
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

    link = extract_miniapp_link(
        ({"type": "json", "data": {"data": json.dumps(payload, ensure_ascii=False)}},)
    )

    assert link is not None
    assert link.title == "马儿空气动力学"
    assert link.url == "https://b23.tv/wrXwLXN?share_source=qq"


def test_removes_tracking_parameters_from_xiaohongshu_discovery_url():
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

    link = extract_miniapp_link(({"type": "json", "data": payload},))

    assert link is not None
    assert link.title == "感受姐1能量"
    assert link.url == "https://www.xiaohongshu.com/discovery/item/6a5db5790000000011013ecf"
    assert "xsec_token=secret" in link.source_url


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
    link = extract_miniapp_link(({"type": "json", "data": payload},))
    assert link is not None

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

    cached = await cache_miniapp_images(link)
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

    assert extract_miniapp_link(({"type": "json", "data": {"data": json.dumps(regular_card)}},)) is None
    assert extract_miniapp_link(({"type": "json", "data": {"data": json.dumps(invalid_miniapp)}},)) is None


def test_ignores_malformed_json_segment():
    assert extract_miniapp_link(({"type": "json", "data": {"data": "not-json"}},)) is None
