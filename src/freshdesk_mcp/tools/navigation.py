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
"""Tool discovery (kept for compatibility) and the connection check."""

from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .._json import compact as to_json
from ..api_client import FreshdeskClient
from ._common import LOCAL, READ_ONLY, ClientFactory, run_tool

DOMAINS: dict[str, tuple[str, list[str]]] = {
    "tickets": (
        "Tickets: search, list, get, create, update, reply, notes, conversations, delete.",
        [
            "freshdesk_tickets_search",
            "freshdesk_tickets_list",
            "freshdesk_tickets_get",
            "freshdesk_tickets_create",
            "freshdesk_tickets_update",
            "freshdesk_tickets_reply",
            "freshdesk_tickets_add_note",
            "freshdesk_tickets_list_conversations",
            "freshdesk_tickets_update_conversation",
            "freshdesk_tickets_delete",
            "freshdesk_tickets_delete_conversation",
        ],
    ),
    "contacts": (
        "Contacts: search, list, get, create, update, autocomplete, make agent, restore, invite, merge, delete.",
        [
            "freshdesk_contacts_search",
            "freshdesk_contacts_list",
            "freshdesk_contacts_get",
            "freshdesk_contacts_create",
            "freshdesk_contacts_update",
            "freshdesk_contacts_autocomplete",
            "freshdesk_contacts_make_agent",
            "freshdesk_contacts_restore",
            "freshdesk_contacts_send_invite",
            "freshdesk_contacts_merge",
            "freshdesk_contacts_soft_delete",
            "freshdesk_contacts_hard_delete",
        ],
    ),
    "companies": (
        "Companies: search, list, get, create, update, autocomplete, delete.",
        [
            "freshdesk_companies_search",
            "freshdesk_companies_list",
            "freshdesk_companies_get",
            "freshdesk_companies_create",
            "freshdesk_companies_update",
            "freshdesk_companies_autocomplete",
            "freshdesk_companies_delete",
        ],
    ),
    "agents": (
        "Agents: list, get, me, create, update, delete.",
        ["freshdesk_agents_list", "freshdesk_agents_get", "freshdesk_agents_me", "freshdesk_agents_create", "freshdesk_agents_update", "freshdesk_agents_delete"],
    ),
    "groups": (
        "Agent groups: list, get, create, update, delete.",
        ["freshdesk_groups_list", "freshdesk_groups_get", "freshdesk_groups_create", "freshdesk_groups_update", "freshdesk_groups_delete"],
    ),
    "solutions": (
        "Knowledge base: categories, folders and articles (list, get, create, update, delete).",
        [
            f"freshdesk_solutions_{kind}_{verb}"
            for kind in ("categories", "folders", "articles")
            for verb in ("list", "get", "create", "update", "delete")
        ],
    ),
    "sla-business": (
        "SLA policies, business hours and canned responses.",
        [
            "freshdesk_sla_list",
            "freshdesk_sla_create",
            "freshdesk_sla_update",
            "freshdesk_business_hours_list",
            "freshdesk_business_hours_get",
            "freshdesk_canned_responses_list_folders",
            "freshdesk_canned_responses_get_folder",
            "freshdesk_canned_responses_list_responses",
        ],
    ),
}

Domain = Literal["tickets", "contacts", "companies", "agents", "groups", "solutions", "sla-business"]


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=LOCAL)
    async def freshdesk_navigate(domain: Annotated[Domain, Field(description="Area to describe.")]) -> str:
        """Describe the tools of one Freshdesk area. All tools are already callable; this changes nothing."""
        summary, tools = DOMAINS[domain]
        return to_json({"domain": domain, "summary": summary, "tools": tools})

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_status() -> str:
        """Check the Freshdesk connection: account domain and the agent who owns the API key."""

        async def action(client: FreshdeskClient) -> dict:
            me = await client.get("agents/me") or {}
            contact = me.get("contact") if isinstance(me, dict) else None
            contact = contact if isinstance(contact, dict) else {}
            return {
                "connected": True,
                "domain": f"{client.subdomain}.freshdesk.com",
                "agent": {"id": me.get("id") if isinstance(me, dict) else None, "name": contact.get("name"), "email": contact.get("email")},
                "domains": list(DOMAINS),
            }

        return await run_tool("freshdesk_status", client_factory, action)
