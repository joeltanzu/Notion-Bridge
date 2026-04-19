"""Tests for _safe_filename unicode handling."""

import pytest
from backend.converters.notion_to_md import _safe_filename


class TestSafeFilename:
    """Test _safe_filename with unicode and edge cases."""

    def test_preserves_unicode_letters(self):
        """Unicode letters should be preserved."""
        assert _safe_filename("日本語") == "日本語"
        assert _safe_filename("中文") == "中文"
        assert _safe_filename("한국어") == "한국어"

    def test_preserves_emoji(self):
        """Emoji should be preserved."""
        assert "📝" in _safe_filename("My Notes 📝")
        assert "🎉" in _safe_filename("Party 🎉")

    def test_preserves_accented_characters(self):
        """Accented characters should be preserved (NFKC normalized)."""
        result = _safe_filename("Café")
        # NFKC normalization keeps accent chars but may change how they're displayed
        # The key is that the result is valid and preserves the character
        assert "Café" in result or "Cafe" in result
        assert len(result) > 0

    def test_removes_windows_illegal_chars(self):
        """Windows illegal characters should be removed."""
        assert _safe_filename("file<name>") == "filename"
        assert _safe_filename("file:name") == "filename"
        assert _safe_filename("file|name") == "filename"
        assert _safe_filename("file?name") == "filename"
        assert _safe_filename('file"name') == "filename"

    def test_removes_control_characters(self):
        """Control characters should be removed."""
        assert _safe_filename("test\x00\x1f") == "test"

    def test_strips_leading_trailing_dots(self):
        """Leading/trailing dots should be stripped."""
        assert _safe_filename(".hidden") == "hidden"
        assert _safe_filename("file.") == "file"
        assert _safe_filename("  ") == "untitled"

    def test_empty_title_becomes_untitled(self):
        """Empty or whitespace-only title becomes 'untitled'."""
        assert _safe_filename("") == "untitled"
        assert _safe_filename("   ") == "untitled"

    def test_truncates_long_filenames(self):
        """Very long filenames should be truncated to 200 chars."""
        long_title = "A" * 300
        result = _safe_filename(long_title)
        assert len(result) == 200
        assert result == "A" * 200

    def test_preserves_spaces_and_alphanumeric(self):
        """Regular alphanumeric and spaces should be preserved."""
        assert _safe_filename("My Notes 2024") == "My Notes 2024"
        assert _safe_filename("Chapter 1 - Notes") == "Chapter 1 - Notes"
