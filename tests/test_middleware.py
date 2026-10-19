# Copyright 2026 MSPbots.ai
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Credential middleware: 401/400 on bad headers, domain normalisation, isolation."""

import asyncio
import base64

import pytest
from starlette.testclient import TestClient

from freshdesk_mcp.__main__ import build_http_app
from freshdesk_mcp.server import (
    GatewayCredentialsMiddleware,
    _credentials_var,
    create_mcp_server,
    get_client_from_context,
    normalize_subdomain,
)

MCP_HEADERS = {"Accept": "application/json, text/event-stream"}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
CREDS = {"X-Freshdesk-Domain": "yourcompany", "X-Freshdesk-Api-Key": "key"}


def _client() -> TestClient:
    return TestClient(build_http_app(create_mcp_server()))


def test_health_is_local():
    with _client() as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-Freshdesk-Domain": "yourcompany"}, {"X-Freshdesk-Api-Key": "key"}, {"X-Freshdesk-Domain": "yourcompany", "X-Freshdesk-Api-Key": "${KEY}"}],
)
def test_missing_headers_return_401(headers):
    with _client() as client:
        resp = client.post("/mcp", json=LIST_TOOLS, headers=MCP_HEADERS | headers)
    assert resp.status_code == 401
    assert resp.json()["required_headers"] == list(CREDS)


@pytest.mark.parametrize("domain", ["https://evil.example.com", "yourcompany.example.org", "a_b"])
def test_non_freshdesk_domain_returns_400(domain):
    with _client() as client:
        resp = client.post("/mcp", json=LIST_TOOLS, headers=MCP_HEADERS | CREDS | {"X-Freshdesk-Domain": domain})
    assert resp.status_code == 400


@pytest.mark.parametrize(
    "value,expected",
    [
        ("yourcompany", "yourcompany"),
        ("YOURCOMPANY", "yourcompany"),
        ("yourcompany.freshdesk.com", "yourcompany"),
        ("https://yourcompany.freshdesk.com/a/tickets/5", "yourcompany"),
        ("yourcompany-support.freshdesk.com/", "yourcompany-support"),
        ("evil.com", None),
        ("", None),
    ],
)
def test_normalize_subdomain(value, expected):
    assert normalize_subdomain(value) == expected


def test_tools_list_over_http():
    with _client() as client:
        resp = client.post("/mcp", json=LIST_TOOLS, headers=MCP_HEADERS | CREDS)
    assert resp.status_code == 200
    for name in ("freshdesk_tickets_search", "freshdesk_contacts_merge", "freshdesk_sla_list"):
        assert name in resp.text


def _run_middleware(headers: list[tuple[bytes, bytes]]) -> dict:
    seen: dict = {}

    async def app(scope, receive, send):
        seen["credentials"] = _credentials_var.get()
        seen["client"] = get_client_from_context()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    asyncio.run(GatewayCredentialsMiddleware(app)({"type": "http", "path": "/mcp", "headers": headers}, receive, send))
    return seen


def test_credentials_reach_request_context_as_basic_auth_and_are_reset():
    seen = _run_middleware([(b"x-freshdesk-domain", b"https://yourcompany.freshdesk.com"), (b"x-freshdesk-api-key", b" key-123 ")])
    client = seen["client"]
    assert client.base_url == "https://yourcompany.freshdesk.com/api/v2"
    assert client._headers()["Authorization"] == "Basic " + base64.b64encode(b"key-123:X").decode()
    assert _credentials_var.get() is None


def test_concurrent_requests_do_not_share_credentials():
    observed: list[tuple[str, str]] = []

    async def app(scope, receive, send):
        before = _credentials_var.get().subdomain
        await asyncio.sleep(0.01)
        observed.append((before, _credentials_var.get().subdomain))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    middleware = GatewayCredentialsMiddleware(app)

    def scope(domain: bytes) -> dict:
        return {"type": "http", "path": "/mcp", "headers": [(b"x-freshdesk-domain", domain), (b"x-freshdesk-api-key", b"k")]}

    async def both():
        await asyncio.gather(middleware(scope(b"tenant-a"), receive, send), middleware(scope(b"tenant-b"), receive, send))

    asyncio.run(both())
    assert sorted(observed) == [("tenant-a", "tenant-a"), ("tenant-b", "tenant-b")]


def test_credentials_repr_hides_key():
    seen = _run_middleware([(b"x-freshdesk-domain", b"yourcompany"), (b"x-freshdesk-api-key", b"very-secret-key")])
    assert "very-secret-key" not in repr(seen["credentials"])
