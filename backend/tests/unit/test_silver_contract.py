"""Stage 6, deterministic parts: how Silver Raw is declared and how a mapped row
is fanned out across canonical entities. No database, no model."""

from __future__ import annotations

import pytest

from cinqflow.dataplane.contract import (
    AUDIT_COLUMNS,
    Layer,
    TypeName,
    quarantine_table,
    record_hash,
    silver_table,
)
from cinqflow.dataplane.pg import render_table
from cinqflow.engine.mapping_exec import (
    FieldOutcome,
    RowOutcome,
    group_by_entity,
    is_empty,
    row_reasons,
)
from cinqflow.knowledge.canonical import load_canonical
from cinqflow.knowledge.export import decisions_of
from cinqflow.workflow.models import MappingField, MappingSpec, MappingVersion, Transform

FIELDS = {"source_system_id": "string", "date_of_birth": "timestamp", "dnc": "bool"}


def test_a_silver_table_carries_the_entity_and_the_audit_columns():
    table = silver_table("members", FIELDS, schema="silver", phi_fields=frozenset({"first_name"}))

    assert table.layer is Layer.SILVER_RAW
    assert table.name == "members"
    # The logical layer is silver_raw; the physical namespace is a rendering choice.
    assert table.schema == "silver"
    assert table.qualified == "silver.members"
    assert table.column_names[: len(FIELDS)] == tuple(FIELDS)
    assert table.column_names[len(FIELDS) :] == tuple(c.name for c in AUDIT_COLUMNS)
    assert table.column("date_of_birth").type is TypeName.TIMESTAMP_UTC
    assert table.column("dnc").type is TypeName.BOOL


def test_silver_is_rebuildable_and_bronze_is_not():
    """Replay rebuilds a batch in Silver, so Silver cannot be append-only. Bronze
    is the append-only record that makes rebuilding safe."""
    from cinqflow.dataplane.contract import bronze_table

    assert silver_table("members", FIELDS, schema="silver").append_only is False
    assert quarantine_table("roster", schema="silver").append_only is False
    assert bronze_table("roster").append_only is True


def test_silver_declares_no_primary_key_because_the_canonical_key_is_unsupplied():
    """`members_addresses` is keyed on address_type, which a roster does not have;
    declaring the key would refuse rows the mapping can legitimately produce."""
    table = silver_table("members_addresses", {"address1": "string"}, schema="silver")
    assert table.primary_key == ()
    assert not any("PRIMARY KEY" in sql for sql in render_table(table))


def test_a_silver_table_renders_into_its_own_schema_with_no_append_only_guard():
    sql = " ".join(render_table(silver_table("members", FIELDS, schema="test_silver_1")))
    assert 'CREATE TABLE IF NOT EXISTS test_silver_1."members"' in sql
    assert '"date_of_birth" TIMESTAMPTZ' in sql
    assert '"dnc" BOOLEAN' in sql
    assert "append_only" not in sql
    assert "CREATE TRIGGER" not in sql


def test_the_quarantine_table_keeps_the_row_and_the_reason():
    table = quarantine_table("member_roster", schema="silver")
    assert table.qualified == "silver.member_roster_quarantine"
    assert {"row_number", "mapping_version", "outcome", "reasons", "raw_row"} <= set(
        table.column_names
    )
    assert table.column("raw_row").is_phi is True


def test_the_canonical_model_answers_per_entity(settings):
    from cinqflow.knowledge.yaml_provider import YamlKnowledgeProvider

    canonical = load_canonical(YamlKnowledgeProvider(settings), "enrollment")
    members = canonical.fields_of("members")

    assert members["first_name"] == "string"
    assert members["date_of_birth"] == "timestamp"
    # system-populated columns are not fields of the entity
    assert "record_hash" not in members and "batch_id" not in members
    assert "first_name" in canonical.phi_of("members")
    assert "care_management_program" not in canonical.phi_of("members")
    assert canonical.fields_of("members_phones")["phone_number"] == "string"
    assert canonical.fields_of("nonexistent") == {}


def test_one_mapped_row_fans_out_to_the_entities_it_populates():
    mapped = {
        "members.first_name": "DANIELLE",
        "members.source_system_id": "M001",
        "members_addresses.city": "ALBANY",
        "members_phones.phone_number": None,
        "unqualified": "ignored",
    }
    assert group_by_entity(mapped) == {
        "members": {"first_name": "DANIELLE", "source_system_id": "M001"},
        "members_addresses": {"city": "ALBANY"},
        "members_phones": {"phone_number": None},
    }


def test_an_entity_with_nothing_but_the_member_key_is_empty():
    """The absence of a phone is not a record of an absence."""
    assert is_empty({"phone_number": None, "source_system_id": "M001"},
                    ignoring=frozenset({"source_system_id"}))
    assert not is_empty({"phone_number": "5550100", "source_system_id": "M001"},
                        ignoring=frozenset({"source_system_id"}))
    assert is_empty({"city": ""})


def test_quarantine_reasons_name_the_field_the_rule_and_the_message():
    row = RowOutcome(
        row_number=7,
        outcome="rejected",
        fields=[
            FieldOutcome("member_id", "members.source_system_id", None, None,
                         "rejected", "source is empty and this field rejects the row", "on_null"),
            FieldOutcome("member_first_name", "members.first_name", "ANN", "ANN", "ok"),
        ],
    )
    assert row_reasons(row) == [
        {
            "source": "member_id",
            "target": "members.source_system_id",
            "rule": "on_null",
            "outcome": "rejected",
            "reason": "source is empty and this field rejects the row",
        }
    ]


def test_record_hash_is_content_addressed_so_a_replay_reproduces_it():
    values = {"first_name": "DANIELLE", "source_system_id": "M001", "sex": None}
    assert record_hash(values) == record_hash(dict(reversed(list(values.items()))))
    assert record_hash(values) != record_hash({**values, "first_name": "KEVIN"})


def _version(*fields: MappingField) -> MappingVersion:
    from datetime import UTC, datetime

    return MappingVersion(
        feed="roster",
        version=2,
        domain="enrollment",
        status="approved",
        spec=MappingSpec(target_table="silver_raw.members", fields=list(fields)),
        created_by="analyst@cinqcare.com",
        created_ts=datetime.now(UTC),
    )


def test_exported_decisions_carry_the_rules_but_never_a_value():
    mapping = _version(
        MappingField(
            source="member_dob",
            target="members.date_of_birth",
            cast="timestamp",
            transform=Transform(op="parse_date", args={"format": "MM/DD/YYYY"}),
            edited=True,
        ),
        MappingField(
            source="member_sex",
            target="members.sex",
            value_map={"F": "female"},
            on_unmapped_value="quarantine",
        ),
        MappingField(source="member_email", target="members_emails.email_address"),
    )
    decisions = decisions_of(mapping)

    assert decisions[0] == {
        "source_field": "member_dob",
        "target": "members.date_of_birth",
        "decided_by": "analyst",
        "cast": "timestamp",
        "transform": {"op": "parse_date", "format": "MM/DD/YYYY"},
    }
    assert decisions[1]["value_map"] == {"F": "female"}
    assert decisions[1]["on_unmapped_value"] == "quarantine"
    # An untouched AI suggestion the analyst approved is recorded as exactly that.
    assert decisions[2] == {
        "source_field": "member_email",
        "target": "members_emails.email_address",
        "decided_by": "analyst_accepted_ai",
    }


def test_an_unsafe_entity_name_never_reaches_sql():
    from cinqflow.dataplane.contract import UnsafeIdentifier

    with pytest.raises(UnsafeIdentifier):
        silver_table('members"; DROP SCHEMA silver', {"a": "string"}, schema="silver")
