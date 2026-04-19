"""Tests for hashing utilities."""

import pytest
from backend.utils.hashing import hash_text, hash_file
import os
import tempfile


class TestHashText:
    """Test hash_text function - content hashing with normalization."""

    def test_same_content_same_hash(self):
        """Identical content should produce identical hash."""
        assert hash_text("hello world") == hash_text("hello world")

    def test_different_content_different_hash(self):
        """Different content should produce different hash."""
        assert hash_text("hello") != hash_text("world")

    def test_strips_trailing_newlines(self):
        """Trailing newlines should be stripped."""
        assert hash_text("content\n") == hash_text("content")
        assert hash_text("content\n\n\n") == hash_text("content")

    def test_normalizes_crlf_to_lf(self):
        """CRLF should be normalized to LF."""
        assert hash_text("line1\r\nline2") == hash_text("line1\nline2")

    def test_strips_leading_trailing_whitespace(self):
        """Leading/trailing whitespace should be stripped."""
        assert hash_text("  content  ") == hash_text("content")

    def test_consistent_with_notion_roundtrip(self):
        """Hash should be consistent through markdown->blocks->markdown roundtrip."""
        original = "# Heading\n\nSome content"
        # This simulates what happens in push_file
        # markdown_to_blocks(original) -> blocks_to_markdown(blocks)
        # The roundtrip normalizes content
        from backend.converters.md_to_notion import markdown_to_blocks
        from backend.converters.notion_to_md import blocks_to_markdown

        blocks = markdown_to_blocks(original)
        roundtripped = blocks_to_markdown(blocks)
        # Both should produce the same hash since whitespace is normalized
        assert hash_text(original) == hash_text(roundtripped)


class TestHashFile:
    """Test hash_file function - file content hashing."""

    def test_same_file_same_hash(self):
        """Identical file content should produce same hash."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("test content")
            f.flush()
            path1 = f.name

        try:
            hash1 = hash_file(path1)
            hash2 = hash_file(path1)
            assert hash1 == hash2
        finally:
            os.unlink(path1)

    def test_different_file_different_hash(self):
        """Different file content should produce different hash."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f1:
            f1.write("content1")
            f1.flush()
            path1 = f1.name

        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f2:
            f2.write("content2")
            f2.flush()
            path2 = f2.name

        try:
            assert hash_file(path1) != hash_file(path2)
        finally:
            os.unlink(path1)
            os.unlink(path2)


class TestHashConsistency:
    """Test hash consistency across operations."""

    def test_notion_hash_consistency(self):
        """Notion hash should remain consistent after push/pull cycle."""
        # This simulates:
        # 1. Pull from Notion -> local file (hash stored in DB)
        # 2. Push from local -> Notion (fetch Notion content to detect conflict)
        # 3. The hash should match because we use canonical form

        markdown = "## Header\n\nSome **bold** text"

        from backend.converters.md_to_notion import markdown_to_blocks
        from backend.converters.notion_to_md import blocks_to_markdown

        # Convert markdown to blocks and back (simulating Notion processing)
        blocks = markdown_to_blocks(markdown)
        canonical = blocks_to_markdown(blocks)

        # Hash should be consistent
        original_hash = hash_text(markdown)
        canonical_hash = hash_text(canonical)
        assert original_hash == canonical_hash
