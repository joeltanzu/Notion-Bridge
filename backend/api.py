"""
PyWebView API class — exposes Python methods to the React frontend via
window.pywebview.api. Methods are called synchronously from the JS/WebView
thread but dispatch work onto the shared asyncio event loop.

Event push: Python → JS via window.evaluate_js dispatching CustomEvents.
"""

import asyncio
import json
import os
import subprocess
import sys
import uuid
import webbrowser
from typing import Any, Optional

import logging

from backend.adapters.watcher import VaultWatcher
from backend.models.vault import Vault
from backend.sync.engine import SyncEngine
from backend.sync.state import StateDB
from backend.utils.keychain import store_token, get_token, store_secret

logger = logging.getLogger(__name__)


class NotionBridgeAPI:
    def __init__(
        self,
        db: StateDB,
        engine: SyncEngine,
        loop: asyncio.AbstractEventLoop,
        watcher: VaultWatcher,
    ):
        self._db = db
        self._engine = engine
        self._loop = loop
        self._watcher = watcher
        self._window = None  # set by main.py after window creation
        self._poll_tasks: dict[str, asyncio.Task] = {}

        # Point engine events back to JS
        engine._emit = self._emit

    def set_window(self, window) -> None:
        """Called by main.py once the PyWebView window is created."""
        self._window = window
        # Start watchers + poll loops for vaults that already exist in DB
        # Use a snapshot to avoid race conditions if vault list changes mid-iteration
        vaults = self._db.list_vaults()
        for vault in vaults:
            try:
                self._start_watcher(vault)
                self._start_poll_loop(vault)
            except Exception as e:
                logger.warning(
                    "Failed to start watcher/poll for vault %s: %s", vault.id, e
                )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _emit(self, event: str, payload: Any) -> None:
        """Push a CustomEvent to the JS frontend."""
        if self._window is None:
            return
        try:
            js = (
                f"window.dispatchEvent("
                f"new CustomEvent({json.dumps(event)}, "
                f"{{detail: {json.dumps(payload)}}})"
                f")"
            )
            self._window.evaluate_js(js)
        except Exception:
            pass  # window may have been closed

    def _run(self, coro) -> Any:
        """Submit a coroutine to the asyncio loop and block until done."""
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result()

    def _run_bg(self, coro) -> None:
        """Submit a coroutine to run in the background (fire-and-forget)."""
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _start_watcher(self, vault: Vault) -> None:
        async def _fs_callback(
            vault_id: str, event_type: str, src: str, dest: Optional[str]
        ) -> None:
            await self._engine.handle_fs_event(vault_id, event_type, src, dest)

        self._watcher.watch(vault.id, vault.local_root, self._loop, _fs_callback)

    def _stop_watcher(self, vault_id: str) -> None:
        self._watcher.unwatch(vault_id)

    def _start_poll_loop(self, vault: Vault) -> None:
        if vault.id in self._poll_tasks:
            return

        async def _poll_forever(v: Vault) -> None:
            while True:
                await asyncio.sleep(v.sync_interval)
                try:
                    current = self._db.get_vault(v.id)
                    if current:
                        await self._engine.poll_vault(current)
                except Exception as e:
                    logger.warning("Poll loop error for vault %s: %s", v.id, e)

        task = self._loop.create_task(_poll_forever(vault))
        self._poll_tasks[vault.id] = task

    def _stop_poll_loop(self, vault_id: str) -> None:
        task = self._poll_tasks.pop(vault_id, None)
        if task:
            task.cancel()

    # ── Token validation ──────────────────────────────────────────────────────

    def validate_token(self, api_token: str) -> dict:
        """Quick smoke-test of a Notion API token. Returns {ok, error}."""

        async def _test():
            from notion_client import AsyncClient
            from notion_client.errors import APIResponseError

            try:
                async with AsyncClient(auth=api_token) as client:
                    await client.users.me()
                return {"ok": True}
            except APIResponseError as e:
                if e.status == 401:
                    return {
                        "ok": False,
                        "error": "Invalid token — check your Integration Secret",
                    }
                if e.status == 403:
                    return {"ok": False, "error": "Token lacks required permissions"}
                return {"ok": False, "error": f"API error ({e.status})"}
            except Exception as e:
                err_msg = str(e).lower()
                if "connect" in err_msg or "network" in err_msg:
                    return {
                        "ok": False,
                        "error": "Network error — check your connection",
                    }
                if "timeout" in err_msg:
                    return {"ok": False, "error": "Request timed out — try again"}
                return {"ok": False, "error": str(e)}

        return self._run(_test())

    def search_notion_pages(self, api_token: str, query: str = "") -> dict:
        """Search for Notion pages accessible to this integration. Returns {results} or {error}."""

        async def _search():
            from notion_client import AsyncClient

            async with AsyncClient(auth=api_token) as client:
                try:
                    resp = await client.search(
                        query=query,
                        filter={"property": "object", "value": "page"},
                        page_size=20,
                    )
                    results = []
                    for page in resp.get("results", []):
                        title = ""
                        props = page.get("properties", {})
                        for prop in props.values():
                            if prop.get("type") == "title":
                                parts = prop.get("title", [])
                                title = "".join(p.get("plain_text", "") for p in parts)
                                break
                        if not title:
                            title = page.get("url", "Untitled").split("/")[-1]
                        results.append(
                            {
                                "id": page["id"],
                                "title": title or "Untitled",
                                "url": page.get("url", ""),
                            }
                        )
                    return {"results": results}
                except Exception as e:
                    return {"error": str(e)}

        return self._run(_search())

    def pick_folder(self) -> dict:
        """Open a native OS folder picker dialog. Returns {path} or {path: None}."""
        import webview

        # FileDialog.FOLDER is the current API; FOLDER_DIALOG is the deprecated alias
        try:
            dialog_type = webview.FileDialog.FOLDER
        except AttributeError:
            dialog_type = webview.FOLDER_DIALOG  # type: ignore[attr-defined]
        result = self._window.create_file_dialog(dialog_type)
        if result:
            return {"path": result[0]}
        return {"path": None}

    # ── Vault management ──────────────────────────────────────────────────────

    def get_vaults(self) -> list[dict]:
        vaults = self._db.list_vaults()
        return [v.model_dump() for v in vaults]

    def get_deleted_vaults(self) -> list[dict]:
        vaults = self._db.list_deleted_vaults()
        return [v.model_dump() for v in vaults]

    def restore_vault(self, vault_id: str, api_token: str) -> dict:
        """
        Restore a soft-deleted vault and re-link to its sync records.
        """
        vault = self._db.get_vault(vault_id)
        if not vault or vault.status != "deleted":
            return {"error": "Vault not found or not deleted"}

        store_token(vault_id, api_token)
        self._engine.prime_adapter(vault_id, api_token)
        restored = self._db.restore_vault(vault_id)
        if not restored:
            return {"error": "Failed to restore vault"}

        vault = self._db.get_vault(vault_id)
        self._start_watcher(vault)
        self._start_poll_loop(vault)
        return vault.model_dump()

    def add_vault(
        self,
        local_path: str,
        notion_root_id: str,
        api_token: str,
        name: str = "",
        allowed_page_ids: Optional[list] = None,
    ) -> dict:
        """
        Create a new vault, store the token in the OS keychain, and kick off
        initial_sync in the background. Returns the new vault dict.

        If a vault with the same local_path and notion_root_id exists (soft-deleted),
        offers to reconnect instead of creating a new one.
        """
        if not os.path.isdir(local_path):
            return {"error": f"Path does not exist or is not a directory: {local_path}"}

        existing_deleted = self._db.get_vault_by_path(local_path, notion_root_id)
        if existing_deleted and existing_deleted.status == "deleted":
            return {
                "reconnect": True,
                "vault_id": existing_deleted.id,
                "vault_name": existing_deleted.name,
                "message": f"Found previously synced vault '{existing_deleted.name}'. Reconnect to restore sync context?",
            }

        import secrets

        secret_key = secrets.token_hex(16)
        vault_id = str(uuid.uuid4())
        vault = Vault(
            id=vault_id,
            name=name or os.path.basename(local_path.rstrip("/\\")),
            local_root=local_path,
            notion_root_id=notion_root_id,
            allowed_page_ids=allowed_page_ids or [],
            secret_key=secret_key,
        )

        self._engine.prime_adapter(vault_id, api_token)

        try:
            self._db.upsert_vault(vault)
        except Exception as e:
            self._engine.invalidate_adapter(vault_id)
            return {"error": f"Failed to create vault: {e}"}

        store_token(vault_id, api_token)

        self._start_watcher(vault)
        self._start_poll_loop(vault)
        return vault.model_dump()

    def remove_vault(self, vault_id: str) -> None:
        self._stop_watcher(vault_id)
        self._stop_poll_loop(vault_id)
        self._engine.invalidate_adapter(vault_id)
        self._db.delete_vault(vault_id)

    def update_vault(
        self,
        vault_id: str,
        name: Optional[str] = None,
        sync_interval: Optional[int] = None,
        allowed_page_ids: Optional[list] = None,
    ) -> Optional[dict]:
        vault = self._db.get_vault(vault_id)
        if not vault:
            return None
        if name is not None:
            vault.name = name
        if sync_interval is not None:
            vault.sync_interval = sync_interval
        if allowed_page_ids is not None:
            vault.allowed_page_ids = allowed_page_ids
        self._db.upsert_vault(vault)
        # Restart poll loop with new interval
        self._stop_poll_loop(vault_id)
        self._start_poll_loop(vault)
        return vault.model_dump()

    # ── Sync control ──────────────────────────────────────────────────────────

    def trigger_sync(self, vault_id: str) -> None:
        """Check for changes and notify UI — does not apply anything."""
        vault = self._db.get_vault(vault_id)
        if vault:
            self._run_bg(self._engine.detect_notion_changes(vault))

    def preview_sync(self, vault_id: str) -> list:
        """Generate a sync plan showing what would happen. Does not execute."""
        vault = self._db.get_vault(vault_id)
        if not vault:
            return []
        return self._run(self._engine.generate_sync_plan(vault))

    def apply_sync(self, vault_id: str, items: list) -> None:
        """Execute the user-selected items from a preview_sync plan."""
        vault = self._db.get_vault(vault_id)
        if not vault:
            return
        self._run_bg(self._engine.apply_sync_plan(vault, items))

    def get_sync_status(self, vault_id: str) -> dict:
        vault = self._db.get_vault(vault_id)
        if not vault:
            return {"error": "Vault not found"}
        records = self._db.list_records(vault_id)
        total = len(records)
        synced = sum(1 for r in records if r.status == "synced")
        conflicts = sum(1 for r in records if r.status == "conflict")
        errors = sum(1 for r in records if r.status == "error")
        return {
            "vaultId": vault_id,
            "status": vault.status,
            "lastPolledAt": vault.last_polled_at,
            "total": total,
            "synced": synced,
            "conflicts": conflicts,
            "errors": errors,
        }

    # ── File tree ─────────────────────────────────────────────────────────────

    def get_file_tree(self, vault_id: str) -> list[dict]:
        """
        Returns all sync records for the vault, enriched with relative path info,
        so the frontend can render a file tree.
        """
        vault = self._db.get_vault(vault_id)
        if not vault:
            return []
        records = self._db.list_records(vault_id)
        tree = []
        for r in records:
            rel = os.path.relpath(r.local_path, vault.local_root)
            tree.append(
                {
                    "id": r.id,
                    "localPath": r.local_path,
                    "relativePath": rel,
                    "notionPageId": r.notion_page_id,
                    "status": r.status,
                    "lastSyncedAt": r.last_synced_at,
                    "errorMessage": r.error_message,
                }
            )
        return tree

    # ── Conflicts ─────────────────────────────────────────────────────────────

    def get_conflicts(self, vault_id: str) -> list[dict]:
        conflicts = self._db.list_conflicts(vault_id)
        return [c.model_dump() for c in conflicts]

    def resolve_conflict(
        self, conflict_id: str, vault_id: str, resolution: str
    ) -> None:
        """resolution: 'local' | 'notion'"""
        self._run(self._engine.resolve_conflict(vault_id, conflict_id, resolution))

    def resolve_deletion(self, vault_id: str, record_id: str, action: str) -> None:
        """action: 'delete_notion' | 'restore_local' | 'keep_orphaned'"""
        self._run(self._engine.resolve_deletion(vault_id, record_id, action))

    def delete_record(self, vault_id: str, record_id: str) -> None:
        """Delete a sync record without affecting Notion."""
        self._engine.delete_record(vault_id, record_id)

    def get_deleted_records(self, vault_id: str) -> list[dict]:
        """Get all records with deleted status."""
        records = self._db.list_records(vault_id)
        deleted = [
            r for r in records if r.status in ("deleted_in_notion", "deleted_local")
        ]
        return [r.model_dump() for r in deleted]

    def check_vault_health(self, vault_id: str) -> dict:
        """Check vault for sync issues: orphaned records, deleted pages, etc."""
        records = self._db.list_records(vault_id)
        issues = []
        for r in records:
            if r.status == "deleted_in_notion":
                issues.append(
                    {
                        "type": "notion_deleted",
                        "record_id": r.id,
                        "local_path": r.local_path,
                        "notion_page_id": r.notion_page_id,
                        "message": "Notion page no longer exists",
                    }
                )
            elif r.status == "deleted_local":
                issues.append(
                    {
                        "type": "local_deleted",
                        "record_id": r.id,
                        "local_path": r.local_path,
                        "notion_page_id": r.notion_page_id,
                        "message": "Local file was deleted",
                    }
                )
            elif r.status == "pushing":
                issues.append(
                    {
                        "type": "incomplete_push",
                        "record_id": r.id,
                        "local_path": r.local_path,
                        "notion_page_id": r.notion_page_id,
                        "message": "Push operation may have failed mid-way",
                    }
                )
            elif r.status == "conflict":
                issues.append(
                    {
                        "type": "conflict",
                        "record_id": r.id,
                        "local_path": r.local_path,
                        "notion_page_id": r.notion_page_id,
                        "message": "Unresolved conflict",
                    }
                )
            elif not file_exists(r.local_path) and r.status not in (
                "deleted_local",
                "deleted_in_notion",
            ):
                issues.append(
                    {
                        "type": "missing_local",
                        "record_id": r.id,
                        "local_path": r.local_path,
                        "notion_page_id": r.notion_page_id,
                        "message": "Local file is missing",
                    }
                )
        return {"vault_id": vault_id, "issues": issues, "count": len(issues)}

    # ── OS integration ────────────────────────────────────────────────────────

    def open_in_notion(self, page_id: str) -> None:
        """Open a Notion page in the default browser."""
        # Notion desktop deeplink; falls back gracefully to web if app not installed
        url = f"notion://www.notion.so/{page_id.replace('-', '')}"
        webbrowser.open(url)

    def reveal_in_finder(self, path: str) -> None:
        """Reveal a file/folder in Finder (macOS) or Explorer (Windows)."""
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
