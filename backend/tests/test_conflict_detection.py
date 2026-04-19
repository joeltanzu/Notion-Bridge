"""Tests for conflict detection - detect_changes function."""

import pytest
from backend.sync.conflict import detect_changes, ChangeResult
from backend.utils.hashing import hash_text


class TestDetectChanges:
    """Test conflict detection with various hash states."""

    def test_both_hashes_none_first_sync(self):
        """First sync ever - no stored hashes."""
        result = detect_changes("local content", "notion content", None, None)
        assert result.local_changed is True
        assert result.notion_changed is True
        assert result.is_conflict is True  # Both changed = conflict for first sync

    def test_partial_baseline_local_never_synced(self):
        """Local never synced, Notion has baseline."""
        result = detect_changes(
            "new local content",
            "notion content",
            None,  # local_hash = None
            "abc123",  # notion_hash exists
        )
        assert result.local_changed is False  # No baseline to compare
        assert result.notion_changed is True  # Notion content differs

    def test_partial_baseline_notion_never_synced(self):
        """Notion never synced, local has baseline."""
        result = detect_changes(
            "new local content",
            "notion content",
            "abc123",  # local_hash exists
            None,  # notion_hash = None
        )
        assert result.local_changed is True
        assert result.notion_changed is False  # No baseline to compare

    def test_no_changes_both_sides(self):
        """Neither side changed - in sync."""
        # Use hash_text which normalizes whitespace
        content = "same content"
        stored_hash = hash_text(content)
        result = detect_changes(content, content, stored_hash, stored_hash)
        assert result.local_changed is False
        assert result.notion_changed is False
        assert result.no_change is True

    def test_only_local_changed(self):
        """Only local changed."""
        local_content = "modified local"
        notion_content = "same notion"
        stored_local = hash_text("old local content")  # Different from current local
        stored_notion = hash_text(notion_content)  # Same as current notion

        result = detect_changes(
            local_content, notion_content, stored_local, stored_notion
        )
        assert result.local_changed is True
        assert result.notion_changed is False
        assert result.only_local is True

    def test_only_notion_changed(self):
        """Only Notion changed."""
        local_content = "same local"
        notion_content = "modified notion"
        stored_local = hash_text(local_content)
        stored_notion = hash_text("old notion content")  # Different from current

        result = detect_changes(
            local_content, notion_content, stored_local, stored_notion
        )
        assert result.local_changed is False
        assert result.notion_changed is True
        assert result.only_notion is True

    def test_both_changed_conflict(self):
        """Both sides changed - conflict."""
        local_content = "modified local"
        notion_content = "modified notion"
        stored_local = hash_text("old local content")
        stored_notion = hash_text("old notion content")

        result = detect_changes(
            local_content, notion_content, stored_local, stored_notion
        )
        assert result.local_changed is True
        assert result.notion_changed is True
        assert result.is_conflict is True

    def test_empty_content_hashing(self):
        """Empty content should hash correctly."""
        # hash_text normalizes, so "" becomes "" after strip
        empty_hash = hash_text("")
        result = detect_changes("", "", empty_hash, empty_hash)
        assert result.no_change is True

    def test_whitespace_normalization(self):
        """Whitespace differences should not cause false conflicts."""
        # hash_text normalizes whitespace, so these hashes match
        local = "line1\nline2"
        notion = "line1\r\nline2"

        local_hash = hash_text(local)
        notion_hash = hash_text(notion)

        # After normalization they should be equal
        result = detect_changes(local, notion, local_hash, notion_hash)
        assert result.no_change is True


class TestChangeResult:
    """Test ChangeResult helper properties."""

    def test_is_conflict_requires_both_changed(self):
        """is_conflict is True only when both changed."""
        result = ChangeResult(local_changed=True, notion_changed=True)
        assert result.is_conflict is True

        result = ChangeResult(local_changed=True, notion_changed=False)
        assert result.is_conflict is False

    def test_only_local_requires_only_local_changed(self):
        """only_local is True only when only local changed."""
        result = ChangeResult(local_changed=True, notion_changed=False)
        assert result.only_local is True

        result = ChangeResult(local_changed=True, notion_changed=True)
        assert result.only_local is False

    def test_only_notion_requires_only_notion_changed(self):
        """only_notion is True only when only notion changed."""
        result = ChangeResult(local_changed=False, notion_changed=True)
        assert result.only_notion is True

        result = ChangeResult(local_changed=True, notion_changed=True)
        assert result.only_notion is False

    def test_no_change_requires_nothing_changed(self):
        """no_change is True only when nothing changed."""
        result = ChangeResult(local_changed=False, notion_changed=False)
        assert result.no_change is True

        result = ChangeResult(local_changed=True, notion_changed=False)
        assert result.no_change is False
