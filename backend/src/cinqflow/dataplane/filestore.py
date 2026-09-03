"""The landing zone: file storage, not tables.

Layout: {domain}/{source_system}/{feed}/{folder}/{business_date}/{filename}
Originals are written once and thereafter only moved between folders.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from enum import StrEnum
from pathlib import Path

from cinqflow.settings import Settings, get_settings


class Folder(StrEnum):
    INCOMING = "incoming"
    PROCESSED = "processed"
    REJECTED = "rejected"
    ARCHIVE = "archive"
    PARKED = "parked"


_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


class UnsafeFilename(ValueError):
    pass


def safe_filename(name: str) -> str:
    """Refuses paths, traversal and control characters. Leading underscores are
    legitimate in this domain (e.g. _CINQDOWNSTATE_Member_Roster_202606.csv)."""
    candidate = name.strip()
    if not candidate or "/" in candidate or "\\" in candidate or ".." in candidate:
        raise UnsafeFilename(name)
    cleaned = _UNSAFE.sub("_", candidate)
    if cleaned in {".", ".."}:
        raise UnsafeFilename(name)
    return cleaned


def fingerprint_bytes(content: bytes) -> str:
    return f"sha256-{hashlib.sha256(content).hexdigest()[:32]}"


def landing_key(
    *, domain: str, source_system: str, feed: str, folder: Folder, business_date: str, filename: str
) -> str:
    return "/".join(
        [domain, source_system, feed, folder.value, business_date, safe_filename(filename)]
    )


class FileStore:
    def __init__(self, settings: Settings | None = None) -> None:
        self.root = (settings or get_settings()).landing_root

    def path(self, key: str) -> Path:
        return self.root / key

    def place(self, key: str, content: bytes) -> Path:
        """Write an original exactly once. Refuses to overwrite."""
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(key)
        target.write_bytes(content)
        return target

    def read_bytes(self, key: str) -> bytes:
        return self.path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self.path(key).exists()

    def move(self, key: str, folder: Folder) -> str:
        """Move between folders, preserving the rest of the layout."""
        parts = key.split("/")
        parts[3] = folder.value
        new_key = "/".join(parts)
        target = self.path(new_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(self.path(key)), str(target))
        return new_key

    def remove(self, key: str) -> None:
        """Only used to clean up a failed upload transaction."""
        self.path(key).unlink(missing_ok=True)
