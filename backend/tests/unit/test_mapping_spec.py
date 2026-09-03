"""The constrained representation: what a spec may say, and what it may not."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from cinqflow.engine.mapping_spec import (
    ALLOWED_OPS,
    InvalidSpec,
    assert_valid,
    diff_specs,
    spec_from_proposal,
    validate_spec,
)
from cinqflow.knowledge.canonical import load_canonical
from cinqflow.knowledge.yaml_provider import YamlKnowledgeProvider
from cinqflow.settings import Settings
from cinqflow.workflow.models import (
    FieldCandidate,
    MappingField,
    MappingSpec,
    Proposal,
    ProposalContent,
    Provenance,
    Transform,
)

KNOWLEDGE_ROOT = Path(__file__).resolve().parents[3] / "knowledge"


@pytest.fixture
def canonical(tmp_path):
    s = Settings(landing_root=tmp_path, knowledge_root=KNOWLEDGE_ROOT, llm_provider="stub")
    return load_canonical(YamlKnowledgeProvider(s), "enrollment")


def spec(*fields: MappingField, table: str = "silver_raw.members") -> MappingSpec:
    return MappingSpec(target_table=table, fields=list(fields))


def field(**kwargs) -> MappingField:
    return MappingField(**{"source": "member_id", "target": "members.source_system_id", **kwargs})


# ------------------------------------------------------------------- valid specs


def test_a_spec_the_engine_could_execute_has_no_errors(canonical):
    valid = spec(
        field(),
        field(
            source="member_dob",
            target="members.date_of_birth",
            cast="timestamp",
            transform=Transform(op="parse_date", args={"format": "%Y-%m-%d"}),
        ),
        field(
            source="member_sex",
            target="members.sex",
            value_map={"M": "male", "F": "female"},
            on_unmapped_value="quarantine",
        ),
        field(source="member_dnc", target="members.dnc", cast="bool", on_null="default",
              default="false"),
    )
    assert validate_spec(valid, canonical) == []
    assert_valid(valid, canonical)  # does not raise


# ----------------------------------------------------------------- target rules


def test_non_canonical_target_is_refused_with_a_field_level_error(canonical):
    errors = validate_spec(spec(field(target="members.member_uuid")), canonical)
    assert len(errors) == 1
    assert errors[0].field_index == 0
    assert errors[0].source == "member_id"
    assert errors[0].attribute == "target"
    assert "not a field in the canonical model" in errors[0].message


def test_contested_field_error_explains_why(canonical):
    errors = validate_spec(spec(field(target="members.guardian_email")), canonical)
    assert "contested/absent" in errors[0].message


def test_platform_populated_column_cannot_be_a_target(canonical):
    errors = validate_spec(spec(field(target="members.record_hash")), canonical)
    assert "populated by the platform" in errors[0].message


def test_two_sources_cannot_claim_one_target(canonical):
    errors = validate_spec(
        spec(field(source="member_id"), field(source="medicaid_id")), canonical
    )
    assert [e.attribute for e in errors] == ["target"]
    assert "one source per target" in errors[0].message


def test_one_source_cannot_be_mapped_twice(canonical):
    errors = validate_spec(
        spec(field(), field(target="members.language")), canonical
    )
    assert any(e.attribute == "source" for e in errors)


def test_unknown_target_table_is_refused(canonical):
    errors = validate_spec(spec(field(), table="silver_raw.nope"), canonical)
    assert errors[0].field_index == -1
    assert errors[0].attribute == "target_table"


# -------------------------------------------------------------- transform rules


def test_unsupported_transform_is_refused(canonical):
    errors = validate_spec(
        spec(field(transform=Transform(op="exec_python", args={"code": "rm -rf /"}))), canonical
    )
    assert errors[0].attribute == "transform"
    assert "not a supported transform" in errors[0].message
    assert "exec_python" not in ALLOWED_OPS


def test_transform_missing_a_required_argument_is_refused(canonical):
    errors = validate_spec(
        spec(field(target="members.date_of_birth", cast="timestamp",
                   transform=Transform(op="parse_date", args={}))),
        canonical,
    )
    assert "requires argument 'format'" in errors[0].message


# ------------------------------------------------------------------- cast rules


def test_cast_must_be_able_to_satisfy_the_declared_type(canonical):
    errors = validate_spec(
        spec(field(target="members.date_of_birth", cast="string")), canonical
    )
    assert errors[0].attribute == "cast"
    assert "declared timestamp" in errors[0].message


def test_unsupported_cast_is_refused(canonical):
    errors = validate_spec(spec(field(cast="blob")), canonical)
    assert any("not a supported cast" in e.message for e in errors)


# ------------------------------------------------------- null and value handling


def test_default_null_handling_needs_a_default(canonical):
    errors = validate_spec(spec(field(on_null="default")), canonical)
    assert errors[0].attribute == "default"


def test_on_unmapped_value_without_a_value_map_is_refused(canonical):
    errors = validate_spec(spec(field(on_unmapped_value="quarantine")), canonical)
    assert errors[0].attribute == "on_unmapped_value"


def test_empty_spec_is_refused(canonical):
    errors = validate_spec(spec(), canonical)
    assert errors[0].attribute == "fields"


def test_invalid_spec_reports_every_problem_at_once(canonical):
    bad = spec(
        field(target="members.member_uuid"),
        field(source="member_dob", target="members.date_of_birth", cast="string"),
        field(source="x", target="members.first_name",
              transform=Transform(op="exec_python", args={})),
    )
    with pytest.raises(InvalidSpec) as exc:
        assert_valid(bad, canonical)
    assert len(exc.value.errors) == 3
    assert {e.attribute for e in exc.value.errors} == {"target", "cast", "transform"}
    assert all("message" in e for e in exc.value.as_list())


def test_a_spec_cannot_carry_unknown_attributes():
    """Extra keys are refused by the model, so no smuggled 'code' field survives."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MappingField(
            source="a", target="members.first_name", python="__import__('os').system('x')"
        )


# ------------------------------------------------------------ seeding from a proposal


def proposal_with(*candidates: FieldCandidate) -> Proposal:
    return Proposal(
        proposal_id="p1",
        batch_id="b1",
        upload_id="u1",
        feed="roster",
        domain="enrollment",
        bronze_profile_id="bp1",
        status="proposed",
        provenance=Provenance(prompt="recommend_mapping@1", model="stub", knowledge=[]),
        content=ProposalContent(fields=list(candidates)),
        created_ts=datetime.now(UTC),
    )


def test_draft_is_seeded_only_from_defensible_candidates(canonical):
    seeded = spec_from_proposal(
        proposal_with(
            FieldCandidate(source="member_id", target="members.source_system_id",
                           confidence=0.9, evidence=["glossary"], status="candidate"),
            FieldCandidate(source="member_dob", target="members.date_of_birth",
                           transform=Transform(op="parse_date", args={"format": "%Y-%m-%d"}),
                           confidence=0.9, evidence=["glossary"], status="candidate"),
            FieldCandidate(source="harp_eligible", target=None, confidence=0.0,
                           evidence=["none"], status="unknown"),
            FieldCandidate(source="bogus", target="members.member_uuid", confidence=0.9,
                           evidence=["invented"], status="invalid"),
        ),
        canonical,
    )

    assert [f.source for f in seeded.fields] == ["member_dob", "member_id"]
    assert seeded.target_table == "silver_raw.members"
    # the proposal's transform is carried over
    dob = next(f for f in seeded.fields if f.source == "member_dob")
    assert dob.transform.op == "parse_date"
    # and the cast matches what the canonical model declares
    assert dob.cast in ("timestamp", "date")
    assert all(f.edited is False for f in seeded.fields)
    # a seeded draft is valid by construction
    assert validate_spec(seeded, canonical) == []


def test_contested_targets_are_left_for_the_analyst_to_decide(canonical):
    """Stage 3 marks these ambiguous; a draft must not pick a winner silently."""
    seeded = spec_from_proposal(
        proposal_with(
            FieldCandidate(source="member_id", target="members.source_system_id",
                           confidence=0.9, evidence=["a"], status="candidate"),
            FieldCandidate(source="medicaid_id", target="members.source_system_id",
                           confidence=0.9, evidence=["b"], status="candidate"),
            FieldCandidate(source="member_first_name", target="members.first_name",
                           confidence=0.9, evidence=["c"], status="candidate"),
        ),
        canonical,
    )
    assert [f.source for f in seeded.fields] == ["member_first_name"]


def test_seeding_a_proposal_with_nothing_usable_yields_an_empty_draft(canonical):
    seeded = spec_from_proposal(
        proposal_with(
            FieldCandidate(source="x", target=None, confidence=0.0, evidence=["n"],
                           status="unknown")
        ),
        canonical,
    )
    assert seeded.fields == []
    assert validate_spec(seeded, canonical)[0].attribute == "fields"


# --------------------------------------------------------------------- the diff


def test_diff_names_what_the_analyst_changed():
    before = spec(
        field(),
        field(source="member_dob", target="members.date_of_birth", cast="timestamp"),
        field(source="drop_me", target="members.language"),
    )
    after = spec(
        field(),
        field(source="member_dob", target="members.death_date", cast="timestamp", edited=True),
        field(source="new_col", target="members.race", edited=True),
    )
    result = diff_specs(before, after)

    assert result["added"] == ["new_col"]
    assert result["removed"] == ["drop_me"]
    assert len(result["changed"]) == 1
    assert result["changed"][0]["source"] == "member_dob"
    assert result["changed"][0]["attributes"]["target"] == {
        "from": "members.date_of_birth",
        "to": "members.death_date",
    }
    assert result["analyst_edited"] == ["member_dob", "new_col"]
    assert result["from_proposal"] == ["member_id"]


def test_diff_against_nothing_reports_every_field_as_added():
    result = diff_specs(None, spec(field()))
    assert result["added"] == ["member_id"]
    assert result["changed"] == []
