"""Tests for filesystem watcher - event handling and debounce logic."""

import pytest
import asyncio
import os
import tempfile
import time
from unittest.mock import Mock, AsyncMock
from backend.adapters.watcher import VaultEventHandler, VaultWatcher


class TestVaultEventHandler:
    """Test VaultEventHandler debounce and event scheduling."""

    @pytest.fixture
    def loop(self):
        """Create a new event loop for each test."""
        return asyncio.new_event_loop()

    @pytest.fixture
    def mock_callback(self):
        """Create a mock async callback."""
        callback = AsyncMock()
        return callback

    def test_ignores_non_markdown_files(self, loop, mock_callback):
        """Non-.md files should be ignored."""
        handler = VaultEventHandler("vault-1", loop, mock_callback)

        # Create a fake event with non-markdown path
        from watchdog.events import FileModifiedEvent

        event = FileModifiedEvent("/tmp/test.txt")
        handler.on_modified(event)

        # Callback should not have been called
        mock_callback.assert_not_called()

    def test_ignores_notion_bridge_directory(self, loop, mock_callback):
        """Changes in .notion-bridge should be ignored."""
        handler = VaultEventHandler("vault-1", loop, mock_callback)

        from watchdog.events import FileModifiedEvent

        event = FileModifiedEvent("/tmp/vault/.notion-bridge/sync.db")
        handler.on_modified(event)

        mock_callback.assert_not_called()

    def test_schedules_markdown_file_event(self, loop, mock_callback):
        """Markdown file events should be scheduled."""
        handler = VaultEventHandler("vault-1", loop, mock_callback)

        from watchdog.events import FileModifiedEvent

        event = FileModifiedEvent("/tmp/vault/notes.md")
        handler.on_modified(event)

        # Should have scheduled a callback (doesn't call immediately due to debounce)
        assert len(handler._debounce_tasks) == 1

    def test_multiple_events_same_file_debounced(self, loop, mock_callback):
        """Multiple events for the same file should be debounced."""
        handler = VaultEventHandler("vault-1", loop, mock_callback)

        from watchdog.events import FileModifiedEvent

        event = FileModifiedEvent("/tmp/vault/notes.md")

        # Fire multiple events rapidly
        handler.on_modified(event)
        handler.on_modified(event)
        handler.on_modified(event)

        # Should still only have one scheduled task
        assert len(handler._debounce_tasks) == 1

    def test_different_files_independent_debounce(self, loop, mock_callback):
        """Different files should be handled independently."""
        handler = VaultEventHandler("vault-1", loop, mock_callback)

        from watchdog.events import FileModifiedEvent

        event1 = FileModifiedEvent("/tmp/vault/notes1.md")
        event2 = FileModifiedEvent("/tmp/vault/notes2.md")

        handler.on_modified(event1)
        handler.on_modified(event2)

        # Both files should have scheduled tasks
        assert len(handler._debounce_tasks) == 2


class TestVaultWatcher:
    """Test VaultWatcher lifecycle."""

    def test_watch_creates_handler(self):
        """Watch should create and store a handler."""
        watcher = VaultWatcher()
        loop = asyncio.new_event_loop()

        callback = AsyncMock()
        watcher.watch("vault-1", "/tmp/test", loop, callback)

        assert "vault-1" in watcher._handlers
        assert watcher._observer.is_alive()

        watcher.stop()
        loop.close()

    def test_unwatch_removes_handler(self):
        """Unwatch should remove the handler."""
        watcher = VaultWatcher()
        loop = asyncio.new_event_loop()

        callback = AsyncMock()
        watcher.watch("vault-1", "/tmp/test", loop, callback)
        watcher.unwatch("vault-1")

        assert "vault-1" not in watcher._handlers

        watcher.stop()
        loop.close()

    def test_stop_cleans_up_observer(self):
        """Stop should shut down the observer."""
        watcher = VaultWatcher()
        loop = asyncio.new_event_loop()

        callback = AsyncMock()
        watcher.watch("vault-1", "/tmp/test", loop, callback)
        watcher.stop()

        assert not watcher._observer.is_alive()
        loop.close()


class TestWatcherEventTypes:
    """Test that all event types are handled."""

    @pytest.fixture
    def loop(self):
        return asyncio.new_event_loop()

    @pytest.fixture
    def callback(self):
        return AsyncMock()

    def test_on_created(self, loop, callback):
        """Created events should be handled."""
        handler = VaultEventHandler("vault-1", loop, callback)

        from watchdog.events import FileCreatedEvent

        event = FileCreatedEvent("/tmp/vault/new.md")
        handler.on_created(event)

        assert len(handler._debounce_tasks) == 1

    def test_on_deleted(self, loop, callback):
        """Deleted events should be handled."""
        handler = VaultEventHandler("vault-1", loop, callback)

        from watchdog.events import FileDeletedEvent

        event = FileDeletedEvent("/tmp/vault/deleted.md")
        handler.on_deleted(event)

        assert len(handler._debounce_tasks) == 1

    def test_on_moved(self, loop, callback):
        """Moved events should be handled with dest path."""
        handler = VaultEventHandler("vault-1", loop, callback)

        from watchdog.events import FileMovedEvent

        event = FileMovedEvent("/tmp/vault/old.md", "/tmp/vault/new.md")
        handler.on_moved(event)

        # Should have scheduled with both src and dest
        assert len(handler._debounce_tasks) == 1

    def test_directories_ignored(self, loop, callback):
        """Directory events should be ignored."""
        handler = VaultEventHandler("vault-1", loop, callback)

        from watchdog.events import FileCreatedEvent

        event = FileCreatedEvent("/tmp/vault/newdir")
        event.is_directory = True
        handler.on_created(event)

        # Directory events should not trigger any scheduled tasks
        assert len(handler._debounce_tasks) == 0
