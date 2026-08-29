"""CF-V1-E5-03 — the classification precedence, and the refusals under it.

Pure. No model, no pins, no Presidio — `classify` takes the scrub evidence as
a dictionary precisely so that the whole precedence table is testable in
milliseconds. Which means the 100% recall property is exercised by every case
in this file rather than by one integration test that has to be run.

Organised as the module is: the five bases in precedence order, then the
asymmetry that defines the story, then the three things nothing may do.
"""

from __future__ import annotations

import pytest

from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.patterns import CodeSet
from cinqflow.core.phi import (
    Basis,
    PhiDowngradeRefusedError,
    PhiKind,
    ScrubEvidence,
    classify,
    masking_policy,
    merge_inference,
    reclassify,
)
from cinqflow.core.profiling import profile_bytes
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm

pytestmark = pytest.mark.unit

STEWARD = Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Ada")
ROBOT = Actor(subject="phi-detection", actor_type=ActorType.AI)

BG_004 = GlossaryTerm(
    glossary_id="BG-004",
    term="Member Date of Birth",
    definition="Date of birth of the member.",
    mapped_columns_original=("DOB", "Patient_dob"),
    mapped_columns_corrected=("Member_Date_Of_Birth",),
    is_phi=True,
)
BG_050 = GlossaryTerm(
    glossary_id="BG-050",
    term="Line of Business",
    definition="The product line a member is enrolled under.",
    mapped_columns_original=("LOB",),
    is_phi=False,
)
GLOSSARY = Glossary(terms=(BG_004, BG_050))

#: One file exercising every branch. `SSN` and `PROV_NPI` are decisive shapes,
#: `DOB` and `LOB` are glossary terms, `NOTES` is prose, and `SUBSCR_REL_CD` is
#: the column nothing identifies — which is the one the story is about.
ROSTER = (
    b"MemberID,DOB,LOB,SSN,PROV_NPI,NOTES,SUBSCR_REL_CD\n"
    b"MBR000001,19360201,MEDICAID,078-05-1120,1234567893,"
    b"Member called about a prior authorisation for an MRI scan,01\n"
    b"MBR000002,19370302,MEDICARE,219-09-9999,1841293990,"
    b"Left a voicemail about the annual wellness visit booking,02\n"
    b"MBR000003,19380403,DUAL,457-55-5462,1215930367,"
    b"Asked for a replacement card to be posted to the home,01\n"
)


@pytest.fixture
def profile():  # type: ignore[no-untyped-def]
    return profile_bytes(ROSTER, file_format="csv", source_key="k", source_fingerprint="sha-a")


def _classify(profile, scrub=None):  # type: ignore[no-untyped-def]
    return classify(profile, feed_id="fidelis-downstate-roster", glossary=GLOSSARY, scrub=scrub)


# ── basis 1 · the glossary, first and alone ──────────────────────────────────


def test_a_glossary_flagged_column_is_protected_before_anything_else_is_looked_at(profile) -> None:  # type: ignore[no-untyped-def]
    """ "100% recall on glossary-flagged PHI."

    Consulted FIRST, which is what makes recall a property of the branch order
    rather than a number somebody measured afterwards.
    """
    dob = _classify(profile).column("DOB")
    assert dob is not None
    assert dob.is_phi
    assert dob.basis is Basis.GLOSSARY
    assert dob.phi_kind is PhiKind.DATE
    assert dob.glossary_id == "BG-004"
    assert "BG-004" in dob.rationale


def test_a_glossary_term_that_is_not_phi_settles_the_column_too(profile) -> None:  # type: ignore[no-untyped-def]
    """The client's own analysts decided `LOB` is not protected. The platform
    does not second-guess them by precaution — that would flag the whole file."""
    lob = _classify(profile).column("LOB")
    assert lob is not None
    assert not lob.is_phi
    assert lob.basis is Basis.GLOSSARY
    assert lob.settled


def test_two_terms_claiming_one_column_protect_it_and_ask_a_steward() -> None:
    """A real ambiguity in the client's own glossary, read the safe way.

    One term flags PHI and the other does not. Picking either silently would
    decide a business meaning on somebody's behalf, so the column is protected
    AND routed to a steward.
    """
    ambiguous = Glossary(
        terms=(
            GlossaryTerm(
                glossary_id="BG-100",
                term="Member Identifier",
                definition="d",
                mapped_columns_original=("ID",),
                is_phi=True,
            ),
            GlossaryTerm(
                glossary_id="BG-101",
                term="Internal Row Identifier",
                definition="d",
                mapped_columns_original=("ID",),
                is_phi=False,
            ),
        )
    )
    profile = profile_bytes(b"ID\nA1\nB2\n", file_format="csv", source_fingerprint="f")
    column = classify(profile, feed_id="f", glossary=ambiguous).column("ID")
    assert column is not None
    assert column.is_phi, "if either term flags PHI, the column is protected"
    assert column.needs_steward_review
    assert "BG-100" in column.rationale and "BG-101" in column.rationale


# ── basis 2 · computation ────────────────────────────────────────────────────


def test_a_decisive_identifier_shape_protects_the_column_by_arithmetic(profile) -> None:  # type: ignore[no-untyped-def]
    ssn = _classify(profile).column("SSN")
    assert ssn is not None
    assert ssn.is_phi
    assert ssn.basis is Basis.COMPUTATION
    assert ssn.phi_kind is PhiKind.SSN
    assert "3 populated values" in ssn.rationale


def test_a_decisive_code_set_is_named_and_left_unprotected(profile) -> None:  # type: ignore[no-untyped-def]
    """Every value passes the NPI checksum, so the column is a provider
    identifier — and a provider is not the member whose record this is.

    Masking this column would break every provider report the platform exists
    to produce, so "not PHI" here is a decision with a consequence, not a
    default.
    """
    npi = _classify(profile).column("PROV_NPI")
    assert npi is not None
    assert not npi.is_phi
    assert npi.code_set is CodeSet.NPI
    assert npi.basis is Basis.COMPUTATION
    assert npi.settled


# ── basis 3 · the scrub ──────────────────────────────────────────────────────


def test_the_scrubber_can_raise_a_flag_on_a_column_nothing_else_identified(profile) -> None:  # type: ignore[no-untyped-def]
    scrub = {
        "SUBSCR_REL_CD": ScrubEvidence(
            entities=("PERSON",), values_scanned=10, values_with_entities=9
        )
    }
    column = _classify(profile, scrub).column("SUBSCR_REL_CD")
    assert column is not None
    assert column.is_phi
    assert column.basis is Basis.SCRUB
    assert column.phi_kind is PhiKind.NAME


def test_one_stray_scrub_hit_is_noise_and_does_not_settle_the_column(profile) -> None:  # type: ignore[no-untyped-def]
    """Below the share floor the entities are evidence, not a finding — the
    column stays an open question rather than becoming a claim."""
    scrub = {
        "SUBSCR_REL_CD": ScrubEvidence(
            entities=("PERSON",), values_scanned=10, values_with_entities=1
        )
    }
    column = _classify(profile, scrub).column("SUBSCR_REL_CD")
    assert column is not None
    assert column.basis is Basis.PRECAUTION
    assert column.is_phi, "still protected — it just was not the scrubber that decided"


def test_an_unknown_scrubber_entity_still_counts_as_evidence(profile) -> None:  # type: ignore[no-untyped-def]
    """The exclusion-list decision, as a test.

    `MEMBER_ID` is emitted by the mock adapter and appears in no Presidio
    catalogue. An allow list of PHI entity names would silently ignore it, and
    recall would drop the day a recogniser was added. Every named entity
    counts unless it is explicitly excluded.
    """
    scrub = {
        "SUBSCR_REL_CD": ScrubEvidence(
            entities=("MEMBER_ID",), values_scanned=10, values_with_entities=10
        )
    }
    column = _classify(profile, scrub).column("SUBSCR_REL_CD")
    assert column is not None
    assert column.basis is Basis.SCRUB


def test_a_url_alone_is_not_treated_as_identifying(profile) -> None:  # type: ignore[no-untyped-def]
    scrub = {
        "SUBSCR_REL_CD": ScrubEvidence(
            entities=("URL",), values_scanned=10, values_with_entities=10
        )
    }
    column = _classify(profile, scrub).column("SUBSCR_REL_CD")
    assert column is not None
    assert column.basis is Basis.PRECAUTION, "excluded entity — falls through to precaution"


# ── basis 5 · precaution, and the asymmetry that defines the story ───────────


def test_free_text_is_protected_until_a_steward_decides(profile) -> None:  # type: ignore[no-untyped-def]
    """ "free-text 'notes' treated PHI until steward decides"

    And note what the platform does NOT do: ask the model about it. The answer
    is already the safe one and a model cannot make it safer, so the question
    goes to a steward instead.
    """
    notes = _classify(profile).column("NOTES")
    assert notes is not None
    assert notes.is_phi
    assert notes.basis is Basis.PRECAUTION
    assert notes.phi_kind is PhiKind.FREE_TEXT
    assert notes.needs_steward_review
    assert notes.is_protected_without_being_identified


def test_a_column_nothing_identifies_is_protected_and_says_so(profile) -> None:  # type: ignore[no-untyped-def]
    """THE ASYMMETRY. CF-V1-E5-02 would write "needs your input" and leave the
    field untyped; this story protects the column and tells a steward.

    Both are the safe answer to "we do not know". They differ because a field
    typed wrongly is caught by the next load and a field unmasked wrongly is a
    disclosure that cannot be recalled.
    """
    column = _classify(profile).column("SUBSCR_REL_CD")
    assert column is not None
    assert column.is_phi
    assert column.basis is Basis.PRECAUTION
    assert column.confidence == 0.0
    assert column.needs_steward_review
    assert not column.settled, "which is why it is the model's question"


def test_a_short_enumeration_of_longish_labels_is_not_free_text() -> None:
    """The bound that stops `PRIMARY CARE PHYSICIAN` reading as prose.

    Without the distinct-ratio test, any column of long-ish category labels
    becomes free text and the whole file drowns in precautionary flags.
    """
    rows = b"SPECIALTY\n" + (b"PRIMARY CARE PHYSICIAN AND FAMILY MEDICINE PRACTICE\n" * 20)
    profile = profile_bytes(rows, file_format="csv", source_fingerprint="f")
    column = profile.column("SPECIALTY")
    assert column is not None
    assert column.max_length > 40, "long enough to look like prose"
    assert not column.is_free_text, "but it repeats, so it is a category"


# ── what nothing may do ──────────────────────────────────────────────────────


def test_a_model_may_not_clear_a_flag_and_the_attempt_is_returned(profile) -> None:  # type: ignore[no-untyped-def]
    """ "downgrade-by-AI refused" — and the refusal reaches the review screen
    rather than a log file, because it is a governance event."""
    column = _classify(profile).column("SUBSCR_REL_CD")
    assert column is not None
    merged, refusals = merge_inference(
        column, {"is_phi": False, "confidence": 0.99}, confidence_floor=0.6
    )
    assert merged.is_phi, "the column stays protected"
    assert merged.needs_steward_review
    assert len(refusals) == 1
    assert "Refused" in refusals[0] and "steward" in refusals[0]


def test_a_model_answer_about_a_settled_column_is_discarded(profile) -> None:  # type: ignore[no-untyped-def]
    """Computed evidence is not up for revision — but agreeing with it is not
    a governance event, so only a CONTRADICTION is recorded."""
    npi = _classify(profile).column("PROV_NPI")
    assert npi is not None

    agreed, quiet = merge_inference(npi, {"is_phi": False, "confidence": 0.9}, confidence_floor=0.6)
    assert agreed == npi and quiet == (), "agreement is not a refusal"

    _, noisy = merge_inference(npi, {"is_phi": True, "confidence": 0.9}, confidence_floor=0.6)
    assert len(noisy) == 1 and "already settled" in noisy[0]


def test_a_model_may_raise_a_flag_but_it_still_reaches_a_steward(profile) -> None:  # type: ignore[no-untyped-def]
    column = _classify(profile).column("SUBSCR_REL_CD")
    assert column is not None
    merged, refusals = merge_inference(
        column,
        {"is_phi": True, "phi_kind": "member_id", "confidence": 0.9, "rationale": "a member link"},
        confidence_floor=0.6,
    )
    assert refusals == ()
    assert merged.basis is Basis.INFERENCE
    assert merged.phi_kind is PhiKind.MEMBER_ID
    assert merged.needs_steward_review, "raised by a model, so a person still confirms it"


def test_low_confidence_never_lowers_protection(profile) -> None:  # type: ignore[no-untyped-def]
    """The floor costs a steward's attention and never a flag. Contrast
    CF-V1-E5-02, where the same floor withholds a TYPE — there the safe move
    is to say nothing, here it is to keep protecting."""
    column = _classify(profile).column("SUBSCR_REL_CD")
    assert column is not None
    merged, _ = merge_inference(
        column, {"is_phi": True, "confidence": 0.1, "rationale": "a guess"}, confidence_floor=0.6
    )
    assert merged.is_phi
    assert merged.needs_steward_review
    assert "below the platform's floor" in merged.rationale


def test_an_agent_cannot_reclassify_at_all(profile) -> None:  # type: ignore[no-untyped-def]
    """Refused on the ACTOR TYPE rather than on a permission.

    A role can be misconfigured; an actor type cannot be mistaken for one.
    """
    column = _classify(profile).column("NOTES")
    assert column is not None
    with pytest.raises(PhiDowngradeRefusedError, match="ai actor"):
        reclassify(column, is_phi=False, steward=ROBOT, rationale="I checked")


def test_a_steward_downgrade_needs_a_reason(profile) -> None:  # type: ignore[no-untyped-def]
    column = _classify(profile).column("NOTES")
    assert column is not None
    with pytest.raises(PhiDowngradeRefusedError, match="needs a reason"):
        reclassify(column, is_phi=False, steward=STEWARD, rationale="   ")

    cleared = reclassify(
        column, is_phi=False, steward=STEWARD, rationale="operational free text, no member data"
    )
    assert not cleared.is_phi
    assert not cleared.needs_steward_review
    assert cleared.rationale.startswith("Ada: ")


def test_raising_a_flag_needs_no_reason(profile) -> None:  # type: ignore[no-untyped-def]
    """Only the downgrade is guarded. Requiring approval to protect MORE data
    would be a control that punishes caution."""
    column = _classify(profile).column("PROV_NPI")
    assert column is not None
    raised = reclassify(column, is_phi=True, steward=STEWARD, rationale="")
    assert raised.is_phi


# ── the gate, and what it drives ─────────────────────────────────────────────


def test_recall_against_the_glossary_is_total(profile) -> None:  # type: ignore[no-untyped-def]
    result = _classify(profile)
    assert result.missed_phi(GLOSSARY) == ()
    assert result.recall_against(GLOSSARY) == (1, 1)


def test_over_flagging_is_reported_and_never_gated(profile) -> None:  # type: ignore[no-untyped-def]
    """A detector that flags everything has told a steward nothing while
    appearing to work — so the number is visible, and it fails nothing."""
    result = _classify(profile)
    over = result.over_flagged(GLOSSARY)
    assert "NOTES" in over and "SUBSCR_REL_CD" in over
    assert result.missed_phi(GLOSSARY) == (), "the gate is unaffected by over-flagging"


def test_the_masking_policy_masks_columns_that_are_still_awaiting_review(profile) -> None:  # type: ignore[no-untyped-def]
    """ "Treated PHI until a steward decides" means the protection is in place
    WHILE the decision is pending. A policy that waited for the review would
    make the review the control, and reviews happen on Thursdays."""
    policy = masking_policy(_classify(profile))
    assert "NOTES" in policy.masked_columns
    assert "NOTES" in policy.pending_steward
    assert "NOTES" not in policy.unmasked_columns
    assert policy.masks_everything_it_should
    assert "PROV_NPI" in policy.unmasked_columns


def test_a_file_of_named_and_computable_columns_needs_no_model() -> None:
    """Zero tokens, not a cheap call — the same bargain CF-V1-E5-02 strikes."""
    rows = b"DOB,LOB,SSN\n19360201,MEDICAID,078-05-1120\n19370302,DUAL,219-09-9999\n"
    profile = profile_bytes(rows, file_format="csv", source_fingerprint="f")
    result = classify(profile, feed_id="f", glossary=GLOSSARY)
    assert result.needs_no_model
    assert result.open_questions == ()
