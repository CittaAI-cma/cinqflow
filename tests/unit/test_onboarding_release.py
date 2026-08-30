"""CF-V1-E4-03 — two signatures, a schedule that starts at publication, and
the journey as one story.

    "Given an onboarding with a green checklist and evidence pack, when the BA
     submits and both approvers accept, then the feed publishes, its first
     scheduled run appears on the operations screens, and the onboarding trail
     reads end to end like a story."
    "Given the BA edits a mapping after the end-to-end test, when she submits,
     then submission is blocked: the evidence no longer matches the
     configuration, and the wizard asks for one more test run."
    "Given an approval is attempted by the author or an unauthorized role, when
     they approve, then the system blocks it and records the attempt."
    — CF-V1-E4-03
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cinqflow.core.compiler.execute import ExecutionResult
from cinqflow.core.model.governed import (
    Actor,
    AuditEntry,
    GovernedObject,
    LifecycleState,
    ObjectType,
    SelfApprovalError,
)
from cinqflow.core.model.identity import Role
from cinqflow.core.model.vocabulary import ActorType, Layer
from cinqflow.core.onboarding import OnboardingInputs, wizard
from cinqflow.core.onboarding.evidence import build_pack, configuration_fingerprint
from cinqflow.core.onboarding.release import (
    Approval,
    IncompleteSignatureError,
    ReleaseError,
    ReleasePacket,
    StaleEvidenceError,
    narrative,
    publish_release,
    refuse_stale_evidence,
    registrable_schedule,
    render_narrative,
    schedule_is_active,
    submit_for_release,
)
from cinqflow.core.recon import StageReconciliation

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
FEED = "centene-medicare-roster"

BA = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Dana")
BUSINESS = Actor(
    subject="dev-business@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Ravi"
)
TECHNICAL = Actor(
    subject="dev-platform@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Mei"
)
AGENT = Actor(subject="mapping-suggestion", actor_type=ActorType.AI)

BUSINESS_ROLES = frozenset({Role.BUSINESS_APPROVER})
TECHNICAL_ROLES = frozenset({Role.PLATFORM_ENGINEER})


def contract(body: dict | None = None) -> GovernedObject:
    return GovernedObject(
        object_type=ObjectType.CONTRACT,
        object_id=FEED,
        version=1,
        lifecycle_state=LifecycleState.APPROVED,
        created_by=BA,
        created_ts=NOW,
        body=body or {"columns": []},
        approved_by=TECHNICAL,
        approved_ts=NOW,
    )


def pack(objects: list[GovernedObject]):
    return build_pack(
        feed_id=FEED,
        result=ExecutionResult(
            loaded=({"m": 1},),
            quarantined=(),
            reconciliation=StageReconciliation(
                batch_id="B-1", stage=Layer.SILVER_RAW, records_in=1, records_out=1
            ),
        ),
        objects=objects,
        now=NOW,
    )


def packet(fingerprint: str) -> ReleasePacket:
    return ReleasePacket(
        feed_id=FEED,
        feed_version=1,
        author_subject=BA.subject,
        evidence_fingerprint=fingerprint,
    )


# ── the two signatures ───────────────────────────────────────────────────────
def test_publication_needs_both_signatures() -> None:
    evidence = pack([contract()])
    signed, _ = packet(evidence.fingerprint).sign(
        Approval.BUSINESS,
        actor=BUSINESS,
        roles=BUSINESS_ROLES,
        comment="The mapping matches what we asked the payer for.",
        evidence=evidence,
        now=NOW,
    )
    assert not signed.is_complete
    assert signed.outstanding == (Approval.TECHNICAL,)
    assert "waiting for the technical signature" in signed.explain()

    both, _ = signed.sign(
        Approval.TECHNICAL,
        actor=TECHNICAL,
        roles=TECHNICAL_ROLES,
        comment="Recovers and reconciles on the sample.",
        evidence=evidence,
        now=NOW,
    )
    assert both.is_complete
    assert "signed off" in both.explain()


def test_one_person_holding_both_roles_still_supplies_only_one_signature() -> None:
    """Two signatures from one person is one approval wearing two hats, and the
    pair exists so that two people looked."""
    evidence = pack([contract()])
    both_roles = BUSINESS_ROLES | TECHNICAL_ROLES
    signed, _ = packet(evidence.fingerprint).sign(
        Approval.BUSINESS,
        actor=BUSINESS,
        roles=both_roles,
        comment="Business content is right.",
        evidence=evidence,
        now=NOW,
    )
    with pytest.raises(ReleaseError) as refused:
        signed.sign(
            Approval.TECHNICAL,
            actor=BUSINESS,
            roles=both_roles,
            comment="And technically fine.",
            evidence=evidence,
            now=NOW,
        )
    assert "two hats" in str(refused.value)


def test_the_author_may_not_sign_their_own_onboarding() -> None:
    evidence = pack([contract()])
    with pytest.raises(SelfApprovalError):
        packet(evidence.fingerprint).sign(
            Approval.BUSINESS,
            actor=BA,
            roles=BUSINESS_ROLES,
            comment="Looks good to me.",
            evidence=evidence,
            now=NOW,
        )


def test_a_role_that_does_not_hold_the_pen_is_refused() -> None:
    evidence = pack([contract()])
    with pytest.raises(ReleaseError) as refused:
        packet(evidence.fingerprint).sign(
            Approval.TECHNICAL,
            actor=BUSINESS,
            roles=BUSINESS_ROLES,
            comment="I'll sign the technical one too.",
            evidence=evidence,
            now=NOW,
        )
    assert "held by platform_engineer" in str(refused.value)


def test_an_agent_cannot_sign() -> None:
    from cinqflow.core.onboarding.release import Signature

    with pytest.raises(ReleaseError) as refused:
        Signature(
            approval=Approval.BUSINESS,
            actor=AGENT,
            signed_ts=NOW,
            evidence_fingerprint="sha256-x",
            comment="Confident.",
        )
    assert "Agents propose; humans dispose" in str(refused.value)


def test_a_signature_must_state_its_rationale() -> None:
    evidence = pack([contract()])
    with pytest.raises(ReleaseError) as refused:
        packet(evidence.fingerprint).sign(
            Approval.BUSINESS,
            actor=BUSINESS,
            roles=BUSINESS_ROLES,
            comment="   ",
            evidence=evidence,
            now=NOW,
        )
    assert "rubber stamp" in str(refused.value)


def test_two_approvers_who_signed_different_configurations_have_not_agreed() -> None:
    """A business approver who signed Tuesday's pack and a technical approver
    who signed Thursday's, with a mapping edited between them, have approved
    two different feeds."""
    from cinqflow.core.onboarding.release import Signature

    mixed = ReleasePacket(
        feed_id=FEED,
        feed_version=1,
        author_subject=BA.subject,
        evidence_fingerprint="sha256-a",
        signatures=(
            Signature(Approval.BUSINESS, BUSINESS, NOW, "sha256-a", "fine"),
            Signature(Approval.TECHNICAL, TECHNICAL, NOW, "sha256-b", "also fine"),
        ),
    )
    assert not mixed.signatures_agree
    assert not mixed.is_complete
    assert "different configurations" in mixed.explain()


def test_a_signature_is_refused_against_a_pack_the_release_was_not_submitted_with() -> None:
    submitted = pack([contract()])
    edited = pack([contract({"columns": [{"name": "changed"}]})])
    with pytest.raises(StaleEvidenceError):
        packet(submitted.fingerprint).sign(
            Approval.BUSINESS,
            actor=BUSINESS,
            roles=BUSINESS_ROLES,
            comment="Approving on the newer pack.",
            evidence=edited,
            now=NOW,
        )


# ── the exception: an edit after the test ────────────────────────────────────
def test_editing_a_mapping_after_the_test_blocks_submission() -> None:
    """THE WAVE'S EXIT CRITERION."""
    tested = pack([contract()])
    after_edit = configuration_fingerprint([contract({"columns": [{"name": "added"}]})])
    with pytest.raises(StaleEvidenceError) as refused:
        refuse_stale_evidence(tested, after_edit)
    assert "Re-run the test" in str(refused.value)


def test_a_configuration_that_did_not_change_keeps_its_evidence() -> None:
    tested = pack([contract()])
    unchanged = configuration_fingerprint([contract()])
    refuse_stale_evidence(tested, unchanged)


def test_submission_reports_the_checklist_before_it_reports_staleness() -> None:
    """Telling a BA her evidence is stale when her real problem is two unmapped
    fields would send her to re-run a test that will not help."""
    feed = GovernedObject(
        object_type=ObjectType.FEED,
        object_id=FEED,
        version=1,
        lifecycle_state=LifecycleState.DRAFT,
        created_by=BA,
        created_ts=NOW,
    )
    unready = wizard(OnboardingInputs(feed_id=FEED))
    with pytest.raises(ReleaseError) as refused:
        submit_for_release(
            feed,
            view=unready,
            pack=pack([contract()]),
            configuration="sha256-anything-else",
            actor=BA,
            now=NOW,
        )
    assert "Upload a sample file" in str(refused.value)


# ── publication ──────────────────────────────────────────────────────────────
def approved_feed() -> GovernedObject:
    return GovernedObject(
        object_type=ObjectType.FEED,
        object_id=FEED,
        version=1,
        lifecycle_state=LifecycleState.APPROVED,
        created_by=BA,
        created_ts=NOW,
        body={"schedule_cron": "0 6 1 * *"},
        approved_by=TECHNICAL,
        approved_ts=NOW,
    )


def test_publication_is_refused_with_one_signature() -> None:
    evidence = pack([contract()])
    half, _ = packet(evidence.fingerprint).sign(
        Approval.BUSINESS,
        actor=BUSINESS,
        roles=BUSINESS_ROLES,
        comment="Business content is right.",
        evidence=evidence,
        now=NOW,
    )
    with pytest.raises(IncompleteSignatureError):
        publish_release(
            approved_feed(), packet=half, actor=TECHNICAL, roles=TECHNICAL_ROLES, now=NOW
        )


def test_both_signatures_publish_the_feed() -> None:
    evidence = pack([contract()])
    signed, _ = packet(evidence.fingerprint).sign(
        Approval.BUSINESS,
        actor=BUSINESS,
        roles=BUSINESS_ROLES,
        comment="Right data.",
        evidence=evidence,
        now=NOW,
    )
    signed, _ = signed.sign(
        Approval.TECHNICAL,
        actor=TECHNICAL,
        roles=TECHNICAL_ROLES,
        comment="Runs clean.",
        evidence=evidence,
        now=NOW,
    )
    published, entry = publish_release(
        approved_feed(), packet=signed, actor=TECHNICAL, roles=TECHNICAL_ROLES, now=NOW
    )
    assert published.lifecycle_state is LifecycleState.PUBLISHED
    assert entry.to_state is LifecycleState.PUBLISHED


# ── scheduling starts at publication ─────────────────────────────────────────
def test_nothing_runs_on_a_schedule_before_publication() -> None:
    draft = GovernedObject(
        object_type=ObjectType.FEED,
        object_id=FEED,
        version=1,
        lifecycle_state=LifecycleState.DRAFT,
        created_by=BA,
        created_ts=NOW,
        body={"schedule_cron": "0 6 1 * *"},
    )
    assert not schedule_is_active(draft)
    assert registrable_schedule(draft) is None


def test_the_schedule_registers_the_moment_the_feed_publishes() -> None:
    published = GovernedObject(
        object_type=ObjectType.FEED,
        object_id=FEED,
        version=1,
        lifecycle_state=LifecycleState.PUBLISHED,
        created_by=BA,
        created_ts=NOW,
        body={"schedule_cron": "0 6 1 * *"},
        approved_by=TECHNICAL,
        approved_ts=NOW,
    )
    assert schedule_is_active(published)
    assert registrable_schedule(published) == "0 6 1 * *"


# ── the journey, as one story ────────────────────────────────────────────────
def entry(action: str, actor: Actor, minutes: int, detail: str = "") -> AuditEntry:
    return AuditEntry(
        object_type=ObjectType.FEED,
        object_id=FEED,
        version=1,
        action=action,
        actor=actor,
        occurred_ts=NOW + timedelta(minutes=minutes),
        detail=detail,
    )


def test_the_trail_reads_end_to_end_like_a_story() -> None:
    chapters = narrative(
        [
            entry("signature:technical", TECHNICAL, 40, "Runs clean."),
            entry("transition:pending_review", BA, 10),
            entry("evidence:produced", BA, 5),
            entry("signature:business", BUSINESS, 30, "Right data."),
            entry("transition:published", TECHNICAL, 50),
        ]
    )
    told = [c.what for c in chapters]
    assert told == [
        "ran the end-to-end test",
        "submitted it for review",
        "signed the business approval",
        "signed the technical approval",
        "published it",
    ]
    rendered = render_narrative(chapters)
    assert "Dana ran the end-to-end test" in rendered
    assert "Mei published it" in rendered


def test_an_act_with_no_phrasing_is_rendered_verbatim_rather_than_dropped() -> None:
    """A story that silently omits the one act nobody wrote a sentence for is
    worse than one that reads a little awkwardly."""
    chapters = narrative([entry("transition:something_new", BA, 1)])
    assert chapters[0].what == "transition:something_new"


def test_an_empty_trail_says_so() -> None:
    assert render_narrative(narrative([])) == "Nothing has happened to this feed yet."
