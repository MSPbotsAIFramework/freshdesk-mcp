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
"""Knowledge base (solutions): categories > folders > articles."""

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
    RecordId,
    compact,
    not_confirmed,
    run_tool,
)

Text = Annotated[str | None, Field(description="Value.")]
PortalIds = Annotated[list[int] | None, Field(description="Portal ids the category is shown in.")]
Visibility = Annotated[int | None, Field(description="1 All users, 2 Logged-in users, 3 Agents only, 4 Selected companies.", ge=1, le=4)]
CompanyIds = Annotated[list[int] | None, Field(description="Companies that can see the folder (visibility 4).")]
ArticleStatus = Annotated[int, Field(description="1 Draft, 2 Published.", ge=1, le=2)]
ArticleType = Annotated[int | None, Field(description="1 Permanent, 2 Workaround.", ge=1, le=2)]
Tags = Annotated[list[str] | None, Field(description="Article tags.")]
Seo = Annotated[dict[str, Any] | None, Field(description="SEO data: {meta_title, meta_description, meta_keywords}.")]


def _require(body: dict) -> dict:
    if not body:
        raise ValueError("Nothing to update: pass at least one field to change.")
    return body


def _folder_visibility(visibility: int | None, company_ids: list[int] | None) -> None:
    if visibility == 4 and not company_ids:
        raise ValueError("visibility 4 (selected companies) needs company_ids.")


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    # ---- categories ------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_solutions_categories_list() -> str:
        """List knowledge-base categories."""

        async def action(client: FreshdeskClient) -> Any:
            return await client.get("solutions/categories")

        return await run_tool("freshdesk_solutions_categories_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_solutions_categories_get(id: RecordId) -> str:
        """Get one knowledge-base category."""

        async def action(client: FreshdeskClient) -> Any:
            return await client.get(f"solutions/categories/{id}")

        return await run_tool("freshdesk_solutions_categories_get", client_factory, action)

    @mcp.tool(annotations=WRITES)
    async def freshdesk_solutions_categories_create(
        name: Annotated[str, Field(description="Category name.", min_length=1)],
        description: Text = None,
        visible_in_portals: PortalIds = None,
    ) -> str:
        """Create a knowledge-base category. Only when the user explicitly asks."""

        async def action(client: FreshdeskClient) -> Any:
            body = compact({"name": name, "description": description, "visible_in_portals": visible_in_portals})
            return await client.post("solutions/categories", body)

        return await run_tool("freshdesk_solutions_categories_create", client_factory, action)

    @mcp.tool(annotations=WRITES_IDEMPOTENT)
    async def freshdesk_solutions_categories_update(
        id: RecordId, name: Text = None, description: Text = None, visible_in_portals: PortalIds = None
    ) -> str:
        """Change a knowledge-base category. Only when the user explicitly asks."""

        async def action(client: FreshdeskClient) -> Any:
            body = _require(compact({"name": name, "description": description, "visible_in_portals": visible_in_portals}))
            return await client.put(f"solutions/categories/{id}", body)

        return await run_tool("freshdesk_solutions_categories_update", client_factory, action)

    @mcp.tool(annotations=DESTRUCTIVE_ONCE)
    async def freshdesk_solutions_categories_delete(id: RecordId, confirm: Confirm = None) -> str:
        """Permanently delete a category with all its folders and articles. Irreversible; needs confirm: true.

        Only when the user explicitly asks.
        """

        async def action(client: FreshdeskClient) -> Any:
            if confirm is not True:
                return not_confirmed(f"Permanently delete category {id} and all its folders and articles")
            await client.delete(f"solutions/categories/{id}")
            return {"done": True, "deleted_category_id": id}

        return await run_tool("freshdesk_solutions_categories_delete", client_factory, action)

    # ---- folders ---------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_solutions_folders_list(category_id: RecordId) -> str:
        """List the folders of a knowledge-base category."""

        async def action(client: FreshdeskClient) -> Any:
            return await client.get(f"solutions/categories/{category_id}/folders")

        return await run_tool("freshdesk_solutions_folders_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_solutions_folders_get(id: RecordId) -> str:
        """Get one knowledge-base folder."""

        async def action(client: FreshdeskClient) -> Any:
            return await client.get(f"solutions/folders/{id}")

        return await run_tool("freshdesk_solutions_folders_get", client_factory, action)

    @mcp.tool(annotations=WRITES)
    async def freshdesk_solutions_folders_create(
        category_id: RecordId,
        name: Annotated[str, Field(description="Folder name.", min_length=1)],
        description: Text = None,
        visibility: Visibility = None,
        company_ids: CompanyIds = None,
    ) -> str:
        """Create a folder in a knowledge-base category. Only when the user explicitly asks."""

        async def action(client: FreshdeskClient) -> Any:
            _folder_visibility(visibility, company_ids)
            body = compact({"name": name, "description": description, "visibility": visibility or 1, "company_ids": company_ids})
            return await client.post(f"solutions/categories/{category_id}/folders", body)

        return await run_tool("freshdesk_solutions_folders_create", client_factory, action)

    @mcp.tool(annotations=WRITES_IDEMPOTENT)
    async def freshdesk_solutions_folders_update(
        id: RecordId, name: Text = None, description: Text = None, visibility: Visibility = None, company_ids: CompanyIds = None
    ) -> str:
        """Change a knowledge-base folder. Only when the user explicitly asks."""

        async def action(client: FreshdeskClient) -> Any:
            _folder_visibility(visibility, company_ids)
            body = _require(compact({"name": name, "description": description, "visibility": visibility, "company_ids": company_ids}))
            return await client.put(f"solutions/folders/{id}", body)

        return await run_tool("freshdesk_solutions_folders_update", client_factory, action)

    @mcp.tool(annotations=DESTRUCTIVE_ONCE)
    async def freshdesk_solutions_folders_delete(id: RecordId, confirm: Confirm = None) -> str:
        """Permanently delete a folder with all its articles. Irreversible; needs confirm: true.

        Only when the user explicitly asks.
        """

        async def action(client: FreshdeskClient) -> Any:
            if confirm is not True:
                return not_confirmed(f"Permanently delete folder {id} and all its articles")
            await client.delete(f"solutions/folders/{id}")
            return {"done": True, "deleted_folder_id": id}

        return await run_tool("freshdesk_solutions_folders_delete", client_factory, action)

    # ---- articles --------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_solutions_articles_list(folder_id: RecordId) -> str:
        """List the articles of a knowledge-base folder."""

        async def action(client: FreshdeskClient) -> Any:
            return await client.get(f"solutions/folders/{folder_id}/articles")

        return await run_tool("freshdesk_solutions_articles_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_solutions_articles_get(id: RecordId) -> str:
        """Get one knowledge-base article with its body, status, tags and SEO data."""

        async def action(client: FreshdeskClient) -> Any:
            return await client.get(f"solutions/articles/{id}")

        return await run_tool("freshdesk_solutions_articles_get", client_factory, action)

    @mcp.tool(annotations=WRITES)
    async def freshdesk_solutions_articles_create(
        folder_id: RecordId,
        title: Annotated[str, Field(description="Article title.", min_length=1)],
        description: Annotated[str, Field(description="Article body (HTML).", min_length=1)],
        status: ArticleStatus,
        type: ArticleType = None,
        tags: Tags = None,
        seo_data: Seo = None,
    ) -> str:
        """Create a knowledge-base article (status 2 publishes it). Only when the user explicitly asks."""

        async def action(client: FreshdeskClient) -> Any:
            body = compact({"title": title, "description": description, "status": status, "type": type, "tags": tags, "seo_data": seo_data})
            return await client.post(f"solutions/folders/{folder_id}/articles", body)

        return await run_tool("freshdesk_solutions_articles_create", client_factory, action)

    @mcp.tool(annotations=WRITES_IDEMPOTENT)
    async def freshdesk_solutions_articles_update(
        id: RecordId,
        title: Text = None,
        description: Annotated[str | None, Field(description="New body (HTML).")] = None,
        status: Annotated[int | None, Field(description="1 Draft, 2 Published.", ge=1, le=2)] = None,
        type: ArticleType = None,
        tags: Tags = None,
        seo_data: Seo = None,
    ) -> str:
        """Change a knowledge-base article; only the fields given change. Only when the user explicitly asks."""

        async def action(client: FreshdeskClient) -> Any:
            body = _require(
                compact({"title": title, "description": description, "status": status, "type": type, "tags": tags, "seo_data": seo_data})
            )
            return await client.put(f"solutions/articles/{id}", body)

        return await run_tool("freshdesk_solutions_articles_update", client_factory, action)

    @mcp.tool(annotations=DESTRUCTIVE_ONCE)
    async def freshdesk_solutions_articles_delete(id: RecordId, confirm: Confirm = None) -> str:
        """Permanently delete a knowledge-base article. Irreversible; needs confirm: true.

        Only when the user explicitly asks.
        """

        async def action(client: FreshdeskClient) -> Any:
            if confirm is not True:
                return not_confirmed(f"Permanently delete article {id}")
            await client.delete(f"solutions/articles/{id}")
            return {"done": True, "deleted_article_id": id}

        return await run_tool("freshdesk_solutions_articles_delete", client_factory, action)
