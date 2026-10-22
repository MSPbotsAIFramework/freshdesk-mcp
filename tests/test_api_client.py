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
    rec = Recorder({"search/tickets": {"results": [{"id": i} for i in range(30)], "total": 45}})
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


# ---- compatibility: enum params accept any casing (normalized) and unknown values (passed through)


@pytest.mark.parametrize(
    "tool,arg,given,sent",
    [
        ("freshdesk_agents_list", "state", "FullTime", "fulltime"),
        ("freshdesk_agents_list", "state", "contractor", "contractor"),
        ("freshdesk_contacts_list", "state", "Blocked", "blocked"),
        ("freshdesk_contacts_list", "state", "archived", "archived"),
        ("freshdesk_tickets_list", "filter", "WATCHING", "watching"),
        ("freshdesk_tickets_list", "filter", "all_tickets", "all_tickets"),
    ],
)
async def test_enum_params_normalized_or_passed_through(monkeypatch, tool, arg, given, sent):
    rec = Recorder()
    result = await _call(monkeypatch, rec, tool, {arg: given})
    assert "error" not in result
    assert rec.requests[0].url.params[arg] == sent



# ---- PRD-21056: search continues inside a page ----------------------------------------------------


def _search_api(total, seen):
    rows = [{"id": i, "subject": f"Ticket {i}", "description_text": "d" * 1500, "custom_fields": {"cf_x": None},
             "cc_emails": [], **{f"f{k}": f"value {k}" for k in range(80)}} for i in range(total)]

    def reply(request):
        page = int(request.url.params["page"])
        seen.append(page)
        chunk = rows[(page - 1) * 30 : min(page * 30, 300)]
        return httpx.Response(200, json={"results": chunk, "total": total})

    return reply


@pytest.mark.parametrize("tool,entity", [("freshdesk_tickets_search", "tickets"), ("freshdesk_contacts_search", "contacts"),
                                         ("freshdesk_companies_search", "companies")])
async def test_search_walks_all_reachable_results_with_next_offset(monkeypatch, tool, entity):
    seen_pages, ids, args, calls = [], [], {"query": "status:2"}, 0
    rec = Recorder({f"search/{entity}": _search_api(389, seen_pages)})
    while True:
        result = await _call(monkeypatch, rec, tool, args)
        calls += 1
        assert "retry_page_size" not in result
        ids += [r["id"] for r in result["results"]]
        if result.get("next_offset") is None:
            break
        args = {"query": "status:2", "offset": result["next_offset"]}
        assert calls < 100
    assert ids == list(range(300))  # Freshdesk's 300-result cap, no gaps or duplicates
    assert calls > 10  # pages were cut and continued mid-page


async def test_search_page_still_works_and_drops_empty_values(monkeypatch):
    rows = [{"id": i, "subject": f"T{i}", "custom_fields": {"cf_x": None}, "cc_emails": []} for i in range(45)]
    rec = Recorder({"search/tickets": lambda r: httpx.Response(
        200, json={"results": rows[(int(r.url.params["page"]) - 1) * 30 :][:30], "total": 45})})
    result = await _call(monkeypatch, rec, "freshdesk_tickets_search", {"query": "status:2", "page": 2})
    assert result["results"][0]["id"] == 30 and result["offset"] == 30
    assert "custom_fields" not in result["results"][0] and "cc_emails" not in result["results"][0]
    assert (result["count"], result["has_more"]) == (15, False)


# ---- PRD-21055: a large ticket keeps its plain-text description ------------------------------------


async def test_large_ticket_keeps_description_text(monkeypatch):
    text = "Plain text body. " * 600  # about 10,000 characters
    ticket = {"id": 1105009, "subject": "Big", "status": 2, "description": "<div>" + "<p>x</p>" * 3000 + "</div>",
              "description_text": text}
    rec = Recorder({"tickets/1105009": ticket})
    result = await _call(monkeypatch, rec, "freshdesk_tickets_get", {"id": 1105009})
    assert (result["id"], result["subject"], result["description_text"]) == (1105009, "Big", text)
    assert result["description"].endswith("...[truncated; see description_text]")


# ---- PRD-21057: long conversations point to paging one at a time ----------------------------------


async def test_long_conversations_note_points_to_per_page_one(monkeypatch):
    convs = [{"id": i, "body": "<p>" + "b" * 6000 + "</p>", "body_text": "t" * 6000} for i in range(5)]
    rec = Recorder({"tickets/7/conversations": convs})
    result = await _call(monkeypatch, rec, "freshdesk_tickets_list_conversations", {"id": 7})
    assert result["fields_truncated"] is True and "perPage 1" in result["note"]
    one = Recorder({"tickets/7/conversations": convs[:1]})
    single = await _call(monkeypatch, one, "freshdesk_tickets_list_conversations", {"id": 7, "perPage": 1, "page": 1})
    assert single["conversations"][0]["body_text"] == "t" * 6000


# ---- Page-number paging that survives truncation (offset / next_offset) -----------------------------
# Self-test checklist: a cut page continues right after the last record returned (from page 1 and
# from a later page); offset aligned / not aligned to a page; offset 0; offset past the end; offset
# wins over page; page-only calls send the same request as before; no retry hint for a page size.

TOTAL = 137
ROWS = [{"id": i, "name": f"Row {i}", **{f"f{k}": f"value {k}" for k in range(40)}} for i in range(TOTAL)]
SMALL = [{"id": i} for i in range(TOTAL)]

PAGED_TOOLS = [
    ("freshdesk_tickets_list", "tickets", "tickets", {}),
    ("freshdesk_tickets_list_conversations", "tickets/7/conversations", "conversations", {"id": 7}),
    ("freshdesk_contacts_list", "contacts", "contacts", {}),
    ("freshdesk_companies_list", "companies", "companies", {}),
    ("freshdesk_agents_list", "agents", "agents", {}),
    ("freshdesk_groups_list", "groups", "groups", {}),
]


def _paged_api(rows):
    """Pages by page / per_page (default 30) like Freshdesk, with a Link rel="next" header when there is more."""

    def reply(request):
        params = request.url.params
        number, size = int(params.get("page", 1)), int(params.get("per_page", 30))
        more = number * size < len(rows)
        return httpx.Response(200, json=rows[(number - 1) * size : number * size], headers=NEXT if more else {})

    return reply


def _sent(rec):
    return dict(rec.requests[0].url.params)


async def _walk(monkeypatch, rec, tool, key, args):
    ids, calls, cut = [], 0, 0
    while True:
        result = await _call(monkeypatch, rec, tool, args)
        calls += 1
        assert "retry_page_size" not in result, result.get("note")
        cut += bool(result.get("truncated"))
        ids += [r["id"] for r in result[key]]
        if result.get("next_offset") is None:
            return ids, cut
        args = {k: v for k, v in args.items() if k != "page"} | {"offset": result["next_offset"]}
        assert calls < 200


@pytest.mark.parametrize("tool,path,key,extra", PAGED_TOOLS)
async def test_cut_pages_continue_without_gaps_from_page_1(monkeypatch, tool, path, key, extra):
    ids, cut = await _walk(monkeypatch, Recorder({path: _paged_api(ROWS)}), tool, key, extra | {"perPage": 50})
    assert cut > 0  # pages really were cut
    assert ids == [r["id"] for r in ROWS]  # no gaps, no duplicates


@pytest.mark.parametrize("tool,path,key,extra", PAGED_TOOLS)
async def test_cut_pages_continue_without_gaps_from_a_later_page(monkeypatch, tool, path, key, extra):
    ids, cut = await _walk(monkeypatch, Recorder({path: _paged_api(ROWS)}), tool, key, extra | {"page": 3, "perPage": 40})
    assert cut > 0
    assert ids == [r["id"] for r in ROWS[80:]]


@pytest.mark.parametrize("tool,path,key,extra", PAGED_TOOLS)
@pytest.mark.parametrize("offset,size,page,first", [(30, 25, 2, 30), (50, 25, 3, 50), (0, 25, 1, 0), (35, None, 2, 35)])
async def test_offset_reads_the_page_that_holds_it(monkeypatch, tool, path, key, extra, offset, size, page, first):
    rec = Recorder({path: _paged_api(SMALL)})
    result = await _call(monkeypatch, rec, tool, extra | {"offset": offset} | ({"perPage": size} if size else {}))
    sent = _sent(rec)
    assert sent["page"] == str(page) and sent.get("per_page") == (str(size) if size else None)
    assert result[key][0]["id"] == first and result["offset"] == offset
    assert result["next_offset"] == page * (size or 30)  # rest of the page returned, next page starts after it


@pytest.mark.parametrize("tool,path,key,extra", PAGED_TOOLS)
async def test_offset_past_the_end_is_empty(monkeypatch, tool, path, key, extra):
    result = await _call(monkeypatch, Recorder({path: _paged_api(SMALL)}), tool, extra | {"offset": 500, "perPage": 25})
    assert (result["count"], result["has_more"], result.get("next_offset")) == (0, False, None)


@pytest.mark.parametrize("tool,path,key,extra", PAGED_TOOLS)
async def test_offset_wins_over_page(monkeypatch, tool, path, key, extra):
    rec = Recorder({path: _paged_api(SMALL)})
    result = await _call(monkeypatch, rec, tool, extra | {"offset": 10, "page": 4, "perPage": 10})
    assert _sent(rec)["page"] == "2" and result[key][0]["id"] == 10


@pytest.mark.parametrize("tool,path,key,extra", PAGED_TOOLS)
@pytest.mark.parametrize("args,sent", [({"page": 2, "perPage": 25}, {"page": "2", "per_page": "25"}), ({"page": 2}, {"page": "2"}), ({}, {})])
async def test_page_only_sends_the_same_request_as_before(monkeypatch, tool, path, key, extra, args, sent):
    rec = Recorder({path: _paged_api(SMALL)})
    result = await _call(monkeypatch, rec, tool, extra | args)
    assert {k: v for k, v in _sent(rec).items() if k in ("page", "per_page")} == sent
    number, size = args.get("page", 1), args.get("perPage", 30)
    assert [r["id"] for r in result[key]] == list(range((number - 1) * size, number * size))
    assert (result["page"], result["offset"], result["next_offset"], result["has_more"], result["next_page"]) == (
        number, (number - 1) * size, number * size, True, number + 1)


async def test_nth_conversation_in_full_with_offset_or_page(monkeypatch):
    convs = [{"id": i, "body": "<p>" + "b" * 6000 + "</p>", "body_text": "t" * 6000} for i in range(5)]
    rec = Recorder({"tickets/7/conversations": _paged_api(convs)})
    by_page = await _call(monkeypatch, rec, "freshdesk_tickets_list_conversations", {"id": 7, "perPage": 1, "page": 4})
    by_offset = await _call(monkeypatch, rec, "freshdesk_tickets_list_conversations", {"id": 7, "perPage": 1, "offset": 3})
    assert by_page["conversations"] == by_offset["conversations"] == [convs[3]]
    assert by_offset["next_offset"] == 4


# ---- PRD-21056: search walks always end; PRD-21057: whole lists in the standard envelope -------------------


def _paged_search(returned, total, big=0):
    """Freshdesk search: pages of 30 from the first ``returned`` results, reporting ``total``."""

    def reply(request):
        page = int(request.url.params["page"])
        rows = [{"id": i, "description": "x" * big} for i in range((page - 1) * 30, min(page * 30, returned))]
        return httpx.Response(200, json={"results": rows, "total": total})

    return reply


async def _walk_search(monkeypatch, tool, entity, returned, total, big=0):
    rec = Recorder({f"search/{entity}": _paged_search(returned, total, big)})
    ids, offset, calls = [], None, 0
    while True:
        out = await _call(monkeypatch, rec, tool, {"query": "status:2"} | ({} if offset is None else {"offset": offset}))
        calls += 1
        assert "retry_page_size" not in out and calls < 200
        ids += [r["id"] for r in out["results"]]
        if out.get("next_offset") is None:
            return ids, out
        assert out["next_offset"] > (offset or 0), "next_offset must move forward"
        offset = out["next_offset"]


@pytest.mark.parametrize(
    "returned,total,expected",
    [
        (290, 389, 290),  # total counts more than the pages return: the short page ends the walk
        (240, 250, 240),
        (300, 389, 300),  # Freshdesk's 300-result cap
        (60, 60, 60),  # total is an exact multiple of 30
        (0, 5, 0),  # total says 5, the page is empty
    ],
)
async def test_search_walk_ends_without_repeating_an_offset(monkeypatch, returned, total, expected):
    ids, last = await _walk_search(monkeypatch, "freshdesk_tickets_search", "tickets", returned, total)
    assert ids == list(range(expected)) and last["has_more"] is False


@pytest.mark.parametrize(
    "tool,entity",
    [("freshdesk_tickets_search", "tickets"), ("freshdesk_contacts_search", "contacts"), ("freshdesk_companies_search", "companies")],
)
async def test_search_walk_with_cut_pages_reaches_every_result_once(monkeypatch, tool, entity):
    ids, _ = await _walk_search(monkeypatch, tool, entity, 389, 389, big=3000)
    assert ids == list(range(300))


async def test_search_short_page_mid_walk_from_an_offset(monkeypatch):
    # Started inside the last, short page: what is left is returned and the walk stops there.
    rec = Recorder({"search/tickets": _paged_search(290, 389)})
    out = await _call(monkeypatch, rec, "freshdesk_tickets_search", {"query": "status:2", "offset": 285})
    assert [r["id"] for r in out["results"]] == list(range(285, 290)) and "next_offset" not in out


async def test_search_without_total_continues_only_after_a_full_page(monkeypatch):
    rec = Recorder({"search/tickets": {"results": [{"id": i} for i in range(30)]}})
    full = await _call(monkeypatch, rec, "freshdesk_tickets_search", {"query": "status:2"})
    assert (full["has_more"], full["next_offset"]) == (True, 30)
    rec = Recorder({"search/tickets": {"results": [{"id": 1}]}})
    short = await _call(monkeypatch, rec, "freshdesk_tickets_search", {"query": "status:2"})
    assert short["has_more"] is False and "next_offset" not in short


@pytest.mark.parametrize(
    "tool,args,path,key",
    [
        ("freshdesk_solutions_categories_list", {}, "solutions/categories", "categories"),
        ("freshdesk_sla_list", {}, "sla_policies", "sla_policies"),
        ("freshdesk_business_hours_list", {}, "business_hours", "business_hours"),
        ("freshdesk_canned_responses_list_folders", {}, "canned_response_folders", "folders"),
        ("freshdesk_contacts_autocomplete", {"term": "ja"}, "contacts/autocomplete", "contacts"),
    ],
)
async def test_whole_lists_use_the_standard_envelope(monkeypatch, tool, args, path, key):
    rows = [{"id": i, "name": f"n{i}"} for i in range(12)]
    rec = Recorder({path: rows})
    out = await _call(monkeypatch, rec, tool, args)
    assert out == {key: rows, "count": 12, "total": 12, "offset": 0, "has_more": False}
    tail = await _call(monkeypatch, rec, tool, args | {"offset": 10})
    assert (tail[key], tail["count"], tail["offset"]) == (rows[10:], 2, 10)
    past = await _call(monkeypatch, rec, tool, args | {"offset": 50})
    assert (past[key], past["count"], past["has_more"]) == ([], 0, False)
    empty = await _call(monkeypatch, Recorder({path: []}), tool, args)
    assert (empty[key], empty["count"], empty["total"]) == ([], 0, 0)


async def test_whole_list_cut_to_fit_continues_from_next_offset(monkeypatch):
    rows = [{"id": i, "name": f"n{i}", **{f"f{k}": "v" * 60 for k in range(40)}} for i in range(150)]
    rec = Recorder({"canned_response_folders": rows})
    ids, offset, cuts = [], None, 0
    while True:
        out = await _call(monkeypatch, rec, "freshdesk_canned_responses_list_folders", {} if offset is None else {"offset": offset})
        assert "retry_page_size" not in out
        cuts += bool(out.get("truncated"))
        ids += [r["id"] for r in out["folders"]]
        offset = out.get("next_offset")
        if offset is None:
            break
    assert cuts > 0 and ids == list(range(150))


async def test_companies_autocomplete_has_a_count(monkeypatch):
    rec = Recorder({"companies/autocomplete": {"companies": [{"id": 1, "name": "Exa"}, {"id": 2, "name": "Exb"}]}})
    out = await _call(monkeypatch, rec, "freshdesk_companies_autocomplete", {"name": "Ex"})
    assert out == {"companies": [{"id": 1, "name": "Exa"}, {"id": 2, "name": "Exb"}], "count": 2, "has_more": False}
