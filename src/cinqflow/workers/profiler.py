"""CF-V1-E5-01 — the seam between the pure profiler and the fitted pins.

    Task pack: Data/pipeline (adapter only) -> Metadata/schema -> Backend API
    (status) -> Eval/test (replay & restart proofs)
    — CINQFLOW_MVP_Backlog.csv, CF-V1-E5-01

`core/profiling` computes; this module SEQUENCES: read the bytes through the
storage pin, compute, store through the metadata pin, hand back the record.
Nothing here decides anything, which is what keeps every decision testable with
no services running.

Two things it deliberately does NOT do:

  • It does not move the file. Profiling is a design-time read of a sample, not
    an ingestion — and the storage port has no write verb anyway.
  • It does not fall back when a file cannot be read. `profile_bytes` returns a
    refusal, the refusal is STORED like any other profile, and the wizard shows
    it. A profiling attempt that left no row would be the one class of file
    nobody can see they tried.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from cinqflow.core.profiling import (
    DEFAULT_MAX_BYTES,
    FileProfile,
    ProgressCallback,
    profile_bytes,
)
from cinqflow.ports.metadata_db import FileProfileRecord, MetadataDbPort
from cinqflow.ports.storage import FileNotFoundInStorageError, StoragePort


class ProfileTargetMissingError(RuntimeError):
    """The file is not in the landing zone.

    Distinct from an unreadable file, and deliberately an exception rather than
    a refusal: "we read your file and could not make sense of it" and "there is
    no such file" are different incidents, and reporting the second as the
    first sends a BA to the payer over a typo in a key.
    """


@dataclass(frozen=True)
class Profiler:
    """Profile a landed sample and store the facts.

    Takes its pins as fields, like every other composition seat here — a
    worker that constructed its own adapters could only be tested the way
    production runs it.
    """

    storage: StoragePort
    metadata: MetadataDbPort
    max_bytes: int = DEFAULT_MAX_BYTES

    def profile(
        self,
        *,
        feed_id: str,
        file_key: str,
        file_format: str,
        profiled_by: str,
        encoding: str = "utf-8",
        delimiter: str | None = None,
        progress: ProgressCallback | None = None,
        now: datetime | None = None,
    ) -> FileProfileRecord:
        """Read, profile, store. Idempotent on unchanged bytes.

        The store's primary key is the digest of the facts, so re-profiling a
        file nobody changed returns the ORIGINAL record — same id, same
        timestamp. That is the replay proof, and it holds without this method
        checking anything.
        """
        try:
            content = self.storage.read_bytes(file_key)
            fingerprint = self.storage.fingerprint(file_key)
        except FileNotFoundInStorageError as missing:
            raise ProfileTargetMissingError(
                f"{file_key!r} is not in the landing zone. Check the key, or upload the "
                "sample again — nothing was profiled."
            ) from missing

        profile = profile_bytes(
            content,
            file_format=file_format,
            source_key=file_key,
            source_fingerprint=fingerprint,
            encoding=encoding,
            delimiter=delimiter,
            max_bytes=self.max_bytes,
            progress=progress,
        )
        return self.metadata.record_profile(
            FileProfileRecord(
                feed_id=feed_id,
                profile=profile,
                profiled_by=profiled_by,
                profiled_ts=now or datetime.now(UTC),
            )
        )

    def latest_for(self, feed_id: str) -> FileProfileRecord | None:
        found = self.metadata.list_profiles(feed_id=feed_id, limit=1)
        return found[0] if found else None

    def already_profiled(self, *, feed_id: str, file_key: str) -> FileProfile | None:
        """Has this exact content already been profiled for this feed?

        Asked before re-reading a 50MB sample, and answered by the file's own
        fingerprint rather than by its name — a payer who re-sends the same
        month under a new name has not given us a new file.
        """
        try:
            fingerprint = self.storage.fingerprint(file_key)
        except FileNotFoundInStorageError:
            return None
        for record in self.metadata.list_profiles(
            feed_id=feed_id, source_fingerprint=fingerprint, limit=1
        ):
            return record.profile
        return None
