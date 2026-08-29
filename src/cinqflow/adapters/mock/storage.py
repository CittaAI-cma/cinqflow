"""memfs — an in-memory landing zone."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime

from cinqflow.core.model.vocabulary import LandingFolder
from cinqflow.ports import port
from cinqflow.ports.storage import FileNotFoundInStorageError, FileRef


@port("storage", "mock")
class MemFsStorage:
    """Files in a dict. Move is a rename; content never changes.

    Immutability is structural here rather than promised: `_content` is only
    ever written by `place`, and `move` re-keys the same bytes. There is no
    code path that edits a stored file, which is what "original source files
    are immutable" has to mean at every rung.
    """

    def __init__(self) -> None:
        self._content: dict[str, bytes] = {}
        self._modified: dict[str, datetime] = {}

    # ── test/simulator affordance, not part of the port ──────────────────────
    def place(self, key: str, content: bytes, *, modified_ts: datetime | None = None) -> FileRef:
        """Deliver a file into the zone. The simulator's folder-drop, in memory."""
        self._content[key] = content
        self._modified[key] = modified_ts or datetime.now(UTC)
        return self._ref(key)

    def _ref(self, key: str) -> FileRef:
        return FileRef(
            key=key,
            size_bytes=len(self._content[key]),
            modified_ts=self._modified[key],
            fingerprint=self.fingerprint(key),
        )

    # ── the port ─────────────────────────────────────────────────────────────
    def list_files(self, prefix: str) -> Iterator[FileRef]:
        for key in sorted(self._content):
            if key.startswith(prefix):
                yield self._ref(key)

    def fingerprint(self, key: str) -> str:
        if key not in self._content:
            raise FileNotFoundInStorageError(key)
        # Content-addressed, so it survives a move — which is what makes replay
        # refusal keep working once processed files are archived.
        return "sha256-" + hashlib.sha256(self._content[key]).hexdigest()[:32]

    def read_bytes(self, key: str) -> bytes:
        if key not in self._content:
            raise FileNotFoundInStorageError(key)
        return self._content[key]

    def move(self, key: str, to_folder: LandingFolder) -> FileRef:
        if key not in self._content:
            raise FileNotFoundInStorageError(key)
        parts = key.split("/")
        for index, part in enumerate(parts):
            if part in {f.value for f in LandingFolder}:
                parts[index] = to_folder.value
                break
        else:  # no folder segment: put one in rather than guessing
            parts.insert(-1, to_folder.value)
        new_key = "/".join(parts)
        self._content[new_key] = self._content.pop(key)
        self._modified[new_key] = self._modified.pop(key)
        return self._ref(new_key)

    def exists(self, key: str) -> bool:
        return key in self._content
