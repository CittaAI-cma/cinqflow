"""CF-V1-E3-05 — the delivery contract. What every connector normalises to.

    connectors: [sftp-poller, api-puller, fhir-puller, storage-event,
                 db-extractor, upload-endpoint, stream-batcher]
    connector_conformance: [connect, list, fetch, checksum_match, move,
                            retry_etiquette]
    — docs/architecture/plates/09-ingestion-and-the-universal-landing-contract.md

    "Every connector normalises to this one contract (ADR-0011), so no delivery
     path can bypass registration, fingerprinting or validation. There is no
     second door."
    — cinqflow.core.landing

THE PLATFORM HAD NO WAY IN. `core/landing` decides what happens to a file that
has ARRIVED, and the storage pin deliberately has no write verb — it models
reading a zone somebody else fills. Between those two there was nothing: no
port, no route, no folder that anything read. The only paths to a landed file
were the simulator and a CLI that generated its own roster, which is why the
wizard's first step said "Upload a sample file" beside no way to upload one.

This module is the missing half, and it is PURE. Deciding where a file must
land, and whether the bytes are the bytes that were promised, needs no
filesystem — so the whole delivery contract is testable in milliseconds and
every connector shares one answer instead of each inventing its own.

WHAT IS REFUSED HERE, AND WHAT IS DELIBERATELY NOT.

Almost nothing is refused at the door, and that is the design. ADR-0011:
"every arriving file is registered — INCLUDING UNEXPECTED ONES, which are
parked and surfaced, never ignored." A file whose name matches no feed, or
which is empty, or twice the size it should be, is not turned away: it lands,
it gets a row, and `core.landing.classify` calls it UNEXPECTED or REJECTED
with a named check. Refusing early would delete exactly the evidence the
control plane exists to keep.

So the door refuses only what is not a delivery at all:

  • a filename that is a PATH — `../`, a separator, a control character. That
    is not a file arriving, it is an attempt to choose where the platform
    writes, and it is refused before any adapter sees it.
  • a manifest whose checksum does not match the bytes. The sender said what
    they were sending; if the bytes disagree, the transfer is damaged and
    landing it would fingerprint the damage as though it were the delivery.

Everything else is landing's decision, made after the bytes are safely down.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime

from cinqflow.core.model.files import FileRef
from cinqflow.core.model.vocabulary import LandingFolder

__all__ = [
    "DELIVERED_BY_UPLOAD",
    "NO_MANIFEST",
    "ChecksumMismatchError",
    "Delivery",
    "DeliveryError",
    "Manifest",
    "UnsafeFilenameError",
    "business_date_of",
    "fingerprint_of",
    "landing_key",
    "safe_filename",
    "verify_manifest",
]

#: Written into the audit row's actor when a person uploads through the API,
#: so "who delivered this" separates a human from a poller without either
#: needing to be inferred from a timestamp later.
DELIVERED_BY_UPLOAD = "upload-endpoint"

#: A filename, and nothing that could be a path. Anchored, no separators, no
#: traversal, no control characters, and a length a filesystem will accept.
#: Deliberately stricter than any single OS: the same name has to be legal on
#: localfs at rung 0.5 and in a blob container at rung 3, and a name that is
#: only legal on one of them is a rung-climbing failure waiting to happen.
_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,254}$")

#: The one exception, and it is an incident. The Fidelis roster genuinely
#: arrives as `_CINQDOWNSTATE_Member_Roster_202608.xlsx`, so a leading
#: underscore has to be expressible. It is allowed HERE (the name is legal) and
#: judged THERE (`RegisteredFeed.allows_leading_underscore`), because whether
#: this feed expects one is a registry fact, not a character-set fact.
_LEADING_UNDERSCORE = re.compile(r"^_[A-Za-z0-9][A-Za-z0-9._\-]{0,253}$")

_BUSINESS_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DeliveryError(ValueError):
    """A delivery that must not be attempted."""


class UnsafeFilenameError(DeliveryError):
    """The name is a path, not a filename."""


class ChecksumMismatchError(DeliveryError):
    """The bytes are not the bytes the sender declared."""


def safe_filename(name: str) -> str:
    """The name, or a refusal naming what is wrong with it.

    A connector composes a storage key out of this. `../../etc/passwd` composed
    into a key is a write outside the landing zone, and `roster/2026.csv` is a
    file that lands one folder deeper than the layout says and is never seen by
    the lister. Both are the same bug — a caller choosing the platform's write
    path — so both are refused in the same place, before any adapter runs.
    """
    candidate = name.strip()
    if not candidate:
        raise UnsafeFilenameError("a delivery with no filename names nothing")
    if "/" in candidate or "\\" in candidate:
        raise UnsafeFilenameError(
            f"{candidate!r} contains a path separator. A delivery supplies a FILE NAME; "
            "the platform composes the path from the feed's landing layout."
        )
    if candidate in {".", ".."} or candidate.startswith("../"):
        raise UnsafeFilenameError(f"{candidate!r} is a path traversal, not a filename")
    if any(character < " " or character == "\x7f" for character in candidate):
        raise UnsafeFilenameError(
            f"{candidate!r} contains a control character. A name that renders differently "
            "in a log than on disk is how a rejected file gets reported as a landed one."
        )
    if _FILENAME.match(candidate) or _LEADING_UNDERSCORE.match(candidate):
        return candidate
    raise UnsafeFilenameError(
        f"{candidate!r} is not a portable filename. Letters, digits, dot, dash and "
        "underscore, up to 255 characters — the intersection of what localfs at rung 0.5 "
        "and a blob container at rung 3 will both accept."
    )


def business_date_of(value: str | date | datetime) -> str:
    """The delivery's business date, as the layout spells it.

    A business date is not the wall clock and is not derived from one here: a
    roster for August delivered on the 3rd of September is August's, and only
    the sender knows that. Accepting a `date` as well as a string is so the
    caller does not format it — a hand-formatted date is how `2026-9-1` and
    `2026-09-01` become two folders holding one month.
    """
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    candidate = value.strip()
    if not _BUSINESS_DATE.match(candidate):
        raise DeliveryError(
            f"{candidate!r} is not a business date. Write it as YYYY-MM-DD — the layout "
            "makes it a folder name, and two spellings of one month are two folders."
        )
    try:
        date.fromisoformat(candidate)
    except ValueError as exc:
        raise DeliveryError(f"{candidate!r} is not a real date: {exc}") from None
    return candidate


def landing_key(
    *,
    landing_path: str,
    filename: str,
    business_date: str | date | datetime,
    folder: LandingFolder = LandingFolder.INCOMING,
) -> str:
    """Where this file must land. THE ONE PLACE THE LAYOUT IS SPELLED.

        landing:
          layout: "{domain}/{source_system}/{feed}/{folder}/{business_date}"
        — profiles/local.yaml

    The feed's `landing_path` is already `{domain}/{source_system}/{feed}` —
    it is registry data, checked at feed creation — so this appends only the
    folder and the business date. Composing it anywhere else would be a second
    answer to "where does this file live", and the lister and the writer
    disagreeing about that is a file that is on disk and invisible.
    """
    name = safe_filename(filename)
    stamp = business_date_of(business_date)
    trimmed = landing_path.strip().strip("/")
    if not trimmed:
        raise DeliveryError(
            "the feed has no landing path, so there is nowhere for its files to land. "
            "A feed cannot be activated without one — see CF-V1-E3-02's readiness list."
        )
    return f"{trimmed}/{folder.value}/{stamp}/{name}"


def fingerprint_of(content: bytes) -> str:
    """The identity these bytes will have once landed.

    THE SAME FORMAT BOTH STORAGE ADAPTERS PRODUCE — `sha256-` and the first 32
    hex characters — computed here so a connector can verify a manifest BEFORE
    writing anything. Landing damaged bytes and deleting them afterwards is not
    available: the storage pin has no delete verb, on purpose.

    A contract test asserts this agrees with what `StoragePort.fingerprint`
    returns for the same bytes. It has to: if the two ever drift, a manifest
    would be checked against one identity and the replay refusal enforced
    against another, and a re-sent file would land twice.
    """
    return "sha256-" + hashlib.sha256(content).hexdigest()[:32]


@dataclass(frozen=True)
class Manifest:
    """What the sender says they are sending. Every field optional.

        manifest_optional: {checksum, declared_row_count, business_date}
        — plate 09

    Optional because most payers send a file and nothing else, and a platform
    that required a manifest would be a platform that could not accept the
    deliveries it exists to accept. Where a field IS supplied it is CHECKED —
    an unchecked declaration is worse than none, because it reads as assurance.
    """

    checksum: str | None = None
    declared_row_count: int | None = None
    business_date: str | None = None

    def __post_init__(self) -> None:
        if self.declared_row_count is not None and self.declared_row_count < 0:
            raise DeliveryError("a declared row count cannot be negative")


#: A delivery that declared nothing. Named rather than written as `Manifest()`
#: at four call sites, because "the sender told us nothing about this file" is a
#: state worth being able to say out loud.
NO_MANIFEST = Manifest()


def verify_manifest(manifest: Manifest, *, fingerprint: str) -> None:
    """Refuse a delivery whose bytes are not the bytes that were promised.

    THE COMPARISON IS AGAINST THE FINGERPRINT THE STORAGE PIN COMPUTES, not a
    second hash taken here. Both localfs and memfs return
    `sha256-<first 32 hex>`, so a sender who quotes a full sha256 is compared
    on the prefix the platform actually stores — and the platform never holds
    two ideas of what this file's identity is.

    A mismatch is refused rather than landed-and-rejected, alone among the
    checks. Everything else about a bad file is worth keeping: the platform
    wants the row, the reason and the parked copy. Damaged bytes are worth
    nothing — fingerprinting them would put the damage in `input_registry`
    under the delivery's name, and the re-send of the CORRECT file would then
    look like a replay of something already processed.
    """
    if manifest.checksum is None:
        return
    declared = manifest.checksum.strip().lower()
    if declared.startswith("sha256-"):
        declared = declared[len("sha256-") :]
    if not declared:
        return
    actual = fingerprint.removeprefix("sha256-").lower()
    if not declared.startswith(actual) and not actual.startswith(declared):
        raise ChecksumMismatchError(
            f"the manifest declares sha256 {declared[:32]}… and the bytes fingerprint as "
            f"{actual}. The transfer is damaged, so nothing is landed: fingerprinting "
            "damaged bytes would register them under this delivery's name and make the "
            "re-send of the correct file look like a replay."
        )


@dataclass(frozen=True)
class Delivery:
    """One file, delivered. What a connector returns and the worker acts on.

    It carries the FileRef rather than the bytes: by the time a delivery
    exists the content is in the zone, and a receipt holding a second copy of
    it in memory is a copy that can disagree with the one on disk.
    """

    file: FileRef
    feed_id: str
    business_date: str
    delivered_by: str
    manifest: Manifest = Manifest()

    @property
    def fingerprint(self) -> str:
        """The identity the input registry compares. Never None on a receipt:
        a delivery the platform cannot fingerprint is one it cannot refuse to
        process twice."""
        if not self.file.fingerprint:
            raise DeliveryError(
                f"{self.file.key} was delivered without a fingerprint, so exactly-once "
                "ingestion cannot be enforced for it."
            )
        return self.file.fingerprint

    @property
    def citation(self) -> str:
        """`file:<fingerprint>` — the address the insight agent already cites
        and the explorer already routes. A delivery needed no new citation
        kind: it IS a file, and `CitationKind.FILE` has meant this since
        Wave 0."""
        return f"file:{self.fingerprint}"
