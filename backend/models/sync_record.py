from pydantic import BaseModel
from typing import Literal, Optional


class SyncRecord(BaseModel):
    id: str
    local_path: str
    notion_page_id: str
    notion_parent_id: Optional[str] = None
    page_type: Literal["page", "database", "database_row"] = "page"

    local_hash: Optional[str] = None
    notion_hash: Optional[str] = None
    last_synced_at: Optional[str] = None
    last_local_mtime: Optional[float] = None
    last_notion_edited: Optional[str] = None

    sync_direction: Literal["both", "to_notion", "to_local"] = "both"
    status: Literal[
        "synced",
        "pending",
        "conflict",
        "error",
        "deleted_in_notion",
        "deleted_local",
        "pushing",
    ] = "pending"
    error_message: Optional[str] = None
    created_at: str
