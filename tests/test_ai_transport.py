from __future__ import annotations

import json
import logging
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request

import httpx
import pytest

from qq_personal_bot import ai_transport as transport
from qq_personal_bot.dsapi import DSAPIError, _request_chat_completion
from qq_personal_bot.settings import AppSettings


@pytest.fixture(autouse=True)
def clean_clients():
    transport.close_ai_clients()
    yield
    transport.close_ai_clients()


def settings(**kwargs):
    return AppSettings(
        db_path=Path("unused-transport-test.sqlite3"), admins=(),
        dsapi_provider_managed=True, dsapi_api_key="private-key", dsapi_provider_id=1,
        **kwargs,
    )


def request(*, method="POST", key="private-key"):
    return Request(
        "https://gateway.example/v1/chat/completions",
        data=b'{}' if method == "POST" else None,
        headers={"Authorization": f"Bearer {key}"}, method=method,
    )


@pytest.fixture
def mock_gateway(monkeypatch):
    clients = []

    def install(handler):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        clients.append(client)
        monkeypatch.setattr(transport, "_get_client", lambda *args: client)
        return client

    yield install
    for client in clients:
        client.close()


def test_reuses_tcp_connection_without_storing_auth_or_payload_in_logs(caplog):
    observed = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            observed.append((self.client_address[1], self.headers.get("Authorization")))
            self.rfile.read(int(self.headers["Content-Length"]))
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
        caplog.set_level(logging.INFO, logger=transport.__name__)
        for key in ("first-private-key", "second-private-key"):
            req = request(key=key)
            req.full_url = url
            req.data = b'{"private":"user message"}'
            assert json.loads(transport.request_ai_bytes(req, settings())) == {"ok": True}
        assert observed[0][0] == observed[1][0]
        assert [entry[1] for entry in observed] == [
            "Bearer first-private-key", "Bearer second-private-key",
        ]
        assert "reused_connection=True" in caplog.text
        assert "private-key" not in caplog.text
        assert "user message" not in caplog.text
    finally:
        transport.close_ai_clients()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize("mode", ["auto", "tls12"])
def test_all_tls_modes_verify_certificates(mode):
    context = transport._ssl_context(mode)
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    if mode == "tls12":
        assert context.maximum_version == ssl.TLSVersion.TLSv1_2


def test_connection_timeout_retries_quickly_with_remaining_budget(mock_gateway, monkeypatch):
    now = [0.0]
    attempts = []
    monkeypatch.setattr(transport.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(transport.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))

    def handle(req):
        attempts.append(req.extensions["timeout"].copy())
        if len(attempts) == 1:
            now[0] += 6
            raise httpx.ConnectTimeout("contains private-key", request=req)
        return httpx.Response(200, json={"ok": True})

    mock_gateway(handle)
    assert json.loads(transport.request_ai_bytes(request(), settings(dsapi_timeout_seconds=30)))
    assert attempts[0]["connect"] == 6
    assert attempts[0]["read"] == 30
    assert attempts[1]["read"] == 23.75
    assert len(attempts) == 2


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_transient_gateway_error_retries_once(status, mock_gateway, monkeypatch):
    attempts = []
    monkeypatch.setattr(transport.time, "sleep", lambda _: None)

    def handle(req):
        attempts.append(req)
        return httpx.Response(status if len(attempts) == 1 else 200, json={"ok": True})

    mock_gateway(handle)
    assert transport.request_ai_bytes(request(), settings())
    assert len(attempts) == 2


@pytest.mark.parametrize("status", [400, 401, 403])
def test_permanent_http_errors_do_not_retry(status, mock_gateway):
    attempts = []

    def handle(req):
        attempts.append(req)
        return httpx.Response(status, text="private error details")

    mock_gateway(handle)
    with pytest.raises(transport.AITransportError, match=f"HTTP {status}") as error:
        transport.request_ai_bytes(request(), settings())
    assert len(attempts) == 1
    assert "private" not in str(error.value)


def test_long_post_read_timeout_does_not_queue_another_generation(mock_gateway):
    attempts = []

    def handle(req):
        attempts.append(req)
        trace = req.extensions["trace"]
        trace("http11.receive_response_headers.started", {})
        trace("http11.receive_response_headers.failed", {})
        trace("http11.response_closed.started", {})
        raise httpx.ReadTimeout("private-key", request=req)

    mock_gateway(handle)
    with pytest.raises(transport.AITransportError, match="receive_response_headers"):
        transport.request_ai_bytes(request(), settings())
    assert len(attempts) == 1


def test_closed_keepalive_connection_retries_once(mock_gateway, monkeypatch):
    attempts = []
    monkeypatch.setattr(transport.time, "sleep", lambda _: None)

    def handle(req):
        attempts.append(req)
        if len(attempts) == 1:
            raise httpx.RemoteProtocolError("server closed connection", request=req)
        return httpx.Response(200, content=b"OK")

    mock_gateway(handle)
    assert transport.request_ai_bytes(request(), settings()) == b"OK"
    assert len(attempts) == 2


def test_retry_does_not_restart_exhausted_waiting_budget(mock_gateway, monkeypatch):
    now = [0.0]
    attempts = []
    monkeypatch.setattr(transport.time, "monotonic", lambda: now[0])

    def handle(req):
        attempts.append(req)
        now[0] += 30
        return httpx.Response(503)

    mock_gateway(handle)
    with pytest.raises(transport.AITransportError, match="HTTP 503"):
        transport.request_ai_bytes(request(), settings(dsapi_timeout_seconds=30))
    assert len(attempts) == 1


def test_rate_limit_retry_after_is_respected(mock_gateway):
    attempts = []

    def handle(req):
        attempts.append(req)
        return httpx.Response(429, headers={"Retry-After": "30"})

    mock_gateway(handle)
    with pytest.raises(transport.AITransportError, match="HTTP 429"):
        transport.request_ai_bytes(request(), settings())
    assert len(attempts) == 1


def test_transport_errors_are_wrapped_for_chat_handlers(mock_gateway):
    def handle(req):
        raise httpx.WriteTimeout("private-key", request=req)

    mock_gateway(handle)
    with pytest.raises(DSAPIError, match="request timed out") as error:
        _request_chat_completion(settings(), [{"role": "user", "content": "hello"}])
    assert "private-key" not in str(error.value)


def test_provider_tls_modes_get_separate_connection_pools():
    url = "https://gateway.example/v1/models"
    first = transport._get_client(url, "auto")
    assert transport._get_client(url, "auto") is first
    assert transport._get_client(url, "tls12") is not first
