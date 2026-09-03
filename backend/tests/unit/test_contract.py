"""The contract is engine-neutral; only pg.py knows SQL. These tests pin both."""

from __future__ import annotations

import pytest

from cinqflow.dataplane import contract
from cinqflow.dataplane.contract import (
    AUDIT_COLUMNS,
    Layer,
    StageCounts,
    UnsafeIdentifier,
    bronze_table,
    record_hash,
    table_identifier,
)
from cinqflow.dataplane.pg import render_table


def test_bronze_table_is_source_aligned_with_audit_columns():
    table = bronze_table("member_roster")

    assert table.layer is Layer.BRONZE
    assert table.qualified == "bronze.member_roster_raw"
    assert table.append_only is True

    names = [c.name for c in table.columns]
    # the source row is preserved whole - no semantic mapping at Bronze
    assert "raw_row" in names
    assert "row_number" in names
    for audit in AUDIT_COLUMNS:
        assert audit.name in names
    assert table.phi_columns == ("raw_row",)


def test_bronze_table_does_not_collide_with_the_previous_build():
    """The prior implementation owns bronze.members_raw; this build must not."""
    assert bronze_table("member_roster").name != "members_raw"


@pytest.mark.parametrize("feed", ["member_roster", "adt", "claims_cclf"])
def test_feed_names_become_safe_identifiers(feed):
    assert table_identifier(feed) == feed


@pytest.mark.parametrize(
    "bad", ['roster"; DROP TABLE x;--', "1roster", "ROSTER;", "a" * 60, "", "roster'"]
)
def test_unsafe_feed_names_are_refused_before_reaching_sql(bad):
    with pytest.raises(UnsafeIdentifier):
        table_identifier(bad)


def test_render_emits_idempotent_statements_and_the_append_only_guard():
    statements = render_table(bronze_table("member_roster"))
    joined = "\n".join(statements)

    assert "CREATE TABLE IF NOT EXISTS" in joined
    assert "CREATE INDEX IF NOT EXISTS" in joined
    assert "TIMESTAMPTZ" in joined and "JSONB" in joined and "BIGINT" in joined
    # append-only is enforced by a trigger, in the layer's own schema
    assert "CREATE TRIGGER" in joined
    assert "bronze.cinqflow_append_only_guard()" in joined
    assert "REVOKE UPDATE, DELETE, TRUNCATE" in joined
    # and it must not touch the previous build's function
    assert "public.cinqflow_reject_mutation" not in joined


def test_record_hash_is_content_addressed_and_order_independent():
    a = record_hash({"member_id": "M1", "product": "TANF Adult"})
    b = record_hash({"product": "TANF Adult", "member_id": "M1"})
    c = record_hash({"member_id": "M2", "product": "TANF Adult"})
    assert a == b
    assert a != c


def test_batch_ids_are_unique_and_short():
    ids = {contract.new_batch_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(len(i) == 12 for i in ids)


def test_balance_equation():
    assert StageCounts(records_in=10, records_out=10).balanced
    assert StageCounts(records_in=10, records_out=8, quarantined=2).balanced
    assert StageCounts(records_in=10, records_out=7, quarantined=2, attributed_drops=1).balanced
    assert not StageCounts(records_in=10, records_out=9).balanced
