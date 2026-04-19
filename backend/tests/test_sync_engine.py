"""Regression tests for sync engine edge cases."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.models.sync_record import SyncRecord
from backend.models.vault import Vault
from backend.sync.engine import SyncEngine
from backend.sync.state import StateDB


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def db(tmp_path):
    return StateDB(str(tmp_path / "sync.db"))


@pytest.fixture
def vault(db, tmp_path):
    vault = Vault(
        id="vault-1",
        name="Test Vault",
        local_root=str(tmp_path),
        notion_root_id="root-page",
        created_at=_now(),
    )
    db.upsert_vault(vault)
    return vault


class TestResolveDeletion:
    @pytest.mark.asyncio
    async def test_restore_local_recreates_deleted_file_from_notion(
        self, db, vault, tmp_path
    ):
        local_path = tmp_path / "deleted.md"
        record = SyncRecord(
            id="record-1",
            local_path=str(local_path),
            notion_page_id="page-123",
            notion_parent_id="root-page",
            page_type="page",
            status="deleted_local",
            created_at=_now(),
        )
        db.upsert_record(vault.id, record)

        engine = SyncEngine(db)
        adapter = SimpleNamespace(
            get_page=AsyncMock(
                return_value={
                    "id": "page-123",
                    "last_edited_time": "2026-04-19T10:00:00+00:00",
                    "properties": {
                        "Name": {
                            "type": "title",
                            "title": [{"plain_text": "Recovered"}],
                        }
                    },
                }
            ),
            get_page_title=AsyncMock(return_value="Recovered"),
            get_block_tree=AsyncMock(
                return_value=[
                    {
                        "id": "block-1",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [{"plain_text": "Recovered from Notion"}]
                        },
                        "children": [],
                    }
                ]
            ),
        )
        engine._adapters[vault.id] = adapter

        await engine.resolve_deletion(vault.id, record.id, "restore_local")

        assert local_path.exists()
        assert local_path.read_text(encoding="utf-8") == "Recovered from Notion\n"
        updated = db.get_record(vault.id, record.id)
        assert updated.status == "synced"

    @pytest.mark.asyncio
    async def test_restore_local_recreates_deleted_notion_page_from_local_file(
        self, db, vault, tmp_path
    ):
        local_path = tmp_path / "local.md"
        local_path.write_text("Local survives\n", encoding="utf-8")
        record = SyncRecord(
            id="record-1",
            local_path=str(local_path),
            notion_page_id="deleted-page",
            notion_parent_id="root-page",
            page_type="page",
            status="deleted_in_notion",
            created_at=_now(),
        )
        db.upsert_record(vault.id, record)

        engine = SyncEngine(db)
        adapter = SimpleNamespace(
            create_page=AsyncMock(return_value={"id": "new-page"}),
            replace_blocks=AsyncMock(),
            update_page_title=AsyncMock(),
            get_last_edited_time=AsyncMock(return_value="2026-04-19T11:00:00+00:00"),
        )
        engine._adapters[vault.id] = adapter

        await engine.resolve_deletion(vault.id, record.id, "restore_local")

        updated = db.get_record(vault.id, record.id)
        assert updated.notion_page_id == "new-page"
        assert updated.status == "synced"
        adapter.create_page.assert_awaited_once()
        adapter.replace_blocks.assert_awaited_once()


class TestHandleFsEvent:
    @pytest.mark.asyncio
    async def test_rename_updates_existing_record_path(self, db, vault, tmp_path):
        old_path = tmp_path / "old.md"
        new_path = tmp_path / "new.md"
        old_path.write_text("Hello\n", encoding="utf-8")

        record = SyncRecord(
            id="record-1",
            local_path=str(old_path),
            notion_page_id="page-123",
            notion_parent_id="root-page",
            page_type="page",
            status="synced",
            created_at=_now(),
        )
        db.upsert_record(vault.id, record)

        engine = SyncEngine(db)
        adapter = SimpleNamespace(update_page_title=AsyncMock())
        engine._adapters[vault.id] = adapter

        await engine.handle_fs_event(vault.id, "moved", str(old_path), str(new_path))

        assert db.get_record_by_path(vault.id, str(old_path)) is None
        updated = db.get_record(vault.id, record.id)
        assert updated.local_path == str(new_path)
        adapter.update_page_title.assert_awaited_once_with("page-123", "new")
