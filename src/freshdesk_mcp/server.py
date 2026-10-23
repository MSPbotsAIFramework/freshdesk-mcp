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
"""FastMCP factory and the per-request credential middleware."""

import contextvars
import re
import uuid
from collections.abc import Callable
from urllib.parse import urlsplit

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ._arguments import EnvelopeFastMCP
from .api_client import FreshdeskClient, FreshdeskCredentials
from .config import Settings

HEADER_DOMAIN = "X-Freshdesk-Domain"
HEADER_API_KEY = "X-Freshdesk-Api-Key"
REQUIRED_HEADERS = [HEADER_DOMAIN, HEADER_API_KEY]

_PLACEHOLDER = re.compile(r"^\$\{.*\}$")
_SUBDOMAIN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

# Per-request isolation: the middleware sets these for the duration of one
# HTTP request and resets them in `finally`. Nothing credential-related is
# ever stored at module level.
_credentials_var: contextvars.ContextVar[FreshdeskCredentials | None] = contextvars.ContextVar(
    "freshdesk_credentials", default=None
)
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("freshdesk_request_id", default="-")

INSTRUCTIONS = (
    "Freshdesk is a helpdesk: customers' requests are tickets, raised by "
    "contacts who belong to companies, worked by agents in groups under SLA "
    "policies, with a knowledge base (solutions: categories > folders > "
    "articles) and canned responses.\n"
    "Usage:\n"
    "- Find tickets with freshdesk_tickets_search (query language, e.g. "
    "status:2 AND priority:4 or agent_id:123) or freshdesk_tickets_list; read "
    "one with freshdesk_tickets_get(include='conversations,requester').\n"
    "- Find people with freshdesk_contacts_search / _autocomplete and "
    "organisations with freshdesk_companies_search / _autocomplete.\n"
    "- Ticket status codes: 2 Open, 3 Pending, 4 Resolved, 5 Closed; "
    "priority: 1 Low, 2 Medium, 3 High, 4 Urgent.\n"
    "- Lists page with page + perPage (max 100); has_more says whether to "
    "page on. Search returns 30 per page, at most 10 pages.\n"
    "Create, update, reply and note tools change live data, and replies "
    "email the customer: call them only when the user explicitly asks, with "
    "the exact content given. Delete, merge and make-agent tools also need "
    "confirm: true after the user approved that exact action."
)


def get_client_from_context() -> FreshdeskClient | None:
    """The client for the current request, or None outside a request."""
    credentials = _credentials_var.get()
    if credentials is None:
        return None
    return FreshdeskClient(credentials)


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text or _PLACEHOLDER.match(text):
        return None
    return text


def normalize_subdomain(value: str) -> str | None:
    """The Freshdesk subdomain from "yourcompany", "yourcompany.freshdesk.com" or a full URL; None if not a freshdesk.com account."""
    text = value.strip().lower()
    if "://" in text:
        host = urlsplit(text).hostname or ""
    else:
        host = text.split("/", 1)[0]
    if host.endswith(".freshdesk.com"):
        host = host[: -len(".freshdesk.com")]
    return host if _SUBDOMAIN.match(host) else None


class GatewayCredentialsMiddleware:
    """Reads X-Freshdesk-Domain and X-Freshdesk-Api-Key on /mcp requests; 401 when missing."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        domain = _clean(request.headers.get(HEADER_DOMAIN))
        api_key = _clean(request.headers.get(HEADER_API_KEY))
        if not (domain and api_key):
            missing = [h for h, v in ((HEADER_DOMAIN, domain), (HEADER_API_KEY, api_key)) if not v]
            await JSONResponse(
                {"error": "Missing credentials", "message": f"Missing header(s): {', '.join(missing)}", "required_headers": REQUIRED_HEADERS},
                status_code=401,
            )(scope, receive, send)
            return
        subdomain = normalize_subdomain(domain)
        if subdomain is None:
            await JSONResponse(
                {
                    "error": "Invalid domain",
                    "message": f"{HEADER_DOMAIN} must be the Freshdesk subdomain (yourcompany), yourcompany.freshdesk.com or https://yourcompany.freshdesk.com",
                },
                status_code=400,
            )(scope, receive, send)
            return

        request_id = (request.headers.get("x-request-id") or "").strip()[:64] or uuid.uuid4().hex[:12]
        creds_token = _credentials_var.set(FreshdeskCredentials(subdomain, api_key))
        rid_token = request_id_var.set(request_id)
        try:
            await self.app(scope, receive, send)
        finally:
            _credentials_var.reset(creds_token)
            request_id_var.reset(rid_token)


def create_mcp_server(settings: Settings | None = None) -> FastMCP:
    """Build the FastMCP server and register every tool."""
    mcp = EnvelopeFastMCP(
        name="freshdesk-mcp",
        instructions=INSTRUCTIONS,
        # The service sits behind the gateway, so Host is never localhost;
        # DNS-rebinding protection would reject those requests with 421.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        stateless_http=True,
    )

    client_factory: Callable[[], FreshdeskClient | None] = get_client_from_context

    from .tools import agents, companies, contacts, groups, navigation, sla_business, solutions, tickets

    for module in (navigation, tickets, contacts, companies, agents, groups, solutions, sla_business):
        module.register(mcp, client_factory)

    return mcp
