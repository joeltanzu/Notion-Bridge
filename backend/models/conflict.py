from pydantic import BaseModel
from typing import Literal, Optional


class Conflict(BaseModel):
    id: str
    sync_record_id: str
    local_path: str
    notion_page_id: str
    local_snapshot: str
    notion_snapshot: str
    detected_at: str
    resolved_at: Optional[str] = None
    resolution: Optional[Literal["local", "notion", "pending"]] = "pending"
