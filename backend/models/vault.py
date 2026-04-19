from pydantic import BaseModel
from typing import Literal, Optional


class Vault(BaseModel):
    id: str
    name: str
    local_root: str
    notion_root_id: str
    sync_interval: int = 300  # seconds
    last_polled_at: Optional[str] = None
    status: Literal["synced", "syncing", "offline", "error", "idle", "deleted"] = "idle"
    allowed_page_ids: list[
        str
    ] = []  # If non-empty, only sync subtrees rooted at these page IDs
    secret_key: Optional[str] = None  # Used to reconnect deleted vaults
