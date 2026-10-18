from __future__ import annotations

import atexit
import logging
import ssl
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request

import httpx

from qq_personal_bot.settings import AppSettings

logger = logging.getLogger(__name__)
_clients: dict[tuple[str, str], httpx.Client] = {}
_clients_lock = threading.Lock()
_RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
_CONNECT_TIMEOUT = 6.0


class AITransportError(OSError):
    """A sanitized transport error; never includes credentials or request content."""


def _ssl_context(tls_mode: str) -> ssl.SSLContext:
    context = ssl.create_default_context()
    if tls_mode == "tls12":
        context.maximum_version = ssl.TLSVersion.TLSv1_2
    return context


def _get_client(url: str, tls_mode: str) -> httpx.Client:
    parsed = urlsplit(url)
    key = (f"{parsed.scheme}://{parsed.netloc}", tls_mode)
    with _clients_lock:
        client = _clients.get(key)
        if client is None:
            context = _ssl_context(tls_mode)
            limits = httpx.Limits(
                max_connections=16, max_keepalive_connections=8, keepalive_expiry=120,
            )
            client = httpx.Client(
                verify=context,
                transport=httpx.HTTPTransport(
                    verify=context, limits=limits, local_address="0.0.0.0", retries=0,
                ),
                limits=limits,
                follow_redirects=False,
            )
            _clients[key] = client
        return client


def close_ai_clients() -> None:
    with _clients_lock:
        clients = list(_clients.values())
        _clients.clear()
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
    client = _get_client(request.full_url, settings.dsapi_tls_mode)
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
            response = client.request(
                method, request.full_url, content=request.data, headers=request.header_items(),
                timeout=httpx.Timeout(
                    remaining, connect=min(_CONNECT_TIMEOUT, remaining),
                    write=min(30, remaining), pool=min(5, remaining),
                ),
                extensions={"trace": trace},
            )
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
        except httpx.TimeoutException:
            last_error = f"request timed out during {trace.error_phase}"
        except httpx.RequestError:
            last_error = f"network error during {trace.error_phase}"
        finally:
            logger.info(
                "AI transport provider=%s operation=%s attempt=%s status=%s phase=%s request_bytes=%s "
                "elapsed_ms=%.1f reused_connection=%s phases_ms=%s",
                settings.dsapi_provider_id, operation, attempt + 1, status, trace.error_phase, len(request.data or b""),
                (time.monotonic() - attempt_started) * 1000,
                trace.reused_connection, trace.phases,
            )
        if attempt or not retry or deadline - time.monotonic() <= 0.25:
            break
        time.sleep(0.25)
    raise AITransportError(last_error)
