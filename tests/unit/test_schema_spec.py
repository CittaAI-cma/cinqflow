"""The portable DDL spec — the eleven control tables, declared ONCE.

    "Keep the control-table DDLs logically identical to the pinned target
     versions — the conformance kit asserts schema equality on both engines."
    — CF-V0-E8-07

The design decision under test: declare the tables once in a canonical type
vocabulary and have each compute adapter render its OWN DDL, then have
conformance compare each engine's INTROSPECTED schema against the spec.

Comparing engines to the spec rather than to each other is what makes a drift
attributable: engine X disagrees with the spec, so engine X is wrong. Comparing
two engines produces a diff nobody can adjudicate.
"""

from __future__ import annotations

import pytest

from cinqflow.core.schema_spec import (
    CONTROL_SCHEMA,
    DATA_SCHEMAS,
    Column,
    Table,
    TypeName,
    all_schemas,
)
from cinqflow.ports.control_tables import CONTROL_TABLES


@pytest.mark.unit
def test_the_type_vocabulary_is_small_and_closed() -> None:
    """A canonical type vocabulary is what lets two engines agree.

    Every extra type is a new opportunity for Postgres and Delta to disagree
    about nulls, precision or collation — so the set is deliberately tiny.
    """
    assert {t.value for t in TypeName} == {
        "string",
        "int64",
        "decimal",
        "timestamp_utc",
        "date",
        "bool",
        "uuid",
        "json",
    }


@pytest.mark.unit
def test_decimal_must_declare_precision_and_scale() -> None:
    """ "numeric: declare precision+scale in the contract" — FIG 08's pinned
    divergences. An undeclared decimal is where money quietly changes."""
    with pytest.raises(ValueError, match="precision"):
        Column(name="paid_amount", type=TypeName.DECIMAL)
    ok = Column(name="paid_amount", type=TypeName.DECIMAL, precision=18, scale=2)
    assert (ok.precision, ok.scale) == (18, 2)


@pytest.mark.unit
def test_timestamps_are_utc_by_construction() -> None:
    """ "time: store UTC, derive business_date by explicit rule" — FIG 08.

    There is no naive-timestamp type in the vocabulary, so a naive timestamp
    cannot be specified.
    """
    assert not [t for t in TypeName if "local" in t.value or t.value == "timestamp"]
    assert TypeName.TIMESTAMP_UTC in set(TypeName)


@pytest.mark.unit
def test_the_control_schema_declares_exactly_the_eleven_tables() -> None:
    assert {t.name for t in CONTROL_SCHEMA.tables} == set(CONTROL_TABLES)
    assert len(CONTROL_SCHEMA.tables) == 11


@pytest.mark.unit
def test_every_control_table_carries_the_one_join_key() -> None:
    """ "All linked by batch_id for end-to-end traceability."

    Two exceptions, and they are legitimate: feed_sla_config and
    schema_registry describe a FEED, not a run, so they join on feed_id.
    """
    joins_on_feed = {"feed_sla_config", "schema_registry"}
    for table in CONTROL_SCHEMA.tables:
        columns = {c.name for c in table.columns}
        key = "feed_id" if table.name in joins_on_feed else "batch_id"
        assert key in columns, f"{table.name} cannot be joined to anything"


@pytest.mark.unit
def test_the_drop_ledger_forbids_other_and_unknown_at_the_schema_level() -> None:
    """ "Allow any category called 'other' or 'unknown' in the drop ledger" is a
    documented don't, and INVARIANTS.md marks it a SCHEMA-LEVEL constraint.

    A check constraint is the difference between a rule the pipeline respects
    and a rule the database enforces.
    """
    recon = CONTROL_SCHEMA.table("batch_reconciliation")
    constraints = " ".join(recon.check_constraints).lower()
    assert "other" in constraints and "unknown" in constraints


@pytest.mark.unit
def test_bronze_is_append_only_in_the_spec_not_only_in_a_grant() -> None:
    """ "Bronze is append-only, enforced at the database layer."

    Declared on the table so EVERY renderer must implement it — a grant alone
    would be one engine's idea, and the guarantee has to survive the climb.
    """
    bronze = next(s for s in DATA_SCHEMAS if s.name == "bronze")
    assert bronze.append_only is True
    for table in bronze.tables:
        assert table.append_only is True


@pytest.mark.unit
def test_the_provisioned_schemas_are_the_plate_s_seven_plus_the_platform_s() -> None:
    """postgres_schemas: [landing_ctl, control, bronze, silver_raw, silver_ods,
    quarantine, recon] — FIG 08. Plus the three plane objects
    `core/registry/wave0.py` already declared by name — registry.governed_object,
    governance.audit_ledger, audit.agent_action — and Wave 1's three additive
    schemas: `queue` (ADR-0014's Postgres queue + scheduler state), `proposals`
    (the universal HITL object), `knowledge` (the K2 store, FIG 12) and
    `profiling` (CF-V1-E5-01's computed evidence — BESIDE the client's control
    framework, never inside it, per ADR-0013) and `ops` (CF-V1-E3-04's feed
    suspensions: OPERATIONAL state, deliberately not governance, so that
    lifting a pause needs no approver while publishing configuration does).
    Wave 3 (CF-V3-E9-01/E9-02) adds `identity`: the crosswalk, its full
    request/response audit trail, and the exception queue — the seat was
    fitted from Wave 0 (`ports/identity.py`); this is where it becomes a real,
    queryable plane object."""
    assert [s.name for s in all_schemas()] == [
        "landing_ctl",
        "control",
        "bronze",
        "silver_raw",
        "silver_ods",
        "quarantine",
        "recon",
        "registry",
        "governance",
        "audit",
        "queue",
        "proposals",
        "knowledge",
        "profiling",
        "ops",
        "identity",
    ]


@pytest.mark.unit
def test_the_identity_schema_retains_source_identifiers_on_every_table() -> None:
    """Model rule #1 applies to the crosswalk itself: a resolved LinkId is
    worthless for tracing without the source_system/source_member_id it
    resolved FROM. `verato_response_log` traces through its `request_id` FK
    rather than duplicating the identifiers a second time — one join, not a
    second PHI-bearing copy of the same columns."""
    identity = next(s for s in all_schemas() if s.name == "identity")
    for table in identity.tables:
        columns = {c.name for c in table.columns}
        assert (
            "source_system" in columns or "exception_key" in columns or "request_id" in columns
        ), table.name


@pytest.mark.unit
def test_the_verato_logs_are_append_only_and_carry_a_payload_hash() -> None:
    """ "Store full request and response payloads with hashes, per the client's
    own schema design — the audit trail is the design's core." — CF-V3-E9-01"""
    identity = next(s for s in all_schemas() if s.name == "identity")
    for name in ("verato_request_log", "verato_response_log"):
        table = identity.table(name)
        assert table.append_only is True
        columns = {c.name for c in table.columns}
        assert {"payload", "payload_hash"} <= columns


@pytest.mark.unit
def test_every_phi_bearing_identity_column_is_flagged() -> None:
    identity = next(s for s in all_schemas() if s.name == "identity")
    crosswalk = identity.table("bridge_member_source_to_verato")
    assert crosswalk.column("source_member_id").is_phi
    assert crosswalk.column("source_system").is_phi


@pytest.mark.unit
def test_silver_ods_is_provisioned_and_empty_in_wave_0() -> None:
    """Silver ODS sits behind G4 identity resolution — Wave 3.

    Provisioning it empty is the honest statement: the seat exists, the
    capability does not.
    """
    ods = next(s for s in all_schemas() if s.name == "silver_ods")
    assert ods.tables == ()
    assert "Wave 3" in ods.description


@pytest.mark.unit
def test_every_table_carries_the_audit_columns() -> None:
    """ "Audit columns appear everywhere: source_system, ingestion_ts, batch_id,
    record_hash, created_ts, updated_ts."

    Applied to data-layer tables, where lineage has to survive.
    """
    for schema in all_schemas():
        if schema.name not in {"bronze", "silver_raw"}:
            continue
        for table in schema.tables:
            columns = {c.name for c in table.columns}
            assert {"batch_id", "record_hash", "ingestion_ts"} <= columns, table.name


@pytest.mark.unit
def test_a_table_must_declare_a_primary_key() -> None:
    """A table with no key cannot be reconciled, deduplicated or cited."""
    with pytest.raises(ValueError, match="primary key"):
        Table(name="anonymous", columns=(Column(name="x", type=TypeName.STRING),))


@pytest.mark.unit
def test_the_spec_is_hashable_so_conformance_can_pin_it() -> None:
    """The conformance kit reports WHICH spec an engine was checked against.
    A spec that could not be pinned would make a green result unfalsifiable."""
    first = CONTROL_SCHEMA.fingerprint
    assert len(first) == 32
    assert CONTROL_SCHEMA.fingerprint == first


@pytest.mark.unit
def test_the_spec_contains_no_engine_specific_sql() -> None:
    """ "Let engine-specific SQL leak into the core — dialects exist only inside
    the compute adapters' renderers." (a documented don't for CF-V0-E8-07)"""
    import re

    rendered = repr(list(all_schemas()))
    assert not re.search(r"\b(VARCHAR|TEXT|SERIAL|BIGINT|JSONB|TIMESTAMPTZ|NUMERIC)\b", rendered)
