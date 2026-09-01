"""CF-V1-E3-02 — everything about a feed that the ORGANISATION needs.

    "Full source/feed aggregate — delivery method, direction, calendars, SLAs,
     volumes, owners, alert chain"
    "One form-backed story that absorbs ~30 Epic-3 bullets; they are fields of
     one aggregate, not separate features."
    — CF-V1-E3-02

    "activation blocked without SLA/owner with plain-language checklist ·
     unique ID + v1 on save · referenced-everywhere view"
    — CINQFLOW_Wave_Implementation_Blueprint.md §4.1

WHY THIS IS A SEPARATE MODULE FROM `feed.py`. `FeedRecord`'s six fields are
what the ENGINE reads, and Wave 0's whole argument was that six is enough to
run a pipeline from stored metadata. Adding owners and escalation tiers to that
dataclass would quietly make them look like engine inputs, and the next person
would wonder which of the twenty fields the loader actually uses. So the engine
record stays six fields, and this is the operational envelope around it:
everything a human needs to run the feed on a Tuesday, and nothing the engine
reads.

THE CHECKLIST IS NOT VALIDATION. A half-filled draft must SAVE — an analyst
gathering an SLA from a payer over three days needs somewhere to keep what
they have. What is blocked is ACTIVATION: `readiness` computes what is still
missing, in plain language, and the lifecycle refuses to submit a feed that is
not ready. The difference matters because validation-at-save teaches people to
put placeholder values in required fields, and a registry full of
`owner@example.com` is worse than one with visible gaps.

NO URL, NO CREDENTIAL, NO PATH IN CORE — and that rule reaches the DATA here,
not only the source. `endpoint_ref` is a NAME the connection profile resolves,
never a host; `LinkedDocument.reference` refuses anything carrying a password
or a token. A registry row is read by more people than any config file, and a
credential pasted into one is a credential in everybody's browser history.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum, unique
from typing import Any, Self

from cinqflow.core.model.governed import GovernedObject, LifecycleViolationError, ObjectType


class OperationsValidationError(ValueError):
    """An operational envelope the platform will not store as stated."""


class ActivationBlockedError(LifecycleViolationError):
    """A feed submitted for review before it can be operated.

        "activation blocked without SLA/owner with plain-language checklist"

    A `LifecycleViolationError` rather than a validation error, deliberately:
    it is refused by the same path that refuses every other illegal
    transition, so it is logged, surfaced and tested the same way — and the
    API needs no special case to turn it into a 403 with a reason.
    """

    def __init__(self, feed_id: str, readiness: Readiness) -> None:
        super().__init__(readiness.explain(feed_id))
        self.readiness = readiness


@unique
class Direction(StrEnum):
    """Which way the data moves. Named from the PLATFORM's point of view."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"


@unique
class DeliveryMethod(StrEnum):
    """How the file arrives, or leaves. A closed set, like every vocabulary.

    Note what is stored per method: NOTHING but the method. The host, the
    port, the key and the bucket live in the connection profile under the
    name `endpoint_ref` gives, because a registry row is read by more people
    than any config file.
    """

    SFTP = "sftp"
    OBJECT_STORE = "object_store"
    API_PULL = "api_pull"
    API_PUSH = "api_push"
    DATABASE_EXTRACT = "database_extract"
    MANUAL_UPLOAD = "manual_upload"

    @property
    def is_automated(self) -> bool:
        """A manual upload has no endpoint and no arrival SLA the platform can
        enforce — somebody drags a file in when they remember."""
        return self is not DeliveryMethod.MANUAL_UPLOAD


@unique
class DeliveryCalendar(StrEnum):
    """WHICH DAYS a delivery is expected. The other half of an arrival SLA.

    "Late by 06:00" means nothing without this: a roster due every business
    day is not late on Saturday, and a month-end extract is not late on the
    3rd. The incumbent platform alerted on both, which is how a team learns to
    ignore alerts.
    """

    EVERY_DAY = "every_day"
    BUSINESS_DAYS = "business_days"
    BUSINESS_DAYS_EXCLUDING_HOLIDAYS = "business_days_excluding_holidays"
    WEEKLY = "weekly"
    MONTH_END = "month_end"
    ON_DEMAND = "on_demand"

    @property
    def has_a_predictable_day(self) -> bool:
        """An on-demand feed cannot be Missing, because nothing said it was
        coming. It can only be late once somebody asks for it."""
        return self is not DeliveryCalendar.ON_DEMAND


@unique
class OwnerRole(StrEnum):
    """Who is accountable, and for what. Three, and all three are needed.

    A feed with only a technical owner has nobody who can ring the payer; a
    feed with only a business owner has nobody who can read the error.
    """

    BUSINESS = "business"
    TECHNICAL = "technical"
    DATA_STEWARD = "data_steward"


@unique
class AlertChannel(StrEnum):
    EMAIL = "email"
    CHAT = "chat"
    PAGER = "pager"
    TICKET = "ticket"


@unique
class DocumentKind(StrEnum):
    """What a linked document is, so a screen can group them and a person can
    find the one they need at 3am."""

    SPECIFICATION = "specification"
    COMPANION_GUIDE = "companion_guide"
    RUNBOOK = "runbook"
    DATA_SHARING_AGREEMENT = "data_sharing_agreement"
    TICKET = "ticket"


#: Mailboxes nobody reads. Deliberately a SHORT, EXPLICIT list rather than a
#: clever heuristic about what looks like a distribution list: a rule that
#: guesses will refuse `data-team-lead@` one day and be disabled the next.
#: What it catches is the case that actually happened — an alert chain whose
#: last tier was an unattended inbox, discovered during an incident.
_UNATTENDED = ("noreply", "no-reply", "donotreply", "do-not-reply", "postmaster", "mailer-daemon")

#: A reference carrying a credential. `https://user:pass@host/spec.pdf` and
#: `?sig=...` are the two shapes that turn up in pasted SharePoint and blob
#: links, and both put a secret in every browser history that opens the page.
_CREDENTIALLED = re.compile(
    r"://[^/@\s]*:[^/@\s]*@|[?&](?:sig|sas|token|api[_-]?key|password|pwd|secret)=",
    re.IGNORECASE,
)

#: An endpoint REFERENCE is a name a connection profile resolves. Anything with
#: a scheme, a host or a path separator is a location, and locations do not
#: belong in a registry row (Law 1, applied to the data as well as the source).
_ENDPOINT_REF = re.compile(r"^[a-z][a-z0-9._-]{1,62}$")


@dataclass(frozen=True)
class Owner:
    """A named PERSON who is accountable. Not a role, not an inbox.

    `display_name` is required for the same reason `UnnamedApproverError`
    exists: accountability that nobody's name is attached to is
    accountability nobody has.
    """

    role: OwnerRole
    subject: str
    display_name: str

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise OperationsValidationError("an owner without a subject is nobody")
        if not self.display_name.strip():
            raise OperationsValidationError(
                f"{self.subject}: an owner needs a name. A feed owned by an address is a "
                "feed nobody will admit to owning when it breaks."
            )
        local = self.subject.split("@", 1)[0].lower()
        if local in _UNATTENDED:
            raise OperationsValidationError(
                f"{self.subject} is an unattended mailbox and cannot own a feed. When this "
                "feed is late at 3am, somebody has to be ringing the payer."
            )


@dataclass(frozen=True)
class ServiceLevel:
    """When the file is due, and when being late becomes somebody's problem.

    THE TIMEZONE IS A NAME, NEVER AN OFFSET. `America/New_York`, not `-05:00`
    — an offset is right for half the year, and a roster due at 06:00 Eastern
    arrives an hour "late" every March for a week until somebody notices.
    """

    expected_by_local_time: str
    timezone: str
    calendar: DeliveryCalendar = DeliveryCalendar.BUSINESS_DAYS
    grace_minutes: int = 30
    escalate_after_minutes: int = 120

    def __post_init__(self) -> None:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", self.expected_by_local_time):
            raise OperationsValidationError(
                f"{self.expected_by_local_time!r} is not a 24-hour local time (HH:MM)"
            )
        if "/" not in self.timezone:
            raise OperationsValidationError(
                f"{self.timezone!r} is not an IANA timezone name. Store "
                "`America/New_York`, never an offset — an offset is wrong for half the "
                "year, and a feed that goes an hour late every March teaches people to "
                "ignore the alert."
            )
        if self.grace_minutes < 0 or self.escalate_after_minutes < 0:
            raise OperationsValidationError("minutes are not negative")
        if self.escalate_after_minutes <= self.grace_minutes:
            raise OperationsValidationError(
                f"escalation at {self.escalate_after_minutes} minutes fires inside the "
                f"{self.grace_minutes}-minute grace period — it would page somebody about "
                "a file that is not late yet."
            )


@dataclass(frozen=True)
class VolumeExpectation:
    """How big a normal delivery is, so an abnormal one is visible.

    The incident this exists for: a payer sent a roster with 40 members
    instead of 40,000, the pipeline loaded all 40 successfully, and the
    membership report was wrong for nine days. Every gate passed — because
    nobody had said what "normal" was.
    """

    minimum_records: int | None = None
    maximum_records: int | None = None
    typical_records: int | None = None
    tolerance_percent: int = 20

    def __post_init__(self) -> None:
        for name in ("minimum_records", "maximum_records", "typical_records"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise OperationsValidationError(f"{name} cannot be negative")
        if (
            self.minimum_records is not None
            and self.maximum_records is not None
            and self.minimum_records > self.maximum_records
        ):
            raise OperationsValidationError(
                "minimum_records exceeds maximum_records — no delivery could be normal"
            )
        if not 0 < self.tolerance_percent <= 100:
            raise OperationsValidationError(
                f"a tolerance of {self.tolerance_percent}% would alert on "
                + ("every delivery" if self.tolerance_percent <= 0 else "nothing")
            )

    @property
    def is_stated(self) -> bool:
        return any(
            v is not None
            for v in (self.minimum_records, self.maximum_records, self.typical_records)
        )

    def is_normal(self, records: int) -> bool:
        """Whether one delivery's row count is within what was declared."""
        if self.minimum_records is not None and records < self.minimum_records:
            return False
        if self.maximum_records is not None and records > self.maximum_records:
            return False
        if self.typical_records is not None:
            slack = self.typical_records * self.tolerance_percent / 100
            return abs(records - self.typical_records) <= slack
        return True


@dataclass(frozen=True)
class AlertTier:
    """One rung of the escalation ladder: after how long, and who."""

    after_minutes: int
    channel: AlertChannel
    notify: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.after_minutes < 0:
            raise OperationsValidationError("a tier cannot fire before the feed is late")
        if not self.notify:
            raise OperationsValidationError(
                f"the tier at {self.after_minutes} minutes notifies nobody — an escalation "
                "step with no recipient is a delay dressed up as a control"
            )


@dataclass(frozen=True)
class LinkedDocument:
    """A spec, a companion guide, a runbook — whatever a person needs to read.

    `reference` is DATA, not source: a URI here is as legitimate as
    `landing_path` is, and Law 1 is about what the code contains. What is
    refused is a reference carrying a CREDENTIAL, because a registry row is
    read by more people than any config file.
    """

    kind: DocumentKind
    label: str
    reference: str

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise OperationsValidationError("a document without a label is a link nobody opens")
        if not self.reference.strip():
            raise OperationsValidationError(f"{self.label}: a document needs a reference")
        if _CREDENTIALLED.search(self.reference):
            raise OperationsValidationError(
                f"{self.label}: that link carries a credential. Store the address without "
                "it — a registry row is read by more people than any config file, and a "
                "shared secret in one is a secret in everybody's browser history."
            )


@dataclass(frozen=True)
class FeedOperations:
    """The whole operational envelope. One aggregate, one form, one version.

    Every field is optional at construction, because a half-gathered feed must
    save. `readiness` is what says whether it can be operated.
    """

    source_id: str = ""
    direction: Direction = Direction.INBOUND
    delivery_method: DeliveryMethod = DeliveryMethod.SFTP
    #: A NAME the connection profile resolves. Never a host, never a path.
    endpoint_ref: str = ""
    owners: tuple[Owner, ...] = ()
    service_level: ServiceLevel | None = None
    volume: VolumeExpectation | None = None
    alert_chain: tuple[AlertTier, ...] = ()
    documents: tuple[LinkedDocument, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        if self.endpoint_ref and not _ENDPOINT_REF.fullmatch(self.endpoint_ref):
            raise OperationsValidationError(
                f"{self.endpoint_ref!r} looks like a location, not a name. `endpoint_ref` "
                "is the key the connection profile resolves — all environment difference "
                "lives in the profile, so a host here would make this row environment-"
                "specific and the registry un-promotable."
            )

        seen_roles: set[OwnerRole] = set()
        for owner in self.owners:
            if owner.role in seen_roles:
                raise OperationsValidationError(
                    f"two {owner.role.value} owners. Shared accountability is no "
                    "accountability — name one person per role."
                )
            seen_roles.add(owner.role)

        minutes = [tier.after_minutes for tier in self.alert_chain]
        if minutes != sorted(set(minutes)):
            raise OperationsValidationError(
                "the alert chain must escalate: tiers strictly increasing in "
                f"after_minutes, got {minutes}. Two tiers at the same minute is two "
                "pages for one event, and a chain that goes backwards is not a chain."
            )
        if len(self.alert_chain) > 1:
            recipients = {frozenset(tier.notify) for tier in self.alert_chain}
            if len(recipients) == 1:
                raise OperationsValidationError(
                    "every tier of this alert chain notifies the same people, which is "
                    "the same alert sent repeatedly rather than an escalation. Escalating "
                    "means reaching somebody who was not reached before."
                )

    # ── the checklist ────────────────────────────────────────────────────────
    def owner(self, role: OwnerRole) -> Owner | None:
        for candidate in self.owners:
            if candidate.role is role:
                return candidate
        return None

    def as_body(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "direction": self.direction.value,
            "delivery_method": self.delivery_method.value,
            "endpoint_ref": self.endpoint_ref,
            "owners": [
                {"role": o.role.value, "subject": o.subject, "display_name": o.display_name}
                for o in self.owners
            ],
            "service_level": (
                None
                if self.service_level is None
                else {
                    "expected_by_local_time": self.service_level.expected_by_local_time,
                    "timezone": self.service_level.timezone,
                    "calendar": self.service_level.calendar.value,
                    "grace_minutes": self.service_level.grace_minutes,
                    "escalate_after_minutes": self.service_level.escalate_after_minutes,
                }
            ),
            "volume": (
                None
                if self.volume is None
                else {
                    "minimum_records": self.volume.minimum_records,
                    "maximum_records": self.volume.maximum_records,
                    "typical_records": self.volume.typical_records,
                    "tolerance_percent": self.volume.tolerance_percent,
                }
            ),
            "alert_chain": [
                {
                    "after_minutes": t.after_minutes,
                    "channel": t.channel.value,
                    "notify": list(t.notify),
                }
                for t in self.alert_chain
            ],
            "documents": [
                {"kind": d.kind.value, "label": d.label, "reference": d.reference}
                for d in self.documents
            ],
            "notes": self.notes,
        }

    @classmethod
    def from_body(cls, raw: dict[str, Any] | None) -> Self:
        """Read an envelope back. An absent one is an EMPTY one, not an error.

        Every feed registered before this story has no `operations` key, and
        those feeds are not broken — they are feeds whose envelope nobody has
        filled in yet, which is exactly what `readiness` will tell them.
        """
        body = raw or {}
        sla = body.get("service_level")
        volume = body.get("volume")
        return cls(
            source_id=str(body.get("source_id", "")),
            direction=Direction(str(body.get("direction", Direction.INBOUND.value))),
            delivery_method=DeliveryMethod(
                str(body.get("delivery_method", DeliveryMethod.SFTP.value))
            ),
            endpoint_ref=str(body.get("endpoint_ref", "")),
            owners=tuple(
                Owner(
                    role=OwnerRole(str(o["role"])),
                    subject=str(o["subject"]),
                    display_name=str(o.get("display_name", "")),
                )
                for o in body.get("owners", ())
            ),
            service_level=(
                None
                if not sla
                else ServiceLevel(
                    expected_by_local_time=str(sla["expected_by_local_time"]),
                    timezone=str(sla["timezone"]),
                    calendar=DeliveryCalendar(
                        str(sla.get("calendar", DeliveryCalendar.BUSINESS_DAYS.value))
                    ),
                    grace_minutes=int(sla.get("grace_minutes", 30)),
                    escalate_after_minutes=int(sla.get("escalate_after_minutes", 120)),
                )
            ),
            volume=(
                None
                if not volume
                else VolumeExpectation(
                    minimum_records=volume.get("minimum_records"),
                    maximum_records=volume.get("maximum_records"),
                    typical_records=volume.get("typical_records"),
                    tolerance_percent=int(volume.get("tolerance_percent", 20)),
                )
            ),
            alert_chain=tuple(
                AlertTier(
                    after_minutes=int(t["after_minutes"]),
                    channel=AlertChannel(str(t["channel"])),
                    notify=tuple(t.get("notify", ())),
                )
                for t in body.get("alert_chain", ())
            ),
            documents=tuple(
                LinkedDocument(
                    kind=DocumentKind(str(d["kind"])),
                    label=str(d["label"]),
                    reference=str(d["reference"]),
                )
                for d in body.get("documents", ())
            ),
            notes=str(body.get("notes", "")),
        )


# ── the plain-language activation checklist ──────────────────────────────────
@dataclass(frozen=True)
class ChecklistItem:
    """One thing that must be true before a feed can be operated.

    THREE STRINGS, NOT ONE. `question` is what a person is being asked;
    `why_it_matters` is the consequence of leaving it blank; `how_to_fix` is
    the next action. A checklist that says only "owner is required" gets an
    owner of `data@company.com` typed into it — the point of the other two
    strings is that somebody reads them and does the real thing instead.
    """

    key: str
    question: str
    satisfied: bool
    why_it_matters: str
    how_to_fix: str


@dataclass(frozen=True)
class Readiness:
    """Whether this feed can be operated, and what is missing if not."""

    feed_id: str
    items: tuple[ChecklistItem, ...] = field(default_factory=tuple)

    @property
    def outstanding(self) -> tuple[ChecklistItem, ...]:
        return tuple(item for item in self.items if not item.satisfied)

    @property
    def is_ready(self) -> bool:
        return not self.outstanding

    def explain(self, feed_id: str = "") -> str:
        """The refusal text a person actually reads. Plain language, and it
        says what to DO — a refusal that only names a field is a puzzle."""
        target = feed_id or self.feed_id
        if self.is_ready:
            return f"{target} is ready to be operated."
        lines = [
            f"{target} cannot be activated yet — {len(self.outstanding)} thing(s) are still "
            "missing, and each one is something somebody needs at 3am:",
            "",
        ]
        for item in self.outstanding:
            lines.append(f"  • {item.question}")
            lines.append(f"    Why it matters: {item.why_it_matters}")
            lines.append(f"    To fix: {item.how_to_fix}")
        return "\n".join(lines)


def readiness(feed_id: str, operations: FeedOperations) -> Readiness:
    """Compute the checklist. Pure, and the same function the screen calls.

    ONE function serves the form's live checklist and the lifecycle's refusal,
    which is what stops a screen showing green while the submit button returns
    403 — the classic shape of a validation rule implemented twice.
    """
    automated = operations.delivery_method.is_automated
    sla = operations.service_level
    items = [
        ChecklistItem(
            key="business_owner",
            question="Who in the business owns this feed?",
            satisfied=operations.owner(OwnerRole.BUSINESS) is not None,
            why_it_matters=(
                "When a payer sends half a roster, somebody has to decide whether to load "
                "it. That is a business call, not an engineering one."
            ),
            how_to_fix="Name a person — not a team address — as the business owner.",
        ),
        ChecklistItem(
            key="technical_owner",
            question="Who operates this feed?",
            satisfied=operations.owner(OwnerRole.TECHNICAL) is not None,
            why_it_matters=(
                "A feed with no technical owner has nobody who can read the error, and "
                "nobody the alert can reach."
            ),
            how_to_fix="Name the engineer accountable for this feed running.",
        ),
        ChecklistItem(
            key="arrival_sla",
            question="When is this feed due, and in which timezone?",
            satisfied=sla is not None or not automated,
            why_it_matters=(
                "Without a due time nothing can be Missing — the platform can only say a "
                "file has not arrived, which it can say about every file that ever will."
            ),
            how_to_fix=(
                "Set the local time it is expected by, its IANA timezone, and the calendar "
                "of days it is expected on."
            ),
        ),
        ChecklistItem(
            key="calendar",
            question="Which days should this feed arrive on?",
            satisfied=sla is None or sla.calendar.has_a_predictable_day or not automated,
            why_it_matters=(
                "A roster due every business day is not late on Saturday. Alerting on days "
                "nothing was expected is how a team learns to ignore alerts."
            ),
            how_to_fix="Choose the delivery calendar that matches the payer's schedule.",
        ),
        ChecklistItem(
            key="alert_chain",
            question="Who is told when it is late, and who is told next?",
            satisfied=bool(operations.alert_chain) or not automated,
            why_it_matters=(
                "An SLA nobody is told about is a note in a database. The second tier is "
                "the one that matters — it is what happens when the first person is asleep."
            ),
            how_to_fix="Add at least one escalation tier with a real recipient.",
        ),
        ChecklistItem(
            key="expected_volume",
            question="How big is a normal delivery?",
            satisfied=operations.volume is not None and operations.volume.is_stated,
            why_it_matters=(
                "A roster of 40 members instead of 40,000 passes every gate and loads "
                "cleanly. Nine days of a wrong membership report later, somebody notices."
            ),
            how_to_fix="State a typical row count, or a minimum and maximum.",
        ),
        ChecklistItem(
            key="endpoint",
            question="Where does this feed come from?",
            satisfied=bool(operations.endpoint_ref) or not automated,
            why_it_matters=(
                "The platform cannot collect a file from an unnamed source, and a manual "
                "workaround is how a feed ends up depending on one person's laptop."
            ),
            how_to_fix=(
                "Give the connection profile's name for this endpoint — the name, never the host."
            ),
        ),
        ChecklistItem(
            key="source",
            question="Which source system does this feed belong to?",
            satisfied=bool(operations.source_id),
            why_it_matters=(
                "Every question worth asking is asked per source: what did Centene send "
                "us this month, whose feeds are late, which payer to ring."
            ),
            how_to_fix="Link this feed to a registered source.",
        ),
    ]
    return Readiness(feed_id=feed_id, items=tuple(items))


def readiness_of(obj: GovernedObject) -> Readiness:
    """The checklist for a stored feed. Non-feeds are trivially ready.

    Total over `ObjectType` on purpose: `core.lifecycle` calls this for every
    submission, and a version that raised on a contract would make the
    lifecycle's own behaviour depend on which type happened to be passed.
    """
    if obj.object_type is not ObjectType.FEED:
        return Readiness(feed_id=obj.object_id)
    return readiness(obj.object_id, FeedOperations.from_body(obj.body.get("operations")))
