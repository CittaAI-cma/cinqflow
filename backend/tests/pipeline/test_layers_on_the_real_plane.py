"""W3-01 — the medallion layer reader, on the real Postgres plane.

    0.5: {plane: postgres, cost: "a local install", proves: real_persistence}
    — docs/architecture/plates/05-socket-ladder.md

The rung-0 suite (`tests/contract/test_layer_routes.py`) proves the SHAPE
against a seeded stand-in. This proves the two things a stand-in cannot:

  · the census matches what `information_schema` actually reports, so a
    declared column the plane never got is reported as absent rather than
    rendered as an empty cell;
  · masking survives real `jsonb`, real `date` and real `uuid` values coming
    back through psycopg — the adapter receives parsed Python objects here and
    strings in a replay fixture, and only one of those two paths is exercised
    by a seed.

Every test runs inside a rolled-back transaction, so they leave nothing behind
and need no cleanup code.
"""

from __future__ import annotations

import pytest

from cinqflow.adapters.local.pg_catalog import PostgresCatalog
from cinqflow.adapters.local.pg_control import Connection
from cinqflow.adapters.local.pg_layers import MAX_PAGE, PostgresLayerReader
from cinqflow.adapters.local.pg_sql_query import PostgresSqlQuery
from cinqflow.core.layers import LayerStatus, spec_of, spine, table_of
from cinqflow.core.model.vocabulary import Layer

pytestmark = [pytest.mark.pipeline, pytest.mark.postgres]


@pytest.fixture
def reader(plane: Connection) -> PostgresLayerReader:
    return PostgresLayerReader(sql=PostgresSqlQuery(plane), catalog=PostgresCatalog(plane))


# ── census ───────────────────────────────────────────────────────────────────
def test_every_layer_answers_and_each_status_means_what_it_says(
    reader: PostgresLayerReader,
) -> None:
    """`census` must not raise for a layer that holds nothing. Raising would
    make those positions unrenderable, which is exactly how they would end up
    quietly dropped from the spine.

    Asserted per STATUS rather than per layer name, because which layer sits in
    which status changes as waves land — `identity` moved from NOT_BUILT to
    PROVISIONED_EMPTY the moment its schema was declared. A test naming the
    layers would have to be edited on every such move; a test naming the
    statuses keeps asserting the thing that must stay true.
    """
    for spec in spine():
        census = reader.census(spec)
        assert census.spec is spec
        if spec.status is LayerStatus.BUILT:
            # Tables, and a real count — possibly 0 if nothing has loaded.
            assert census.tables, spec.layer
            assert census.row_count is not None, spec.layer
        elif spec.status is LayerStatus.NOT_BUILT:
            # No schema at all, so nothing to describe and no count to give.
            assert spec.schema is None, spec.layer
            assert census.tables == (), spec.layer
            assert census.row_count is None, spec.layer
        else:
            # PROVISIONED_EMPTY: the schema is real and every table in it is
            # empty. `identity` has five tables here and `silver_ods` has none,
            # and both are correct — what the status promises is that nothing
            # has loaded, never that nothing was provisioned.
            assert spec.schema is not None, spec.layer
            assert all(t.row_count == 0 for t in census.tables), spec.layer


def test_the_built_layers_declared_tables_are_all_on_the_plane(
    reader: PostgresLayerReader,
) -> None:
    """A declared table with no migration behind it is a provisioning gap. If
    this fails, `cinqflow install` has not been run against this plane — which
    is a different failure from "the reader is wrong", and the message says
    which."""
    for spec in (spec_of(Layer.BRONZE), spec_of(Layer.SILVER_RAW)):
        for table in reader.census(spec).tables:
            assert table.present_on_plane, (
                f"{table.schema}.{table.name} is declared and absent — run "
                "`cinqflow install --profile profiles/local.yaml`"
            )
            assert all(c.present_on_plane for c in table.columns), table.name


def test_the_engine_type_is_reported_beside_the_declared_one(
    reader: PostgresLayerReader,
) -> None:
    """The comparison the conformance kit makes, carried so a person can see
    it. `timestamp_utc` against `timestamptz` is a MATCH — the portable type
    exists precisely so that Databricks reporting `TIMESTAMP` is also one."""
    (members,) = reader.census(spec_of(Layer.SILVER_RAW)).tables
    columns = {c.name: c for c in members.columns}
    assert columns["ingestion_ts"].declared_type == "timestamp_utc"
    assert columns["ingestion_ts"].engine_type == "timestamptz"
    assert columns["member_row_id"].engine_type == "uuid"
    assert columns["date_of_birth"].engine_type == "date"
    # jsonb, not json — the DDL renderer's choice, read back off the engine.
    (raw,) = reader.census(spec_of(Layer.BRONZE)).tables
    assert {c.name: c.engine_type for c in raw.columns}["raw_row"] == "jsonb"


def test_bronze_is_reported_append_only_because_the_contract_says_so(
    reader: PostgresLayerReader,
) -> None:
    (raw,) = reader.census(spec_of(Layer.BRONZE)).tables
    assert raw.append_only is True
    (members,) = reader.census(spec_of(Layer.SILVER_RAW)).tables
    assert members.append_only is False


def test_a_table_the_plane_has_and_the_contract_does_not_is_reported_and_masked(
    reader: PostgresLayerReader, plane: Connection
) -> None:
    """A hand-made table in a governed schema. Reported rather than skipped —
    a screen that hides it is how it stays hidden — and every column masked,
    because unclassified is not public.

    Created inside the rolled-back transaction, so this exercises the real
    `information_schema` path and leaves nothing behind.
    """
    plane.execute("CREATE TABLE silver_raw.hand_made_fix (member_id text, ssn text, note text)")
    plane.execute("INSERT INTO silver_raw.hand_made_fix VALUES ('M0001', '078-05-1120', 'manual')")
    census = reader.census(spec_of(Layer.SILVER_RAW))
    found = {table.name: table for table in census.tables}
    assert "hand_made_fix" in found, "a table nothing declares must still be visible"
    intruder = found["hand_made_fix"]
    assert intruder.row_count == 1
    assert "NOT IN THE SCHEMA CONTRACT" in intruder.comment
    assert all(column.is_phi for column in intruder.columns)
    assert intruder.phi_column_count == 3


def test_the_count_is_exact_and_not_an_estimate(
    reader: PostgresLayerReader, plane: Connection
) -> None:
    """`reltuples` from `pg_class` is an ANALYZE-stale estimate, and a
    reconciliation screen whose counts are approximate cannot settle whether a
    batch balanced — which is the one thing these counts are for.

    Asserted by writing a row inside the transaction: a cached or estimated
    count would not move, and `reltuples` on a table this new is -1.
    """
    spec = spec_of(Layer.SILVER_RAW)
    before = reader.census(spec).tables[0].row_count
    assert before is not None
    plane.execute(
        "INSERT INTO silver_raw.members (member_row_id, feed_id, source_member_id, is_active, "
        "source_system, ingestion_ts, batch_id, record_hash, created_ts) "
        "VALUES (gen_random_uuid(), 'test-feed', 'M-EXACT', true, 'TEST', now(), "
        "'batch-exact', 'h', now())"
    )
    assert reader.census(spec).tables[0].row_count == before + 1


# ── rows ─────────────────────────────────────────────────────────────────────
def test_real_values_come_back_masked(reader: PostgresLayerReader, plane: Connection) -> None:
    """The path a seed cannot exercise: psycopg returns `date` and `uuid`
    OBJECTS, not strings, and the flagged ones must never be rendered."""
    plane.execute(
        "INSERT INTO silver_raw.members (member_row_id, feed_id, source_member_id, first_name, "
        "last_name, date_of_birth, gender, line_of_business, is_active, source_system, "
        "ingestion_ts, batch_id, record_hash, created_ts) "
        "VALUES (gen_random_uuid(), 'test-feed', 'M-SECRET', 'Ada', 'Okafor', "
        "'1936-02-01', 'F', 'MEDICARE', true, 'TEST', now(), 'batch-mask', 'h', now())"
    )
    spec = spec_of(Layer.SILVER_RAW)
    page = reader.rows(spec, table_of(spec, "members"), batch_id="batch-mask")
    (row,) = page.rows
    for column in ("source_member_id", "first_name", "last_name", "date_of_birth"):
        assert row[column].masked, column
    serialized = repr(page)
    for secret in ("Ada", "Okafor", "M-SECRET", "1936-02-01", "1936"):
        assert secret not in serialized, f"{secret} survived masking"
    # Not masked, and worth asserting: over-masking makes the screen useless.
    assert row["gender"].value == "F"
    assert row["line_of_business"].value == "MEDICARE"
    assert row["is_active"].value == "true"


def test_a_real_jsonb_column_keeps_its_keys_and_loses_its_values(
    reader: PostgresLayerReader, plane: Connection
) -> None:
    """psycopg hands `jsonb` back already parsed as a dict — the branch a
    string-based seed never reaches."""
    plane.execute(
        "INSERT INTO bronze.members_raw (bronze_id, feed_id, row_number, raw_row, "
        "source_system, ingestion_ts, batch_id, record_hash, created_ts) "
        "VALUES (gen_random_uuid(), 'test-feed', 1, "
        '\'{"MemberID": "M-SECRET", "Last_Name": "Okafor"}\'::jsonb, '
        "'TEST', now(), 'batch-json', 'h', now())"
    )
    spec = spec_of(Layer.BRONZE)
    page = reader.rows(spec, table_of(spec, "members_raw"), batch_id="batch-json")
    (row,) = page.rows
    rendered = str(row["raw_row"].value)
    assert "MemberID" in rendered and "Last_Name" in rendered
    assert "M-SECRET" not in rendered and "Okafor" not in rendered


def test_a_batch_filter_narrows_the_total_as_well_as_the_page(
    reader: PostgresLayerReader, plane: Connection
) -> None:
    """`total_rows` must be the count of what MATCHED, not the table's size —
    otherwise a filtered screen reports "25 of 294" for a batch of three."""
    for number in range(3):
        plane.execute(
            "INSERT INTO silver_raw.members (member_row_id, feed_id, source_member_id, "
            "is_active, source_system, ingestion_ts, batch_id, record_hash, created_ts) "
            "VALUES (gen_random_uuid(), 'test-feed', %s, true, 'TEST', now(), "
            "'batch-narrow', 'h', now())",
            (f"M-{number}",),
        )
    spec = spec_of(Layer.SILVER_RAW)
    page = reader.rows(spec, table_of(spec, "members"), batch_id="batch-narrow")
    assert page.total_rows == 3
    assert len(page.rows) == 3
    assert page.batch_id == "batch-narrow"
    assert page.truncated is False


def test_paging_by_primary_key_never_overlaps(
    reader: PostgresLayerReader, plane: Connection
) -> None:
    """A non-unique sort makes page 2 overlap page 1 silently, which is how a
    reader concludes a batch has duplicates it does not have. Every row here
    shares one `ingestion_ts`, which is the column a screen would naturally
    have sorted by."""
    for number in range(6):
        plane.execute(
            "INSERT INTO silver_raw.members (member_row_id, feed_id, source_member_id, "
            "is_active, source_system, ingestion_ts, batch_id, record_hash, created_ts) "
            "VALUES (gen_random_uuid(), 'test-feed', %s, true, 'TEST', "
            "'2026-06-01T06:00:00+00', 'batch-page', 'h', now())",
            (f"M-{number}",),
        )
    spec = spec_of(Layer.SILVER_RAW)
    table = table_of(spec, "members")
    first = reader.rows(spec, table, batch_id="batch-page", limit=3, offset=0)
    second = reader.rows(spec, table, batch_id="batch-page", limit=3, offset=3)
    keys = [row["member_row_id"].value for row in (*first.rows, *second.rows)]
    assert len(keys) == len(set(keys)) == 6
    assert first.truncated is True
    assert second.truncated is False


def test_the_page_size_is_capped_however_much_a_caller_asks_for(
    reader: PostgresLayerReader,
) -> None:
    """A disclosure guard, not a performance one: masking makes a row safe to
    look at, not a hundred thousand of them safe to export."""
    spec = spec_of(Layer.BRONZE)
    page = reader.rows(spec, table_of(spec, "members_raw"), limit=100_000)
    assert len(page.rows) <= MAX_PAGE


def test_an_identifier_that_did_not_come_from_the_contract_is_refused(
    reader: PostgresLayerReader,
) -> None:
    """The last line of the injection defence. Nothing should ever reach it —
    the API resolves names through `schema_spec` first — so a name arriving
    with a quote in it is a resolution bug, and it must fail here rather than
    compose into a query."""
    from cinqflow.adapters.local.pg_layers import _ident

    for hostile in ('members" --', "members; DROP TABLE bronze.members_raw", "a b"):
        with pytest.raises(ValueError, match="contract identifier"):
            _ident(hostile)
    assert _ident("members_raw") == '"members_raw"'


# ── the gate's evidence ──────────────────────────────────────────────────────
def test_quarantine_is_grouped_by_rule_and_carries_no_row(
    reader: PostgresLayerReader, plane: Connection
) -> None:
    """ "17 rows dropped" is a number nobody can act on. The rule id names the
    thing to go and fix — and the raw row is never selected, not even to be
    discarded, so no PHI enters a result set for no reader."""
    plane.execute(
        "INSERT INTO quarantine.quarantined_rows (quarantine_id, batch_id, stage_name, rule_id, "
        "reason, record_key, raw_row, quarantined_ts) VALUES "
        "(gen_random_uuid(), 'batch-q', 'silver_raw', 'DQ-999', 'invented for the test', '1', "
        '\'{"Last_Name": "Okafor"}\'::jsonb, now())'
    )
    reasons = reader.quarantine_reasons(batch_id="batch-q")
    assert [(r.rule_id, r.row_count) for r in reasons] == [("DQ-999", 1)]
    assert "Okafor" not in repr(reasons)


def test_reconciliation_reports_the_ledgers_verdict_and_derives_the_gap(
    reader: PostgresLayerReader, plane: Connection
) -> None:
    """`balanced` comes off the row an auditor reads; `unattributed` is
    computed. A batch recorded as balanced with unexplained rows behind it must
    show BOTH — that combination is what G3 exists to make impossible, so it
    has to be visible when it happens."""
    plane.execute(
        "INSERT INTO recon.recon_history (history_id, batch_id, feed_id, stage_name, "
        "records_in, records_out, quarantined, attributed_drops, balanced, recorded_ts) "
        "VALUES (gen_random_uuid(), 'batch-recon', 'test-feed', 'silver_raw', "
        "100, 90, 5, 0, true, now())"
    )
    (line,) = reader.reconciliation(batch_id="batch-recon")
    assert line.balanced is True  # what the ledger says
    assert line.unattributed == 5  # what the arithmetic says
    assert line.stage == "silver_raw"
