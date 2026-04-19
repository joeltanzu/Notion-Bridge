"""
SQLite-backed sync state. All operations are synchronous (called from async
contexts via asyncio.to_thread where needed).
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from backend.models.conflict import Conflict
from backend.models.sync_record import SyncRecord
from backend.models.vault import Vault
from backend.utils.keychain import (
    get_token,
    store_token,
    delete_token,
    get_secret,
    store_secret,
    delete_secret,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()
        self._migrate_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def checkpoint(self) -> None:
        """Run WAL checkpoint to merge WAL file into main database."""
        with self._connect() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def run_in_transaction(self, func: callable) -> Any:
        """Run a function within a transaction. Commits on success, rolls back on exception."""
        with self._connect() as conn:
            try:
                result = func(conn)
                conn.commit()
                return result
            except Exception:
                conn.rollback()
                raise

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS vaults (
                    id                TEXT PRIMARY KEY,
                    name              TEXT NOT NULL,
                    local_root        TEXT NOT NULL,
                    notion_root_id    TEXT NOT NULL,
                    sync_interval     INTEGER DEFAULT 300,
                    last_polled_at    TEXT,
                    status            TEXT DEFAULT 'idle',
                    allowed_page_ids  TEXT NOT NULL DEFAULT '[]',
                    created_at        TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sync_records (
                    id                  TEXT PRIMARY KEY,
                    vault_id            TEXT NOT NULL REFERENCES vaults(id) ON DELETE CASCADE,
                    local_path          TEXT NOT NULL,
                    notion_page_id      TEXT NOT NULL,
                    notion_parent_id    TEXT,
                    page_type           TEXT DEFAULT 'page',
                    local_hash          TEXT,
                    notion_hash         TEXT,
                    last_synced_at      TEXT,
                    last_local_mtime    REAL,
                    last_notion_edited  TEXT,
                    sync_direction      TEXT DEFAULT 'both',
                    status              TEXT DEFAULT 'pending',
                    error_message       TEXT,
                    created_at          TEXT NOT NULL,
                    UNIQUE(vault_id, local_path),
                    UNIQUE(vault_id, notion_page_id)
                );

                CREATE TABLE IF NOT EXISTS conflicts (
                    id              TEXT PRIMARY KEY,
                    sync_record_id  TEXT NOT NULL REFERENCES sync_records(id) ON DELETE CASCADE,
                    local_path      TEXT NOT NULL,
                    notion_page_id  TEXT NOT NULL,
                    local_snapshot  TEXT NOT NULL,
                    notion_snapshot TEXT NOT NULL,
                    detected_at     TEXT NOT NULL,
                    resolved_at     TEXT,
                    resolution      TEXT DEFAULT 'pending'
                );

                CREATE INDEX IF NOT EXISTS idx_sr_path ON sync_records(local_path);
                CREATE INDEX IF NOT EXISTS idx_sr_notion ON sync_records(notion_page_id);
                CREATE INDEX IF NOT EXISTS idx_sr_status ON sync_records(status);
                CREATE INDEX IF NOT EXISTS idx_sr_vault ON sync_records(vault_id);
            """)

    def _migrate_db(self) -> None:
        """Apply additive schema migrations so existing DBs stay compatible."""
        with self._connect() as conn:
            cols = {
                row[1] for row in conn.execute("PRAGMA table_info(vaults)").fetchall()
            }
            if "allowed_page_ids" not in cols:
                conn.execute(
                    "ALTER TABLE vaults ADD COLUMN allowed_page_ids TEXT NOT NULL DEFAULT '[]'"
                )
            if "secret_key" not in cols:
                conn.execute("ALTER TABLE vaults ADD COLUMN secret_key TEXT")

    # ── Vaults ────────────────────────────────────────────────────────────────

    def _row_to_vault(self, row: sqlite3.Row) -> Vault:
        d = dict(row)
        d["allowed_page_ids"] = json.loads(d.get("allowed_page_ids") or "[]")
        return Vault(**d)

    def get_vault_by_secret(self, secret_key: str) -> Optional[Vault]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM vaults WHERE secret_key=?", (secret_key,)
            ).fetchone()
        return self._row_to_vault(row) if row else None

    def get_vault_by_path(
        self, local_root: str, notion_root_id: str
    ) -> Optional[Vault]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM vaults WHERE local_root=? AND notion_root_id=?",
                (local_root, notion_root_id),
            ).fetchone()
        return self._row_to_vault(row) if row else None

    def list_vaults(self) -> list[Vault]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM vaults WHERE status != 'deleted' ORDER BY name"
            ).fetchall()
        return [self._row_to_vault(r) for r in rows]

    def list_deleted_vaults(self) -> list[Vault]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM vaults WHERE status = 'deleted' ORDER BY name"
            ).fetchall()
        return [self._row_to_vault(r) for r in rows]

    def restore_vault(self, vault_id: str) -> Optional[Vault]:
        with self._connect() as conn:
            conn.execute("UPDATE vaults SET status='idle' WHERE id=?", (vault_id,))
        return self.get_vault(vault_id)

    def get_vault(self, vault_id: str) -> Optional[Vault]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM vaults WHERE id=?", (vault_id,)
            ).fetchone()
        return self._row_to_vault(row) if row else None

    def upsert_vault(self, vault: Vault) -> None:
        data = vault.model_dump()
        data["allowed_page_ids"] = json.dumps(data["allowed_page_ids"])
        secret_key = data.pop("secret_key", None)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO vaults (id, name, local_root, notion_root_id, sync_interval,
                                    last_polled_at, status, allowed_page_ids, secret_key, created_at)
                VALUES (:id, :name, :local_root, :notion_root_id, :sync_interval,
                        :last_polled_at, :status, :allowed_page_ids, :secret_key, COALESCE(
                            (SELECT created_at FROM vaults WHERE id=:id), :now))
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, local_root=excluded.local_root,
                    notion_root_id=excluded.notion_root_id,
                    sync_interval=excluded.sync_interval,
                    last_polled_at=excluded.last_polled_at,
                    status=excluded.status,
                    allowed_page_ids=excluded.allowed_page_ids,
                    secret_key=COALESCE(excluded.secret_key, vaults.secret_key)
            """,
                {**data, "secret_key": secret_key, "now": _now()},
            )

    def delete_vault(self, vault_id: str, hard_delete: bool = False) -> None:
        vault = self.get_vault(vault_id)
        if vault and vault.secret_key and not hard_delete:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE vaults SET status='deleted' WHERE id=?", (vault_id,)
                )
            delete_token(vault_id)
        else:
            with self._connect() as conn:
                conn.execute("DELETE FROM vaults WHERE id=?", (vault_id,))
            delete_token(vault_id)
            if vault and vault.secret_key:
                delete_secret(vault_id)

    def set_vault_status(
        self, vault_id: str, status: str, last_polled_at: Optional[str] = None
    ) -> None:
        with self._connect() as conn:
            if last_polled_at:
                conn.execute(
                    "UPDATE vaults SET status=?, last_polled_at=? WHERE id=?",
                    (status, last_polled_at, vault_id),
                )
            else:
                conn.execute(
                    "UPDATE vaults SET status=? WHERE id=?", (status, vault_id)
                )

    # ── Sync Records ──────────────────────────────────────────────────────────

    def get_record(self, vault_id: str, record_id: str) -> Optional[SyncRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sync_records WHERE vault_id=? AND id=?",
                (vault_id, record_id),
            ).fetchone()
        return SyncRecord(**dict(row)) if row else None

    def get_record_by_path(
        self, vault_id: str, local_path: str
    ) -> Optional[SyncRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sync_records WHERE vault_id=? AND local_path=?",
                (vault_id, local_path),
            ).fetchone()
        return SyncRecord(**dict(row)) if row else None

    def get_record_by_notion_id(
        self, vault_id: str, notion_id: str
    ) -> Optional[SyncRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sync_records WHERE vault_id=? AND notion_page_id=?",
                (vault_id, notion_id),
            ).fetchone()
        return SyncRecord(**dict(row)) if row else None

    def list_records(self, vault_id: str) -> list[SyncRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sync_records WHERE vault_id=? ORDER BY local_path",
                (vault_id,),
            ).fetchall()
        return [SyncRecord(**dict(r)) for r in rows]

    def upsert_record(self, vault_id: str, record: SyncRecord) -> None:
        with self._connect() as conn:
            updated = conn.execute(
                """
                UPDATE sync_records SET
                    local_path=:local_path,
                    notion_page_id=:notion_page_id,
                    notion_parent_id=:notion_parent_id,
                    page_type=:page_type,
                    local_hash=:local_hash,
                    notion_hash=:notion_hash,
                    last_synced_at=:last_synced_at,
                    last_local_mtime=:last_local_mtime,
                    last_notion_edited=:last_notion_edited,
                    sync_direction=:sync_direction,
                    status=:status,
                    error_message=:error_message
                WHERE id=:id AND vault_id=:vault_id
            """,
                {**record.model_dump(), "vault_id": vault_id},
            )
            if updated.rowcount:
                return

            conn.execute(
                """
                INSERT INTO sync_records (
                    id, vault_id, local_path, notion_page_id, notion_parent_id,
                    page_type, local_hash, notion_hash, last_synced_at,
                    last_local_mtime, last_notion_edited, sync_direction,
                    status, error_message, created_at
                ) VALUES (
                    :id, :vault_id, :local_path, :notion_page_id, :notion_parent_id,
                    :page_type, :local_hash, :notion_hash, :last_synced_at,
                    :last_local_mtime, :last_notion_edited, :sync_direction,
                    :status, :error_message, COALESCE(
                        (SELECT created_at FROM sync_records WHERE id=:id), :now)
                )
                ON CONFLICT(vault_id, local_path) DO UPDATE SET
                    notion_page_id=excluded.notion_page_id,
                    notion_parent_id=excluded.notion_parent_id,
                    page_type=excluded.page_type,
                    local_hash=excluded.local_hash,
                    notion_hash=excluded.notion_hash,
                    last_synced_at=excluded.last_synced_at,
                    last_local_mtime=excluded.last_local_mtime,
                    last_notion_edited=excluded.last_notion_edited,
                    sync_direction=excluded.sync_direction,
                    status=excluded.status,
                    error_message=excluded.error_message
            """,
                {**record.model_dump(), "vault_id": vault_id, "now": _now()},
            )

    def update_record_status(
        self, record_id: str, status: str, error: Optional[str] = None
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE sync_records SET status=?, error_message=? WHERE id=?",
                (status, error, record_id),
            )

    def update_record_hashes(
        self,
        record_id: str,
        local_hash: Optional[str],
        notion_hash: Optional[str],
        last_synced_at: str,
        last_local_mtime: float,
        last_notion_edited: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE sync_records SET
                    local_hash=?, notion_hash=?, last_synced_at=?,
                    last_local_mtime=?, last_notion_edited=?, status='synced', error_message=NULL
                WHERE id=?
            """,
                (
                    local_hash,
                    notion_hash,
                    last_synced_at,
                    last_local_mtime,
                    last_notion_edited,
                    record_id,
                ),
            )

    def delete_record(self, record_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sync_records WHERE id=?", (record_id,))

    # ── Conflicts ─────────────────────────────────────────────────────────────

    def list_conflicts(self, vault_id: str) -> list[Conflict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT c.* FROM conflicts c
                JOIN sync_records sr ON c.sync_record_id = sr.id
                WHERE sr.vault_id=? AND c.resolution='pending'
                ORDER BY c.detected_at DESC
            """,
                (vault_id,),
            ).fetchall()
        return [Conflict(**dict(r)) for r in rows]

    def create_conflict(
        self,
        record_id: str,
        local_path: str,
        notion_page_id: str,
        local_snapshot: str,
        notion_snapshot: str,
    ) -> Conflict:
        conflict = Conflict(
            id=str(uuid.uuid4()),
            sync_record_id=record_id,
            local_path=local_path,
            notion_page_id=notion_page_id,
            local_snapshot=local_snapshot,
            notion_snapshot=notion_snapshot,
            detected_at=_now(),
            resolution="pending",
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conflicts (id, sync_record_id, local_path, notion_page_id,
                                       local_snapshot, notion_snapshot, detected_at, resolution)
                VALUES (:id, :sync_record_id, :local_path, :notion_page_id,
                        :local_snapshot, :notion_snapshot, :detected_at, :resolution)
            """,
                conflict.model_dump(),
            )
        return conflict

    def resolve_conflict(self, conflict_id: str, resolution: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE conflicts SET resolution=?, resolved_at=? WHERE id=?",
                (resolution, _now(), conflict_id),
            )
            # clear the conflict status on the sync record
            conn.execute(
                """
                UPDATE sync_records SET status='synced', error_message=NULL
                WHERE id=(SELECT sync_record_id FROM conflicts WHERE id=?)
            """,
                (conflict_id,),
            )

    def get_conflict(self, conflict_id: str) -> Optional[Conflict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM conflicts WHERE id=?", (conflict_id,)
            ).fetchone()
        return Conflict(**dict(row)) if row else None
