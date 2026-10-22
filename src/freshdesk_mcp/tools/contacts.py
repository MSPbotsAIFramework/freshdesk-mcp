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
"""Contacts: the people who raise tickets."""

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ..api_client import FreshdeskClient
from ._common import (
    DESTRUCTIVE,
    DESTRUCTIVE_ONCE,
    READ_ONLY,
    WRITES,
    WRITES_IDEMPOTENT,
    ClientFactory,
    Confirm,
    CustomFields,
    OptionalId,
    Page_,
    PerPage,
    RecordId,
    SearchOffset,
    SearchPage,
    SearchQuery,
    Strings,
    compact,
    enum_value,
    not_confirmed,
    page_params,
    page_result,
    run_tool,
    search,
)

Text = Annotated[str | None, Field(description="Value.")]
ContactState = Literal["verified", "unverified", "blocked", "deleted"]


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_contacts_search(
        query: SearchQuery, page: SearchPage = None, offset: SearchOffset = None
    ) -> str:
        """Search contacts with the Freshdesk query language, e.g. email:'jane@example.com' or company_id:12."""

        async def action(client: FreshdeskClient) -> dict:
            return await search(client, "contacts", query, page, offset)

        return await run_tool("freshdesk_contacts_search", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_contacts_list(
        page: Page_ = None,
        perPage: PerPage = None,
        email: Annotated[str | None, Field(description="Exact email.")] = None,
        company_id: OptionalId = None,
        state: Annotated[ContactState | str | None, Field(description="Contact state.")] = None,
    ) -> str:
        """List contacts, optionally by email, company or state."""

        async def action(client: FreshdeskClient) -> dict:
            params = compact({"email": email, "company_id": company_id, "state": enum_value(state, ContactState)}) | page_params(page, perPage)
            return page_result("contacts", await client.page("contacts", params), page)

        return await run_tool("freshdesk_contacts_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_contacts_get(id: RecordId) -> str:
        """Get one contact: name, emails, phones, company, job title, tags and custom fields."""

        async def action(client: FreshdeskClient) -> Any:
            return await client.get(f"contacts/{id}")

        return await run_tool("freshdesk_contacts_get", client_factory, action)

    @mcp.tool(annotations=WRITES)
    async def freshdesk_contacts_create(
        name: Annotated[str, Field(description="Contact name.", min_length=1)],
        email: Annotated[str | None, Field(description="Primary email (one of email / phone / mobile / twitter_id is required).")] = None,
        phone: Text = None,
        mobile: Text = None,
        twitter_id: Text = None,
        company_id: OptionalId = None,
        job_title: Text = None,
        address: Text = None,
        time_zone: Text = None,
        language: Annotated[str | None, Field(description="Language code, e.g. en.")] = None,
        tags: Strings = None,
        other_emails: Strings = None,
        custom_fields: CustomFields = None,
    ) -> str:
        """Create a contact. Only when the user explicitly asks."""

        async def action(client: FreshdeskClient) -> Any:
            if not (email or phone or mobile or twitter_id):
                raise ValueError("Give at least one of email, phone, mobile or twitter_id.")
            body = compact(
                {
                    "name": name,
                    "email": email,
                    "phone": phone,
                    "mobile": mobile,
                    "twitter_id": twitter_id,
                    "company_id": company_id,
                    "job_title": job_title,
                    "address": address,
                    "time_zone": time_zone,
                    "language": language,
                    "tags": tags,
                    "other_emails": other_emails,
                    "custom_fields": custom_fields,
                }
            )
            return await client.post("contacts", body)

        return await run_tool("freshdesk_contacts_create", client_factory, action)

    @mcp.tool(annotations=WRITES_IDEMPOTENT)
    async def freshdesk_contacts_update(
        id: RecordId,
        name: Text = None,
        email: Text = None,
        phone: Text = None,
        mobile: Text = None,
        company_id: OptionalId = None,
        job_title: Text = None,
        address: Text = None,
        tags: Annotated[list[str] | None, Field(description="Replaces all tags.")] = None,
        custom_fields: CustomFields = None,
    ) -> str:
        """Change a contact; only the fields given change. Only when the user explicitly asks."""

        async def action(client: FreshdeskClient) -> Any:
            body = compact(
                {
                    "name": name,
                    "email": email,
                    "phone": phone,
                    "mobile": mobile,
                    "company_id": company_id,
                    "job_title": job_title,
                    "address": address,
                    "tags": tags,
                    "custom_fields": custom_fields,
                }
            )
            if not body:
                raise ValueError("Nothing to update: pass at least one field to change.")
            return await client.put(f"contacts/{id}", body)

        return await run_tool("freshdesk_contacts_update", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_contacts_autocomplete(
        term: Annotated[str, Field(description="Start of a name or email.", min_length=1)],
    ) -> str:
        """Quick contact lookup by the start of a name or email; returns ids and names."""

        async def action(client: FreshdeskClient) -> Any:
            return await client.get("contacts/autocomplete", {"term": term.strip()})

        return await run_tool("freshdesk_contacts_autocomplete", client_factory, action)

    @mcp.tool(annotations=DESTRUCTIVE_ONCE)
    async def freshdesk_contacts_make_agent(id: RecordId, confirm: Confirm = None) -> str:
        """Convert a contact into an agent (uses an agent seat; extra emails are dropped). Needs confirm: true.

        Only when the user explicitly asks.
        """

        async def action(client: FreshdeskClient) -> Any:
            if confirm is not True:
                return not_confirmed(f"Convert contact {id} into an agent (uses a paid agent seat)")
            return await client.put(f"contacts/{id}/make_agent")

        return await run_tool("freshdesk_contacts_make_agent", client_factory, action)

    @mcp.tool(annotations=WRITES_IDEMPOTENT)
    async def freshdesk_contacts_restore(id: RecordId) -> str:
        """Restore a soft-deleted contact. Only when the user explicitly asks."""

        async def action(client: FreshdeskClient) -> Any:
            await client.put(f"contacts/{id}/restore")
            return {"done": True, "restored_contact_id": id}

        return await run_tool("freshdesk_contacts_restore", client_factory, action)

    @mcp.tool(annotations=WRITES)
    async def freshdesk_contacts_send_invite(id: RecordId) -> str:
        """Email a contact an invitation to activate their support-portal account. Only when explicitly asks."""

        async def action(client: FreshdeskClient) -> Any:
            await client.put(f"contacts/{id}/send_invite")
            return {"done": True, "invited_contact_id": id}

        return await run_tool("freshdesk_contacts_send_invite", client_factory, action)

    @mcp.tool(annotations=DESTRUCTIVE_ONCE)
    async def freshdesk_contacts_merge(
        primary_contact_id: Annotated[int, Field(description="Contact that remains.", ge=1)],
        secondary_contact_ids: Annotated[list[int], Field(description="Contacts merged into it (and removed).", min_length=1)],
        contact: Annotated[
            dict[str, Any] | None,
            Field(description="Fields to keep on the primary, e.g. {email, phone, mobile, other_emails, company_ids}."),
        ] = None,
        confirm: Confirm = None,
    ) -> str:
        """Merge duplicate contacts into one; the secondaries disappear. Irreversible; needs confirm: true.

        Only when the user explicitly asks.
        """

        async def action(client: FreshdeskClient) -> Any:
            secondary = [i for i in dict.fromkeys(secondary_contact_ids) if i != primary_contact_id]
            if not secondary:
                raise ValueError("secondary_contact_ids must contain contacts other than the primary.")
            if confirm is not True:
                return not_confirmed(f"Merge contacts {secondary} into contact {primary_contact_id}")
            body = compact({"primary_contact_id": primary_contact_id, "secondary_contact_ids": secondary, "contact": contact})
            await client.post("contacts/merge", body)
            return {"done": True, "primary_contact_id": primary_contact_id, "merged_contact_ids": secondary}

        return await run_tool("freshdesk_contacts_merge", client_factory, action)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def freshdesk_contacts_soft_delete(id: RecordId, confirm: Confirm = None) -> str:
        """Soft-delete (deactivate) a contact; freshdesk_contacts_restore undoes it. Needs confirm: true.

        Only when the user explicitly asks.
        """

        async def action(client: FreshdeskClient) -> Any:
            if confirm is not True:
                return not_confirmed(f"Soft-delete contact {id}")
            await client.delete(f"contacts/{id}")
            return {"done": True, "soft_deleted_contact_id": id}

        return await run_tool("freshdesk_contacts_soft_delete", client_factory, action)

    @mcp.tool(annotations=DESTRUCTIVE_ONCE)
    async def freshdesk_contacts_hard_delete(
        id: RecordId,
        force: Annotated[bool | None, Field(description="Delete even if the contact was not soft-deleted first.")] = None,
        confirm: Confirm = None,
    ) -> str:
        """Permanently delete a contact and their data. Irreversible; needs confirm: true.

        Only when the user explicitly asks. Without force the contact must be soft-deleted first.
        """

        async def action(client: FreshdeskClient) -> Any:
            if confirm is not True:
                return not_confirmed(f"Permanently delete contact {id}{' (forced)' if force else ''}")
            await client.delete(f"contacts/{id}/hard_delete", {"force": True if force else None})
            return {"done": True, "hard_deleted_contact_id": id}

        return await run_tool("freshdesk_contacts_hard_delete", client_factory, action)
