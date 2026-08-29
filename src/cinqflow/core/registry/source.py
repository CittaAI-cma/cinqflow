"""CF-V1-E3-02 — the source system: who sends us data, and who to ring.

    "One governed source of truth for every source and feed."
    — Epic 3

A SOURCE is the organisation; a FEED is one thing that organisation sends. The
distinction is worth a separate object type because the questions differ:
"which of Centene's feeds are late this morning" is a source question, and it
cannot be answered by a registry where the payer's name is a string repeated
on nine rows with three spellings — which is exactly what the incumbent had.

`ObjectType.SOURCE` already existed in the governed model, so a source
inherits the one lifecycle, the audit trail and both universal negatives for
free — and the reference graph in `core/impact` can already see a feed's
dependence on it, which is what makes retiring a source show its nine feeds.

Law 1 reaches the DATA here, not only the source code: a source stores no
host, no port and no credential. What it stores is the NAME its connection
profile is registered under, so promoting the registry between environments
changes nothing in these rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum, unique
from typing import Any, Self

from cinqflow.core.model.governed import (
    Actor,
    GovernedObject,
    LifecycleState,
    ObjectType,
)
from cinqflow.core.registry.operations import OperationsValidationError, Owner, OwnerRole

#: An identifier, so it can be cited, filtered on and used as a folder name.
_SOURCE_ID = re.compile(r"^[a-z][a-z0-9-]{1,62}$")


class SourceValidationError(ValueError):
    """A source record the platform will not store as stated."""


@unique
class SourceKind(StrEnum):
    """What sort of organisation this is. Drives nothing technical; drives
    every conversation about who to ring."""

    PAYER = "payer"
    PROVIDER = "provider"
    VENDOR = "vendor"
    INTERNAL = "internal"
    GOVERNMENT = "government"


@dataclass(frozen=True)
class SourceRecord:
    """One organisation that sends or receives data.

    `line_of_business` and `state` are here because every real question about
    this estate is scoped by them — "which of Centene's New York Medicaid
    feeds", not "which of Centene's feeds" — and a registry that cannot answer
    the scoped question sends people back to the spreadsheet.
    """

    source_id: str
    name: str
    kind: SourceKind
    #: The connection profile's name for this organisation's endpoint. A NAME.
    #: Never a host, a bucket or a URL: see the module docstring.
    endpoint_ref: str = ""
    line_of_business: tuple[str, ...] = ()
    states: tuple[str, ...] = ()
    owners: tuple[Owner, ...] = ()
    #: The person at the OTHER organisation. Free text, because it is their
    #: staff list and not ours — but recorded, because "who at Fidelis do we
    #: ring" was a thing three people knew and nobody had written down.
    counterparty_contact: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if not _SOURCE_ID.fullmatch(self.source_id):
            raise SourceValidationError(
                f"{self.source_id!r} is not a source id. Lowercase letters, digits and "
                "hyphens — it is an address, and it appears in citations and folder names."
            )
        if not self.name.strip():
            raise SourceValidationError(
                f"{self.source_id}: a source needs a name people would recognise. "
                "`centene-ny` is an id; `Centene of New York` is what somebody says."
            )
        seen: set[OwnerRole] = set()
        for owner in self.owners:
            if owner.role in seen:
                raise OperationsValidationError(
                    f"two {owner.role.value} owners for {self.source_id}. Shared "
                    "accountability is no accountability."
                )
            seen.add(owner.role)

    @property
    def relationship_owner(self) -> Owner | None:
        """Who owns the RELATIONSHIP — the person who rings the payer.

        The business owner, deliberately: the technical owner can read the
        error, and the person on the other end of the phone is a counterpart,
        not a queue.
        """
        for owner in self.owners:
            if owner.role is OwnerRole.BUSINESS:
                return owner
        return None

    def as_governed(
        self, *, author: Actor, version: int = 1, created_ts: datetime | None = None
    ) -> GovernedObject:
        return GovernedObject(
            object_type=ObjectType.SOURCE,
            object_id=self.source_id,
            version=version,
            lifecycle_state=LifecycleState.DRAFT,
            created_by=author,
            created_ts=created_ts or datetime.now(UTC),
            body=self.as_body(),
        )

    def as_body(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "endpoint_ref": self.endpoint_ref,
            "line_of_business": list(self.line_of_business),
            "states": list(self.states),
            "owners": [
                {"role": o.role.value, "subject": o.subject, "display_name": o.display_name}
                for o in self.owners
            ],
            "counterparty_contact": self.counterparty_contact,
            "notes": self.notes,
        }

    @classmethod
    def from_governed(cls, obj: GovernedObject) -> Self:
        if obj.object_type is not ObjectType.SOURCE:
            raise SourceValidationError(f"{obj.object_type} is not a source")
        body = obj.body
        return cls(
            source_id=obj.object_id,
            name=str(body.get("name", "")),
            kind=SourceKind(str(body.get("kind", SourceKind.PAYER.value))),
            endpoint_ref=str(body.get("endpoint_ref", "")),
            line_of_business=tuple(body.get("line_of_business") or ()),
            states=tuple(body.get("states") or ()),
            owners=tuple(
                Owner(
                    role=OwnerRole(str(o["role"])),
                    subject=str(o["subject"]),
                    display_name=str(o.get("display_name", "")),
                )
                for o in body.get("owners", ())
            ),
            counterparty_contact=str(body.get("counterparty_contact", "")),
            notes=str(body.get("notes", "")),
        )

    # DELIBERATELY NO `citation()`. A source has no citation kind, because
    # adding one costs a route, a test and a real UI page — a toll the platform
    # charges on purpose — and nothing cites a source yet. The tempting
    # shortcut, returning `feed:<source_id>`, would produce an address that
    # resolves to a feed page for a feed that does not exist: a citation that
    # reads as evidence and opens onto nothing, which is the one thing
    # `UnresolvableCitationError` exists to prevent. When a story needs to cite
    # a source, it pays the toll.
