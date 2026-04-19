"""
Convert Markdown strings to Notion block objects for API upload.
Uses markdown-it-py for parsing.
Handles notion:image, notion:child_page, and notion:database placeholder comments.
"""

import json
import logging
from typing import Any, Optional
from markdown_it import MarkdownIt
from .rich_text import md_inline_to_rich_text

logger = logging.getLogger(__name__)


_md = MarkdownIt("gfm-like", {"breaks": False, "html": True})


def markdown_to_blocks(md_text: str) -> list[dict]:
    """Parse a markdown string and return a list of Notion block dicts."""
    tokens = _md.parse(md_text)
    blocks: list[dict] = []
    _process_tokens(tokens, blocks)
    return blocks


def _process_tokens(tokens: list, blocks: list[dict], depth: int = 0) -> int:
    """Walk token stream and append Notion blocks. Returns number of tokens consumed."""
    i = 0
    while i < len(tokens):
        tok = tokens[i]

        if tok.type == "heading_open":
            level = int(tok.tag[1])  # h1 -> 1
            inline_tok = tokens[i + 1]
            text = inline_tok.content
            blocks.append(_heading_block(level, text))
            i += 3  # heading_open, inline, heading_close
            continue

        if tok.type == "paragraph_open":
            inline_tok = tokens[i + 1]
            text = inline_tok.content
            blocks.append(_paragraph_block(text))
            i += 3
            continue

        if tok.type == "fence":
            lang = tok.info.strip()
            content = tok.content.rstrip("\n")
            blocks.append(_code_block(lang, content))
            i += 1
            continue

        if tok.type == "hr":
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            i += 1
            continue

        if tok.type == "html_block":
            content = tok.content.strip()
            # Bottom sync marker — never push to Notion
            if content.startswith("<!-- notion-bridge ") and content.endswith("-->"):
                i += 1
                continue
            # Divider placeholder — restore as a Notion divider block
            if content == "<!-- notion:divider -->":
                blocks.append({"object": "block", "type": "divider", "divider": {}})
                i += 1
                continue
            # child_page and database blocks are preserved server-side — no block to create
            if content.startswith("<!-- notion:child_page ") or content.startswith(
                "<!-- notion:database "
            ):
                i += 1
                continue
            # Reconstruct image blocks from structured placeholder comments
            if content.startswith("<!-- notion:image ") and content.endswith("-->"):
                raw = content[18:].rstrip().rstrip("-->").rstrip()
                try:
                    img_meta = json.loads(raw)
                    img_block = _image_block(img_meta)
                    if img_block:
                        blocks.append(img_block)
                except (json.JSONDecodeError, KeyError, ValueError) as e:
                    logger.warning("Failed to parse image placeholder: %s — %s", raw, e)
            i += 1
            continue

        if tok.type == "blockquote_open":
            # Collect all tokens until blockquote_close
            depth_count = 1
            j = i + 1
            inner: list = []
            while j < len(tokens) and depth_count > 0:
                if tokens[j].type == "blockquote_open":
                    depth_count += 1
                elif tokens[j].type == "blockquote_close":
                    depth_count -= 1
                if depth_count > 0:
                    inner.append(tokens[j])
                j += 1
            # Extract text from inner inline tokens
            text_parts = [t.content for t in inner if t.type == "inline"]
            text = "\n".join(text_parts)
            blocks.append(_quote_block(text))
            # Notion doesn't support dividers nested inside quotes — emit any
            # <!-- notion:divider --> html_block tokens as sibling blocks after the quote.
            for t in inner:
                if (
                    t.type == "html_block"
                    and t.content.strip() == "<!-- notion:divider -->"
                ):
                    blocks.append({"object": "block", "type": "divider", "divider": {}})
            i = j      # advance past blockquote_close
            continue   # skip the i += 1 at the bottom

        if tok.type in ("bullet_list_open", "ordered_list_open"):
            is_ordered = tok.type == "ordered_list_open"
            depth = 1
            j = i + 1
            while j < len(tokens) and depth > 0:
                t = tokens[j]
                if t.type in ("bullet_list_open", "ordered_list_open"):
                    depth += 1
                elif t.type in ("bullet_list_close", "ordered_list_close"):
                    depth -= 1
                elif t.type == "inline" and depth == 1:
                    text = t.content
                    if not is_ordered and (
                        text.startswith("[ ] ")
                        or text.startswith("[x] ")
                        or text.startswith("[X] ")
                    ):
                        checked = text[1].lower() == "x"
                        blocks.append(_todo_block(text[4:], checked))
                    elif is_ordered:
                        blocks.append(_numbered_block(text))
                    else:
                        blocks.append(_bullet_block(text))
                j += 1
            i = j
            continue

        if tok.type == "table_open":
            logger.warning(
                "Tables are not fully supported — content may be lost during push to Notion"
            )
            i += 1
            continue

        i += 1

    return len(tokens)


def _rich_text(text: str) -> list[dict]:
    return md_inline_to_rich_text(text)


def _heading_block(level: int, text: str) -> dict:
    btype = f"heading_{level}"
    return {"object": "block", "type": btype, btype: {"rich_text": _rich_text(text)}}


def _paragraph_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _rich_text(text)},
    }


def _code_block(lang: str, content: str) -> dict:
    return {
        "object": "block",
        "type": "code",
        "code": {"rich_text": _rich_text(content), "language": lang or "plain text"},
    }


def _quote_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "quote",
        "quote": {"rich_text": _rich_text(text)},
    }


def _bullet_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": _rich_text(text)},
    }


def _numbered_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "numbered_list_item",
        "numbered_list_item": {"rich_text": _rich_text(text)},
    }


def _todo_block(text: str, checked: bool) -> dict:
    return {
        "object": "block",
        "type": "to_do",
        "to_do": {"rich_text": _rich_text(text), "checked": checked},
    }


def _image_block(meta: dict) -> Optional[dict]:
    """Reconstruct a Notion image block from a placeholder comment's parsed metadata.

    For 'file' type images, the URL is a pre-signed S3 link that may have expired.
    We mark these with _needs_url_refresh so the sync engine can fetch a fresh URL
    via the Notion API before pushing.
    """
    img_type = meta.get("type", "external")
    url = meta.get("url", "")
    caption_text = meta.get("caption", "")
    caption = (
        [{"type": "text", "text": {"content": caption_text}}] if caption_text else []
    )

    if img_type == "external":
        if not url:
            return None
        return {
            "object": "block",
            "type": "image",
            "image": {"type": "external", "external": {"url": url}, "caption": caption},
        }
    elif img_type == "file":
        block_id = meta.get("id", "")
        if not block_id:
            return None
        # URL may be stale — engine will refresh before pushing
        return {
            "object": "block",
            "type": "image",
            "image": {"type": "external", "external": {"url": url}, "caption": caption},
            "_needs_url_refresh": block_id,
        }
    return None
