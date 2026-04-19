"""
Filesystem adapter: read/write markdown files without any embedded sync metadata.
Sync state is stored exclusively in the SQLite database (sync_records table).

Legacy files may still have a <!-- notion-bridge {...} --> marker comment at the bottom
from an older version of this adapter. read_file() strips that comment so it doesn't
appear as content when the file is pushed to Notion.
"""

import os
import re
import tempfile

# Regex to detect the legacy bottom sync marker (still needed to strip old files on read)
_MARKER_RE = re.compile(r"^<!-- notion-bridge (\{.*\}) -->$")

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB limit for memory safety


class FileTooLargeError(Exception):
    """Raised when a file exceeds the maximum allowed size."""

    pass


def read_file(path: str) -> str:
    """
    Read a markdown file and return its body as a string.
    Strips any legacy <!-- notion-bridge {...} --> marker comment from the bottom.
    Also strips YAML front-matter (python-frontmatter) for files written by even
    older versions that used --- fences.

    Raises FileTooLargeError if file exceeds MAX_FILE_SIZE.
    """
    # Check file size before reading
    file_size = os.path.getsize(path)
    if file_size > MAX_FILE_SIZE:
        raise FileTooLargeError(
            f"File {path} ({file_size} bytes) exceeds maximum size of {MAX_FILE_SIZE} bytes"
        )

    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()

    # --- Strip legacy bottom marker comment ---
    stripped = raw.rstrip("\n")
    last_newline = stripped.rfind("\n")
    last_line = stripped[last_newline + 1 :] if last_newline >= 0 else stripped

    if _MARKER_RE.match(last_line):
        # Body is everything before the marker line
        body = stripped[:last_newline].rstrip("\n") if last_newline >= 0 else ""
        return body

    # --- Strip legacy YAML front-matter ---
    try:
        import frontmatter

        post = frontmatter.loads(raw)
        if post.metadata:
            return post.content.rstrip("\n")
    except Exception:
        pass

    return raw.rstrip("\n")


def write_file(path: str, body: str) -> None:
    """
    Write a markdown file atomically. Writes only the body — no sync metadata appended.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)

    content = body.rstrip("\n") + "\n"

    dir_ = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)  # atomic on POSIX
    except Exception:
        os.unlink(tmp_path)
        raise


def file_exists(path: str) -> bool:
    return os.path.isfile(path)


def get_mtime(path: str) -> float:
    return os.path.getmtime(path)


def delete_file(path: str) -> None:
    if os.path.exists(path):
        os.remove(path)


def list_markdown_files(root: str) -> list[str]:
    """Return all .md files under root, excluding .notion-bridge/ directory."""
    result = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip hidden .notion-bridge metadata directory
        dirnames[:] = [d for d in dirnames if d != ".notion-bridge"]
        for fname in filenames:
            if fname.endswith(".md"):
                result.append(os.path.join(dirpath, fname))
    return result
