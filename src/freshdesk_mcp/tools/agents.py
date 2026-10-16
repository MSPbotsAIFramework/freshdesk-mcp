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
"""Agents: the helpdesk staff."""

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..api_client import FreshdeskClient
from ._common import (
    DESTRUCTIVE_ONCE,
    READ_ONLY,
    WRITES,
    WRITES_IDEMPOTENT,
    ClientFactory,
    Confirm,
    Ids,
    Page_,
    PerPage,
    RecordId,
    compact,
    not_confirmed,
    page_params,
    page_result,
    run_tool,
)

TicketScope = Annotated[int, Field(description="1 Global access, 2 Group access, 3 Restricted access.", ge=1, le=3)]
_AGENT_TYPES = {"support_agent": 1, "field_agent": 2, "collaborator": 3}


def agent_type(value: str | None) -> int | None:
    """Freshdesk's numeric agent_type from a name (support_agent / field_agent / collaborator) or number."""
    if value is None or not value.strip():
        return None
    text = value.strip().lower().replace(" ", "_")
    if text in _AGENT_TYPES:
        return _AGENT_TYPES[text]
    if text in ("1", "2", "3"):
        return int(text)
    raise ValueError("type must be support_agent, field_agent or collaborator.")


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_agents_list(
        page: Page_ = None,
        perPage: PerPage = None,
        email: Annotated[str | None, Field(description="Exact email.")] = None,
        state: Annotated[Literal["fulltime", "occasional"] | None, Field(description="Full-time or occasional agents.")] = None,
    ) -> str:
        """List agents with name, email, ticket scope, groups, roles and availability."""

        async def action(client: FreshdeskClient) -> dict:
            params = compact({"email": email, "state": state}) | page_params(page, perPage)
            return page_result("agents", await client.page("agents", params), page)

        return await run_tool("freshdesk_agents_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_agents_get(id: RecordId) -> str:
        """Get one agent's details."""

        async def action(client: FreshdeskClient) -> Any:
            return await client.get(f"agents/{id}")

        return await run_tool("freshdesk_agents_get", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_agents_me() -> str:
        """Get the agent who owns the API key."""

        async def action(client: FreshdeskClient) -> Any:
            return await client.get("agents/me")

        return await run_tool("freshdesk_agents_me", client_factory, action)

    @mcp.tool(annotations=WRITES)
    async def freshdesk_agents_create(
        email: Annotated[str, Field(description="Agent email.", min_length=3)],
        ticket_scope: TicketScope,
        occasional: Annotated[bool | None, Field(description="Occasional (day-pass) agent instead of full-time.")] = None,
        signature: Annotated[str | None, Field(description="Signature (HTML).")] = None,
        group_ids: Ids = None,
        role_ids: Ids = None,
        type: Annotated[str | None, Field(description="support_agent (default), field_agent or collaborator.")] = None,
    ) -> str:
        """Create an agent; a full-time agent uses a paid seat. Only when the user explicitly asks."""

        async def action(client: FreshdeskClient) -> Any:
            body = compact(
                {
                    "email": email,
                    "ticket_scope": ticket_scope,
                    "occasional": occasional,
                    "signature": signature,
                    "group_ids": group_ids,
                    "role_ids": role_ids,
                    "agent_type": agent_type(type),
                }
            )
            return await client.post("agents", body)

        return await run_tool("freshdesk_agents_create", client_factory, action)

    @mcp.tool(annotations=WRITES_IDEMPOTENT)
    async def freshdesk_agents_update(
        id: RecordId,
        ticket_scope: Annotated[int | None, Field(description="1 Global, 2 Group, 3 Restricted.", ge=1, le=3)] = None,
        occasional: Annotated[bool | None, Field(description="Occasional instead of full-time.")] = None,
        signature: Annotated[str | None, Field(description="Signature (HTML).")] = None,
        group_ids: Annotated[list[int] | None, Field(description="Replaces the agent's groups.")] = None,
        role_ids: Annotated[list[int] | None, Field(description="Replaces the agent's roles.")] = None,
    ) -> str:
        """Change an agent's scope, groups, roles or signature. Only when the user explicitly asks."""

        async def action(client: FreshdeskClient) -> Any:
            body = compact(
                {"ticket_scope": ticket_scope, "occasional": occasional, "signature": signature, "group_ids": group_ids, "role_ids": role_ids}
            )
            if not body:
                raise ValueError("Nothing to update: pass at least one field to change.")
            return await client.put(f"agents/{id}", body)

        return await run_tool("freshdesk_agents_update", client_factory, action)

    @mcp.tool(annotations=DESTRUCTIVE_ONCE)
    async def freshdesk_agents_delete(id: RecordId, confirm: Confirm = None) -> str:
        """Delete an agent, turning them into a contact. Irreversible; needs confirm: true.

        Only when the user explicitly asks for that agent.
        """

        async def action(client: FreshdeskClient) -> Any:
            if confirm is not True:
                return not_confirmed(f"Delete agent {id} (they become a contact)")
            await client.delete(f"agents/{id}")
            return {"done": True, "deleted_agent_id": id}

        return await run_tool("freshdesk_agents_delete", client_factory, action)
