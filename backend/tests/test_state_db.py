"""Tests for StateDB - SQLite state management."""

import pytest
import os
import tempfile
from backend.sync.state import StateDB
from backend.models.vault import Vault
from backend.models.sync_record import SyncRecord
import uuid
from datetime import datetime, timezone


class TestStateDB:
    """Test StateDB operations including checkpoint and transactions."""

    @pytest.fixture
    def db(self):
        """Create a temporary database for testing."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db = StateDB(path)
        yield db
        os.unlink(path)

    def test_checkpoint_runs_without_error(self, db):
        """Checkpoint should run without raising exceptions."""
        # This tests the WAL checkpoint functionality
        db.checkpoint()  # Should not raise

    def test_run_in_transaction_success(self, db):
        """Successful transaction should commit."""
        vault = Vault(
            id=str(uuid.uuid4()),
            name="Test Vault",
            local_root="/tmp/test",
            notion_root_id="abc123",
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        result = db.run_in_transaction(lambda conn: db.upsert_vault(vault))

        # Verify the vault was created
        retrieved = db.get_vault(vault.id)
        assert retrieved is not None
        assert retrieved.name == "Test Vault"

    def test_run_in_transaction_rollback_on_error(self, db):
        """Failed transaction should rollback."""
        # Create a vault first
        vault = Vault(
            id=str(uuid.uuid4()),
            name="Test Vault",
            local_root="/tmp/test",
            notion_root_id="abc123",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        db.upsert_vault(vault)

        # Try to run a transaction that fails
        with pytest.raises(Exception):
            db.run_in_transaction(lambda conn: 1 / 0)

        # Original vault should still exist
        retrieved = db.get_vault(vault.id)
        assert retrieved is not None


class TestVaultOperations:
    """Test vault CRUD operations in StateDB."""

    @pytest.fixture
    def db(self):
        """Create a temporary database for testing."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db = StateDB(path)
        yield db
        os.unlink(path)

    def test_upsert_vault(self, db):
        """Test creating a new vault."""
        vault = Vault(
            id="vault-1",
            name="My Vault",
            local_root="/Users/joel/notes",
            notion_root_id="notion-page-123",
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        db.upsert_vault(vault)

        retrieved = db.get_vault("vault-1")
        assert retrieved is not None
        assert retrieved.name == "My Vault"
        assert retrieved.local_root == "/Users/joel/notes"

    def test_upsert_vault_updates_existing(self, db):
        """Test updating an existing vault."""
        vault = Vault(
            id="vault-1",
            name="Original Name",
            local_root="/tmp/notes",
            notion_root_id="abc",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        db.upsert_vault(vault)

        vault.name = "Updated Name"
        db.upsert_vault(vault)

        retrieved = db.get_vault("vault-1")
        assert retrieved.name == "Updated Name"

    def test_list_vaults_excludes_deleted(self, db):
        """Test that list_vaults excludes soft-deleted vaults."""
        vault = Vault(
            id="vault-1",
            name="Test",
            local_root="/tmp",
            notion_root_id="abc",
            secret_key="secret123",  # Add secret_key to trigger soft delete path
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        db.upsert_vault(vault)

        # Soft delete (secret_key exists, so it sets status to 'deleted')
        db.delete_vault("vault-1")

        vaults = db.list_vaults()
        assert len(vaults) == 0

        deleted = db.list_deleted_vaults()
        assert len(deleted) == 1

    def test_delete_vault_hard_delete(self, db):
        """Test hard delete removes vault completely."""
        vault = Vault(
            id="vault-1",
            name="Test",
            local_root="/tmp",
            notion_root_id="abc",
            secret_key="secret123",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        db.upsert_vault(vault)

        # Hard delete
        db.delete_vault("vault-1", hard_delete=True)

        assert db.get_vault("vault-1") is None


class TestSyncRecordOperations:
    """Test sync record CRUD operations."""

    @pytest.fixture
    def db(self):
        """Create a temporary database for testing."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db = StateDB(path)
        yield db
        os.unlink(path)

    @pytest.fixture
    def vault(self, db):
        """Create a test vault."""
        vault = Vault(
            id="vault-1",
            name="Test",
            local_root="/tmp",
            notion_root_id="abc",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        db.upsert_vault(vault)
        return vault

    def test_upsert_record(self, db, vault):
        """Test creating a sync record."""
        record = SyncRecord(
            id="record-1",
            local_path="/tmp/notes/page.md",
            notion_page_id="notion-123",
            status="synced",
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        db.upsert_record(vault.id, record)

        retrieved = db.get_record(vault.id, "record-1")
        assert retrieved is not None
        assert retrieved.local_path == "/tmp/notes/page.md"

    def test_update_record_status(self, db, vault):
        """Test updating record status."""
        record = SyncRecord(
            id="record-1",
            local_path="/tmp/page.md",
            notion_page_id="notion-123",
            status="pending",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        db.upsert_record(vault.id, record)

        db.update_record_status("record-1", "conflict", "Local and Notion differ")

        updated = db.get_record(vault.id, "record-1")
        assert updated.status == "conflict"
        assert updated.error_message == "Local and Notion differ"

    def test_delete_record(self, db, vault):
        """Test deleting a sync record."""
        record = SyncRecord(
            id="record-1",
            local_path="/tmp/page.md",
            notion_page_id="notion-123",
            status="synced",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        db.upsert_record(vault.id, record)

        db.delete_record("record-1")

        assert db.get_record(vault.id, "record-1") is None

    def test_upsert_record_updates_path_for_existing_id(self, db, vault):
        """Updating a tracked record should allow local path changes."""
        record = SyncRecord(
            id="record-1",
            local_path="/tmp/old.md",
            notion_page_id="notion-123",
            status="synced",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        db.upsert_record(vault.id, record)

        record.local_path = "/tmp/new.md"
        db.upsert_record(vault.id, record)

        updated = db.get_record(vault.id, "record-1")
        assert updated.local_path == "/tmp/new.md"
        assert db.get_record_by_path(vault.id, "/tmp/old.md") is None
