"""CF-V1-E6-03 — the transform taxonomy, and what a mapping refuses to store.

    "Manual mapping editor — full transform taxonomy (cast, split, lookup,
     conditional, default, null handling)"
    "Humans must be able to do by hand everything the AI proposes."

The taxonomy is harvested from the client's own source-to-Silver-Raw workbooks,
so these tests are written against shapes that appear in them: the `Values`
column's literals, the `reference` sheet's 70-code translations, the
`COALESCE with service_from_date` note, and the `NO MAP Fields` sheet whose
last column is `Reason`.

`tests/pipeline/test_mapping_on_the_real_plane.py` runs the same objects
through the live Postgres plane and the real lifecycle.
"""

from __future__ import annotations

import pytest

from cinqflow.core.mapping import (
    REJECT,
    Case,
    FeedMapping,
    FindingSeverity,
    LineStatus,
    MappingError,
    MappingLine,
    NullPolicy,
    Transform,
    TransformKind,
    TransformShapeError,
    UnlistedCode,
    apply,
    blocking,
    from_governed,
    mapping_as_governed,
    validate,
)
from cinqflow.core.model.governed import Actor, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry.canonical import build
from cinqflow.core.registry.contract import ContractColumn, SchemaContract
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm
from cinqflow.core.schema_spec import Column, Schema, Table, TypeName

pytestmark = pytest.mark.unit

BA = Actor(subject="ba@cinqcare.com", actor_type=ActorType.HUMAN, display_name="A BA")


def line(**kwargs: object) -> MappingLine:
    defaults: dict[str, object] = {
        "target_entity": "patient",
        "target_field": "first_name",
        "source_columns": ("First_Name",),
    }
    return MappingLine(**{**defaults, **kwargs})  # type: ignore[arg-type]


# ── the taxonomy is closed, and its shapes are checked at construction ───────


def test_every_transform_kind_has_a_declared_shape() -> None:
    """The completeness test. A new kind added to the enum without a shape
    entry would raise KeyError on first construction — which is a crash, not a
    refusal, and crashes are where taxonomies grow an `expression` escape."""
    from cinqflow.core.mapping import _SHAPE

    assert set(_SHAPE) == set(TransformKind)


def test_a_transform_has_no_expression_field_anywhere() -> None:
    """THE SECURITY PROPERTY, asserted rather than described.

    A mapping is configuration a steward approves by reading it. The moment a
    line can carry an expression, approving a mapping means reading a language,
    and a registry row can change what code runs without anyone approving code.
    """
    fields = set(Transform.__dataclass_fields__) | set(MappingLine.__dataclass_fields__)
    for banned in ("expression", "sql", "formula", "code", "script", "predicate"):
        assert banned not in fields, f"a mapping may not carry {banned!r}"


def test_a_lookup_with_no_table_is_refused() -> None:
    with pytest.raises(TransformShapeError, match="needs lookup"):
        Transform(kind=TransformKind.LOOKUP)


def test_a_direct_transform_carrying_a_separator_is_refused() -> None:
    """A parameter that changes nothing on the review screen teaches the
    reviewer to stop reading parameters."""
    with pytest.raises(TransformShapeError, match="no use for separator"):
        Transform(kind=TransformKind.DIRECT, separator="|")


def test_a_lookup_listing_the_same_code_twice_is_refused() -> None:
    with pytest.raises(TransformShapeError, match="same source code twice"):
        Transform(
            kind=TransformKind.LOOKUP,
            lookup=(("1", "Hospice"), ("1", "Day Care Medical")),
        )


def test_substituting_for_unlisted_codes_needs_something_to_substitute() -> None:
    """Otherwise 'substitute' means 'silently null', which is the outcome the
    setting exists to avoid."""
    with pytest.raises(TransformShapeError, match="needs a default_value"):
        Transform(
            kind=TransformKind.LOOKUP,
            lookup=(("20", "Physician"),),
            on_unlisted=UnlistedCode.SUBSTITUTE,
        )


def test_split_parts_are_one_based() -> None:
    with pytest.raises(TransformShapeError, match="part 0 is not a part"):
        Transform(kind=TransformKind.SPLIT, separator="^", part=0)


# ── unmapped is a decision, and the client's own workbooks say so ───────────


def test_a_field_with_no_source_and_no_reason_is_refused() -> None:
    """`CCLF_to_SilverRaw_Mapping` ships a `NO MAP Fields` sheet whose last
    column is `Reason`. A blank here is the difference between "we looked and
    there is nothing" and "nobody got to it"."""
    with pytest.raises(MappingError, match="no source and no reason"):
        MappingLine(target_entity="patient", target_field="hicn_id")


def test_an_unmapped_field_with_a_reason_is_a_legitimate_line() -> None:
    unmapped = MappingLine(
        target_entity="patient",
        target_field="hicn_id",
        unmapped_reason="Equitable BIC HICN — not mbi_id; the payer stopped sending it in 2025.",
    )
    assert unmapped.status is LineStatus.UNMAPPED
    assert not unmapped.is_mapped
    assert "not mbi_id" in unmapped.describe()


def test_status_is_computed_so_it_cannot_disagree_with_the_line() -> None:
    """The client's workbooks carry status as a typed column, and typed columns
    drift — a line whose sources were deleted keeps saying MAPPED until
    somebody notices."""
    assert line().status is LineStatus.MAPPED
    assert line(platform_supplied=True).status is LineStatus.PLATFORM_SUPPLIED
    assert (
        line(
            source_columns=(),
            transform=Transform(kind=TransformKind.CONSTANT, literal="FIDELIS"),
        ).status
        is LineStatus.CONSTANT
    )


def test_a_constant_that_also_reads_a_column_is_refused() -> None:
    """One of those is a lie and a reviewer cannot tell which."""
    with pytest.raises(MappingError, match="is a constant AND reads"):
        line(transform=Transform(kind=TransformKind.CONSTANT, literal="D0284"))


def test_coalescing_over_one_column_is_refused() -> None:
    """With one column it is pass-through under another name, and a reviewer
    reading `COALESCE` would believe there was a fallback."""
    with pytest.raises(MappingError, match="coalesces over one source"):
        line(null_policy=NullPolicy.COALESCE)


def test_substituting_on_null_with_nothing_to_substitute_is_refused() -> None:
    with pytest.raises(MappingError, match="substitutes on null with nothing"):
        line(null_policy=NullPolicy.SUBSTITUTE)


# ── applying a line: pure, and therefore identical on both planes ───────────


def test_direct_copies_the_value() -> None:
    assert apply(line(), {"First_Name": " Ada "}) == "Ada"


def test_a_constant_needs_no_source_row_at_all() -> None:
    """The enrollment workbook's `Values` column: `D0284`, `"Primary"`."""
    constant = line(
        source_columns=(),
        target_field="source_system_id",
        transform=Transform(kind=TransformKind.CONSTANT, literal="D0284"),
    )
    assert apply(constant, {}) == "D0284"


def test_split_takes_the_named_part() -> None:
    split = line(
        target_field="last_name",
        source_columns=("Member_Name",),
        transform=Transform(kind=TransformKind.SPLIT, separator=",", part=1),
    )
    assert apply(split, {"Member_Name": "Lovelace, Ada"}) == "Lovelace"


def test_split_past_the_end_is_empty_rather_than_an_error() -> None:
    """A payer omitting the middle initial is an ordinary delivery, not an
    incident."""
    split = line(
        target_field="middle_name",
        source_columns=("Member_Name",),
        transform=Transform(kind=TransformKind.SPLIT, separator=",", part=3),
    )
    assert apply(split, {"Member_Name": "Lovelace, Ada"}) is None


def test_concat_joins_the_populated_columns() -> None:
    joined = line(
        target_field="full_address",
        source_columns=("Address1", "Address2"),
        transform=Transform(kind=TransformKind.CONCAT, separator=" "),
    )
    assert apply(joined, {"Address1": "1 Main St", "Address2": "Apt 4"}) == "1 Main St Apt 4"
    assert apply(joined, {"Address1": "1 Main St", "Address2": ""}) == "1 Main St"


def test_lookup_translates_a_listed_code() -> None:
    """The claims workbook's `reference` sheet, in miniature."""
    translated = line(
        target_field="servicing_facility_category",
        source_columns=("facility_code",),
        transform=Transform(
            kind=TransformKind.LOOKUP,
            lookup=(("12", "Day Care Medical"), ("14", "Ambulatory Surgery Center")),
        ),
    )
    assert apply(translated, {"facility_code": "12"}) == "Day Care Medical"


def test_a_bare_string_of_source_columns_is_refused() -> None:
    """Found by a missing comma in the test above, which is exactly how it
    would reach production. `source_columns="facility_code"` iterates letter by
    letter, so the line reads eleven one-character columns and maps from none
    of them — silently, with no error anywhere."""
    with pytest.raises(MappingError, match="letter by letter"):
        line(source_columns="First_Name")


def test_an_unlisted_code_rejects_the_row_by_default() -> None:
    """THE 71ST CODE. The client's reference sheet lists 70; one more arrives
    next month, and the default answer must not be a silent null."""
    translated = line(
        target_field="servicing_facility_category",
        source_columns=("facility_code",),
        transform=Transform(kind=TransformKind.LOOKUP, lookup=(("12", "Day Care Medical"),)),
    )
    assert apply(translated, {"facility_code": "99"}) is REJECT


def test_an_unlisted_code_may_be_passed_through_when_somebody_decides_so() -> None:
    """Honest, and readable downstream as 'not translated' rather than as a
    translation that happens to be wrong."""
    translated = line(
        target_field="servicing_facility_category",
        source_columns=("facility_code",),
        transform=Transform(
            kind=TransformKind.LOOKUP,
            lookup=(("12", "Day Care Medical"),),
            on_unlisted=UnlistedCode.PASS_THROUGH,
        ),
    )
    assert apply(translated, {"facility_code": "99"}) == "99"


def test_conditional_matches_a_case_and_falls_back() -> None:
    conditional = line(
        target_field="claim_type",
        source_columns=("scope",),
        transform=Transform(
            kind=TransformKind.CONDITIONAL,
            cases=(Case(when_in=("IP H", "IP L"), then="inpatient"),),
            default_value="other",
        ),
    )
    assert apply(conditional, {"scope": "IP L"}) == "inpatient"
    assert apply(conditional, {"scope": "PROF H"}) == "other"


def test_coalesce_falls_back_to_the_next_column() -> None:
    """`OP service date -> service_date (date). COALESCE with
    service_from_date` — verbatim from the client's claims workbook."""
    coalesced = line(
        target_field="service_date",
        source_columns=("service_date", "service_from_date"),
        null_policy=NullPolicy.COALESCE,
    )
    assert apply(coalesced, {"service_date": "", "service_from_date": "20260101"}) == "20260101"


def test_reject_on_null_attributes_the_row_rather_than_dropping_it() -> None:
    required = line(target_field="source_claim_id", null_policy=NullPolicy.REJECT_ROW)
    assert apply(required, {"First_Name": ""}) is REJECT


def test_a_platform_supplied_field_reads_nothing_from_the_source() -> None:
    """`batch_id`, `record_hash`, `is_deleted` — the client's `AUDIT` status."""
    supplied = MappingLine(target_entity="patient", target_field="batch_id", platform_supplied=True)
    assert supplied.status is LineStatus.PLATFORM_SUPPLIED
    assert supplied.is_mapped
    assert apply(supplied, {"batch_id": "not-this-one"}) is None


def test_the_first_rejecting_line_wins() -> None:
    """A row can only be dropped once, and attributing it to three lines would
    treble-count it and break the ledger's balance."""
    mapping = FeedMapping(
        feed_id="fidelis-downstate-roster",
        lines=(
            line(target_field="first_name", null_policy=NullPolicy.REJECT_ROW),
            line(
                target_field="last_name",
                source_columns=("Last_Name",),
                null_policy=NullPolicy.REJECT_ROW,
            ),
        ),
    )
    mapped, rejected = mapping.apply_to({"First_Name": "", "Last_Name": ""})
    assert mapped == {}
    assert rejected is not None and rejected.target_field == "first_name"


# ── validation reaches BOTH ends, which only a mapping can do ───────────────

CONTRACT = SchemaContract(
    feed_id="fidelis-downstate-roster",
    version=3,
    columns=(
        ContractColumn("first_name", TypeName.STRING, source_name="First_Name", is_phi=True),
        ContractColumn("line_of_business", TypeName.STRING, source_name="LOB"),
    ),
)

DEPLOYED = Schema(
    name="silver_ods",
    description="test",
    tables=(
        Table(
            name="patient",
            columns=(
                Column("first_name", TypeName.STRING, is_phi=True),
                Column("line_of_business", TypeName.STRING),
                Column("record_hash", TypeName.STRING, nullable=False),
            ),
            primary_key=("record_hash",),
        ),
    ),
)

GLOSSARY = Glossary(
    terms=(
        GlossaryTerm(
            glossary_id="BG-001",
            term="Member First Name",
            definition="The member's given name.",
            mapped_domains=("Enrollment",),
            mapped_tables=("patient",),
            mapped_columns_corrected=("first_name",),
            is_phi=True,
        ),
        GlossaryTerm(
            glossary_id="BG-050",
            term="Line of Business",
            definition="The product line a member is enrolled under.",
            mapped_domains=("Enrollment",),
            mapped_tables=("patient",),
            mapped_columns_corrected=("line_of_business",),
        ),
    )
)

MODEL = build((DEPLOYED,), GLOSSARY)


def test_a_source_column_the_contract_does_not_have_blocks() -> None:
    findings = validate(
        FeedMapping(feed_id="f", lines=(line(source_columns=("MBR_FNAME",)),)),
        contract=CONTRACT,
    )
    assert [f.key for f in blocking(findings)] == ["unknown_source"]
    assert "empty on every row" in findings[0].why_it_matters


def test_a_target_field_the_canonical_model_does_not_have_blocks() -> None:
    findings = validate(
        FeedMapping(feed_id="f", lines=(line(target_field="favourite_colour"),)),
        model=MODEL,
    )
    assert [f.key for f in blocking(findings)] == ["unknown_field"]


def test_mapping_the_same_target_twice_blocks() -> None:
    findings = validate(FeedMapping(feed_id="f", lines=(line(), line())))
    assert [f.key for f in blocking(findings)] == ["duplicate_target"]
    assert "depend on ordering" in findings[0].why_it_matters


def test_carrying_phi_into_an_unflagged_target_blocks() -> None:
    """THE ONE NEITHER END CAN SEE ALONE. `First_Name` is flagged PHI on the
    contract; `line_of_business` is not flagged on the target. Landing the
    first in the second takes the value out of the masking policy without
    breaking any rule anywhere."""
    findings = validate(
        FeedMapping(
            feed_id="f",
            lines=(line(target_field="line_of_business", source_columns=("First_Name",)),),
        ),
        contract=CONTRACT,
        model=MODEL,
    )
    assert [f.key for f in blocking(findings)] == ["phi_laundering"]
    assert "masking reads the target" in findings[0].why_it_matters.lower()


def test_phi_into_a_flagged_target_is_fine() -> None:
    findings = validate(
        FeedMapping(feed_id="f", lines=(line(source_columns=("First_Name",)),)),
        contract=CONTRACT,
        model=MODEL,
    )
    assert blocking(findings) == ()


def test_a_designed_but_undeployed_target_is_advisory_not_blocking() -> None:
    """The client has designed twenty entities and deployed one. Refusing to
    map against the rest would make the studio unusable until Wave 3."""
    undeployed = build((DEPLOYED,), GLOSSARY)
    designed = Glossary(
        terms=(
            GlossaryTerm(
                glossary_id="BG-900",
                term="Care Gap",
                definition="An open quality measure.",
                mapped_domains=("Quality",),
                mapped_tables=("care_gap",),
                mapped_columns_corrected=("gap_code",),
            ),
        )
    )
    model = build((DEPLOYED,), designed)
    findings = validate(
        FeedMapping(
            feed_id="f",
            lines=(line(target_entity="care_gap", target_field="gap_code"),),
        ),
        model=model,
    )
    assert blocking(findings) == ()
    assert [f.severity for f in findings] == [FindingSeverity.ADVISORY]
    assert undeployed.entity("patient") is not None


def test_a_mapping_may_be_validated_against_one_end_only() -> None:
    """A BA drafting against a feed whose contract is still in review needs to
    be able to save."""
    assert validate(FeedMapping(feed_id="f", lines=(line(),))) == ()


# ── coverage, and the governed round trip ───────────────────────────────────


def test_coverage_is_two_integers_not_a_percentage() -> None:
    mapping = FeedMapping(
        feed_id="f",
        lines=(
            line(),
            MappingLine(
                target_entity="patient", target_field="hicn_id", unmapped_reason="not sent"
            ),
        ),
    )
    assert mapping.coverage == (1, 2)
    assert [line.target_field for line in mapping.unmapped] == ["hicn_id"]


def test_a_mapping_round_trips_through_its_governed_body() -> None:
    original = FeedMapping(
        feed_id="fidelis-downstate-roster",
        version=2,
        contract_version=3,
        lines=(
            line(glossary_id="BG-001"),
            line(
                target_field="servicing_facility_category",
                source_columns=("facility_code",),
                transform=Transform(
                    kind=TransformKind.LOOKUP,
                    lookup=(("12", "Day Care Medical"),),
                    on_unlisted=UnlistedCode.PASS_THROUGH,
                ),
            ),
            MappingLine(
                target_entity="patient",
                target_field="hicn_id",
                unmapped_reason="the payer stopped sending it",
            ),
        ),
    )
    obj = mapping_as_governed(original, author=BA)
    assert obj.object_type is ObjectType.MAPPING
    assert from_governed(obj) == original


def test_the_governed_body_carries_the_keys_lineage_already_expects() -> None:
    """`core.impact.REFERENCES` declares that a MAPPING's `feed_id`,
    `contract_id` and `glossary_ids` point at other objects. Writing those keys
    means lineage works the moment the object is stored, with no second
    declaration to keep in step."""
    from cinqflow.core.impact import REFERENCES

    obj = mapping_as_governed(
        FeedMapping(feed_id="f", contract_version=3, lines=(line(glossary_id="BG-001"),)),
        author=BA,
    )
    for spec in REFERENCES[ObjectType.MAPPING]:
        assert spec.body_key in obj.body, f"lineage reads {spec.body_key}, and it is not written"
    assert obj.body["glossary_ids"] == ["BG-001"]


def test_a_stored_line_carries_its_computed_status_for_readers() -> None:
    """Derived, and written anyway: a query counting unmapped fields should not
    have to re-implement the rule."""
    obj = mapping_as_governed(
        FeedMapping(
            feed_id="f",
            lines=(
                MappingLine(
                    target_entity="patient", target_field="hicn_id", unmapped_reason="not sent"
                ),
            ),
        ),
        author=BA,
    )
    assert obj.body["lines"][0]["status"] == LineStatus.UNMAPPED.value
