import asyncio
import json
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from fastapi import HTTPException
from fastapi.responses import (
    HTMLResponse,
    Response,
)
from nonebot import get_bot, logger
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from pydantic import BaseModel, Field

from qq_personal_bot.miniapp import (
    XiaoheiheCaptchaRequired,
    cache_xiaoheihe_images_with_captcha,
)
from qq_personal_bot.xiaoheihe_captcha import get_xiaoheihe_captcha_store


class XiaoheiheCaptchaPayload(BaseModel):
    ticket: str = Field(min_length=1, max_length=4096)
    randstr: str = Field(min_length=1, max_length=1024)


_TENCENT_CAPTCHA_SDK_URL = "https://turing.captcha.qcloud.com/TCaptcha.js"

_MAX_CAPTCHA_SDK_BYTES = 512_000


def _fetch_tencent_captcha_sdk() -> bytes:
    request = UrlRequest(
        _TENCENT_CAPTCHA_SDK_URL,
        headers={
            "Accept": "application/javascript,*/*;q=0.8",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
            ),
        },
    )
    with urlopen(request, timeout=15) as response:
        final_url = urlparse(response.geturl())
        if final_url.scheme != "https" or final_url.hostname != "turing.captcha.qcloud.com":
            raise ValueError("Tencent CAPTCHA SDK redirected to an unsupported host")
        content = response.read(_MAX_CAPTCHA_SDK_BYTES + 1)
    if len(content) > _MAX_CAPTCHA_SDK_BYTES:
        raise ValueError("Tencent CAPTCHA SDK is too large")
    if b"TencentCaptcha" not in content:
        raise ValueError("Tencent CAPTCHA SDK response is invalid")
    return content


def _xiaoheihe_captcha_page_html(appid: str) -> str:
    appid_json = json.dumps(appid)
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="referrer" content="no-referrer" />
  <title>小黑盒验证</title>
  <style>
    :root { color-scheme: light; --accent: #2563eb; --muted: #64748b; }
    * { box-sizing: border-box; }
    body {
      min-height: 100vh; margin: 0; display: grid; place-items: center;
      padding: 24px; background: #f1f5f9; color: #0f172a;
      font-family: "Noto Sans SC", "Microsoft YaHei", sans-serif;
    }
    main {
      width: min(92vw, 420px); padding: 30px; border-radius: 18px;
      background: #fff; box-shadow: 0 20px 60px rgba(15, 23, 42, .12);
    }
    h1 { margin: 0; font-size: 26px; }
    p { margin: 12px 0 0; color: var(--muted); line-height: 1.7; }
    button {
      width: 100%; margin-top: 24px; border: 0; border-radius: 12px;
      padding: 14px 18px; background: var(--accent); color: #fff;
      font: inherit; font-weight: 700; cursor: pointer;
    }
    button:disabled { cursor: wait; opacity: .65; }
    #status { min-height: 24px; color: #334155; }
  </style>
</head>
<body>
  <main>
    <h1>小黑盒安全验证</h1>
    <p>完成验证后，机器人会自动重新解析卡片，并把图片发回原群。</p>
    <button id="verify" type="button" disabled>正在加载验证组件</button>
    <p id="status">正在连接腾讯验证码服务……</p>
  </main>
  <script>
    const appid = __APPID__;
    const button = document.getElementById("verify");
    const statusNode = document.getElementById("status");
    const sdkUrls = [
      `${location.pathname}/sdk.js`,
      "https://turing.captcha.qcloud.com/TCaptcha.js",
      "https://turing.captcha.qcloud.com/TJCaptcha.js"
    ];
    let sdkPromise = null;

    function setStatus(message, failed = false) {
      statusNode.textContent = message;
      statusNode.style.color = failed ? "#dc2626" : "#334155";
    }

    function loadScript(url) {
      return new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = url;
        script.async = true;
        script.referrerPolicy = "no-referrer";
        script.onload = () => {
          if (typeof window.TencentCaptcha === "function") {
            resolve();
          } else {
            reject(new Error("SDK 未完成初始化"));
          }
        };
        script.onerror = () => reject(new Error("SDK 下载失败"));
        document.head.appendChild(script);
      });
    }

    function loadCaptchaSdk() {
      if (typeof window.TencentCaptcha === "function") {
        return Promise.resolve();
      }
      if (sdkPromise) {
        return sdkPromise;
      }
      sdkPromise = (async () => {
        let lastError = null;
        for (const url of sdkUrls) {
          try {
            await loadScript(url);
            return;
          } catch (error) {
            lastError = error;
          }
        }
        throw lastError || new Error("验证码 SDK 加载失败");
      })().catch((error) => {
        sdkPromise = null;
        throw error;
      });
      return sdkPromise;
    }

    async function prepareCaptcha() {
      button.disabled = true;
      button.textContent = "正在加载验证组件";
      setStatus("正在连接腾讯验证码服务……");
      try {
        await loadCaptchaSdk();
        button.disabled = false;
        button.textContent = "开始验证";
        setStatus("链接 10 分钟内有效，且只能成功使用一次。");
        return true;
      } catch (error) {
        button.disabled = false;
        button.textContent = "重新加载";
        setStatus("验证码组件加载失败，请用系统浏览器打开或点击重试。", true);
        return false;
      }
    }

    button.addEventListener("click", async () => {
      if (typeof window.TencentCaptcha !== "function" && !(await prepareCaptcha())) {
        return;
      }
      let captcha;
      try {
        captcha = new window.TencentCaptcha(appid, async (result) => {
          if (!result || result.ret !== 0) {
            setStatus("验证已取消，可以重新尝试。", true);
            return;
          }
          button.disabled = true;
          setStatus("验证通过，正在重新解析并发送图片……");
          try {
            const response = await fetch(`${location.pathname}/verify`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ ticket: result.ticket, randstr: result.randstr })
            });
            const data = await response.json();
            if (!response.ok) {
              throw new Error(typeof data.detail === "string" ? data.detail : "处理失败");
            }
            setStatus(data.message || "验证成功，图片已发送回原群。");
            button.textContent = "已完成";
          } catch (error) {
            button.disabled = false;
            setStatus(error.message || "处理失败，请重试。", true);
          }
        });
      } catch (error) {
        setStatus("验证码初始化失败，请刷新页面后重试。", true);
        return;
      }
      captcha.show();
    });

    prepareCaptcha();
  </script>
</body>
</html>""".replace("__APPID__", appid_json)


def _xiaoheihe_captcha_expired_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>验证链接已失效</title>
</head>
<body style="font-family: sans-serif; padding: 32px; color: #0f172a">
  <h1>验证链接已失效</h1>
  <p>请让群友重新发送小黑盒卡片，机器人会私聊管理员新的验证链接。</p>
</body>
</html>"""


def register_routes(app, require_token):
    @app.get("/xiaoheihe-captcha/{token}/sdk.js")
    async def xiaoheihe_captcha_sdk(token: str) -> Response:
        if get_xiaoheihe_captcha_store().get(token) is None:
            raise HTTPException(status_code=410, detail="验证链接已过期")
        try:
            content = await asyncio.to_thread(_fetch_tencent_captcha_sdk)
        except (OSError, ValueError) as exc:
            logger.warning(f"Failed to proxy Tencent CAPTCHA SDK: {exc}")
            raise HTTPException(status_code=502, detail="验证码组件暂时不可用") from exc
        return Response(
            content=content,
            media_type="application/javascript",
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/xiaoheihe-captcha/{token}")
    async def xiaoheihe_captcha_page(token: str) -> Response:
        challenge = get_xiaoheihe_captcha_store().get(token)
        if challenge is None:
            return HTMLResponse(
                _xiaoheihe_captcha_expired_html(),
                status_code=410,
                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
            )
        return HTMLResponse(
            _xiaoheihe_captcha_page_html(challenge.appid),
            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
        )

    @app.post("/xiaoheihe-captcha/{token}/verify")
    async def verify_xiaoheihe_captcha(
        token: str,
        payload: XiaoheiheCaptchaPayload,
    ) -> dict[str, Any]:
        captcha_store = get_xiaoheihe_captcha_store()
        if captcha_store.get(token) is None:
            raise HTTPException(status_code=410, detail="验证链接已过期，请重新发送小黑盒卡片")
        challenge = captcha_store.claim(token)
        if challenge is None:
            raise HTTPException(status_code=409, detail="正在处理本次验证，请稍候")

        try:
            try:
                cached_images = await cache_xiaoheihe_images_with_captcha(
                    challenge.source_url,
                    ticket=payload.ticket,
                    randstr=payload.randstr,
                )
            except XiaoheiheCaptchaRequired as exc:
                raise HTTPException(
                    status_code=409,
                    detail="验证码未通过或已失效，请重试",
                ) from exc
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                logger.warning(f"Failed to retry Xiaoheihe after CAPTCHA: {exc}")
                raise HTTPException(status_code=502, detail="小黑盒解析失败，请稍后重试") from exc

            try:
                if not cached_images.paths:
                    raise HTTPException(status_code=502, detail="没有解析到可发送的图片")
                try:
                    bot = get_bot(challenge.bot_id)
                except (KeyError, ValueError) as exc:
                    raise HTTPException(
                        status_code=503, detail="机器人当前未连接，请稍后重试"
                    ) from exc

                message = Message()
                for path in cached_images.paths:
                    message += MessageSegment.image(path.resolve().as_uri())
                try:
                    await bot.send_group_msg(group_id=challenge.group_id, message=message)
                except Exception as exc:
                    logger.warning(f"Failed to send CAPTCHA result to QQ group: {exc}")
                    raise HTTPException(status_code=502, detail="图片发送失败，请稍后重试") from exc
            finally:
                cached_images.cleanup()
        except Exception:
            captcha_store.release(token)
            raise

        captcha_store.consume(token)
        return {"ok": True, "message": "验证成功，图片已发送回原群"}
