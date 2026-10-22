# Freshdesk MCP Server

An [MCP](https://modelcontextprotocol.io) server for the [Freshdesk](https://www.freshdesk.com)
API v2. It lets an AI agent search and read tickets and their conversations, contacts,
companies, agents, groups, the knowledge base (solutions), SLA policies, business hours and
canned responses and, on explicit request, create and update records, reply to tickets, add
notes and (with confirmation) delete, merge or convert records.

- Transport: MCP Streamable HTTP at `POST /mcp`, health probe at `GET /health`
- Stateless and multi-tenant: credentials arrive on every request as HTTP headers and are
  never stored; every tool is listed from the first `tools/list`
- Python 3.12, [uv](https://docs.astral.sh/uv/), FastMCP, httpx
- License: Apache-2.0, Copyright 2026 MSPbots.ai

## Authentication headers

| Header | Required | Type | Default | Allowed values | Description | Example |
|---|---|---|---|---|---|---|
| `X-Freshdesk-Domain` | Yes | string | none | subdomain, `<sub>.freshdesk.com` or `https://<sub>.freshdesk.com/...` | The account's Freshdesk subdomain | `yourcompany` |
| `X-Freshdesk-Api-Key` | Yes | string | none | - | API key of the agent the server acts as | `your-freshdesk-api-key` |

Where to get them: the subdomain is the `yourcompany` in `https://yourcompany.freshdesk.com`. The API key is
under **Profile settings -> View API Key** of the agent whose permissions the agent should have;
every action is performed, and audited, as that agent.

How the key is sent to Freshdesk: every call goes to `https://{subdomain}.freshdesk.com/api/v2/...`
with HTTP Basic authentication (the API key as user name, `X` as password). Only
`freshdesk.com` accounts are accepted, so the key is never sent to another host.

A request to `/mcp` without both headers gets HTTP 401 naming the missing one; a domain that is
not a Freshdesk account gets HTTP 400.

## Tools

All tools return a JSON string. List tools take `page` and `perPage` (max 100) and return
`{count, page, has_more, next_page}`; search tools return 30 results per page for at most 10
pages (300 results). Search tools also take `offset` (0-299) and return `next_offset`: when a
page is cut to fit the response size limit, `next_offset` starts right after the last result
returned, so pass it as `offset` to continue without gaps. Empty values are left out of search
results. A record too large on its own (e.g. a ticket with a long HTML description) comes back
with long fields shortened and `fields_truncated: true`; `freshdesk_tickets_get` shortens the
HTML `description` first and keeps `description_text`. Freshdesk has no endpoint for one
conversation: to read a long one in full, call `freshdesk_tickets_list_conversations` with
`perPage: 1` and `page: N`. Ticket status: 2 Open, 3 Pending, 4 Resolved, 5 Closed; priority: 1 Low, 2 Medium,
3 High, 4 Urgent. **write** marks tools that change data; **confirm** marks tools that also
need `confirm: true`.

| Area | Tools |
|---|---|
| Discovery | `freshdesk_navigate` (describe an area), `freshdesk_status` (connection and API key owner) |
| Tickets | `freshdesk_tickets_search` (**query**), `freshdesk_tickets_list` (filter, requester_id, company_id, updated_since), `freshdesk_tickets_get` (**id**, include), `freshdesk_tickets_list_conversations` (**id**); **write**: `freshdesk_tickets_create` (**subject**, **description**, **status**, **priority**, requester as email / requester_id / phone), `freshdesk_tickets_update` (**id**), `freshdesk_tickets_reply` (**id**, **body**; emails the requester), `freshdesk_tickets_add_note` (**id**, **body**; private by default), `freshdesk_tickets_update_conversation` (**conversation_id**, **body**); **confirm**: `freshdesk_tickets_delete` (**id**; to trash), `freshdesk_tickets_delete_conversation` (**conversation_id**) |
| Contacts | `freshdesk_contacts_search` (**query**), `freshdesk_contacts_list` (email, company_id, state), `freshdesk_contacts_get` (**id**), `freshdesk_contacts_autocomplete` (**term**); **write**: `freshdesk_contacts_create` (**name**), `freshdesk_contacts_update` (**id**), `freshdesk_contacts_restore` (**id**), `freshdesk_contacts_send_invite` (**id**); **confirm**: `freshdesk_contacts_make_agent` (**id**), `freshdesk_contacts_merge` (**primary_contact_id**, **secondary_contact_ids**), `freshdesk_contacts_soft_delete` (**id**), `freshdesk_contacts_hard_delete` (**id**, force) |
| Companies | `freshdesk_companies_search` (**query**), `freshdesk_companies_list`, `freshdesk_companies_get` (**id**), `freshdesk_companies_autocomplete` (**name**); **write**: `freshdesk_companies_create` (**name**), `freshdesk_companies_update` (**id**); **confirm**: `freshdesk_companies_delete` (**id**) |
| Agents | `freshdesk_agents_list` (email, state), `freshdesk_agents_get` (**id**), `freshdesk_agents_me`; **write**: `freshdesk_agents_create` (**email**, **ticket_scope**, type), `freshdesk_agents_update` (**id**); **confirm**: `freshdesk_agents_delete` (**id**) |
| Groups | `freshdesk_groups_list`, `freshdesk_groups_get` (**id**); **write**: `freshdesk_groups_create` (**name**), `freshdesk_groups_update` (**id**); **confirm**: `freshdesk_groups_delete` (**id**) |
| Knowledge base | `freshdesk_solutions_categories_list` / `_get`, `freshdesk_solutions_folders_list` (**category_id**) / `_get`, `freshdesk_solutions_articles_list` (**folder_id**) / `_get`; **write**: `..._categories_create` / `_update`, `..._folders_create` (**category_id**, **name**) / `_update`, `..._articles_create` (**folder_id**, **title**, **description**, **status**) / `_update`; **confirm**: `..._categories_delete`, `..._folders_delete`, `..._articles_delete` |
| SLA and setup | `freshdesk_sla_list`, `freshdesk_business_hours_list`, `freshdesk_business_hours_get` (**id**), `freshdesk_canned_responses_list_folders`, `freshdesk_canned_responses_get_folder` (**id**), `freshdesk_canned_responses_list_responses` (**id**); **write**: `freshdesk_sla_create` (**name**), `freshdesk_sla_update` (**id**) |

Errors come back as a JSON envelope instead of an exception:

```json
{"error": {"code": "invalid_argument", "message": "Freshdesk returned HTTP 400 for contacts: Validation failed - email: It should be a valid email address", "retryable": false}}
```

`code` is one of `not_configured`, `unauthorized`, `not_found`, `invalid_argument`,
`rate_limited`, `upstream_error`; Freshdesk's per-field validation messages are kept. Reads
are retried with backoff on 429, 5xx and network errors; writes only on 429 (honouring
`Retry-After`). Freshdesk limits API calls per minute according to the plan.

## Run locally

```bash
uv sync
uv run python -m freshdesk_mcp          # listens on 0.0.0.0:8080
```

or with Docker:

```bash
docker build --platform linux/amd64 -t freshdesk-mcp:dev .
docker run --rm -p 8080:8080 freshdesk-mcp:dev
```

Configuration (environment variables, all optional): `MCP_HTTP_HOST` (default `0.0.0.0`),
`MCP_HTTP_PORT` (default `8080`), `LOG_LEVEL` (default `INFO`). Credentials are never read
from the environment.

## Testing example

```bash
# list tools
curl -s http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-Freshdesk-Domain: yourcompany" \
  -H "X-Freshdesk-Api-Key: your-freshdesk-api-key" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

# open urgent tickets
curl -s http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-Freshdesk-Domain: yourcompany" \
  -H "X-Freshdesk-Api-Key: your-freshdesk-api-key" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"freshdesk_tickets_search","arguments":{"query":"status:2 AND priority:4"}}}'
```

Unit tests (no network access needed):

```bash
uv run pytest
```

## Delivery notes

- **Tool count (66).** Kept as is because the tool names are an existing contract; every tool
  is listed from the first `tools/list`.
- **Confirmation for irreversible actions.** Older versions asked the user to confirm deletes
  mid-call and carried on when the client could not show the prompt, which is always the case
  behind a stateless gateway. The 12 delete / merge / make-agent tools now need
  `confirm: true` and otherwise return a "not done" result describing the action.
- **Fixes against the Freshdesk API reference.** `freshdesk_contacts_send_invite` now uses PUT
  (the documented method); `freshdesk_agents_create` sends the agent `type` as Freshdesk's
  numeric `agent_type` (support_agent 1, field_agent 2, collaborator 3) instead of an unknown
  `type` field.
- **Search.** Queries are wrapped in the double quotes Freshdesk requires; string values inside
  take single quotes (`tag:'vip'`).
- **Notes** default to private; replies email the requester, which the description states.

## License

Apache License 2.0 - see [LICENSE](LICENSE) and [NOTICE](NOTICE).
