"""Tests for NotionAdapter - retry logic and API error handling."""

import pytest
import asyncio
from unittest.mock import AsyncMock, Mock, patch
from notion_client.errors import APIResponseError

from backend.adapters.notion_adapter import (
    NotionAdapter,
    RETRYABLE_STATUS_CODES,
    MAX_RETRIES,
)


class TestNotionAdapterRetry:
    """Test NotionAdapter retry logic for transient errors."""

    @pytest.fixture
    def adapter(self):
        """Create a NotionAdapter with a mock token."""
        return NotionAdapter("test-token")

    @pytest.mark.asyncio
    async def test_successful_call_no_retry(self, adapter):
        """Successful call should return immediately without retries."""

        async def success_coro():
            return {"result": "success"}

        result = await adapter._call(success_coro(), retries=3)
        assert result == {"result": "success"}

    @pytest.mark.asyncio
    async def test_retries_with_fresh_coroutines(self, adapter):
        """Retry paths should recreate the coroutine for each attempt."""
        attempts = 0

        async def flaky_call():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("transient")
            return {"result": "success"}

        result = await adapter._call(flaky_call, retries=3)
        assert result == {"result": "success"}
        assert attempts == 2

    def test_retryable_status_codes_config(self):
        """Verify the set of retryable status codes."""
        expected = {429, 500, 502, 503, 504}
        assert RETRYABLE_STATUS_CODES == expected


class TestNotionAdapterRateLimiter:
    """Test that NotionAdapter uses rate limiting."""

    def test_limiter_configured(self):
        """Adapter should have a rate limiter configured."""
        adapter = NotionAdapter("test-token")
        assert adapter._limiter is not None

    def test_limiter_has_expected_attributes(self):
        """Rate limiter should have rate and capacity attributes."""
        from backend.utils.rate_limiter import TokenBucketRateLimiter

        adapter = NotionAdapter("test-token")

        assert isinstance(adapter._limiter, TokenBucketRateLimiter)
        assert hasattr(adapter._limiter, "rate")
        assert hasattr(adapter._limiter, "capacity")


class TestNotionAdapterMethods:
    """Test that NotionAdapter methods use _call correctly."""

    @pytest.mark.asyncio
    async def test_get_page_uses_call(self):
        """get_page should use the _call wrapper."""
        adapter = NotionAdapter("test-token")
        adapter._call = AsyncMock(return_value={"properties": {}})

        await adapter.get_page("page-123")

        adapter._call.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_page_uses_call(self):
        """create_page should use the _call wrapper."""
        adapter = NotionAdapter("test-token")
        adapter._call = AsyncMock(return_value={"id": "new-page-id"})

        result = await adapter.create_page("parent-123", "Test Page")

        adapter._call.assert_called_once()
        assert result["id"] == "new-page-id"

    @pytest.mark.asyncio
    async def test_get_block_tree_returns_list(self):
        """get_block_tree should return a list of blocks."""
        adapter = NotionAdapter("test-token")

        # Mock get_blocks to return some blocks
        mock_blocks = [
            {"id": "block1", "type": "paragraph", "has_children": False},
        ]
        adapter.get_blocks = AsyncMock(return_value=mock_blocks)

        result = await adapter.get_block_tree("page-123")

        # get_block_tree should call get_blocks
        adapter.get_blocks.assert_called_once_with("page-123")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_get_blocks_uses_retry_wrapper(self):
        """Block pagination should go through _call so retries apply."""
        adapter = NotionAdapter("test-token")
        adapter._call = AsyncMock(
            return_value={"results": [{"id": "block1"}], "has_more": False}
        )

        result = await adapter.get_blocks("page-123")

        adapter._call.assert_awaited_once()
        assert result == [{"id": "block1"}]

    @pytest.mark.asyncio
    async def test_walk_page_tree_traverses_database_rows_without_emitting_database(self):
        """Database containers should be traversed, but only row pages emitted."""
        adapter = NotionAdapter("test-token")
        adapter.get_page = AsyncMock(
            side_effect=[
                {
                    "id": "root",
                    "last_edited_time": "t1",
                    "properties": {
                        "Name": {
                            "type": "title",
                            "title": [{"plain_text": "Root"}],
                        }
                    },
                },
                {
                    "id": "row-1",
                    "last_edited_time": "t2",
                    "properties": {
                        "Name": {
                            "type": "title",
                            "title": [{"plain_text": "Row 1"}],
                        }
                    },
                },
            ]
        )
        adapter.get_page_title = AsyncMock(side_effect=["Root", "Row 1"])
        adapter.get_child_pages = AsyncMock(return_value=[])
        adapter.get_child_databases = AsyncMock(
            side_effect=[[{"id": "db-1", "child_database": {"title": "Tasks"}}], []]
        )
        adapter.get_database_rows = AsyncMock(
            return_value=[{"id": "row-1", "object": "page"}]
        )

        result = await adapter.walk_page_tree("root")

        assert [item["page_id"] for item in result] == ["root", "row-1"]
        assert result[1]["page_type"] == "database_row"
        adapter.get_database_rows.assert_awaited_once_with("db-1")
