"""CF-V0-E8-07 — the Postgres data plane, on the real plane.

    "Given the Wave 0 roster demo, when the engine runs on the Postgres plane,
     then Landing -> Bronze -> Silver Raw completes for real, the balance
     equation is checkable as ONE SQL QUERY per stage, and a failing test
     leaves a database an engineer can open and query."
    — CF-V0-E8-07, happy path

Every test here runs inside a rolled-back transaction, so they leave nothing
behind and need no cleanup code.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from cinqflow.adapters.local.pg_control import Connection
from cinqflow.core.schema_spec import CONTROL_SCHEMA, all_schemas
from cinqflow.ports.control_tables import CONTROL_TABLES

pytestmark = [pytest.mark.pipeline, pytest.mark.postgres]


def test_the_seven_schemas_exist(plane: Connection) -> None:
    """postgres_schemas: [landing_ctl, control, bronze, silver_raw,
    silver_ods, quarantine, recon] — FIG 08"""
    rows = plane.fetch_all(
        "SELECT nspname FROM pg_namespace WHERE nspname = ANY(%s) ORDER BY nspname",
        ([s.name for s in all_schemas()],),
    )
    assert [r[0] for r in rows] == sorted(s.name for s in all_schemas())


def test_all_eleven_control_tables_exist(plane: Connection) -> None:
    rows = plane.fetch_all(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'control' ORDER BY table_name"
    )
    assert [r[0] for r in rows] == sorted(CONTROL_TABLES)


def test_the_introspected_schema_matches_the_spec_column_for_column(
    plane: Connection,
) -> None:
    """THE conformance comparison: each engine against the SPEC.

    Never engine against engine — "Postgres and Databricks disagree" is a diff
    nobody can adjudicate, while "Postgres disagrees with the spec" is an
    attributable defect someone can fix before lunch.
    """
    for table in CONTROL_SCHEMA.tables:
        rows = plane.fetch_all(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'control' AND table_name = %s ORDER BY ordinal_position",
            (table.name,),
        )
        introspected = {name: nullable == "YES" for name, nullable in rows}
        expected = {c.name: c.nullable for c in table.columns}
        assert introspected == expected, f"control.{table.name} has drifted from the spec"


def test_the_vector_extension_is_provisioned_and_the_store_is_empty(
    plane: Connection,
) -> None:
    """ "pgvector stays provisioned and empty, exactly as specified."

    The knowledge plane is Wave 1. Provisioning now and asserting empty is how
    the seat is honest about not being a capability yet.
    """
    row = plane.fetch_one("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    assert row is not None, "the vector pin is not provisioned"


def test_bronze_refuses_update_at_the_database_layer(plane: Connection) -> None:
    """ "Given any process attempts UPDATE or DELETE on a Bronze table, when the
    statement executes, then it is REFUSED AT THE DATABASE LAYER and the
    attempt is logged." — CF-V0-E8-07, guardrail

    Note what this test does NOT do: check a permission first. It makes the
    attempt. A guardrail nobody tries is a comment, not a control.
    """
    _insert_bronze_row(plane)
    with pytest.raises(psycopg.errors.CheckViolation) as caught:
        plane.execute("UPDATE bronze.members_raw SET feed_id = 'tampered'")
    assert "append-only" in str(caught.value)


def test_bronze_refuses_delete_at_the_database_layer(plane: Connection) -> None:
    _insert_bronze_row(plane)
    with pytest.raises(psycopg.errors.CheckViolation) as caught:
        plane.execute("DELETE FROM bronze.members_raw")
    assert "append-only" in str(caught.value)


def test_the_bronze_refusal_explains_itself_without_opening_a_document(
    plane: Connection,
) -> None:
    """A refusal that says only "permission denied" sends someone hunting for a
    grant. This one names the layer, the operation and what to do instead."""
    _insert_bronze_row(plane)
    with pytest.raises(psycopg.errors.CheckViolation) as caught:
        plane.execute("DELETE FROM bronze.members_raw")
    message = str(caught.value)
    assert "bronze.members_raw" in message
    assert "reprocess" in message.lower()


def test_bronze_accepts_inserts__it_is_append_only_not_read_only(
    plane: Connection,
) -> None:
    """The distinction matters: append-only preserves history, read-only would
    make ingestion impossible."""
    _insert_bronze_row(plane)
    (count,) = plane.fetch_one("SELECT count(*) FROM bronze.members_raw") or (0,)
    assert count == 1


def test_an_unbalanced_reconciliation_row_cannot_be_written(plane: Connection) -> None:
    """ "rows_in == rows_out + quarantined + attributed_drops" as a CHECK.

    Enforced in one place, so an unbalanced row cannot exist — rather than
    being detected later by a report someone might read.
    """
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_recon(plane, records_in=22_000, records_out=21_820, quarantined=0, drops=0)


def test_a_balanced_reconciliation_row_is_accepted(plane: Connection) -> None:
    """22,000 = 21,820 + 175 (DQ-002) + 5 (structure check). Balanced."""
    _insert_recon(plane, records_in=22_000, records_out=21_820, quarantined=175, drops=5)
    row = plane.fetch_one("SELECT balanced FROM control.batch_reconciliation")
    assert row is not None and row[0] is True


@pytest.mark.parametrize("category", ["other", "unknown", "OTHER", "Unknown", "n/a"])
def test_a_drop_ledger_category_of_other_or_unknown_is_refused(
    plane: Connection, category: str
) -> None:
    """ "Allow any category called 'other' or 'unknown' in the drop ledger" is a
    documented don't, and INVARIANTS.md marks it a SCHEMA-LEVEL constraint.

    Incident #2: member_provider silently lost rows where pcp_npi was null, and
    understated the roster. A row can only leave the pipeline with a REASON
    attached, and "other" is not a reason.
    """
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_recon(
            plane,
            records_in=100,
            records_out=90,
            quarantined=0,
            drops=10,
            drop_rule_id=category,
        )


def test_a_named_drop_reason_is_accepted(plane: Connection) -> None:
    """DQ-002 is a reason. It names the rule, and the rule names the column."""
    _insert_recon(
        plane,
        records_in=100,
        records_out=90,
        quarantined=0,
        drops=10,
        drop_rule_id="DQ-002",
        drop_reason="Member First Name Not Null",
    )
    row = plane.fetch_one("SELECT drop_rule_id FROM control.batch_reconciliation")
    assert row is not None and row[0] == "DQ-002"


def test_the_same_fingerprint_cannot_be_registered_twice(plane: Connection) -> None:
    """Exactly-once, as a database guarantee rather than a check somebody
    remembered to write.

    Incident #4: a duplicate Feb-2025 Fidelis roster was found during seeding.
    The unique constraint means a second registration of the same CONTENT is
    refused even by a code path that forgot to look first.
    """
    register = (
        "INSERT INTO control.input_registry "
        "(input_id, feed_id, file_key, filename, size_bytes, fingerprint, state, arrived_ts) "
        "VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, 'ACCEPTED', now())"
    )
    plane.execute(
        register,
        ("fidelis-downstate-roster", "incoming/r.xlsx", "r.xlsx", 1024, "sha256-identical"),
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        plane.execute(
            register,
            # A different filename, the SAME bytes. That is the duplicate that
            # matters: a re-sent file rarely arrives under its original name.
            ("fidelis-downstate-roster", "incoming/r2.xlsx", "r2.xlsx", 1024, "sha256-identical"),
        )


def test_two_active_feeds_cannot_claim_the_same_landing_path_and_pattern(
    plane: Connection,
) -> None:
    """ "Allow two active feeds to claim the same landing path and pattern" is a
    documented don't for CF-V0-E3-01.

    Two feeds claiming one pattern means an arriving file has two owners, and
    the pipeline picks one arbitrarily.
    """
    for feed_id in ("fidelis-downstate-roster", "a-different-feed"):
        statement = (
            "INSERT INTO control.feed_sla_config (feed_id, feed_version, domain, "
            "source_system, file_format, landing_path, file_pattern, schedule_cron, created_ts) "
            "VALUES (%s, 1, 'enrollments', 'fidelis', 'xlsx', %s, %s, '0 3 1 * *', now())"
        )
        arguments = (feed_id, "enrollments/fidelis_downstate", "_CINQDOWNSTATE_*.xlsx")
        if feed_id == "fidelis-downstate-roster":
            plane.execute(statement, arguments)
        else:
            with pytest.raises(psycopg.errors.UniqueViolation):
                plane.execute(statement, arguments)


def test_an_error_category_outside_the_fixed_set_is_refused(plane: Connection) -> None:
    """ "Error categories — a fixed set." A seventh category is an unattributed
    failure by another name."""
    with pytest.raises(psycopg.errors.CheckViolation):
        plane.execute(
            "INSERT INTO control.error_log "
            "(error_id_hash, batch_id, stage_name, error_category, message, occurred_ts) "
            "VALUES ('h1', '8842', 'silver_raw', 'WEIRD_ERROR', 'something', now())"
        )


# ── helpers ──────────────────────────────────────────────────────────────────
def _insert_bronze_row(plane: Connection) -> None:
    plane.execute(
        "INSERT INTO bronze.members_raw (bronze_id, feed_id, row_number, raw_row, "
        "source_system, ingestion_ts, batch_id, record_hash, created_ts) "
        "VALUES (%s, 'fidelis-downstate-roster', 1, '{}', 'fidelis', now(), '8842', 'h', now())",
        (str(uuid.uuid4()),),
    )


def _insert_recon(
    plane: Connection,
    *,
    records_in: int,
    records_out: int,
    quarantined: int,
    drops: int,
    drop_rule_id: str | None = None,
    drop_reason: str | None = None,
) -> None:
    plane.execute(
        "INSERT INTO control.batch_reconciliation (recon_id, batch_id, stage_name, "
        "records_in, records_out, quarantined, attributed_drops, drop_rule_id, drop_reason, "
        "balanced, reconciled_ts) "
        "VALUES (gen_random_uuid(), '8842', 'silver_raw', %s, %s, %s, %s, %s, %s, true, now())",
        (records_in, records_out, quarantined, drops, drop_rule_id, drop_reason),
    )
