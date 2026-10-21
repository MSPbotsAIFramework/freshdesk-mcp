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
"""Tickets and their conversations (replies and notes)."""

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

Status = Annotated[int | None, Field(description="2 Open, 3 Pending, 4 Resolved, 5 Closed (or a custom status id).", ge=2)]
Priority = Annotated[int | None, Field(description="1 Low, 2 Medium, 3 High, 4 Urgent.", ge=1, le=4)]
TicketView = Literal["new_and_my_open", "watching", "spam", "deleted"]
_INCLUDES = {"conversations", "requester", "company", "stats"}


def _include(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    parts = [p.strip() for p in value.split(",") if p.strip()]
    bad = [p for p in parts if p not in _INCLUDES]
    if bad:
        raise ValueError(f"include accepts {', '.join(sorted(_INCLUDES))} (got {', '.join(bad)}).")
    return ",".join(parts)


def register(mcp: FastMCP, client_factory: ClientFactory) -> None:
    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_tickets_search(query: SearchQuery, page: SearchPage = None) -> str:
        """Search tickets with the Freshdesk query language, e.g. status:2 AND priority:4, or agent_id:123.

        String values take single quotes: tag:'vip'. 30 results per page, at most 10 pages.
        """

        async def action(client: FreshdeskClient) -> dict:
            return await search(client, "tickets", query, page)

        return await run_tool("freshdesk_tickets_search", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_tickets_list(
        page: Page_ = None,
        perPage: PerPage = None,
        filter: Annotated[TicketView | str | None, Field(description="Predefined view.")] = None,
        requester_id: OptionalId = None,
        company_id: OptionalId = None,
        updated_since: Annotated[
            str | None, Field(description="ISO 8601; without it Freshdesk returns tickets created in the last 30 days.")
        ] = None,
    ) -> str:
        """List tickets, newest first, optionally by view, requester, company or update time."""

        async def action(client: FreshdeskClient) -> dict:
            params = compact(
                {"filter": enum_value(filter, TicketView), "requester_id": requester_id, "company_id": company_id, "updated_since": updated_since}
            ) | page_params(page, perPage)
            return page_result("tickets", await client.page("tickets", params), page)

        return await run_tool("freshdesk_tickets_list", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_tickets_get(
        id: RecordId,
        include: Annotated[str | None, Field(description="Comma-separated: conversations, requester, company, stats.")] = None,
    ) -> str:
        """Get one ticket, optionally with its conversations (first 10), requester, company or stats."""

        async def action(client: FreshdeskClient) -> Any:
            return await client.get(f"tickets/{id}", {"include": _include(include)})

        return await run_tool("freshdesk_tickets_get", client_factory, action)

    @mcp.tool(annotations=WRITES)
    async def freshdesk_tickets_create(
        subject: Annotated[str, Field(description="Ticket subject.", min_length=1)],
        description: Annotated[str, Field(description="Ticket body (HTML).", min_length=1)],
        status: Annotated[int, Field(description="2 Open, 3 Pending, 4 Resolved, 5 Closed.", ge=2)],
        priority: Annotated[int, Field(description="1 Low, 2 Medium, 3 High, 4 Urgent.", ge=1, le=4)],
        email: Annotated[str | None, Field(description="Requester email (or requester_id / phone).")] = None,
        requester_id: OptionalId = None,
        phone: Annotated[str | None, Field(description="Requester phone; also give name for a new contact.")] = None,
        name: Annotated[str | None, Field(description="Requester name.")] = None,
        source: Annotated[int | None, Field(description="1 Email, 2 Portal, 3 Phone, 7 Chat, 9 Feedback widget, 10 Outbound email.")] = None,
        type: Annotated[str | None, Field(description="Ticket type, e.g. Question, Incident, Problem.")] = None,
        group_id: OptionalId = None,
        responder_id: Annotated[int | None, Field(description="Agent id to assign.", ge=1)] = None,
        company_id: OptionalId = None,
        tags: Strings = None,
        cc_emails: Strings = None,
        custom_fields: CustomFields = None,
    ) -> str:
        """Create a ticket for a requester (email, requester_id or phone). Only when the user explicitly asks."""

        async def action(client: FreshdeskClient) -> Any:
            if not (email or requester_id or phone):
                raise ValueError("Give the requester as email, requester_id or phone.")
            body = compact(
                {
                    "subject": subject,
                    "description": description,
                    "status": status,
                    "priority": priority,
                    "email": email,
                    "requester_id": requester_id,
                    "phone": phone,
                    "name": name,
                    "source": source,
                    "type": type,
                    "group_id": group_id,
                    "responder_id": responder_id,
                    "company_id": company_id,
                    "tags": tags,
                    "cc_emails": cc_emails,
                    "custom_fields": custom_fields,
                }
            )
            return await client.post("tickets", body)

        return await run_tool("freshdesk_tickets_create", client_factory, action)

    @mcp.tool(annotations=WRITES_IDEMPOTENT)
    async def freshdesk_tickets_update(
        id: RecordId,
        subject: Annotated[str | None, Field(description="New subject.")] = None,
        description: Annotated[str | None, Field(description="New body (HTML).")] = None,
        status: Status = None,
        priority: Priority = None,
        type: Annotated[str | None, Field(description="Ticket type.")] = None,
        group_id: OptionalId = None,
        responder_id: Annotated[int | None, Field(description="Agent id to assign.", ge=1)] = None,
        company_id: OptionalId = None,
        tags: Annotated[list[str] | None, Field(description="Replaces all tags.")] = None,
        custom_fields: CustomFields = None,
    ) -> str:
        """Change ticket fields (status, priority, assignee, ...); only the fields given change.

        Only when the user explicitly asks.
        """

        async def action(client: FreshdeskClient) -> Any:
            body = compact(
                {
                    "subject": subject,
                    "description": description,
                    "status": status,
                    "priority": priority,
                    "type": type,
                    "group_id": group_id,
                    "responder_id": responder_id,
                    "company_id": company_id,
                    "tags": tags,
                    "custom_fields": custom_fields,
                }
            )
            if not body:
                raise ValueError("Nothing to update: pass at least one field to change.")
            return await client.put(f"tickets/{id}", body)

        return await run_tool("freshdesk_tickets_update", client_factory, action)

    @mcp.tool(annotations=WRITES)
    async def freshdesk_tickets_reply(
        id: RecordId,
        body: Annotated[str, Field(description="Reply text (HTML); it is emailed to the requester.", min_length=1)],
        from_email: Annotated[str | None, Field(description="Support address to send from.")] = None,
        user_id: Annotated[int | None, Field(description="Agent the reply is sent as.", ge=1)] = None,
        cc_emails: Strings = None,
        bcc_emails: Strings = None,
    ) -> str:
        """Send a public reply on a ticket; the requester gets it by email. Only when the user explicitly asks."""

        async def action(client: FreshdeskClient) -> Any:
            payload = compact(
                {"body": body, "from_email": from_email, "user_id": user_id, "cc_emails": cc_emails, "bcc_emails": bcc_emails}
            )
            return await client.post(f"tickets/{id}/reply", payload)

        return await run_tool("freshdesk_tickets_reply", client_factory, action)

    @mcp.tool(annotations=WRITES)
    async def freshdesk_tickets_add_note(
        id: RecordId,
        body: Annotated[str, Field(description="Note text (HTML).", min_length=1)],
        private: Annotated[bool | None, Field(description="Private (agents only) note; default true.")] = None,
        user_id: Annotated[int | None, Field(description="Agent the note is added as.", ge=1)] = None,
        notify_emails: Annotated[list[str] | None, Field(description="Agent emails to notify.")] = None,
    ) -> str:
        """Add a note to a ticket, private (agents only) by default. Only when the user explicitly asks."""

        async def action(client: FreshdeskClient) -> Any:
            payload = compact(
                {"body": body, "private": private is not False, "user_id": user_id, "notify_emails": notify_emails}
            )
            return await client.post(f"tickets/{id}/notes", payload)

        return await run_tool("freshdesk_tickets_add_note", client_factory, action)

    @mcp.tool(annotations=READ_ONLY)
    async def freshdesk_tickets_list_conversations(id: RecordId, page: Page_ = None, perPage: PerPage = None) -> str:
        """List a ticket's conversations (replies and notes) in order, with author and private flag."""

        async def action(client: FreshdeskClient) -> dict:
            return page_result("conversations", await client.page(f"tickets/{id}/conversations", page_params(page, perPage)), page)

        return await run_tool("freshdesk_tickets_list_conversations", client_factory, action)

    @mcp.tool(annotations=WRITES_IDEMPOTENT)
    async def freshdesk_tickets_update_conversation(
        conversation_id: RecordId,
        body: Annotated[str, Field(description="New text (HTML).", min_length=1)],
    ) -> str:
        """Edit the text of a reply or note. Only when the user explicitly asks."""

        async def action(client: FreshdeskClient) -> Any:
            return await client.put(f"conversations/{conversation_id}", {"body": body})

        return await run_tool("freshdesk_tickets_update_conversation", client_factory, action)

    @mcp.tool(annotations=DESTRUCTIVE)
    async def freshdesk_tickets_delete(id: RecordId, confirm: Confirm = None) -> str:
        """Move a ticket to trash (removed from views and reports). Needs confirm: true.

        Only when the user explicitly asks for that ticket.
        """

        async def action(client: FreshdeskClient) -> Any:
            if confirm is not True:
                return not_confirmed(f"Move ticket {id} to trash")
            await client.delete(f"tickets/{id}")
            return {"done": True, "deleted_ticket_id": id}

        return await run_tool("freshdesk_tickets_delete", client_factory, action)

    @mcp.tool(annotations=DESTRUCTIVE_ONCE)
    async def freshdesk_tickets_delete_conversation(conversation_id: RecordId, confirm: Confirm = None) -> str:
        """Permanently delete a reply or note from a ticket. Irreversible; needs confirm: true.

        Only when the user explicitly asks for that conversation.
        """

        async def action(client: FreshdeskClient) -> Any:
            if confirm is not True:
                return not_confirmed(f"Permanently delete conversation {conversation_id}")
            await client.delete(f"conversations/{conversation_id}")
            return {"done": True, "deleted_conversation_id": conversation_id}

        return await run_tool("freshdesk_tickets_delete_conversation", client_factory, action)


