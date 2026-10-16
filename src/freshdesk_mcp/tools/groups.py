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
"""Agent groups (teams that tickets are assigned to)."""

from typing import Annotated, Any

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
    Page_,
    PerPage,
    RecordId,
    compact,
    not_confirmed,
    page_params,
    page_result,
    run_tool,
)

AgentIds = Annotated[list[int] | None, Field(description="Agent ids in the group (replaces the members on update).")]
UnassignedFor = Annotated[
    str | None, Field(description="Escalate unassigned tickets after: 30m, 1h, 2h, 4h, 8h, 12h, 1d, 2d or 3d.")
]


def _body(**values: Any) -> dict:
    return compact(values)


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_groups_list(page: Page_ = None, perPage: PerPage = None) -> str:
        """List agent groups with members, escalation and business hours."""

        async def action(client: FreshdeskClient) -> dict:
            return page_result("groups", await client.page("groups", page_params(page, perPage)), page)

        return await run_tool("freshdesk_groups_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_groups_get(id: RecordId) -> str:
        """Get one agent group."""

        async def action(client: FreshdeskClient) -> Any:
            return await client.get(f"groups/{id}")

        return await run_tool("freshdesk_groups_get", client_factory, action)

    @mcp.tool(annotations=WRITES)
    async def freshdesk_groups_create(
        name: Annotated[str, Field(description="Group name (unique).", min_length=1)],
        description: Annotated[str | None, Field(description="Description.")] = None,
        agent_ids: AgentIds = None,
        auto_ticket_assign: Annotated[bool | None, Field(description="Round-robin assignment.")] = None,
        escalate_to: Annotated[int | None, Field(description="Agent id notified when tickets stay unassigned.", ge=1)] = None,
        unassigned_for: UnassignedFor = None,
        business_hour_id: Annotated[int | None, Field(description="Business hours id.", ge=1)] = None,
    ) -> str:
        """Create an agent group. Only when the user explicitly asks."""

        async def action(client: FreshdeskClient) -> Any:
            body = _body(
                name=name,
                description=description,
                agent_ids=agent_ids,
                auto_ticket_assign=auto_ticket_assign,
                escalate_to=escalate_to,
                unassigned_for=unassigned_for,
                business_hour_id=business_hour_id,
            )
            return await client.post("groups", body)

        return await run_tool("freshdesk_groups_create", client_factory, action)

    @mcp.tool(annotations=WRITES_IDEMPOTENT)
    async def freshdesk_groups_update(
        id: RecordId,
        name: Annotated[str | None, Field(description="New name.")] = None,
        description: Annotated[str | None, Field(description="Description.")] = None,
        agent_ids: AgentIds = None,
        auto_ticket_assign: Annotated[bool | None, Field(description="Round-robin assignment.")] = None,
        escalate_to: Annotated[int | None, Field(description="Agent id notified when tickets stay unassigned.", ge=1)] = None,
        unassigned_for: UnassignedFor = None,
        business_hour_id: Annotated[int | None, Field(description="Business hours id.", ge=1)] = None,
    ) -> str:
        """Change an agent group; only the fields given change. Only when the user explicitly asks."""

        async def action(client: FreshdeskClient) -> Any:
            body = _body(
                name=name,
                description=description,
                agent_ids=agent_ids,
                auto_ticket_assign=auto_ticket_assign,
                escalate_to=escalate_to,
                unassigned_for=unassigned_for,
                business_hour_id=business_hour_id,
            )
            if not body:
                raise ValueError("Nothing to update: pass at least one field to change.")
            return await client.put(f"groups/{id}", body)

        return await run_tool("freshdesk_groups_update", client_factory, action)

    @mcp.tool(annotations=DESTRUCTIVE_ONCE)
    async def freshdesk_groups_delete(id: RecordId, confirm: Confirm = None) -> str:
        """Permanently delete an agent group (its tickets lose the group). Irreversible; needs confirm: true.

        Only when the user explicitly asks for that group.
        """

        async def action(client: FreshdeskClient) -> Any:
            if confirm is not True:
                return not_confirmed(f"Permanently delete group {id}")
            await client.delete(f"groups/{id}")
            return {"done": True, "deleted_group_id": id}

        return await run_tool("freshdesk_groups_delete", client_factory, action)
