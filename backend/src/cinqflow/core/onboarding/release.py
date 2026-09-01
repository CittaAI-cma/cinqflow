"""CF-V1-E4-03 — two signatures, a schedule that starts at publication, and
the journey as one story.

    "Require both business and technical approval before publication, each
     seeing the evidence pack."
    "Activate scheduling only at publication — nothing runs on a schedule
     before."
    "Record the entire journey (who drafted, tested, approved, published, when)
     as one narrative view."
    "Given the BA edits a mapping after the end-to-end test, when she submits,
     then submission is blocked: the evidence no longer matches the
     configuration — staleness is caught mechanically."
    — CF-V1-E4-03

WHY A SECOND OBJECT AND NOT A SECOND FIELD. `core.model.governed` already
carries `approved_by` — one approver, one timestamp — and that is correct for
every object type in the platform. Publication of a feed is the one act that
needs two, and the wrong fix is a nullable `second_approver` column on
GovernedObject: every other object type would then carry a field that is always
null, the lifecycle would grow a branch for the type that fills it, and the two
universal negatives would have to be re-checked in that branch.

So the dual signature lives HERE, as a packet the publish path consults, and
the lifecycle is untouched. `core.lifecycle.APPROVAL_ROUTING[FEED]` already
names both roles as publishers; this says both must actually sign.

EACH SIGNATURE RECORDS THE FINGERPRINT IT SAW. "Each seeing the evidence pack"
is not satisfied by both approvers having access to a pack — it is satisfied by
both having signed for the SAME configuration. A business approver who signed
on Tuesday's pack and a technical approver who signed on Thursday's, with a
mapping edited between them, have approved two different feeds. `Signature`
carries the fingerprint, and `ReleasePacket.signatures_agree` is the check.

STALENESS IS CAUGHT AT SUBMISSION, MECHANICALLY. `refuse_stale_evidence`
compares the pack's fingerprint to the configuration's, and both come from
`core.onboarding.evidence.configuration_fingerprint` — the same function, so
there is no second opinion about what "changed" means.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum, unique

from cinqflow.core.model.governed import (
    Actor,
    AuditEntry,
    GovernedObject,
    LifecycleState,
    LifecycleViolationError,
    ObjectType,
    SelfApprovalError,
)
from cinqflow.core.model.identity import Role
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.onboarding import Wizard
from cinqflow.core.onboarding.evidence import EvidencePack


class ReleaseError(LifecycleViolationError):
    """A publication that will not happen, and why."""


class StaleEvidenceError(ReleaseError):
    """The configuration changed after the evidence was produced.

        "Allow publication with expired or superseded evidence (if the config
         changed after testing, the test must rerun)." — the don't

    Not a warning. A pack describes a configuration; when the configuration
    moves, the pack is evidence about something that is no longer being
    approved, and there is no amount of reviewer diligence that fixes that.
    """


class IncompleteSignatureError(ReleaseError):
    """Publication attempted without both signatures."""


# ── the two signatures ───────────────────────────────────────────────────────
@unique
class Approval(StrEnum):
    """The two lanes, named for what each one is actually judging.

    BUSINESS asks "is this the data we meant, and are the rules right?";
    TECHNICAL asks "will this run, and does it belong on the platform?". They
    are different questions, which is why one person holding both roles still
    cannot supply both signatures — see `ReleasePacket.sign`.
    """

    BUSINESS = "business"
    TECHNICAL = "technical"

    @property
    def role(self) -> Role:
        return {
            Approval.BUSINESS: Role.BUSINESS_APPROVER,
            Approval.TECHNICAL: Role.PLATFORM_ENGINEER,
        }[self]

    @property
    def question(self) -> str:
        return {
            Approval.BUSINESS: (
                "Is this the data we meant, mapped the way the business reads it, with the "
                "rules that matter?"
            ),
            Approval.TECHNICAL: (
                "Will this run, recover and reconcile — and is it configured the way this "
                "platform expects?"
            ),
        }[self]


@dataclass(frozen=True)
class Signature:
    """One approver's act, and the configuration they were looking at."""

    approval: Approval
    actor: Actor
    signed_ts: datetime
    #: The evidence pack's fingerprint at the moment of signing. See the module
    #: docstring: two signatures on two configurations are not two approvals of
    #: one feed.
    evidence_fingerprint: str
    comment: str = ""

    def __post_init__(self) -> None:
        if self.actor.actor_type is not ActorType.HUMAN:
            raise ReleaseError(
                f"{self.actor.subject} is a {self.actor.actor_type.value} actor and cannot "
                "sign a release. Agents propose; humans dispose."
            )
        if not self.comment.strip():
            raise ReleaseError(
                "a signature must state its rationale — it becomes part of the audit "
                "record, and an approval nobody explained is the rubber stamp this "
                "platform refuses to make available"
            )


@dataclass(frozen=True)
class ReleasePacket:
    """One feed's publication, and who has signed for it.

    Immutable: `sign` returns a new packet, so a partially-signed release
    cannot be mutated into a fully-signed one by anything holding a reference
    to it.
    """

    feed_id: str
    feed_version: int
    author_subject: str
    evidence_fingerprint: str
    signatures: tuple[Signature, ...] = ()

    def signature(self, approval: Approval) -> Signature | None:
        for candidate in self.signatures:
            if candidate.approval is approval:
                return candidate
        return None

    @property
    def outstanding(self) -> tuple[Approval, ...]:
        return tuple(a for a in Approval if self.signature(a) is None)

    @property
    def is_complete(self) -> bool:
        return not self.outstanding and self.signatures_agree

    @property
    def signatures_agree(self) -> bool:
        """Both approvers signed for the SAME configuration."""
        seen = {s.evidence_fingerprint for s in self.signatures}
        return len(seen) <= 1

    def sign(
        self,
        approval: Approval,
        *,
        actor: Actor,
        roles: frozenset[Role],
        comment: str,
        evidence: EvidencePack,
        now: datetime | None = None,
    ) -> tuple[ReleasePacket, AuditEntry]:
        """Add one signature, or refuse — four gates, in the order that gives
        the signer the message they can act on.

        THE THIRD GATE IS THE ONE THAT IS EASY TO MISS: a person who holds both
        the business and the platform-engineer role may supply only ONE of the
        two signatures. Two signatures from one person is one approval wearing
        two hats, and the whole point of the pair is that two people looked.
        """
        stamp = now or datetime.now(UTC)

        if actor.subject == self.author_subject:
            raise SelfApprovalError(
                f"{actor.subject} authored this onboarding and may not approve it. "
                "The author of a change never approves it."
            )
        if approval.role not in roles:
            held = ", ".join(sorted(r.value for r in roles)) or "no role"
            raise ReleaseError(
                f"the {approval.value} signature is held by {approval.role.value}; the "
                f"caller holds {held}."
            )
        if any(s.actor.subject == actor.subject for s in self.signatures):
            raise ReleaseError(
                f"{actor.subject} has already signed this release. Two signatures from one "
                "person is one approval wearing two hats — the pair exists so that two "
                "people looked."
            )
        if self.signature(approval) is not None:
            raise ReleaseError(f"the {approval.value} signature is already recorded")
        if evidence.is_stale_for(self.evidence_fingerprint):
            raise StaleEvidenceError(
                "the evidence pack in front of this approver is not the one this release "
                "was submitted with. Re-run the end-to-end test and resubmit."
            )

        signature = Signature(
            approval=approval,
            actor=actor,
            signed_ts=stamp,
            evidence_fingerprint=evidence.fingerprint,
            comment=comment,
        )
        entry = AuditEntry(
            object_type=ObjectType.FEED,
            object_id=self.feed_id,
            version=self.feed_version,
            action=f"signature:{approval.value}",
            actor=actor,
            occurred_ts=stamp,
            detail=comment.strip(),
        )
        return replace(self, signatures=(*self.signatures, signature)), entry

    def explain(self) -> str:
        if self.is_complete:
            names = ", ".join(
                f"{s.approval.value} by {s.actor.display_name or s.actor.subject}"
                for s in self.signatures
            )
            return f"{self.feed_id} is signed off — {names}."
        if not self.signatures_agree:
            return (
                f"{self.feed_id}: the two approvers signed different configurations. "
                "Re-run the end-to-end test and collect both signatures on one pack."
            )
        waiting = ", ".join(a.value for a in self.outstanding)
        return f"{self.feed_id} is waiting for the {waiting} signature."


# ── the gates ────────────────────────────────────────────────────────────────
def refuse_stale_evidence(pack: EvidencePack, configuration: str) -> None:
    """The wave's exit criterion, as one function.

        "…then edits a mapping post-test and watches submission blocked for
         stale evidence."

    Called by the submission path AND by every signature, because the edit can
    land between the two — and a gate that only fires at submission would let a
    mapping change slip in after the business approver signed.
    """
    if pack.is_stale_for(configuration):
        raise StaleEvidenceError(
            "the configuration changed after the last end-to-end test. The pack describes "
            f"`{pack.fingerprint}` and this feed is now `{configuration}` — so the evidence "
            "vouches for something other than what is being approved. Re-run the test; it "
            "takes minutes."
        )


def refuse_unready(view: Wizard) -> None:
    """Nothing is submitted with a red checklist.

    Reuses the wizard's own obstacle list rather than restating the rules,
    which is what stops the screen showing green while submit returns 403 —
    the classic shape of a validation rule implemented twice.
    """
    if view.is_publishable:
        return
    raise ReleaseError(view.explain())


def submit_for_release(
    feed: GovernedObject,
    *,
    view: Wizard,
    pack: EvidencePack,
    configuration: str,
    actor: Actor,
    now: datetime | None = None,
) -> tuple[GovernedObject, AuditEntry, ReleasePacket]:
    """Step 5. Checklist first, then staleness, then the lifecycle move.

    The ORDER is chosen for the message: telling a BA her evidence is stale
    when her real problem is two unmapped fields would send her to re-run a
    test that will not help.
    """
    refuse_unready(view)
    refuse_stale_evidence(pack, configuration)

    from cinqflow.core.lifecycle import submit as lifecycle_submit

    moved, entry = lifecycle_submit(
        feed,
        actor=actor,
        comment=f"Submitted for release with evidence {pack.fingerprint}",
        now=now,
    )
    packet = ReleasePacket(
        feed_id=feed.object_id,
        feed_version=feed.version,
        author_subject=feed.created_by.subject,
        evidence_fingerprint=pack.fingerprint,
    )
    return moved, entry, packet


def publish_release(
    feed: GovernedObject,
    *,
    packet: ReleasePacket,
    actor: Actor,
    roles: frozenset[Role],
    now: datetime | None = None,
) -> tuple[GovernedObject, AuditEntry]:
    """Approved -> Published, but only with both signatures on one pack.

    The lifecycle's own publish still runs — routing, the named approver, the
    state machine — and this adds the one thing the generic path cannot know:
    that a FEED needs two.
    """
    if not packet.is_complete:
        raise IncompleteSignatureError(packet.explain())

    from cinqflow.core.lifecycle import publish as lifecycle_publish

    return lifecycle_publish(feed, actor=actor, roles=roles, now=now)


# ── scheduling starts at publication, and not before ─────────────────────────
def schedule_is_active(feed: GovernedObject) -> bool:
    """Nothing runs on a schedule before publication.

    A property of the FEED's lifecycle state, asked here so that the
    orchestration wiring has one place to consult rather than a convention.
    `core.registry.wave0` already says the engine reads published metadata;
    this says the SCHEDULER does too, which is the same rule applied to the
    thing that starts runs rather than to the thing that runs them.
    """
    return feed.lifecycle_state is LifecycleState.PUBLISHED


def registrable_schedule(feed: GovernedObject) -> str | None:
    """The cron to register, or None while the feed is not published.

    Returning None rather than raising: the registrar sweeps every feed, and a
    draft feed is not an error — it is a feed whose schedule has not started
    yet, which is exactly what the story asks for.
    """
    if not schedule_is_active(feed):
        return None
    cron = str(feed.body.get("schedule_cron") or "").strip()
    return cron or None


# ── the journey, as one story ────────────────────────────────────────────────
@dataclass(frozen=True)
class Chapter:
    """One event in the feed's life, in the reader's language."""

    occurred_ts: datetime
    who: str
    what: str
    object_type: ObjectType
    detail: str = ""


#: How each audit action reads in the narrative. A TABLE, so a new governance
#: act appears in the story by adding a row — and the completeness test makes
#: an omission visible instead of letting an act happen invisibly.
_CHAPTER_WORDS: dict[str, str] = {
    "transition:pending_review": "submitted it for review",
    "transition:approved": "approved it",
    "transition:rejected": "rejected it",
    "transition:published": "published it",
    "transition:retired": "retired it",
    "transition:draft": "sent it back for changes",
    "signature:business": "signed the business approval",
    "signature:technical": "signed the technical approval",
    "suspension:paused": "paused it",
    "suspension:resumed": "resumed it",
    "evidence:produced": "ran the end-to-end test",
}


def narrative(entries: Sequence[AuditEntry]) -> tuple[Chapter, ...]:
    """The whole journey, oldest first, in one list.

        "Record the entire journey (who drafted, tested, approved, published,
         when) as one narrative view."

    Built from the AUDIT LEDGER rather than from a parallel history table, so
    there is nothing to keep in step and nothing that can be written to
    without also being auditable. An act that produced no audit entry does not
    appear in the story — which is correct, because as far as this platform is
    concerned it did not happen.

    An action with no phrasing is rendered VERBATIM rather than dropped. A
    story that silently omits the one act nobody wrote a sentence for is worse
    than one that reads a little awkwardly.
    """
    return tuple(
        Chapter(
            occurred_ts=entry.occurred_ts,
            who=entry.actor.display_name or entry.actor.subject,
            what=_CHAPTER_WORDS.get(entry.action, entry.action),
            object_type=entry.object_type,
            detail=entry.detail,
        )
        for entry in sorted(entries, key=lambda e: e.occurred_ts)
    )


def render_narrative(chapters: Sequence[Chapter]) -> str:
    """The story, as the approval trail reads end to end."""
    if not chapters:
        return "Nothing has happened to this feed yet."
    return "\n".join(
        f"{chapter.occurred_ts.date().isoformat()} — {chapter.who} "
        f"{chapter.what} ({chapter.object_type.value})"
        + (f": {chapter.detail}" if chapter.detail else "")
        for chapter in chapters
    )
