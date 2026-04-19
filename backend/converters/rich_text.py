"""
Convert between Notion rich_text arrays and markdown inline spans.
"""

from typing import Any


def rich_text_to_md(rich_text: list[dict]) -> str:
    """Convert a Notion rich_text array to a markdown inline string."""
    parts = []
    for rt in rich_text:
        text = rt.get("plain_text", "")
        if not text:
            continue
        annotations = rt.get("annotations", {})
        href = rt.get("href")

        # Apply annotations (order matters for nested markdown)
        if annotations.get("code"):
            text = f"`{text}`"
        else:
            if annotations.get("bold"):
                text = f"**{text}**"
            if annotations.get("italic"):
                text = f"*{text}*"
            if annotations.get("strikethrough"):
                text = f"~~{text}~~"
            if annotations.get("underline"):
                text = f"<u>{text}</u>"

        if href:
            text = f"[{text}]({href})"

        parts.append(text)

    return "".join(parts)


_NOTION_TEXT_LIMIT = 2000


def md_inline_to_rich_text(text: str) -> list[dict]:
    """
    Convert a plain markdown inline string to a minimal Notion rich_text array.
    Chunks text into ≤2000-char segments to satisfy the Notion API limit.
    """
    chunks = [text[i:i + _NOTION_TEXT_LIMIT] for i in range(0, len(text), _NOTION_TEXT_LIMIT)] if text else [""]
    return [
        {
            "type": "text",
            "text": {"content": chunk, "link": None},
            "annotations": {
                "bold": False, "italic": False, "strikethrough": False,
                "underline": False, "code": False, "color": "default"
            },
            "plain_text": chunk,
            "href": None
        }
        for chunk in chunks
    ]
