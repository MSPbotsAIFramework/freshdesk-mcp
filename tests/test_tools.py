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
"""tools/list snapshot and error-envelope mapping. No network access."""

import asyncio
import json

import pytest

from freshdesk_mcp.api_client import FreshdeskError
from freshdesk_mcp.server import INSTRUCTIONS, create_mcp_server

ID = {"id"}

# Tool names are a public contract: existing agents and tenant connections
# depend on them. Changing this snapshot is a breaking change.
EXPECTED_TOOLS: dict[str, set[str]] = {
    "freshdesk_navigate": {"domain"},
    "freshdesk_status": set(),
    "freshdesk_tickets_search": {"query"},
    "freshdesk_tickets_list": set(),
    "freshdesk_tickets_get": ID,
    "freshdesk_tickets_create": {"subject", "description", "status", "priority"},
    "freshdesk_tickets_update": ID,
    "freshdesk_tickets_reply": {"id", "body"},
    "freshdesk_tickets_add_note": {"id", "body"},
    "freshdesk_tickets_list_conversations": ID,
    "freshdesk_tickets_update_conversation": {"conversation_id", "body"},
    "freshdesk_tickets_delete": ID,
    "freshdesk_tickets_delete_conversation": {"conversation_id"},
    "freshdesk_contacts_search": {"query"},
    "freshdesk_contacts_list": set(),
    "freshdesk_contacts_get": ID,
    "freshdesk_contacts_create": {"name"},
    "freshdesk_contacts_update": ID,
    "freshdesk_contacts_autocomplete": {"term"},
    "freshdesk_contacts_make_agent": ID,
    "freshdesk_contacts_restore": ID,
    "freshdesk_contacts_send_invite": ID,
    "freshdesk_contacts_merge": {"primary_contact_id", "secondary_contact_ids"},
    "freshdesk_contacts_soft_delete": ID,
    "freshdesk_contacts_hard_delete": ID,
    "freshdesk_companies_search": {"query"},
    "freshdesk_companies_list": set(),
    "freshdesk_companies_get": ID,
    "freshdesk_companies_create": {"name"},
    "freshdesk_companies_update": ID,
    "freshdesk_companies_autocomplete": {"name"},
    "freshdesk_companies_delete": ID,
    "freshdesk_agents_list": set(),
    "freshdesk_agents_get": ID,
    "freshdesk_agents_me": set(),
    "freshdesk_agents_create": {"email", "ticket_scope"},
    "freshdesk_agents_update": ID,
    "freshdesk_agents_delete": ID,
    "freshdesk_groups_list": set(),
    "freshdesk_groups_get": ID,
    "freshdesk_groups_create": {"name"},
    "freshdesk_groups_update": ID,
    "freshdesk_groups_delete": ID,
    "freshdesk_solutions_categories_list": set(),
    "freshdesk_solutions_categories_get": ID,
    "freshdesk_solutions_categories_create": {"name"},
    "freshdesk_solutions_categories_update": ID,
    "freshdesk_solutions_categories_delete": ID,
    "freshdesk_solutions_folders_list": {"category_id"},
    "freshdesk_solutions_folders_get": ID,
    "freshdesk_solutions_folders_create": {"category_id", "name"},
    "freshdesk_solutions_folders_update": ID,
    "freshdesk_solutions_folders_delete": ID,
    "freshdesk_solutions_articles_list": {"folder_id"},
    "freshdesk_solutions_articles_get": ID,
    "freshdesk_solutions_articles_create": {"folder_id", "title", "description", "status"},
    "freshdesk_solutions_articles_update": ID,
    "freshdesk_solutions_articles_delete": ID,
    "freshdesk_sla_list": set(),
    "freshdesk_sla_create": {"name"},
    "freshdesk_sla_update": ID,
    "freshdesk_business_hours_list": set(),
    "freshdesk_business_hours_get": ID,
    "freshdesk_canned_responses_list_folders": set(),
    "freshdesk_canned_responses_get_folder": ID,
    "freshdesk_canned_responses_list_responses": ID,
}
# Irreversible or high-impact tools: gated by confirm: true.
CONFIRMED_TOOLS = {
    "freshdesk_tickets_delete",
    "freshdesk_tickets_delete_conversation",
    "freshdesk_contacts_make_agent",
    "freshdesk_contacts_merge",
    "freshdesk_contacts_soft_delete",
    "freshdesk_contacts_hard_delete",
    "freshdesk_companies_delete",
    "freshdesk_agents_delete",
    "freshdesk_groups_delete",
    "freshdesk_solutions_categories_delete",
    "freshdesk_solutions_folders_delete",
    "freshdesk_solutions_articles_delete",
}
READ_TOOLS = {
    n
    for n in EXPECTED_TOOLS
    if n.endswith(("_list", "_get", "_search", "_autocomplete", "_me", "_folders", "_get_folder", "_responses", "_conversations"))
    or n in ("freshdesk_navigate", "freshdesk_status")
}


@pytest.fixture(scope="module")
def tools():
    return {t.name: t for t in asyncio.run(create_mcp_server().list_tools())}


def test_tool_names_are_stable(tools):
    assert set(tools) == set(EXPECTED_TOOLS)


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_required_params(tools, name):
    assert set(tools[name].inputSchema.get("required", [])) == EXPECTED_TOOLS[name]


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_description_budget(tools, name):
    description = (tools[name].description or "").strip()
    assert description, f"{name} has no description"
    assert len(description) <= 500
    assert len(description.splitlines()[0]) <= 100


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_annotations_match_side_effects(tools, name):
    annotations = tools[name].annotations
    assert annotations is not None
    assert annotations.readOnlyHint is (name in READ_TOOLS)
    assert bool(annotations.destructiveHint) is (name in CONFIRMED_TOOLS)


@pytest.mark.parametrize("name", sorted(set(EXPECTED_TOOLS) - READ_TOOLS))
def test_write_tools_say_when_to_use_them(tools, name):
    assert "explicitly ask" in tools[name].description


@pytest.mark.parametrize("name", sorted(CONFIRMED_TOOLS))
def test_destructive_tools_take_confirm(tools, name):
    assert "confirm" in tools[name].inputSchema["properties"]


def test_instructions_budget():
    assert len(INSTRUCTIONS) <= 1500


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "unauthorized", False),
        (403, "unauthorized", False),
        (404, "not_found", False),
        (400, "invalid_argument", False),
        (409, "invalid_argument", False),
        (429, "rate_limited", True),
        (500, "upstream_error", True),
        (0, "upstream_error", True),
    ],
)
def test_error_envelope(status, code, retryable):
    envelope = json.loads(FreshdeskError(status, "boom").to_envelope())
    assert envelope == {"error": {"code": code, "message": "boom", "retryable": retryable}}


def test_tool_without_credentials_returns_not_configured():
    content, _ = asyncio.run(create_mcp_server().call_tool("freshdesk_tickets_list", {}))
    assert json.loads(content[0].text)["error"]["code"] == "not_configured"


def test_navigate_lists_solutions_tools():
    content, _ = asyncio.run(create_mcp_server().call_tool("freshdesk_navigate", {"domain": "solutions"}))
    assert len(json.loads(content[0].text)["tools"]) == 15
