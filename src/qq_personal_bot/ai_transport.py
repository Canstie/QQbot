from __future__ import annotations

import atexit
import logging
import ssl
import threading
import time
from contextlib import contextmanager
from functools import wraps
from urllib.parse import urlsplit
from urllib.request import Request

import httpx

from qq_personal_bot.settings import AppSettings

logger = logging.getLogger(__name__)
_clients: dict[tuple[str, str, str, str], httpx.Client] = {}
_clients_lock = threading.RLock()
_borrowers: dict[httpx.Client, int] = {}
_retired: set[httpx.Client] = set()
_RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
_CONNECT_TIMEOUT = 6.0


class AITransportError(OSError):
    """A sanitized transport error; never includes credentials or request content."""


def _ssl_context(tls_mode: str, server_hostname: str = "") -> ssl.SSLContext:
    context = ssl.create_default_context()
    if tls_mode == "tls12":
        context.maximum_version = ssl.TLSVersion.TLSv1_2
    if server_hostname:
        # HTTPcore 1.0 ignores sni_hostname for CONNECT tunnels. Bind the upstream
        # context to its DNS name, preserving certificate checks in both routes.
        # HTTPS proxies use their own SSL context, so their hostname is unaffected.
        def bind_hostname(wrap):
            @wraps(wrap)
            def verified_wrap(*args, **kwargs):
                kwargs["server_hostname"] = server_hostname
                return wrap(*args, **kwargs)
            return verified_wrap

        context.wrap_socket = bind_hostname(context.wrap_socket)
        context.wrap_bio = bind_hostname(context.wrap_bio)
    return context


def _get_client(
    url: str, tls_mode: str, proxy_url: str = "", connect_ip: str = "",
) -> httpx.Client:
    parsed = urlsplit(url)
    key = (f"{parsed.scheme}://{parsed.netloc}", tls_mode, proxy_url, connect_ip)
    with _clients_lock:
        client = _clients.get(key)
        if client is None:
            context = _ssl_context(tls_mode, parsed.hostname if connect_ip else "")
            limits = httpx.Limits(
                max_connections=32, max_keepalive_connections=16, keepalive_expiry=600,
            )
            client = httpx.Client(
                verify=context,
                transport=httpx.HTTPTransport(
                    verify=context, limits=limits, local_address="0.0.0.0", retries=0,
                    proxy=proxy_url or None, trust_env=False,
                ),
                limits=limits,
                follow_redirects=False,
                trust_env=False,
            )
            _clients[key] = client
        return client


@contextmanager
def _borrow_client(url: str, settings: AppSettings, connect_ip: str):
    with _clients_lock:
        client = _get_client(url, settings.dsapi_tls_mode, settings.ai_proxy_url, connect_ip)
        _borrowers[client] = _borrowers.get(client, 0) + 1
    try:
        yield client
    finally:
        with _clients_lock:
            remaining = _borrowers[client] - 1
            if remaining:
                _borrowers[client] = remaining
            else:
                del _borrowers[client]
            close = not remaining and client in _retired
            if close:
                _retired.remove(client)
        if close:
            client.close()


def _retire_client(client: httpx.Client) -> None:
    # A failed CONNECT TLS handshake can leave an unusable ACTIVE tunnel in
    # HTTPcore's pool. Stop lending that pool; close after its real users finish.
    with _clients_lock:
        keys = [key for key, cached in _clients.items() if cached is client]
        for key in keys:
            del _clients[key]
        if keys:
            _retired.add(client)


def close_ai_clients() -> None:
    with _clients_lock:
        clients = set(_clients.values()) | _retired
        _clients.clear()
        _retired.clear()
    for client in clients:
        client.close()


atexit.register(close_ai_clients)


class _TimingTrace:
    def __init__(self) -> None:
        self.phase = "connection_pool"
        self.failed_phase = ""
        self.phases: dict[str, float] = {}
        self.starts: dict[str, float] = {}

    def __call__(self, event: str, info: dict) -> None:
        name, _, state = event.rpartition(".")
        short_name = name.rpartition(".")[2]
        if state == "started":
            self.phase = short_name
            self.starts[name] = time.monotonic()
        elif state in {"complete", "failed"} and name in self.starts:
            self.phases[short_name] = round((time.monotonic() - self.starts[name]) * 1000, 1)
            if state == "failed":
                self.failed_phase = self.failed_phase or short_name

    @property
    def error_phase(self) -> str:
        return self.failed_phase or self.phase

    @property
    def reused_connection(self) -> bool:
        return any(name.endswith(".send_request_headers") for name in self.starts) and not any(
            name.endswith(".connect_tcp") for name in self.starts
        )


def request_ai_bytes(request: Request, settings: AppSettings) -> bytes:
    """Reuse verified connections and give retries only the remaining request budget."""
    started = time.monotonic()
    deadline = started + settings.dsapi_timeout_seconds
    original_url = httpx.URL(request.full_url)
    connect_ip = dict(settings.ai_host_overrides).get(original_url.host.rstrip("."), "")
    target_url = original_url.copy_with(host=connect_ip) if connect_ip else original_url
    headers = httpx.Headers(request.header_items())
    if connect_ip:
        headers["Host"] = original_url.netloc.decode("ascii")
    method = request.get_method()
    last_error = "request timed out"
    operation = "models" if method == "GET" else "completion"
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        attempt_started = time.monotonic()
        trace = _TimingTrace()

        retry = False
        status: int | None = None
        try:
            with _borrow_client(request.full_url, settings, connect_ip) as client:
                try:
                    response = client.request(
                        method, target_url, content=request.data, headers=headers,
                        timeout=httpx.Timeout(
                            remaining, connect=min(_CONNECT_TIMEOUT, remaining),
                            write=min(30, remaining), pool=min(5, remaining),
                        ),
                        extensions={"trace": trace},
                    )
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError):
                    _retire_client(client)
                    raise
            status = response.status_code
            if 200 <= status < 300:
                return response.content
            last_error = f"HTTP {status}"
            retry = status in _RETRY_STATUSES
            if status == 429:
                try:
                    # Do not immediately replay a request the gateway asked us to delay.
                    retry = float(response.headers.get("Retry-After", "0")) <= 0.25
                except ValueError:
                    retry = False
        except (httpx.ConnectError, httpx.ConnectTimeout):
            last_error = f"connection failed during {trace.error_phase}"
            retry = True
        except (httpx.ReadError, httpx.RemoteProtocolError):
            last_error = f"connection interrupted during {trace.error_phase}"
            retry = True
        except httpx.ReadTimeout:
            last_error = f"request timed out during {trace.error_phase}"
            # A long POST may already be generating; don't queue another full generation.
            retry = method == "GET"
        except httpx.PoolTimeout:
            last_error = "AI connection pool is busy; please retry shortly"
        except httpx.TimeoutException:
            last_error = f"request timed out during {trace.error_phase}"
        except httpx.ProxyError:
            last_error = f"proxy connection failed during {trace.error_phase}"
        except httpx.RequestError:
            last_error = f"network error during {trace.error_phase}"
        finally:
            logger.info(
                "AI transport provider=%s operation=%s attempt=%s status=%s phase=%s request_bytes=%s "
                "elapsed_ms=%.1f reused_connection=%s via_proxy=%s connect_ip=%s phases_ms=%s",
                settings.dsapi_provider_id, operation, attempt + 1, status, trace.error_phase, len(request.data or b""),
                (time.monotonic() - attempt_started) * 1000,
                trace.reused_connection, bool(settings.ai_proxy_url), connect_ip or "dns", trace.phases,
            )
        if attempt or not retry or deadline - time.monotonic() <= 0.25:
            break
        time.sleep(0.25)
    raise AITransportError(last_error)
