import hashlib


def hash_text(content: str) -> str:
    """SHA-256 hash of a UTF-8 string.

    Normalises whitespace before hashing to prevent false conflicts caused by
    trailing newlines or CRLF line endings that editors silently add/remove.
    """
    normalised = content.strip().replace("\r\n", "\n")
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def hash_file(path: str) -> str:
    """SHA-256 hash of a file's content."""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()
