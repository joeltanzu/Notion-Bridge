"""
Convert Notion block trees to Markdown strings.
Images are preserved as structured placeholder comments for round-trip fidelity.
Child pages and databases are rendered as comments to prevent accidental deletion.
"""

import json
from typing import Any
from .rich_text import rich_text_to_md


def blocks_to_markdown(blocks: list[dict], depth: int = 0) -> str:
    lines: list[str] = []
    indent = "  " * depth

    i = 0
    while i < len(blocks):
        block = blocks[i]
        btype = block.get("type", "")
        data = block.get(btype, {})
        children = block.get("children", [])  # pre-fetched by adapter

        if btype == "paragraph":
            text = rich_text_to_md(data.get("rich_text", []))
            lines.append(f"{indent}{text}" if text else "")

        elif btype == "heading_1":
            text = rich_text_to_md(data.get("rich_text", []))
            lines.append(f"{indent}# {text}")

        elif btype == "heading_2":
            text = rich_text_to_md(data.get("rich_text", []))
            lines.append(f"{indent}## {text}")

        elif btype == "heading_3":
            text = rich_text_to_md(data.get("rich_text", []))
            lines.append(f"{indent}### {text}")

        elif btype == "bulleted_list_item":
            text = rich_text_to_md(data.get("rich_text", []))
            lines.append(f"{indent}- {text}")
            if children:
                lines.append(blocks_to_markdown(children, depth + 1))

        elif btype == "numbered_list_item":
            text = rich_text_to_md(data.get("rich_text", []))
            lines.append(f"{indent}1. {text}")
            if children:
                lines.append(blocks_to_markdown(children, depth + 1))

        elif btype == "to_do":
            text = rich_text_to_md(data.get("rich_text", []))
            checked = "x" if data.get("checked") else " "
            lines.append(f"{indent}- [{checked}] {text}")
            if children:
                lines.append(blocks_to_markdown(children, depth + 1))

        elif btype == "toggle":
            text = rich_text_to_md(data.get("rich_text", []))
            lines.append(f"{indent}<details>")
            lines.append(f"{indent}<summary>{text}</summary>")
            lines.append("")
            if children:
                lines.append(blocks_to_markdown(children, depth))
            lines.append("")
            lines.append(f"{indent}</details>")

        elif btype == "quote":
            text = rich_text_to_md(data.get("rich_text", []))
            for line in text.split("\n"):
                lines.append(f"{indent}> {line}")

        elif btype == "callout":
            text = rich_text_to_md(data.get("rich_text", []))
            icon = data.get("icon", {})
            emoji = icon.get("emoji", "") if icon.get("type") == "emoji" else ""
            lines.append(f"{indent}> [!NOTE] {emoji}")
            for line in text.split("\n"):
                lines.append(f"{indent}> {line}")

        elif btype == "code":
            text = rich_text_to_md(data.get("rich_text", []))
            lang = data.get("language", "")
            lines.append(f"{indent}```{lang}")
            lines.append(text)
            lines.append(f"{indent}```")

        elif btype == "divider":
            pass  # dividers not rendered in local markdown

        elif btype == "image":
            block_id = block.get("id", "")
            img_type = "file" if "file" in data else "external"
            img_url = (data.get("file") or data.get("external") or {}).get("url", "")
            caption = rich_text_to_md(data.get("caption", []))
            payload = json.dumps(
                {"id": block_id, "type": img_type, "url": img_url, "caption": caption},
                separators=(",", ":"),
            )
            lines.append(f"{indent}<!-- notion:image {payload} -->")

        elif btype == "table":
            rows = [c for c in children if c.get("type") == "table_row"]
            if rows:
                lines.append(_table_to_md(rows, indent))

        elif btype == "child_page":
            block_id = block.get("id", "")
            title = data.get("title", "Untitled")
            safe = _safe_filename(title)
            lines.append(
                f'{indent}<!-- notion:child_page id="{block_id}" title="{title}" path="{safe}.md" -->'
            )

        elif btype == "child_database":
            block_id = block.get("id", "")
            title = data.get("title", "Untitled")
            lines.append(
                f'{indent}<!-- notion:database id="{block_id}" title="{title}" -->'
            )

        elif btype in ("bookmark", "link_preview"):
            url = data.get("url", "")
            caption_rt = data.get("caption", [])
            caption = rich_text_to_md(caption_rt) or url
            lines.append(f"{indent}[{caption}]({url})")

        elif btype == "embed":
            url = data.get("url", "")
            lines.append(f"{indent}<!-- notion:embed {url} -->")

        elif btype == "equation":
            expression = data.get("expression", "")
            lines.append(f"{indent}$${expression}$$")

        else:
            # Unknown block type — emit a comment so round-trip doesn't lose it
            lines.append(f"{indent}<!-- notion:{btype} -->")

        lines.append("")  # blank separator after each block
        i += 1

    # Collapse consecutive blank lines (toggle/code already add their own blanks)
    # and strip trailing blanks
    collapsed: list[str] = []
    prev_empty = False
    for line in lines:
        is_empty = line == ""
        if is_empty and prev_empty:
            continue
        collapsed.append(line)
        prev_empty = is_empty
    while collapsed and collapsed[-1] == "":
        collapsed.pop()
    return "\n".join(collapsed)


def _table_to_md(row_blocks: list[dict], indent: str) -> str:
    rows = []
    for rb in row_blocks:
        cells = rb.get("table_row", {}).get("cells", [])
        row = [rich_text_to_md(cell) for cell in cells]
        rows.append(row)

    if not rows:
        return ""

    col_count = max(len(r) for r in rows)
    # Pad rows to equal width
    rows = [r + [""] * (col_count - len(r)) for r in rows]

    header = "| " + " | ".join(rows[0]) + " |"
    separator = "| " + " | ".join(["---"] * col_count) + " |"
    body = "\n".join("| " + " | ".join(r) + " |" for r in rows[1:])

    parts = [indent + header, indent + separator]
    if body:
        parts.append(indent + body)
    return "\n".join(parts)


def _safe_filename(title: str) -> str:
    """Convert a Notion page title to a safe filename stem.

    Preserves Unicode characters (including emoji) as most modern filesystems
    support UTF-8. Removes only characters that are unsafe across platforms:
    - Control characters (0x00-0x1f)
    - Characters illegal in Windows filenames: < > : " / \\ | ? *
    - Leading/trailing dots (hidden files on Unix)
    - Truncates to 200 chars to avoid filesystem limits
    """
    import re
    import unicodedata

    # Normalize unicode (e.g., é -> e, but keep emoji)
    # NFKD normalization separates accent chars from base letters
    normalized = unicodedata.normalize("NFKC", title)

    # Remove control characters and illegal filename chars
    # Keep: alphanumeric, spaces, punctuation, emoji, and unicode letters
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", normalized)

    # Remove leading/trailing dots and spaces
    safe = safe.strip(". ")

    # Truncate to avoid filesystem limits (leave room for .md extension)
    max_len = 200
    if len(safe) > max_len:
        safe = safe[:max_len]

    return safe or "untitled"
