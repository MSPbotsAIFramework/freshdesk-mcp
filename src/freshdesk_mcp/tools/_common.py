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
"""Shared argument types, paging / search helpers, confirmation and the error-handling wrapper."""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, get_args

from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped, error_envelope
from ..api_client import FreshdeskClient, FreshdeskError, Page
from ..config import MAX_PER_PAGE

logger = logging.getLogger("freshdesk_mcp.tools")

ClientFactory = Callable[[], FreshdeskClient | None]

READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)
LOCAL = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
WRITES_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True)
DESTRUCTIVE_ONCE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)

NOT_CONFIGURED = error_envelope(
    "not_configured", "Freshdesk credentials are missing for this request (X-Freshdesk-Domain, X-Freshdesk-Api-Key).", False
)

RecordId = Annotated[int, Field(description="Numeric Freshdesk id.", ge=1)]
OptionalId = Annotated[int | None, Field(description="Numeric Freshdesk id.", ge=1)]
Page_ = Annotated[int | None, Field(description="Page number, from 1 (default 1).", ge=1)]
PerPage = Annotated[int | None, Field(description=f"Records per page (default 30, max {MAX_PER_PAGE}).", ge=1, le=MAX_PER_PAGE)]
Offset = Annotated[
    int | None,
    Field(description="Record position to start from (0-based, overrides page); pass next_offset from the previous result.", ge=0),
]
# Freshdesk list endpoints return 30 records per page when per_page is not sent.
DEFAULT_PER_PAGE = 30
SearchQuery = Annotated[str, Field(description="Freshdesk query language, without surrounding quotes, e.g. status:2 AND priority:4.", min_length=1, max_length=510)]
SearchPage = Annotated[int | None, Field(description="Result page, 1-10 (30 results per page).", ge=1, le=10)]
SearchOffset = Annotated[
    int | None,
    Field(description="Result position to start from, 0-299 (overrides page); pass next_offset from the previous result.", ge=0, le=299),
]
# Freshdesk search returns 30 results per page and at most 10 pages.
SEARCH_PAGE_SIZE = 30
SEARCH_MAX_RESULTS = 300
Confirm = Annotated[bool | None, Field(description="Must be true, set only after the user approved this exact action.")]
Ids = Annotated[list[int] | None, Field(description="Numeric ids.")]
Strings = Annotated[list[str] | None, Field(description="Values.")]
CustomFields = Annotated[dict[str, Any] | None, Field(description="Custom field values keyed by field name (cf_...).")]


def compact(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None}


def page_params(page: int | None, per_page: int | None) -> dict[str, int | None]:
    return {"page": page, "per_page": per_page}


def locate(offset: int | None, page: int | None, per_page: int | None) -> tuple[int, int, int]:
    """(page, per_page, start) for a read from record ``start``.

    Freshdesk lists page by number only, so a position is reached by asking for the page that
    holds it (same page size) and skipping the records before it.
    """
    size = per_page or DEFAULT_PER_PAGE
    start = offset if offset is not None else ((page or 1) - 1) * size
    return start // size + 1, size, start


def paging(offset: int | None, page: int | None, per_page: int | None) -> tuple[dict[str, int | None], int, int, int]:
    """(query params, page, per_page, start); without offset the params are exactly page / per_page as given."""
    number, size, start = locate(offset, page, per_page)
    return page_params(page if offset is None else number, per_page), number, size, start


def page_result(key: str, page: Page, number: int | None, size: int | None = None, start: int | None = None) -> dict:
    """Records from ``start`` of page ``number``, plus paging.

    {key, count, page, has_more, next_page, offset, next_offset}. next_offset continues right after
    the last record returned, also when the result is cut to fit the size limit (a page number
    cannot: the rest of a cut page is not on any other page).
    """
    current = number or 1
    size = size or DEFAULT_PER_PAGE
    first = (current - 1) * size
    start = first if start is None else start
    items = page.items[start - first :]
    return compact(
        {
            key: items,
            "count": len(items),
            "page": current,
            "has_more": page.has_more,
            "next_page": current + 1 if page.has_more else None,
            "offset": start,
            "next_offset": start + len(items) if page.has_more else None,
            "_resume": {"key": "next_offset", "start": start},
        }
    )


def quoted(query: str) -> str:
    """Freshdesk search queries must be wrapped in double quotes."""
    text = query.strip()
    if not text:
        raise ValueError("query must not be blank.")
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text
    if '"' in text:
        raise ValueError("Use single quotes for string values inside the query, e.g. tag:'vip'.")
    return f'"{text}"'


def prune(value: Any) -> Any:
    """Drop null, empty-string, empty-list and empty-object values (search results are full of them)."""
    if isinstance(value, dict):
        kept = {k: prune(v) for k, v in value.items()}
        return {k: v for k, v in kept.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [prune(v) for v in value]
    return value


async def search(client: FreshdeskClient, entity: str, query: str, page: int | None, offset: int | None = None) -> dict:
    """One Freshdesk search page, sliced from ``offset`` so a cut result can continue mid-page.

    Freshdesk returns pages of 30 and has no page size, so a page that does not fit the size
    limit is cut; next_offset then starts right after the last result returned.
    """
    start = offset if offset is not None else ((page or 1) - 1) * SEARCH_PAGE_SIZE
    current, skip = start // SEARCH_PAGE_SIZE + 1, start % SEARCH_PAGE_SIZE
    payload = await client.get(f"search/{entity}", {"query": quoted(query), "page": current}) or {}
    results = payload.get("results", []) if isinstance(payload, dict) else []
    total = payload.get("total") if isinstance(payload, dict) else None
    shown = [prune(r) for r in results[skip:]]
    after = start + len(shown)
    reachable = min(total, SEARCH_MAX_RESULTS) if isinstance(total, int) else after
    more = after < reachable
    return compact(
        {
            "results": shown,
            "count": len(shown),
            "total": total,
            "page": current,
            "offset": start,
            "has_more": more,
            "next_offset": after if more else None,
            "_resume": {"key": "next_offset", "start": start},
        }
    )


def not_confirmed(action: str) -> dict:
    return {
        "done": False,
        "action": action,
        "message": "Not done. Describe this action to the user and call again with confirm: true only after they approve it.",
    }


def enum_value(value: Any, choices: Any) -> Any:
    """Canonical spelling of a case-insensitive match in ``choices`` (a Literal type).

    Any other value passes through unchanged, so the API decides (older callers sent free text).
    """
    if not isinstance(value, str):
        return value
    wanted = value.strip().lower()
    return next((c for c in get_args(choices) if isinstance(c, str) and c.lower() == wanted), value)


async def run_tool(
    name: str,
    client_factory: ClientFactory,
    action: Callable[[FreshdeskClient], Awaitable[Any]],
) -> str:
    """Resolve the caller's client, run ``action``, and always return a string.

    Errors never escape as exceptions: they become the standard error envelope.
    """
    client = client_factory()
    if client is None:
        return NOT_CONFIGURED
    started = time.monotonic()
    try:
        result = await action(client)
    except FreshdeskError as e:
        logger.info("tool=%s outcome=error status=%s ms=%d", name, e.status_code, _ms(started))
        return e.to_envelope()
    except ValueError as e:
        logger.info("tool=%s outcome=invalid_argument ms=%d", name, _ms(started))
        return error_envelope("invalid_argument", str(e), False)
    except Exception as e:  # noqa: BLE001 - tools must never raise
        logger.warning("tool=%s outcome=unexpected error=%s ms=%d", name, type(e).__name__, _ms(started))
        return error_envelope("upstream_error", f"Unexpected error: {type(e).__name__}", True)
    logger.info("tool=%s outcome=ok ms=%d", name, _ms(started))
    if result is None:
        result = {"done": True}
    return result if isinstance(result, str) else dump_json_capped(result)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
