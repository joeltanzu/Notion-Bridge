"""
Conflict detection: compare local and Notion content hashes against
the stored baseline to determine what changed on each side.
"""

from typing import Optional

from backend.utils.hashing import hash_text


class ChangeResult:
    __slots__ = ("local_changed", "notion_changed")

    def __init__(self, local_changed: bool, notion_changed: bool):
        self.local_changed = local_changed
        self.notion_changed = notion_changed

    @property
    def is_conflict(self) -> bool:
        return self.local_changed and self.notion_changed

    @property
    def only_local(self) -> bool:
        return self.local_changed and not self.notion_changed

    @property
    def only_notion(self) -> bool:
        return self.notion_changed and not self.local_changed

    @property
    def no_change(self) -> bool:
        return not self.local_changed and not self.notion_changed


def detect_changes(
    current_local_content: str,
    current_notion_content: str,
    stored_local_hash: Optional[str],
    stored_notion_hash: Optional[str],
) -> ChangeResult:
    current_local_hash = hash_text(current_local_content)
    current_notion_hash = hash_text(current_notion_content)

    # First sync ever - no stored hashes at all
    if stored_local_hash is None and stored_notion_hash is None:
        return ChangeResult(local_changed=True, notion_changed=True)

    # Partial baseline: local never synced, but Notion has baseline
    if stored_local_hash is None:
        notion_changed = current_notion_hash != stored_notion_hash
        return ChangeResult(local_changed=False, notion_changed=notion_changed)

    # Partial baseline: Notion never synced, but local has baseline
    if stored_notion_hash is None:
        local_changed = current_local_hash != stored_local_hash
        return ChangeResult(local_changed=local_changed, notion_changed=False)

    # Normal case: both hashes exist, compare against baseline
    local_changed = current_local_hash != stored_local_hash
    notion_changed = current_notion_hash != stored_notion_hash
    return ChangeResult(local_changed, notion_changed)
