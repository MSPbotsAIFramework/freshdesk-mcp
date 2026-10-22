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
"""SLA policies, business hours and canned responses."""

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..api_client import FreshdeskClient
from ._common import (
    READ_ONLY,
    WRITES,
    WRITES_IDEMPOTENT,
    ClientFactory,
    LocalOffset,
    RecordId,
    compact,
    run_tool,
    whole_list,
)

SlaTarget = Annotated[
    dict[str, Any] | None,
    Field(description='Targets per priority, e.g. {"priority_4": {"respond_within": 3600, "resolve_within": 14400, "business_hours": false, "escalation_enabled": true}}.'),
]
ApplicableTo = Annotated[
    dict[str, Any] | None, Field(description="Which tickets the policy covers: {company_ids, group_ids, sources, ticket_types, product_ids}.")
]
Escalation = Annotated[dict[str, Any] | None, Field(description="Escalation rules: {response: {...}, resolution: {level_1: {...}, ...}}.")]


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_sla_list(offset: LocalOffset = None) -> str:
        """List SLA policies with their response / resolution targets and which tickets they cover."""

        async def action(client: FreshdeskClient) -> Any:
            return whole_list("sla_policies", await client.get("sla_policies"), offset)

        return await run_tool("freshdesk_sla_list", client_factory, action)

    @mcp.tool(annotations=WRITES)
    async def freshdesk_sla_create(
        name: Annotated[str, Field(description="Policy name.", min_length=1)],
        description: Annotated[str | None, Field(description="Description.")] = None,
        sla_target: SlaTarget = None,
        applicable_to: ApplicableTo = None,
        escalation: Escalation = None,
    ) -> str:
        """Create an SLA policy (targets in seconds per priority). Only when the user explicitly asks."""

        async def action(client: FreshdeskClient) -> Any:
            body = compact(
                {"name": name, "description": description, "sla_target": sla_target, "applicable_to": applicable_to, "escalation": escalation}
            )
            return await client.post("sla_policies", body)

        return await run_tool("freshdesk_sla_create", client_factory, action)

    @mcp.tool(annotations=WRITES_IDEMPOTENT)
    async def freshdesk_sla_update(
        id: RecordId,
        name: Annotated[str | None, Field(description="New name.")] = None,
        description: Annotated[str | None, Field(description="Description.")] = None,
        sla_target: SlaTarget = None,
        applicable_to: ApplicableTo = None,
        escalation: Escalation = None,
    ) -> str:
        """Change an SLA policy; only the parts given change. Only when the user explicitly asks."""

        async def action(client: FreshdeskClient) -> Any:
            body = compact(
                {"name": name, "description": description, "sla_target": sla_target, "applicable_to": applicable_to, "escalation": escalation}
            )
            if not body:
                raise ValueError("Nothing to update: pass at least one field to change.")
            return await client.put(f"sla_policies/{id}", body)

        return await run_tool("freshdesk_sla_update", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_business_hours_list(offset: LocalOffset = None) -> str:
        """List business-hours schedules (working days, hours, time zone)."""

        async def action(client: FreshdeskClient) -> Any:
            return whole_list("business_hours", await client.get("business_hours"), offset)

        return await run_tool("freshdesk_business_hours_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_business_hours_get(id: RecordId) -> str:
        """Get one business-hours schedule, including holidays."""

        async def action(client: FreshdeskClient) -> Any:
            return await client.get(f"business_hours/{id}")

        return await run_tool("freshdesk_business_hours_get", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_canned_responses_list_folders(offset: LocalOffset = None) -> str:
        """List canned-response folders."""

        async def action(client: FreshdeskClient) -> Any:
            return whole_list("folders", await client.get("canned_response_folders"), offset)

        return await run_tool("freshdesk_canned_responses_list_folders", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_canned_responses_get_folder(id: RecordId) -> str:
        """Get one canned-response folder."""

        async def action(client: FreshdeskClient) -> Any:
            return await client.get(f"canned_response_folders/{id}")

        return await run_tool("freshdesk_canned_responses_get_folder", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_canned_responses_list_responses(id: RecordId) -> str:
        """List the canned responses in a folder (title and content)."""

        async def action(client: FreshdeskClient) -> Any:
            return await client.get(f"canned_response_folders/{id}/responses")

        return await run_tool("freshdesk_canned_responses_list_responses", client_factory, action)
