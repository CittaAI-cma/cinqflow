"""The `storage` pin — list, fingerprint and move files.

    verb: list/fingerprint/move   mock: memfs   dev: localfs|minio   target: adls_gen2
    — docs/architecture/plates/04-pin-out-map.md

One adapter, three URL schemes: file:// at rung 0.5, s3:// (MinIO) at rung 1,
abfs:// (ADLS Gen2) at rung 3. This pin is where "Azure Blob is not
S3-compatible" gets ABSORBED rather than discovered at integration time.

The verbs are the landing zone's vocabulary, not a filesystem's. There is no
`write` verb and no `delete` verb, deliberately:

    "original source files are immutable and archived; nothing is ever
     silently dropped"
    — docs/architecture/INVARIANTS.md, data plane

A file moves between folders (incoming -> processed | rejected | archive |
parked) and is never edited or removed. A port that cannot express deletion is
a stronger guarantee than a port that documents not deleting.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from cinqflow.core.model.files import FileRef
from cinqflow.core.model.vocabulary import LandingFolder

__all__ = [
    "FileNotFoundInStorageError",
    "FileRef",
    "StorageError",
    "StoragePort",
]


class StorageError(RuntimeError):
    """The adapter could not honour the verb. Never silently swallowed."""


class FileNotFoundInStorageError(StorageError):
    """Named separately because 'the file is gone' and 'the store is down' are
    different incidents and must not be reported as one."""


@runtime_checkable
class StoragePort(Protocol):
    """list · fingerprint · move. No write. No delete."""

    def list_files(self, prefix: str) -> Iterator[FileRef]:
        """Every file under a prefix. Ordered, so listings are comparable."""
        ...

    def fingerprint(self, key: str) -> str:
        """A content fingerprint, stable across moves.

        Stable-across-moves is the requirement that matters: a file archived
        after processing must still match if it is re-delivered, or replay
        refusal silently stops working the moment archiving is introduced.
        """
        ...

    def read_bytes(self, key: str) -> bytes:
        """Hand the bytes to a parser. core/ never opens a file itself."""
        ...

    def move(self, key: str, to_folder: LandingFolder) -> FileRef:
        """Move a file between landing folders, preserving its content.

        Returns the new reference so the caller records where it went — a move
        with no recorded destination is how files become mysteries.
        """
        ...

    def exists(self, key: str) -> bool: ...
