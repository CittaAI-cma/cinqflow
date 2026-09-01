"""What an arriving file looks like to the platform.

A value type, not a port verb. `core/landing` classifies files — register,
fingerprint, route — and that decision is pure: given a file and the registered
feeds, what happens to it. Pure means testable without a filesystem, which is
why the shape it reasons about has to live underneath the storage pin rather
than inside it.

`ports/storage.py` re-exports `FileRef`, so no adapter or caller changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class FileRef:
    """One file in the landing zone, as the platform sees it.

    `fingerprint` is what makes exactly-once ingestion real: it is compared
    against input_registry, and a match means the file is skipped WITH an audit
    entry rather than reprocessed.
    """

    key: str
    size_bytes: int
    modified_ts: datetime
    fingerprint: str | None = None

    @property
    def filename(self) -> str:
        return self.key.rsplit("/", 1)[-1]

    @property
    def starts_with_underscore(self) -> bool:
        """Incident #1, encoded where it can be asked about.

        A Fidelis file named `_CINQDOWNSTATE_Member_Roster_*.xlsx` once broke
        the Excel reader. The platform is never allowed to re-learn that.
        """
        return self.filename.startswith("_")
