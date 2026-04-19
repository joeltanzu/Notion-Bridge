"""
Watchdog-based filesystem event handler with 2-second debounce.
"""

import asyncio
import os
import time
from typing import Callable, Optional

from watchdog.events import (
    FileCreatedEvent,
    FileModifiedEvent,
    FileDeletedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer


class VaultEventHandler(FileSystemEventHandler):
    def __init__(
        self, vault_id: str, loop: asyncio.AbstractEventLoop, callback: Callable
    ):
        super().__init__()
        self.vault_id = vault_id
        self.loop = loop
        self.callback = callback
        self._debounce_tasks: dict[str, asyncio.TimerHandle] = {}
        self._last_processed: dict[str, float] = {}
        self._debounce_delay: float = 2.0  # seconds to wait before processing
        self._min_interval: float = 0.5  # minimum interval between same-file events

    def _schedule(self, event_type: str, src: str, dest: Optional[str] = None) -> None:
        if not src.endswith(".md"):
            return
        if ".notion-bridge" in src:
            return

        key = src
        now = time.monotonic()

        # Cancel any pending callback for this file
        existing = self._debounce_tasks.get(key)
        if existing:
            existing.cancel()

        # Rate limit: if we processed this file recently, start the timer from the last event
        last_processed = self._last_processed.get(key, 0)
        time_since_last = now - last_processed

        # If events are coming in too rapidly for the same file, debounce longer
        if time_since_last < self._min_interval:
            delay = self._debounce_delay * 2  # Double delay for rapid bursts
        else:
            delay = self._debounce_delay

        handle = self.loop.call_later(
            delay,
            lambda: self._execute_callback(key, event_type, src, dest),
        )
        self._debounce_tasks[key] = handle

    def _execute_callback(
        self, key: str, event_type: str, src: str, dest: Optional[str]
    ) -> None:
        self._last_processed[key] = time.monotonic()
        self._debounce_tasks.pop(key, None)
        asyncio.run_coroutine_threadsafe(
            self.callback(self.vault_id, event_type, src, dest),
            self.loop,
        )

    def on_created(self, event):
        if not event.is_directory:
            self._schedule("created", event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._schedule("modified", event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            self._schedule("deleted", event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._schedule("moved", event.src_path, event.dest_path)


class VaultWatcher:
    def __init__(self):
        self._observer = Observer()
        self._handlers: dict[str, VaultEventHandler] = {}

    def watch(
        self,
        vault_id: str,
        path: str,
        loop: asyncio.AbstractEventLoop,
        callback: Callable,
    ) -> None:
        handler = VaultEventHandler(vault_id, loop, callback)
        self._handlers[vault_id] = handler
        self._observer.schedule(handler, path, recursive=True)
        if not self._observer.is_alive():
            self._observer.start()

    def unwatch(self, vault_id: str) -> None:
        self._handlers.pop(vault_id, None)

    def stop(self) -> None:
        if self._observer.is_alive():
            self._observer.stop()
            self._observer.join()
