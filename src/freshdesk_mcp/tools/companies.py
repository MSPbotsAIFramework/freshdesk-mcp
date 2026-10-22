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
"""Companies: the organisations contacts belong to."""

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
    CustomFields,
    Offset,
    Page_,
    PerPage,
    RecordId,
    SearchOffset,
    SearchPage,
    SearchQuery,
    compact,
    not_confirmed,
    page_result,
    paging,
    run_tool,
    search,
)

Text = Annotated[str | None, Field(description="Value.")]
Domains = Annotated[list[str] | None, Field(description="Email domains of the company, e.g. example.com.")]


def _fields(**values: Any) -> dict:
    return compact(values)


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_companies_search(
        query: SearchQuery, page: SearchPage = None, offset: SearchOffset = None
    ) -> str:
        """Search companies with the Freshdesk query language, e.g. name:'Example Inc' or domain:'example.com'."""

        async def action(client: FreshdeskClient) -> dict:
            return await search(client, "companies", query, page, offset)

        return await run_tool("freshdesk_companies_search", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_companies_list(page: Page_ = None, perPage: PerPage = None, offset: Offset = None) -> str:
        """List companies with name, domains, health score, account tier and industry."""

        async def action(client: FreshdeskClient) -> dict:
            paged, number, size, start = paging(offset, page, perPage)
            return page_result("companies", await client.page("companies", paged), number, size, start)

        return await run_tool("freshdesk_companies_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_companies_get(id: RecordId) -> str:
        """Get one company with its domains, notes and custom fields."""

        async def action(client: FreshdeskClient) -> Any:
            return await client.get(f"companies/{id}")

        return await run_tool("freshdesk_companies_get", client_factory, action)

    @mcp.tool(annotations=WRITES)
    async def freshdesk_companies_create(
        name: Annotated[str, Field(description="Company name (unique).", min_length=1)],
        description: Text = None,
        note: Text = None,
        domains: Domains = None,
        health_score: Text = None,
        account_tier: Text = None,
        industry: Text = None,
        renewal_date: Annotated[str | None, Field(description="ISO 8601 date.")] = None,
        custom_fields: CustomFields = None,
    ) -> str:
        """Create a company. Only when the user explicitly asks."""

        async def action(client: FreshdeskClient) -> Any:
            body = _fields(
                name=name,
                description=description,
                note=note,
                domains=domains,
                health_score=health_score,
                account_tier=account_tier,
                industry=industry,
                renewal_date=renewal_date,
                custom_fields=custom_fields,
            )
            return await client.post("companies", body)

        return await run_tool("freshdesk_companies_create", client_factory, action)

    @mcp.tool(annotations=WRITES_IDEMPOTENT)
    async def freshdesk_companies_update(
        id: RecordId,
        name: Text = None,
        description: Text = None,
        note: Text = None,
        domains: Annotated[list[str] | None, Field(description="Replaces all email domains.")] = None,
        health_score: Text = None,
        account_tier: Text = None,
        industry: Text = None,
        renewal_date: Annotated[str | None, Field(description="ISO 8601 date.")] = None,
        custom_fields: CustomFields = None,
    ) -> str:
        """Change a company; only the fields given change. Only when the user explicitly asks."""

        async def action(client: FreshdeskClient) -> Any:
            body = _fields(
                name=name,
                description=description,
                note=note,
                domains=domains,
                health_score=health_score,
                account_tier=account_tier,
                industry=industry,
                renewal_date=renewal_date,
                custom_fields=custom_fields,
            )
            if not body:
                raise ValueError("Nothing to update: pass at least one field to change.")
            return await client.put(f"companies/{id}", body)

        return await run_tool("freshdesk_companies_update", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_companies_autocomplete(
        name: Annotated[str, Field(description="Start of the company name.", min_length=1)],
    ) -> str:
        """Quick company lookup by the start of its name; returns ids and names."""

        async def action(client: FreshdeskClient) -> Any:
            return await client.get("companies/autocomplete", {"name": name.strip()})

        return await run_tool("freshdesk_companies_autocomplete", client_factory, action)

    @mcp.tool(annotations=DESTRUCTIVE_ONCE)
    async def freshdesk_companies_delete(id: RecordId, confirm: Confirm = None) -> str:
        """Permanently delete a company (its contacts stay, unlinked). Irreversible; needs confirm: true.

        Only when the user explicitly asks for that company.
        """

        async def action(client: FreshdeskClient) -> Any:
            if confirm is not True:
                return not_confirmed(f"Permanently delete company {id}")
            await client.delete(f"companies/{id}")
            return {"done": True, "deleted_company_id": id}

        return await run_tool("freshdesk_companies_delete", client_factory, action)
