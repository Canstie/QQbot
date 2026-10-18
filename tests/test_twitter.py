from __future__ import annotations

import asyncio
from io import BytesIO

import httpx
import pytest
from PIL import Image

from qq_personal_bot import twitter
from qq_personal_bot.settings import AppSettings

STATUS_ID = "2111677165825933692"
LINK = f"https://x.com/Kyzen6mhr/status/{STATUS_ID}?s=20"
PROXY = "http://127.0.0.1:20171"


def photo(name, **extra):
    return {"type": "photo", "url": f"https://pbs.twimg.com/media/{name}.png?name=small", **extra}


def payload(photos):
    return {"code": 200, "tweet": {"id": STATUS_ID, "media": {"all": photos, "photos": photos}}}


def image_bytes():
    output = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(output, format="PNG")
    return output.getvalue()


def install_client(monkeypatch, handler):
    real_client = httpx.AsyncClient
    options = []

    def factory(**kwargs):
        options.append(dict(kwargs))
        kwargs.pop("proxy")
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(twitter.httpx, "AsyncClient", factory)
    return options


@pytest.mark.parametrize("url", [
    LINK, f"https://twitter.com/Kyzen6mhr/status/{STATUS_ID}/photo/2",
    f"https://mobile.x.com/i/web/status/{STATUS_ID}", f"https://x.com/i/status/{STATUS_ID}",
])
def test_post_links_are_normalized_without_tracking_or_image_position(url):
    assert twitter.twitter_status_url(url) == f"https://x.com/i/status/{STATUS_ID}"


@pytest.mark.parametrize("url", [
    "https://x.com/Kyzen6mhr", "https://x.com.evil.test/user/status/123",
    "https://x.com@evil.test/user/status/123", "https://evil.test/?url=" + LINK,
    "https://x.com:8443/user/status/123", "https://x.com/user/status/123/evil",
])
def test_non_posts_and_lookalike_hosts_are_not_parsed(url):
    assert twitter.twitter_status_url(url) is None


def test_media_order_is_preserved_and_video_thumbnails_quotes_and_avatars_are_excluded():
    data = payload([photo("first"), {"type": "video", "thumbnail_url": photo("thumbnail")["url"]},
                    photo("second")])
    data["tweet"]["media"]["photos"] = [photo("second"), photo("third")]
    data["tweet"]["quote"] = {"media": {"photos": [photo("quoted")]}}
    urls = twitter._photo_urls(data, STATUS_ID)
    assert [httpx.URL(url).path for url in urls] == ["/media/first", "/media/second", "/media/third"]
    assert all(dict(httpx.URL(url).params) == {"format": "png", "name": "orig"} for url in urls)


@pytest.mark.parametrize("url", [
    "https://evil.test/media/test.png", "http://pbs.twimg.com/media/test.png",
    "https://pbs.twimg.com:8443/media/test.png", "https://pbs.twimg.com/profile_images/avatar.png",
    "https://pbs.twimg.com/media/test.svg", "https://user:secret@pbs.twimg.com/media/test.png",
])
def test_untrusted_image_addresses_are_rejected(url):
    with pytest.raises(twitter.TwitterImageError):
        twitter._photo_urls(payload([{"type": "photo", "url": url}]), STATUS_ID)


@pytest.mark.asyncio
async def test_no_proxy_never_falls_back_to_a_direct_request(monkeypatch):
    monkeypatch.setattr(twitter.httpx, "AsyncClient", lambda **kwargs: pytest.fail("must not connect"))
    with pytest.raises(twitter.TwitterImageError, match="显式代理"):
        await twitter.cache_twitter_images(LINK, proxy_url="")


@pytest.mark.asyncio
async def test_every_photo_is_downloaded_through_the_explicit_proxy_in_post_order(monkeypatch):
    requests = []
    photos = [photo(f"photo{i}") for i in range(4)]

    async def handler(request):
        requests.append(request)
        if request.url.host == "api.fxtwitter.com":
            return httpx.Response(200, json=payload(photos))
        assert request.url.host == "pbs.twimg.com"
        assert request.url.params["name"] == "orig"
        if request.url.path.endswith("photo0"):
            await asyncio.sleep(0.01)
        return httpx.Response(200, content=image_bytes(), headers={"Content-Type": "image/png"})

    options = install_client(monkeypatch, handler)
    cached = await twitter.cache_twitter_images(LINK, proxy_url=PROXY)
    try:
        assert [path.name for path in cached.paths] == ["01.png", "02.png", "03.png", "04.png"]
        assert all(path.read_bytes() == image_bytes() for path in cached.paths)
        assert len(requests) == 5
        assert options[0]["proxy"] == PROXY
        assert options[0]["trust_env"] is False and options[0]["follow_redirects"] is False
    finally:
        cached.cleanup()
    assert not cached.directory.exists()


@pytest.mark.asyncio
async def test_unavailable_primary_api_uses_v2_contract(monkeypatch):
    urls = []

    def handler(request):
        urls.append(str(request.url))
        if request.url.path.startswith("/status/"):
            return httpx.Response(503)
        if request.url.path.startswith("/2/status/"):
            return httpx.Response(200, json={"status": payload([photo("image")])["tweet"]})
        return httpx.Response(200, content=image_bytes(), headers={"Content-Type": "image/png"})

    install_client(monkeypatch, handler)
    cached = await twitter.cache_twitter_images(LINK, proxy_url=PROXY)
    try:
        assert len(cached.paths) == 1
        assert len(urls) == 3
    finally:
        cached.cleanup()


@pytest.mark.asyncio
async def test_one_failed_image_does_not_send_a_silently_incomplete_album(monkeypatch, tmp_path):
    directory = tmp_path / "cache"
    directory.mkdir()
    monkeypatch.setattr(twitter.tempfile, "mkdtemp", lambda **kwargs: str(directory))

    def handler(request):
        if request.url.host == "api.fxtwitter.com":
            return httpx.Response(200, json=payload([photo("good"), photo("bad")]))
        if request.url.path.endswith("bad"):
            return httpx.Response(403)
        return httpx.Response(200, content=image_bytes(), headers={"Content-Type": "image/png"})

    install_client(monkeypatch, handler)
    with pytest.raises(twitter.TwitterImageError, match="下载失败"):
        await twitter.cache_twitter_images(LINK, proxy_url=PROXY)
    assert not directory.exists()


@pytest.mark.asyncio
async def test_metadata_redirect_is_not_followed_to_an_arbitrary_host(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})

    install_client(monkeypatch, handler)
    with pytest.raises(twitter.TwitterImageError, match="HTTP 302"):
        await twitter.cache_twitter_images(LINK, proxy_url=PROXY)
    assert len(requests) == 2 and all(r.url.host == "api.fxtwitter.com" for r in requests)


def test_twitter_proxy_is_independent_of_ai_route_choice_and_validated():
    settings = AppSettings.from_env({"QQBOT_AI_PROXY_URL": PROXY})
    assert settings.twitter_proxy_url == PROXY
    override = AppSettings.from_env({"QQBOT_AI_PROXY_URL": PROXY, "QQBOT_TWITTER_PROXY_URL": "http://127.0.0.1:7890"})
    assert override.twitter_proxy_url == "http://127.0.0.1:7890"
    with pytest.raises(ValueError, match="QQBOT_TWITTER_PROXY_URL") as error:
        AppSettings.from_env({"QQBOT_TWITTER_PROXY_URL": "socks5://user:private-secret@127.0.0.1:1080"}).validate()
    assert "private-secret" not in str(error.value)
