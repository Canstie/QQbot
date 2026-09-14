from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import quote, urlparse

XIAOHEIHE_CAPTCHA_TTL_SECONDS = 10 * 60
_MAX_PENDING_CHALLENGES = 64


@dataclass(frozen=True)
class XiaoheiheCaptchaChallenge:
    token: str
    source_url: str
    group_id: int
    bot_id: str
    appid: str
    expires_at: float


class XiaoheiheCaptchaStore:
    def __init__(self) -> None:
        self._items: dict[str, XiaoheiheCaptchaChallenge] = {}
        self._processing: set[str] = set()
        self._lock = threading.RLock()

    def create(
        self,
        *,
        source_url: str,
        group_id: int,
        bot_id: str,
        appid: str,
        now: float | None = None,
    ) -> tuple[XiaoheiheCaptchaChallenge, bool]:
        current = time.time() if now is None else float(now)
        with self._lock:
            self._prune(current)
            for challenge in self._items.values():
                if (
                    challenge.source_url == source_url
                    and challenge.group_id == int(group_id)
                    and challenge.bot_id == str(bot_id)
                ):
                    return challenge, False

            while len(self._items) >= _MAX_PENDING_CHALLENGES:
                oldest_token = next(iter(self._items))
                self._items.pop(oldest_token, None)
                self._processing.discard(oldest_token)

            token = secrets.token_urlsafe(32)
            challenge = XiaoheiheCaptchaChallenge(
                token=token,
                source_url=source_url,
                group_id=int(group_id),
                bot_id=str(bot_id),
                appid=appid,
                expires_at=current + XIAOHEIHE_CAPTCHA_TTL_SECONDS,
            )
            self._items[token] = challenge
            return challenge, True

    def get(
        self,
        token: str,
        *,
        now: float | None = None,
    ) -> XiaoheiheCaptchaChallenge | None:
        current = time.time() if now is None else float(now)
        with self._lock:
            self._prune(current)
            return self._items.get(token)

    def consume(self, token: str) -> XiaoheiheCaptchaChallenge | None:
        with self._lock:
            self._processing.discard(token)
            return self._items.pop(token, None)

    def claim(
        self,
        token: str,
        *,
        now: float | None = None,
    ) -> XiaoheiheCaptchaChallenge | None:
        current = time.time() if now is None else float(now)
        with self._lock:
            self._prune(current)
            challenge = self._items.get(token)
            if challenge is None or token in self._processing:
                return None
            self._processing.add(token)
            return challenge

    def release(self, token: str) -> None:
        with self._lock:
            self._processing.discard(token)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._processing.clear()

    def _prune(self, now: float) -> None:
        expired = [token for token, item in self._items.items() if item.expires_at <= now]
        for token in expired:
            self._items.pop(token, None)
            self._processing.discard(token)


_captcha_store = XiaoheiheCaptchaStore()


def get_xiaoheihe_captcha_store() -> XiaoheiheCaptchaStore:
    return _captcha_store


def build_xiaoheihe_captcha_url(public_base_url: str, token: str) -> str:
    base_url = public_base_url.strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("QQBOT_PUBLIC_BASE_URL must be an absolute HTTP(S) URL")
    return f"{base_url}/xiaoheihe-captcha/{quote(token, safe='')}"
