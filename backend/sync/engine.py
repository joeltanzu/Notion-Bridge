"""
Core sync engine: initial vault setup, local→Notion push, Notion→local pull,
conflict handling, and file-event processing.
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

logger = logging.getLogger(__name__)

from backend.adapters.fs_adapter import (
    read_file,
    write_file,
    file_exists,
    get_mtime,
    list_markdown_files,
    FileTooLargeError,
)
from backend.adapters.notion_adapter import NotionAdapter
from notion_client.errors import APIResponseError
from backend.converters.notion_to_md import blocks_to_markdown, _safe_filename
from backend.converters.md_to_notion import markdown_to_blocks
from backend.models.sync_record import SyncRecord
from backend.models.vault import Vault
from backend.sync.conflict import detect_changes
from backend.sync.state import StateDB
from backend.utils.hashing import hash_text
from backend.utils.keychain import get_token


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SyncEngine:
    def __init__(self, db: StateDB, event_callback: Optional[Callable] = None):
        self.db = db
        self._emit = event_callback or (lambda *a, **kw: None)
        self._adapters: dict[str, NotionAdapter] = {}
        # Paths we wrote ourselves — suppress the resulting watchdog events
        self._own_writes: set[str] = set()

    def _adapter(self, vault_id: str) -> NotionAdapter:
        if vault_id not in self._adapters:
            token = get_token(vault_id)
            if not token:
                raise ValueError(f"No API token stored for vault {vault_id}")
            self._adapters[vault_id] = NotionAdapter(token)
        return self._adapters[vault_id]

    def invalidate_adapter(self, vault_id: str) -> None:
        self._adapters.pop(vault_id, None)

    def prime_adapter(self, vault_id: str, token: str) -> None:
        """Pre-warm the adapter cache with a known token (avoids a second keychain read)."""
        self._adapters[vault_id] = NotionAdapter(token)

    # ── Initial sync ─────────────────────────────────────────────────────

    # ── Sync plan (preview without executing) ────────────────────────────────

    async def generate_sync_plan(self, vault: Vault) -> list:
        """
        Compute what would happen on the next sync without executing anything.
        Returns a flat list of SyncPlanItem dicts.
        """
        adapter = self._adapter(vault.id)

        # 1. Walk Notion tree
        if vault.allowed_page_ids:
            notion_pages = []
            for root_id in vault.allowed_page_ids:
                notion_pages.extend(await adapter.walk_page_tree(root_id))
        else:
            notion_pages = await adapter.walk_page_tree(vault.notion_root_id)

        # 2. List local files
        local_files = list_markdown_files(vault.local_root)

        # 3. Index DB records
        records = self.db.list_records(vault.id)
        records_by_notion_id = {r.notion_page_id: r for r in records}
        records_by_local_path = {r.local_path: r for r in records}

        items = []
        covered_local_paths = set()

        # 4. Process Notion pages
        for page in notion_pages:
            page_id = page["page_id"]
            # Skip the root page - it's the "folder", not actual content
            if page_id == vault.notion_root_id:
                continue
            title = page.get("title") or "Untitled"
            last_edited = page.get("last_edited_time", "")
            record = records_by_notion_id.get(page_id)

            if record is None:
                # Not yet local — suggest a path
                safe = _safe_filename(title)
                suggested = _deduplicate_path(
                    os.path.join(vault.local_root, f"{safe}.md"),
                    vault_id=vault.id,
                    notion_page_id=page_id,
                    db=self.db,
                )
                items.append(
                    {
                        "id": str(uuid.uuid4()),
                        "action": "new_notion",
                        "notionPageId": page_id,
                        "notionTitle": title,
                        "lastNotionEdited": last_edited,
                        "suggestedLocalPath": suggested,
                    }
                )
            else:
                covered_local_paths.add(record.local_path)
                notion_changed = (
                    last_edited and last_edited != record.last_notion_edited
                )

                if notion_changed:
                    # Check if local also changed → conflict
                    local_dirty = False
                    if file_exists(record.local_path):
                        try:
                            body = read_file(record.local_path)
                            local_dirty = hash_text(body) != record.local_hash
                        except FileTooLargeError:
                            logger.warning(
                                "File too large to read for sync plan: %s",
                                record.local_path,
                            )
                        except Exception:
                            pass

                    action = "conflict" if local_dirty else "pull_notion"
                    items.append(
                        {
                            "id": str(uuid.uuid4()),
                            "action": action,
                            "notionPageId": page_id,
                            "notionTitle": title,
                            "localPath": record.local_path,
                            "localTitle": os.path.basename(record.local_path),
                            "lastNotionEdited": last_edited,
                            "lastLocalModified": datetime.fromtimestamp(
                                record.last_local_mtime, tz=timezone.utc
                            ).isoformat()
                            if record.last_local_mtime and record.last_local_mtime > 0
                            else None,
                        }
                    )
                else:
                    # Notion hasn't changed — check if local has
                    local_dirty = False
                    if file_exists(record.local_path):
                        try:
                            body = read_file(record.local_path)
                            local_dirty = hash_text(body) != record.local_hash
                        except FileTooLargeError:
                            logger.warning(
                                "File too large to read for sync plan: %s",
                                record.local_path,
                            )
                        except Exception:
                            pass

                    try:
                        mtime = get_mtime(record.local_path)
                        mtime_iso = datetime.fromtimestamp(
                            mtime, tz=timezone.utc
                        ).isoformat()
                    except Exception:
                        mtime_iso = None

                    if local_dirty:
                        items.append(
                            {
                                "id": str(uuid.uuid4()),
                                "action": "push_local",
                                "localPath": record.local_path,
                                "localTitle": os.path.basename(record.local_path),
                                "notionPageId": page_id,
                                "lastLocalModified": mtime_iso,
                            }
                        )
                    else:
                        items.append(
                            {
                                "id": str(uuid.uuid4()),
                                "action": "in_sync",
                                "notionPageId": page_id,
                                "notionTitle": title,
                                "localPath": record.local_path,
                                "localTitle": os.path.basename(record.local_path),
                            }
                        )

        # 5. Process local files not already covered by a Notion page
        for local_path in local_files:
            if local_path in covered_local_paths:
                continue
            record = records_by_local_path.get(local_path)
            local_title = os.path.basename(local_path)

            try:
                mtime = get_mtime(local_path)
                mtime_iso = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
            except Exception:
                mtime_iso = None

            if record is None:
                items.append(
                    {
                        "id": str(uuid.uuid4()),
                        "action": "new_local",
                        "localPath": local_path,
                        "localTitle": local_title,
                        "lastLocalModified": mtime_iso,
                    }
                )
            else:
                try:
                    body = read_file(local_path)
                    local_changed = hash_text(body) != record.local_hash
                except Exception as e:
                    logger.warning("Could not read %s for sync plan: %s", local_path, e)
                    local_changed = False

                if local_changed:
                    items.append(
                        {
                            "id": str(uuid.uuid4()),
                            "action": "push_local",
                            "localPath": local_path,
                            "localTitle": local_title,
                            "notionPageId": record.notion_page_id,
                            "lastLocalModified": mtime_iso,
                        }
                    )

        return items

    async def apply_sync_plan(self, vault: Vault, items: list) -> None:
        """Execute only the items the user selected from generate_sync_plan."""
        self._emit("vault:status", {"vaultId": vault.id, "status": "syncing"})
        adapter = self._adapter(vault.id)
        total = len(items)

        for idx, item in enumerate(items):
            action = item.get("action")
            self._emit(
                "sync:progress",
                {
                    "vaultId": vault.id,
                    "current": idx + 1,
                    "total": total,
                    "currentFile": item.get("localTitle")
                    or item.get("notionTitle")
                    or "",
                },
            )

            try:
                if action in ("push_local", "new_local"):
                    local_path = item.get("localPath")
                    if local_path:
                        await self.push_file(vault, local_path)

                elif action in ("pull_notion", "new_notion"):
                    page_id = item.get("notionPageId")
                    if not page_id:
                        continue
                    page = await adapter.get_page(page_id)
                    title = await adapter.get_page_title(page)
                    local_path = item.get("localPath") or item.get("suggestedLocalPath")
                    if not local_path:
                        safe = _safe_filename(title)
                        local_path = _deduplicate_path(
                            os.path.join(vault.local_root, f"{safe}.md"),
                            vault_id=vault.id,
                            notion_page_id=page_id,
                            db=self.db,
                        )
                    page_info = {
                        "page_id": page_id,
                        "parent_id": None,
                        "title": title,
                        "page_type": "page",
                        "last_edited_time": page.get("last_edited_time", ""),
                        "page": page,
                    }
                    await self._pull_page_to_file(vault, adapter, page_info, local_path)

                # "conflict" and "in_sync" are skipped — conflicts go through ConflictPanel
            except Exception as e:
                self._emit(
                    "sync:error",
                    {
                        "vaultId": vault.id,
                        "path": item.get("localPath") or item.get("notionPageId") or "",
                        "message": str(e),
                    },
                )

        self.db.set_vault_status(vault.id, "synced", last_polled_at=_now())
        self._emit("vault:status", {"vaultId": vault.id, "status": "synced"})

    # ── Notion change detection (non-destructive poll) ────────────────────────

    async def detect_notion_changes(self, vault: Vault) -> None:
        """
        Lightweight check: compare last_edited_time for known records.
        Also walks the Notion tree to detect new pages not yet in the database.
        Also detects deleted records that need resolution.
        Emits vault:pending_changes if anything is out of date or new pages found.
        Does NOT pull.
        """
        adapter = self._adapter(vault.id)
        records = self.db.list_records(vault.id)

        # Check for records that need resolution (deleted states)
        for record in records:
            if record.status in ("deleted_in_notion", "deleted_local"):
                self._emit("vault:pending_changes", {"vaultId": vault.id})
                return

        # Check for records stuck in pushing state (mid-push failure)
        pushing_records = [r for r in records if r.status == "pushing"]
        if pushing_records:
            self._emit("vault:pending_changes", {"vaultId": vault.id})
            return

        # Check for changes to known records
        for record in records:
            if record.status == "conflict":
                continue
            try:
                last_edited = await adapter.get_last_edited_time(record.notion_page_id)
                if last_edited and last_edited != record.last_notion_edited:
                    self._emit("vault:pending_changes", {"vaultId": vault.id})
                    return
            except Exception:
                continue

        # Check for new Notion pages not yet tracked
        try:
            if vault.allowed_page_ids:
                notion_pages = []
                for root_id in vault.allowed_page_ids:
                    notion_pages.extend(await adapter.walk_page_tree(root_id))
            else:
                notion_pages = await adapter.walk_page_tree(vault.notion_root_id)

            existing_notion_ids = {r.notion_page_id for r in records}
            for page in notion_pages:
                page_id = page["page_id"]
                # Skip the root page - it's the "folder", not actual content
                if page_id == vault.notion_root_id:
                    continue
                if page_id not in existing_notion_ids:
                    self._emit("vault:new_pages", {"vaultId": vault.id, "count": 1})
                    self._emit("vault:pending_changes", {"vaultId": vault.id})
                    return
        except Exception:
            pass

    # ── Pull Notion → local ───────────────────────────────────────────────

    async def pull_page(self, vault: Vault, notion_page_id: str) -> None:
        adapter = self._adapter(vault.id)
        page = await adapter.get_page(notion_page_id)
        title = await adapter.get_page_title(page)
        safe = _safe_filename(title)
        record = self.db.get_record_by_notion_id(vault.id, notion_page_id)

        if record:
            local_path = record.local_path
        else:
            local_path = os.path.join(vault.local_root, f"{safe}.md")
            local_path = _deduplicate_path(
                local_path,
                vault_id=vault.id,
                notion_page_id=notion_page_id,
                db=self.db,
            )

        page_info = {
            "page_id": notion_page_id,
            "parent_id": None,
            "title": title,
            "page_type": "page",
            "last_edited_time": page.get("last_edited_time", ""),
            "page": page,
        }
        await self._pull_page_to_file(vault, adapter, page_info, local_path)

    async def _pull_page_to_file(
        self, vault: Vault, adapter: NotionAdapter, page_info: dict, local_path: str
    ) -> None:
        page_id = page_info["page_id"]
        title = page_info.get("title", "Untitled")
        last_edited = page_info.get("last_edited_time", "")

        blocks = await adapter.get_block_tree(page_id)
        md_body = blocks_to_markdown(blocks)
        notion_hash = hash_text(md_body)

        record = self.db.get_record_by_notion_id(vault.id, page_id)

        # Conflict check: if file exists and local is dirty
        if file_exists(local_path) and record:
            try:
                existing_body = read_file(local_path)
            except FileTooLargeError:
                logger.warning(
                    "File too large to read for conflict check: %s", local_path
                )
                existing_body = ""
            except Exception:
                existing_body = ""
            local_hash = hash_text(existing_body)
            changes = detect_changes(
                existing_body, md_body, record.local_hash, record.notion_hash
            )
            if changes.is_conflict:
                conflict = self.db.create_conflict(
                    record.id,
                    local_path,
                    page_id,
                    local_snapshot=existing_body,
                    notion_snapshot=md_body,
                )
                self.db.update_record_status(record.id, "conflict")
                self._emit(
                    "sync:conflict",
                    {
                        "conflictId": conflict.id,
                        "localPath": local_path,
                        "notionPageId": page_id,
                        "vaultId": vault.id,
                    },
                )
                return
            elif changes.no_change:
                return

        self._own_writes.add(local_path)
        write_file(local_path, md_body)

        local_mtime = get_mtime(local_path)
        local_hash = hash_text(md_body)

        if record is None:
            record = SyncRecord(
                id=str(uuid.uuid4()),
                local_path=local_path,
                notion_page_id=page_id,
                notion_parent_id=page_info.get("parent_id"),
                page_type=page_info.get("page_type", "page"),
                local_hash=local_hash,
                notion_hash=notion_hash,
                last_synced_at=_now(),
                last_local_mtime=local_mtime,
                last_notion_edited=last_edited,
                status="synced",
                created_at=_now(),
            )
            self.db.upsert_record(vault.id, record)
        else:
            self.db.update_record_hashes(
                record.id, local_hash, notion_hash, _now(), local_mtime, last_edited
            )

        self._emit(
            "sync:file_changed",
            {
                "vaultId": vault.id,
                "path": local_path,
                "direction": "to_local",
                "status": "synced",
            },
        )

    # ── Push local → Notion ───────────────────────────────────────────────

    async def push_file(
        self, vault: Vault, local_path: str, force: bool = False
    ) -> None:
        if not file_exists(local_path):
            return

        try:
            body = read_file(local_path)
        except FileTooLargeError as e:
            self._emit(
                "sync:error",
                {"vaultId": vault.id, "path": local_path, "message": str(e)},
            )
            return
        except Exception as e:
            self._emit(
                "sync:error",
                {"vaultId": vault.id, "path": local_path, "message": str(e)},
            )
            return

        adapter = self._adapter(vault.id)
        record = self.db.get_record_by_path(vault.id, local_path)
        notion_id = record.notion_page_id if record else None

        # New file — create a new page in Notion
        if not notion_id:
            title = os.path.splitext(os.path.basename(local_path))[0]
            new_page = await adapter.create_page(vault.notion_root_id, title)
            notion_id = new_page["id"]
            # Save record immediately so the page is tracked even if later steps fail.
            # Without this, a crash between create_page and the end of push_file leaves
            # an orphaned Notion page with no DB entry — on the next sync plan it gets
            # classified as new_notion and _deduplicate_path suggests "file-1.md".
            record = SyncRecord(
                id=str(uuid.uuid4()),
                local_path=local_path,
                notion_page_id=notion_id,
                local_hash=None,
                notion_hash=None,
                last_synced_at=_now(),
                last_local_mtime=get_mtime(local_path),
                last_notion_edited=None,
                status="synced",
                created_at=_now(),
            )
            self.db.upsert_record(vault.id, record)

        # Mark as pushing before making API changes - enables crash recovery
        self.db.update_record_status(record.id, "pushing")

        try:
            # Conflict check: was Notion edited since we last synced?
            # When force=True (e.g., explicit conflict resolution), skip the conflict detection but still fetch for hash.
            current_notion_body = ""
            if record and record.notion_hash:
                try:
                    blocks = await adapter.get_block_tree(notion_id)
                    current_notion_body = blocks_to_markdown(blocks)
                except Exception:
                    current_notion_body = ""

            # Skip conflict detection when force=True, but still compute for hash update
            if not force and current_notion_body and record and record.notion_hash:
                changes = detect_changes(
                    body, current_notion_body, record.local_hash, record.notion_hash
                )
                if changes.is_conflict:
                    conflict = self.db.create_conflict(
                        record.id,
                        local_path,
                        notion_id,
                        local_snapshot=body,
                        notion_snapshot=current_notion_body,
                    )
                    self.db.update_record_status(record.id, "conflict")
                    self._emit(
                        "sync:conflict",
                        {
                            "conflictId": conflict.id,
                            "localPath": local_path,
                            "notionPageId": notion_id,
                            "vaultId": vault.id,
                        },
                    )
                    return

            new_blocks = markdown_to_blocks(body)
            new_blocks = await self._refresh_image_urls(vault.id, adapter, new_blocks)
            await adapter.replace_blocks(notion_id, new_blocks)

            # Also update title from filename
            title = os.path.splitext(os.path.basename(local_path))[0]
            await adapter.update_page_title(notion_id, title)

            last_edited = await adapter.get_last_edited_time(notion_id)
            # Round-trip normalise: convert markdown → blocks → markdown to get the canonical
            # form that blocks_to_markdown produces on pull, so notion_hash stays consistent.
            # When force=True, we skip the conflict check but still need to update hashes.
            canonical_body = blocks_to_markdown(markdown_to_blocks(body))
            notion_hash = hash_text(canonical_body)
            local_hash = hash_text(body)
            local_mtime = get_mtime(local_path)

            # record is always set here: either it existed before push, or we created and
            # saved it immediately after create_page above.
            self.db.update_record_hashes(
                record.id, local_hash, notion_hash, _now(), local_mtime, last_edited
            )

            self._emit(
                "sync:file_changed",
                {
                    "vaultId": vault.id,
                    "path": local_path,
                    "direction": "to_notion",
                    "status": "synced",
                },
            )
        except Exception as e:
            # Keep as "pushing" so poll_vault can retry
            self.db.update_record_status(record.id, "pushing", str(e))
            self._emit(
                "sync:error",
                {
                    "vaultId": vault.id,
                    "path": local_path,
                    "message": str(e),
                },
            )
            raise

    # ── File event handler (from watchdog) ────────────────────────────────

    async def handle_fs_event(
        self, vault_id: str, event_type: str, src: str, dest: Optional[str]
    ) -> None:
        vault = self.db.get_vault(vault_id)
        if not vault:
            logger.warning("handle_fs_event: vault %s not found", vault_id)
            return

        if event_type in ("created", "modified"):
            # Suppress events triggered by our own writes (e.g. after a pull)
            if src in self._own_writes:
                self._own_writes.discard(src)
                return
            # Don't auto-push — notify the UI that changes are pending review
            self._emit("vault:pending_changes", {"vaultId": vault_id})

        elif event_type == "deleted":
            record = self.db.get_record_by_path(vault_id, src)
            if record:
                self.db.update_record_status(
                    record.id, "deleted_local", "Local file deleted"
                )
                self._emit(
                    "sync:file_changed",
                    {
                        "vaultId": vault_id,
                        "path": src,
                        "direction": "local_deleted",
                        "status": "deleted_local",
                    },
                )

        elif event_type == "moved" and dest:
            record = self.db.get_record_by_path(vault_id, src)
            if record:
                # Check if dest already exists and would cause a conflict
                if file_exists(dest) and dest != record.local_path:
                    dest = _deduplicate_path(
                        dest,
                        vault_id=vault_id,
                        db=self.db,
                    )
                # Update Notion page title from new filename
                adapter = self._adapter(vault_id)
                new_title = os.path.splitext(os.path.basename(dest))[0]
                try:
                    await adapter.update_page_title(record.notion_page_id, new_title)
                except Exception:
                    pass
                # Update local_path in DB
                record.local_path = dest
                self.db.upsert_record(vault_id, record)
                self._emit(
                    "sync:file_changed",
                    {
                        "vaultId": vault_id,
                        "path": dest,
                        "direction": "renamed",
                        "status": "synced",
                    },
                )

    # ── Poll (Notion → local) ─────────────────────────────────────────────

    async def poll_vault(self, vault: Vault) -> None:
        """Check all known pages for Notion-side changes."""
        self.db.set_vault_status(vault.id, "syncing")
        self._emit("vault:status", {"vaultId": vault.id, "status": "syncing"})
        adapter = self._adapter(vault.id)
        records = self.db.list_records(vault.id)

        try:
            for record in records:
                if record.status == "conflict":
                    continue
                try:
                    last_edited = await adapter.get_last_edited_time(
                        record.notion_page_id
                    )
                    if last_edited != record.last_notion_edited:
                        page = await adapter.get_page(record.notion_page_id)
                        title = await adapter.get_page_title(page)
                        page_info = {
                            "page_id": record.notion_page_id,
                            "parent_id": record.notion_parent_id,
                            "title": title,
                            "page_type": record.page_type,
                            "last_edited_time": last_edited,
                            "page": page,
                        }
                        await self._pull_page_to_file(
                            vault, adapter, page_info, record.local_path
                        )
                except APIResponseError as e:
                    status = "deleted_in_notion" if e.status == 404 else "error"
                    self.db.update_record_status(record.id, status, str(e))
                except Exception as e:
                    self.db.update_record_status(record.id, "error", str(e))

            # Also check for records stuck in "pushing" state and retry them
            pushing_records = [r for r in records if r.status == "pushing"]
            for record in pushing_records:
                if file_exists(record.local_path):
                    await self.push_file(vault, record.local_path)

        except Exception:
            self.db.set_vault_status(vault.id, "idle")
            self._emit("vault:status", {"vaultId": vault.id, "status": "idle"})
            raise

        self.db.set_vault_status(vault.id, "synced", last_polled_at=_now())
        self._emit("vault:status", {"vaultId": vault.id, "status": "synced"})

    # ── Conflict resolution ───────────────────────────────────────────────

    async def resolve_conflict(
        self,
        vault_id: str,
        conflict_id: str,
        resolution: str,  # "local" | "notion"
    ) -> None:
        conflict = self.db.get_conflict(conflict_id)
        if not conflict:
            return

        vault = self.db.get_vault(vault_id)
        if not vault:
            return

        adapter = self._adapter(vault_id)

        if resolution == "local":
            # Clear notion_hash so push_file skips the conflict re-check.
            # The user has explicitly chosen to override Notion with their local version.
            record = self.db.get_record_by_path(vault_id, conflict.local_path)
            if record:
                self.db.update_record_hashes(
                    record.id,
                    record.local_hash,
                    None,
                    record.last_synced_at,
                    record.last_local_mtime,
                    record.last_notion_edited,
                )
            await self.push_file(vault, conflict.local_path, force=True)
        elif resolution == "notion":
            # Overwrite local with Notion content
            record = self.db.get_record_by_notion_id(vault_id, conflict.notion_page_id)
            if record:
                page = await adapter.get_page(conflict.notion_page_id)
                title = await adapter.get_page_title(page)
                page_info = {
                    "page_id": conflict.notion_page_id,
                    "parent_id": record.notion_parent_id,
                    "title": title,
                    "page_type": record.page_type,
                    "last_edited_time": page.get("last_edited_time", ""),
                    "page": page,
                }
                # Force overwrite — clear hashes to None so detect_changes treats
                # the next pull as a first-sync (no stored baseline → no conflict)
                self.db.update_record_hashes(record.id, None, None, _now(), 0.0, "")

                # Ensure parent directory exists (in case local file was deleted)
                local_dir = os.path.dirname(conflict.local_path)
                if local_dir and not os.path.isdir(local_dir):
                    os.makedirs(local_dir, exist_ok=True)

                await self._pull_page_to_file(
                    vault, adapter, page_info, conflict.local_path
                )

        self.db.resolve_conflict(conflict_id, resolution)

    async def resolve_deletion(
        self, vault_id: str, record_id: str, action: str
    ) -> None:
        """
        Resolve a deleted record (local or Notion).
        action: "delete_notion" | "restore_local" | "keep_orphaned"
        """
        record = self.db.get_record(vault_id, record_id)
        if not record:
            return

        vault = self.db.get_vault(vault_id)
        if not vault:
            return

        adapter = self._adapter(vault_id)

        if action == "delete_notion" and record.status == "deleted_local":
            try:
                await adapter.delete_page(record.notion_page_id)
            except Exception:
                pass
            self.db.delete_record(record_id)
            self._emit(
                "sync:file_changed",
                {
                    "vaultId": vault_id,
                    "path": record.local_path,
                    "direction": "deleted",
                    "status": "resolved",
                },
            )

        elif action == "restore_local" and record.status in (
            "deleted_in_notion",
            "deleted_local",
        ):
            if record.status == "deleted_local":
                page = await adapter.get_page(record.notion_page_id)
                title = await adapter.get_page_title(page)
                page_info = {
                    "page_id": record.notion_page_id,
                    "parent_id": record.notion_parent_id,
                    "title": title,
                    "page_type": record.page_type,
                    "last_edited_time": page.get("last_edited_time", ""),
                    "page": page,
                }
            else:
                page_info = None

            if page_info:
                local_dir = os.path.dirname(record.local_path)
                if local_dir and not os.path.isdir(local_dir):
                    os.makedirs(local_dir, exist_ok=True)
                await self._pull_page_to_file(
                    vault, adapter, page_info, record.local_path
                )
            else:
                if not file_exists(record.local_path):
                    raise FileNotFoundError(
                        f"Cannot restore deleted Notion page because local file is missing: {record.local_path}"
                    )
                title = os.path.splitext(os.path.basename(record.local_path))[0]
                parent_id = record.notion_parent_id or vault.notion_root_id
                new_page = await adapter.create_page(
                    parent_id,
                    title,
                    is_database_row=(record.page_type == "database_row"),
                )
                record.notion_page_id = new_page["id"]
                record.notion_hash = None
                record.last_notion_edited = None
                record.status = "pending"
                record.error_message = None
                self.db.upsert_record(vault_id, record)
                await self.push_file(vault, record.local_path, force=True)

            self._emit(
                "sync:file_changed",
                {
                    "vaultId": vault_id,
                    "path": record.local_path,
                    "direction": "restored",
                    "status": "synced",
                },
            )

        elif action == "keep_orphaned":
            self.db.update_record_status(record.id, "synced")

    def delete_record(self, vault_id: str, record_id: str) -> None:
        """Delete a sync record without affecting Notion."""
        self.db.delete_record(record_id)

    # ── Internal helpers ──────────────────────────────────────────────────

    async def _refresh_image_urls(
        self, vault_id: str, adapter: "NotionAdapter", blocks: list[dict]
    ) -> list[dict]:
        """Replace stale pre-signed URLs in file-type image blocks.

        Notion-hosted images have expiring S3 URLs. When the markdown placeholder
        was written at pull time the URL may now be expired. We use the stored
        block_id to fetch a fresh URL before pushing.
        """
        refreshed = []
        for block in blocks:
            block_id = block.pop("_needs_url_refresh", None)
            if block_id:
                try:
                    fresh = await adapter.get_block(block_id)
                    fresh_data = fresh.get("image", {})
                    fresh_url = (
                        fresh_data.get("file", {}).get("url")
                        or fresh_data.get("external", {}).get("url")
                        or ""
                    )
                    if fresh_url:
                        block["image"]["external"]["url"] = fresh_url
                except Exception as e:
                    logger.warning(
                        "Failed to refresh image URL for block %s: %s", block_id, e
                    )
                    self._emit(
                        "sync:warning",
                        {
                            "vaultId": vault_id,
                            "message": f"Failed to refresh image URL: {e}",
                        },
                    )
            refreshed.append(block)
        return refreshed


# ── Helpers ───────────────────────────────────────────────────────────────────


def _deduplicate_path(
    path: str, vault_id: str = None, notion_page_id: str = None, db=None
) -> str:
    """If path already exists as a different file, add a numeric suffix.

    If notion_page_id is provided and db is available, check if any existing
    record already links to that Notion page — if so, return that existing path.

    Handles filesystem errors gracefully (permission denied, path too long, etc).
    """
    if notion_page_id and vault_id and db:
        try:
            existing_record = db.get_record_by_notion_id(vault_id, notion_page_id)
            if existing_record and file_exists(existing_record.local_path):
                return existing_record.local_path
        except Exception:
            pass

    try:
        if not os.path.exists(path):
            return path
    except OSError as e:
        logger.warning("Error checking path existence for %s: %s", path, e)
        return path

    base, ext = os.path.splitext(path)
    i = 1
    try:
        while os.path.exists(f"{base}-{i}{ext}"):
            i += 1
    except OSError as e:
        logger.warning("Error checking deduplicated path: %s", e)
        return path
    return f"{base}-{i}{ext}"
