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
"""Request shapes, paging, retries and tool behaviour against a mocked Freshdesk API."""

import json

import httpx
import pytest

from freshdesk_mcp import api_client
from freshdesk_mcp.api_client import FreshdeskClient, FreshdeskCredentials, FreshdeskError
from freshdesk_mcp.server import _credentials_var, create_mcp_server

CREDS = FreshdeskCredentials("yourcompany", "key-123")
NEXT = {"link": '<https://yourcompany.freshdesk.com/api/v2/tickets?page=2>; rel="next"'}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(api_client.asyncio, "sleep", instant)


class Recorder:
    """Mock transport: replies by method + path below /api/v2/."""

    def __init__(self, routes=None, default=None):
        self.requests: list[httpx.Request] = []
        self.routes = routes or {}
        self.default = default if default is not None else []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/api/v2/")
        reply = self.routes.get(f"{request.method} {path}", self.routes.get(path, self.default))
        if callable(reply):
            return reply(request)
        return httpx.Response(200, json=reply)

    def calls(self, path: str, method: str | None = None) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == f"/api/v2/{path}" and (method is None or r.method == method)]


def body(request: httpx.Request):
    return json.loads(request.content) if request.content else None


async def _call(monkeypatch, recorder: Recorder, tool: str, args: dict):
    http = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    monkeypatch.setattr(api_client, "_get_shared_http_client", lambda: http)
    ctx = _credentials_var.set(CREDS)
    try:
        content, _ = await create_mcp_server().call_tool(tool, args)
    finally:
        _credentials_var.reset(ctx)
    return json.loads(content[0].text)


def _client(recorder: Recorder) -> FreshdeskClient:
    return FreshdeskClient(CREDS, httpx.AsyncClient(transport=httpx.MockTransport(recorder)))


# ---- client ----------------------------------------------------------------


async def test_get_retries_5xx_but_writes_do_not():
    replies = iter([httpx.Response(503), httpx.Response(200, json=[])])
    rec = Recorder({"GET tickets": lambda r: next(replies), "POST tickets": lambda r: httpx.Response(502, json={})})
    client = _client(rec)
    await client.get("tickets")
    with pytest.raises(FreshdeskError):
        await client.post("tickets", {})
    assert len(rec.calls("tickets", "GET")) == 2 and len(rec.calls("tickets", "POST")) == 1


async def test_validation_errors_name_fields(monkeypatch):
    error = {"description": "Validation failed", "errors": [{"field": "email", "message": "It should be a valid email address", "code": "invalid_value"}]}
    rec = Recorder({"POST contacts": lambda r: httpx.Response(400, json=error)})
    result = await _call(monkeypatch, rec, "freshdesk_contacts_create", {"name": "Jane", "email": "nope"})
    assert result["error"]["code"] == "invalid_argument"
    assert "email: It should be a valid email address" in result["error"]["message"]


async def test_rate_limit_retries_with_retry_after(monkeypatch):
    replies = iter([httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(200, json={"id": 1})])
    rec = Recorder({"GET agents/me": lambda r: next(replies)})
    result = await _call(monkeypatch, rec, "freshdesk_agents_me", {})
    assert result == {"id": 1}


# ---- tickets ---------------------------------------------------------------


async def test_search_wraps_query_in_double_quotes(monkeypatch):
    rec = Recorder({"search/tickets": {"results": [{"id": 1}], "total": 45}})
    result = await _call(monkeypatch, rec, "freshdesk_tickets_search", {"query": "status:2 AND priority:4"})
    assert rec.requests[0].url.params["query"] == '"status:2 AND priority:4"'
    assert (result["total"], result["has_more"]) == (45, True)


async def test_search_rejects_inner_double_quotes(monkeypatch):
    rec = Recorder()
    result = await _call(monkeypatch, rec, "freshdesk_contacts_search", {"query": 'email:"a@b.c"'})
    assert result["error"]["code"] == "invalid_argument"
    assert rec.requests == []


async def test_list_tickets_paging_via_link_header(monkeypatch):
    rec = Recorder({"tickets": lambda r: httpx.Response(200, json=[{"id": 1}], headers=NEXT)})
    result = await _call(monkeypatch, rec, "freshdesk_tickets_list", {"perPage": 1, "filter": "watching"})
    params = rec.requests[0].url.params
    assert (params["per_page"], params["filter"]) == ("1", "watching")
    assert (result["has_more"], result["next_page"]) == (True, 2)


async def test_ticket_include_is_validated(monkeypatch):
    rec = Recorder({"tickets/5": {"id": 5}})
    await _call(monkeypatch, rec, "freshdesk_tickets_get", {"id": 5, "include": "conversations, requester"})
    assert rec.requests[0].url.params["include"] == "conversations,requester"
    result = await _call(monkeypatch, rec, "freshdesk_tickets_get", {"id": 5, "include": "everything"})
    assert result["error"]["code"] == "invalid_argument"


async def test_create_ticket_needs_a_requester(monkeypatch):
    rec = Recorder()
    args = {"subject": "Printer", "description": "<p>Down</p>", "status": 2, "priority": 3}
    result = await _call(monkeypatch, rec, "freshdesk_tickets_create", args)
    assert result["error"]["code"] == "invalid_argument"
    rec = Recorder({"POST tickets": {"id": 9}})
    await _call(monkeypatch, rec, "freshdesk_tickets_create", args | {"email": "jane@example.com", "tags": ["vip"]})
    assert body(rec.requests[0]) == args | {"email": "jane@example.com", "tags": ["vip"]}


async def test_note_is_private_by_default(monkeypatch):
    rec = Recorder({"POST tickets/5/notes": {"id": 1}})
    await _call(monkeypatch, rec, "freshdesk_tickets_add_note", {"id": 5, "body": "internal"})
    assert body(rec.requests[0]) == {"body": "internal", "private": True}


async def test_reply_posts_to_reply_endpoint(monkeypatch):
    rec = Recorder({"POST tickets/5/reply": {"id": 2}})
    await _call(monkeypatch, rec, "freshdesk_tickets_reply", {"id": 5, "body": "Hi", "cc_emails": ["a@example.com"]})
    assert body(rec.requests[0]) == {"body": "Hi", "cc_emails": ["a@example.com"]}


async def test_update_conversation_uses_conversations_route(monkeypatch):
    rec = Recorder({"PUT conversations/77": {"id": 77}})
    await _call(monkeypatch, rec, "freshdesk_tickets_update_conversation", {"conversation_id": 77, "body": "fixed"})
    assert rec.calls("conversations/77", "PUT")


# ---- confirmation ----------------------------------------------------------


@pytest.mark.parametrize(
    "tool,args,method,path",
    [
        ("freshdesk_tickets_delete", {"id": 5}, "DELETE", "tickets/5"),
        ("freshdesk_tickets_delete_conversation", {"conversation_id": 7}, "DELETE", "conversations/7"),
        ("freshdesk_contacts_soft_delete", {"id": 3}, "DELETE", "contacts/3"),
        ("freshdesk_contacts_make_agent", {"id": 3}, "PUT", "contacts/3/make_agent"),
        ("freshdesk_companies_delete", {"id": 4}, "DELETE", "companies/4"),
        ("freshdesk_agents_delete", {"id": 6}, "DELETE", "agents/6"),
        ("freshdesk_groups_delete", {"id": 8}, "DELETE", "groups/8"),
        ("freshdesk_solutions_categories_delete", {"id": 1}, "DELETE", "solutions/categories/1"),
        ("freshdesk_solutions_folders_delete", {"id": 2}, "DELETE", "solutions/folders/2"),
        ("freshdesk_solutions_articles_delete", {"id": 3}, "DELETE", "solutions/articles/3"),
    ],
)
async def test_destructive_tools_need_confirm(monkeypatch, tool, args, method, path):
    rec = Recorder(default={})
    result = await _call(monkeypatch, rec, tool, args)
    assert result["done"] is False and rec.requests == []
    await _call(monkeypatch, rec, tool, args | {"confirm": True})
    assert rec.calls(path, method)


async def test_hard_delete_force_flag(monkeypatch):
    rec = Recorder({"DELETE contacts/3/hard_delete": lambda r: httpx.Response(204)})
    await _call(monkeypatch, rec, "freshdesk_contacts_hard_delete", {"id": 3, "force": True, "confirm": True})
    assert rec.requests[0].url.params["force"] == "true"


async def test_merge_body_and_self_merge_guard(monkeypatch):
    rec = Recorder({"POST contacts/merge": lambda r: httpx.Response(204)})
    result = await _call(monkeypatch, rec, "freshdesk_contacts_merge", {"primary_contact_id": 1, "secondary_contact_ids": [1]})
    assert result["error"]["code"] == "invalid_argument"
    args = {"primary_contact_id": 1, "secondary_contact_ids": [2, 3, 2], "contact": {"email": "a@example.com"}, "confirm": True}
    await _call(monkeypatch, rec, "freshdesk_contacts_merge", args)
    assert body(rec.calls("contacts/merge", "POST")[0]) == {"primary_contact_id": 1, "secondary_contact_ids": [2, 3], "contact": {"email": "a@example.com"}}


async def test_send_invite_uses_put(monkeypatch):
    rec = Recorder({"PUT contacts/3/send_invite": lambda r: httpx.Response(204)})
    result = await _call(monkeypatch, rec, "freshdesk_contacts_send_invite", {"id": 3})
    assert result["done"] is True and rec.calls("contacts/3/send_invite", "PUT")


# ---- other domains ---------------------------------------------------------


async def test_agent_create_maps_type_to_agent_type(monkeypatch):
    rec = Recorder({"POST agents": {"id": 1}})
    await _call(monkeypatch, rec, "freshdesk_agents_create", {"email": "a@example.com", "ticket_scope": 2, "type": "collaborator"})
    assert body(rec.requests[0]) == {"email": "a@example.com", "ticket_scope": 2, "agent_type": 3}
    result = await _call(monkeypatch, rec, "freshdesk_agents_create", {"email": "a@example.com", "ticket_scope": 2, "type": "robot"})
    assert result["error"]["code"] == "invalid_argument"


async def test_autocomplete_params(monkeypatch):
    rec = Recorder()
    await _call(monkeypatch, rec, "freshdesk_contacts_autocomplete", {"term": " jan "})
    await _call(monkeypatch, rec, "freshdesk_companies_autocomplete", {"name": "Exa"})
    assert rec.calls("contacts/autocomplete")[0].url.params["term"] == "jan"
    assert rec.calls("companies/autocomplete")[0].url.params["name"] == "Exa"


async def test_folder_visibility_four_needs_companies(monkeypatch):
    rec = Recorder()
    result = await _call(monkeypatch, rec, "freshdesk_solutions_folders_create", {"category_id": 1, "name": "VIP", "visibility": 4})
    assert result["error"]["code"] == "invalid_argument"
    rec = Recorder({"POST solutions/categories/1/folders": {"id": 2}})
    await _call(monkeypatch, rec, "freshdesk_solutions_folders_create", {"category_id": 1, "name": "FAQ"})
    assert body(rec.requests[0]) == {"name": "FAQ", "visibility": 1}


@pytest.mark.parametrize(
    "tool,args,path",
    [
        ("freshdesk_sla_list", {}, "sla_policies"),
        ("freshdesk_business_hours_get", {"id": 4}, "business_hours/4"),
        ("freshdesk_canned_responses_list_folders", {}, "canned_response_folders"),
        ("freshdesk_canned_responses_list_responses", {"id": 2}, "canned_response_folders/2/responses"),
        ("freshdesk_solutions_folders_list", {"category_id": 1}, "solutions/categories/1/folders"),
        ("freshdesk_solutions_articles_list", {"folder_id": 2}, "solutions/folders/2/articles"),
        ("freshdesk_groups_list", {}, "groups"),
        ("freshdesk_companies_list", {}, "companies"),
    ],
)
async def test_read_paths(monkeypatch, tool, args, path):
    rec = Recorder()
    await _call(monkeypatch, rec, tool, args)
    assert rec.calls(path, "GET")


async def test_status_reports_agent(monkeypatch):
    rec = Recorder({"agents/me": {"id": 9, "contact": {"name": "Jane", "email": "jane@example.com"}}})
    result = await _call(monkeypatch, rec, "freshdesk_status", {})
    assert result["domain"] == "yourcompany.freshdesk.com" and result["agent"]["name"] == "Jane"
