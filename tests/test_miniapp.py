from __future__ import annotations

import json

from qq_personal_bot.miniapp import extract_miniapp_link, format_miniapp_link


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
