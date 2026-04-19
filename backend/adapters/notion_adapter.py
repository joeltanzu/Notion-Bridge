"""
Wrapper around the official notion-client SDK with rate limiting,
pagination, and block tree fetching.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Optional

from notion_client import AsyncClient
from notion_client.errors import APIResponseError
from backend.utils.rate_limiter import TokenBucketRateLimiter

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_RETRIES = 3
BASE_DELAY = 1.0


class NotionAdapter:
    def __init__(self, token: str):
        self._client = AsyncClient(auth=token)
        self._limiter = TokenBucketRateLimiter(rate=2.5, capacity=8)

    async def _call(
        self,
        request: Callable[[], Awaitable[Any]] | Awaitable[Any],
        retries: int = MAX_RETRIES,
    ) -> Any:
        """Execute a request with retry logic for transient errors."""
        last_exception = None
        for attempt in range(retries):
            try:
                await self._limiter.acquire()
                coro = request() if callable(request) else request
                return await coro
            except APIResponseError as e:
                last_exception = e
                if e.status not in RETRYABLE_STATUS_CODES or attempt == retries - 1:
                    raise
                delay = BASE_DELAY * (2**attempt)
                logger.warning(
                    "Notion API error %s on attempt %d/%d, retrying in %.1fs: %s",
                    e.status,
                    attempt + 1,
                    retries,
                    delay,
                    str(e),
                )
                await asyncio.sleep(delay)
            except Exception as e:
                last_exception = e
                if attempt == retries - 1:
                    raise
                if not callable(request):
                    raise
                delay = BASE_DELAY * (2**attempt)
                logger.warning(
                    "Notion API error on attempt %d/%d, retrying in %.1fs: %s",
                    attempt + 1,
                    retries,
                    delay,
                    str(e),
                )
                await asyncio.sleep(delay)
        raise last_exception

    # ── Pages ──────────────────────────────────────────────────────────────

    async def get_page(self, page_id: str) -> dict:
        return await self._call(lambda: self._client.pages.retrieve(page_id=page_id))

    async def get_database(self, database_id: str) -> dict:
        return await self._call(
            lambda: self._client.databases.retrieve(database_id=database_id)
        )

    async def get_page_title(self, page: dict) -> str:
        props = page.get("properties", {})
        for prop in props.values():
            if prop.get("type") == "title":
                parts = prop.get("title", [])
                return "".join(p.get("plain_text", "") for p in parts)
        # Fallback for child_page blocks
        return page.get("child_page", {}).get("title", "Untitled")

    async def update_page_title(self, page_id: str, title: str) -> None:
        await self._call(
            lambda: self._client.pages.update(
                page_id=page_id,
                properties={
                    "title": {"title": [{"type": "text", "text": {"content": title}}]}
                },
            )
        )

    async def create_page(
        self, parent_id: str, title: str, is_database_row: bool = False
    ) -> dict:
        if is_database_row:
            parent = {"database_id": parent_id}
        else:
            parent = {"page_id": parent_id}
        return await self._call(
            lambda: self._client.pages.create(
                parent=parent,
                properties={
                    "title": {"title": [{"type": "text", "text": {"content": title}}]}
                },
            )
        )

    # ── Blocks ─────────────────────────────────────────────────────────────

    async def get_blocks(self, block_id: str) -> list[dict]:
        """Fetch all children of a block/page, handling pagination."""
        blocks = []
        cursor = None
        while True:
            kwargs: dict = {"block_id": block_id, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = await self._call(
                lambda kwargs=kwargs: self._client.blocks.children.list(**kwargs)
            )
            blocks.extend(resp.get("results", []))
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return blocks

    async def get_block_tree(self, page_id: str) -> list[dict]:
        """
        Recursively fetch all blocks for a page, attaching children
        as a 'children' key on each block dict.
        """
        blocks = await self.get_blocks(page_id)
        children_needed = [b for b in blocks if b.get("has_children")]
        if children_needed:
            results = await asyncio.gather(
                *[self.get_block_tree(b["id"]) for b in children_needed]
            )
            for block, children in zip(children_needed, results):
                block["children"] = children
        for block in blocks:
            if "children" not in block:
                block["children"] = []
        return blocks

    async def replace_blocks(self, page_id: str, new_blocks: list[dict]) -> None:
        """
        Replace all content blocks of a page.
        Locks child_page and child_database blocks in place (deleting them would
        destroy the sub-pages/databases). All other blocks are deleted and
        replaced with new_blocks.
        """
        _LOCKED = {"child_page", "child_database"}
        existing = await self.get_blocks(page_id)
        await asyncio.gather(
            *[
                self._call(
                    lambda block_id=b["id"]: self._client.blocks.delete(
                        block_id=block_id
                    )
                )
                for b in existing
                if b.get("type") not in _LOCKED
            ]
        )

        # Append in batches of 100
        for i in range(0, len(new_blocks), 100):
            batch = new_blocks[i : i + 100]
            await self._call(
                lambda batch=batch: self._client.blocks.children.append(
                    block_id=page_id, children=batch
                )
            )

    # ── Page tree traversal ────────────────────────────────────────────────

    async def get_child_pages(self, page_id: str) -> list[dict]:
        """Return immediate child_page blocks."""
        blocks = await self.get_blocks(page_id)
        return [b for b in blocks if b.get("type") == "child_page"]

    async def get_child_databases(self, page_id: str) -> list[dict]:
        """Return immediate child_database blocks."""
        blocks = await self.get_blocks(page_id)
        return [b for b in blocks if b.get("type") == "child_database"]

    async def get_database_rows(self, database_id: str) -> list[dict]:
        """Return all page rows in a database."""
        rows = []
        cursor = None
        while True:
            kwargs: dict = {"database_id": database_id, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = await self._call(
                lambda kwargs=kwargs: self._client.databases.query(**kwargs)
            )
            rows.extend(r for r in resp.get("results", []) if r.get("object") == "page")
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return rows

    async def walk_page_tree(
        self, root_id: str, progress_cb=None, _depth: int = 0
    ) -> list[dict]:
        """
        BFS traversal of a Notion page tree.
        Database containers are traversed to discover row pages, but only
        page-backed content is returned as syncable items.
        """
        result = []
        queue = [("page", root_id, None)]
        visited = set()
        total = 0

        while queue:
            item_type, item_id, parent_id = queue.pop(0)
            visit_key = (item_type, item_id)
            if visit_key in visited:
                continue
            visited.add(visit_key)

            if item_type == "database":
                try:
                    rows = await self.get_database_rows(item_id)
                except Exception:
                    continue
                for row in rows:
                    queue.append(("database_row", row["id"], item_id))
                continue

            try:
                page = await self.get_page(item_id)
            except Exception:
                continue

            title = await self.get_page_title(page)
            page_type = "database_row" if item_type == "database_row" else "page"
            result.append(
                {
                    "page_id": item_id,
                    "parent_id": parent_id,
                    "title": title,
                    "page_type": page_type,
                    "is_database": False,
                    "last_edited_time": page.get("last_edited_time", ""),
                }
            )
            total += 1
            if progress_cb:
                await progress_cb(total, title)

            # Get child pages
            children = await self.get_child_pages(item_id)
            for child in children:
                child_id = child["id"]
                queue.append(("page", child_id, item_id))

            databases = await self.get_child_databases(item_id)
            for db in databases:
                db_id = db["id"]
                queue.append(("database", db_id, item_id))

        return result

    async def get_block(self, block_id: str) -> dict:
        """Fetch a single block by ID."""
        return await self._call(
            lambda: self._client.blocks.retrieve(block_id=block_id)
        )

    # ── Metadata ───────────────────────────────────────────────────────────

    async def get_last_edited_time(self, page_id: str) -> str:
        page = await self.get_page(page_id)
        return page.get("last_edited_time", "")

    async def delete_page(self, page_id: str) -> None:
        """Delete a page or block."""
        await self._call(lambda: self._client.blocks.delete(block_id=page_id))
