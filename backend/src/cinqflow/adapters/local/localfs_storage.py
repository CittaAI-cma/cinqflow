"""localfs — the landing zone on a real filesystem. Rung 0.5's storage seat.

    "storage: list/fingerprint/move   mock: memfs   dev: localfs|minio
     target: adls_gen2"
    — docs/architecture/plates/04-pin-out-map.md

Real files, moved between real folders, never edited and never deleted. The
port has no write verb and no delete verb, and this adapter adds none: `place`
is the delivery affordance (a connector's or the simulator's folder-drop), and
a move re-links the same bytes.

Fingerprints are content-addressed with the SAME prefix format as memfs, so a
file the simulator placed in memory and the file a connector lands on disk
fingerprint identically — replay refusal does not care which rung it is on.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from cinqflow.core.model.vocabulary import LandingFolder
from cinqflow.ports import port
from cinqflow.ports.storage import FileNotFoundInStorageError, FileRef


@port("storage", "localfs")
class LocalFsStorage:
    """Keys are landing-relative paths; `root` anchors them on disk.

    Constructed with no arguments it lands in a fresh temporary directory —
    which is what the contract suite needs and what an exploratory shell
    deserves. A real socket passes the profile's `root`.
    """

    def __init__(self, root: str | None = None) -> None:
        self._root = Path(root) if root else Path(tempfile.mkdtemp(prefix="cinqflow-landing-"))
        self._root.mkdir(parents=True, exist_ok=True)

    # ── delivery affordance, not part of the port ─────────────────────────────
    def place(self, key: str, content: bytes, *, modified_ts: datetime | None = None) -> FileRef:
        """Deliver a file into the zone — the folder-drop, on disk."""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        if modified_ts is not None:
            os.utime(path, (modified_ts.timestamp(), modified_ts.timestamp()))
        return self._ref(key)

    # ── the port ─────────────────────────────────────────────────────────────
    def list_files(self, prefix: str) -> Iterator[FileRef]:
        keys = sorted(
            str(path.relative_to(self._root)) for path in self._root.rglob("*") if path.is_file()
        )
        for key in keys:
            if key.startswith(prefix):
                yield self._ref(key)

    def fingerprint(self, key: str) -> str:
        path = self._existing(key)
        # Content-addressed, so it survives a move — which is what makes replay
        # refusal keep working once processed files are archived.
        return "sha256-" + hashlib.sha256(path.read_bytes()).hexdigest()[:32]

    def read_bytes(self, key: str) -> bytes:
        return self._existing(key).read_bytes()

    def move(self, key: str, to_folder: LandingFolder) -> FileRef:
        source = self._existing(key)
        parts = key.split("/")
        for index, part in enumerate(parts):
            if part in {f.value for f in LandingFolder}:
                parts[index] = to_folder.value
                break
        else:  # no folder segment: put one in rather than guessing
            parts.insert(-1, to_folder.value)
        new_key = "/".join(parts)
        destination = self._path(new_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)
        return self._ref(new_key)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    # ── internals ─────────────────────────────────────────────────────────────
    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root.resolve()):
            raise FileNotFoundInStorageError(f"{key!r} escapes the landing root")
        return path

    def _existing(self, key: str) -> Path:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundInStorageError(key)
        return path

    def _ref(self, key: str) -> FileRef:
        path = self._existing(key)
        stat = path.stat()
        return FileRef(
            key=key,
            size_bytes=stat.st_size,
            modified_ts=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            fingerprint=self.fingerprint(key),
        )
