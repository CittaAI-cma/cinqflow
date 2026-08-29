"""The ONE contract suite for DDL rendering.

pg-compute today; databricks-compute later. Both render from the SAME spec, and
both run this file.

    "Given a golden pipeline produces different rows on the Postgres rendering
     and the Databricks Free rendering, when the nightly cross-engine
     comparison runs, then the mismatch fails the build."
    — CF-V0-E8-07, exception

These tests assert the properties a rendering must have REGARDLESS of dialect,
so a second renderer is certified by them rather than trusted.
"""

from __future__ import annotations

import re

import pytest

from cinqflow.adapters.local.pg_ddl import PostgresDdlRenderer
from cinqflow.core.schema_spec import BRONZE_SCHEMA, CONTROL_SCHEMA, all_schemas

pytestmark = pytest.mark.contract

RENDERERS = [pytest.param(PostgresDdlRenderer(), id="pg-compute")]


@pytest.mark.parametrize("renderer", RENDERERS)
def test_every_schema_in_the_spec_renders(renderer: PostgresDdlRenderer) -> None:
    for schema in all_schemas():
        statements = renderer.render_schema(schema)
        assert statements, f"{schema.name} rendered nothing"


@pytest.mark.parametrize("renderer", RENDERERS)
def test_every_column_in_the_spec_appears_in_the_rendering(
    renderer: PostgresDdlRenderer,
) -> None:
    """A renderer that silently drops a column would pass a smoke test and fail
    reconciliation months later, on a column nobody remembers declaring."""
    rendered = "\n".join(renderer.render_schema(CONTROL_SCHEMA))
    for table in CONTROL_SCHEMA.tables:
        for column in table.columns:
            assert re.search(rf"\b{column.name}\b", rendered), f"{table.name}.{column.name}"


@pytest.mark.parametrize("renderer", RENDERERS)
def test_decimals_render_with_their_declared_precision_and_scale(
    renderer: PostgresDdlRenderer,
) -> None:
    """FIG 08's first pinned divergence. An undeclared decimal is where money
    quietly changes between engines."""
    rendered = "\n".join(renderer.render_schema(CONTROL_SCHEMA))
    assert "NUMERIC(18,2)" in rendered.upper().replace(" ", "").replace("NUMERIC(", "NUMERIC(")


@pytest.mark.parametrize("renderer", RENDERERS)
def test_timestamps_render_as_utc_aware(renderer: PostgresDdlRenderer) -> None:
    """ "store UTC, derive business_date by explicit rule" — a naive timestamp
    column would make that rule unenforceable."""
    rendered = "\n".join(renderer.render_schema(CONTROL_SCHEMA)).upper()
    assert "TIMESTAMPTZ" in rendered or "TIMESTAMP WITH TIME ZONE" in rendered
    assert not re.search(r"\bTIMESTAMP\b(?!\s*WITH|TZ)", rendered.replace("TIMESTAMPTZ", ""))


@pytest.mark.parametrize("renderer", RENDERERS)
def test_bronze_immutability_renders_as_grants_and_a_trigger(
    renderer: PostgresDdlRenderer,
) -> None:
    """ "Enforce Bronze immutability structurally (insert-only grants PLUS a
    reject trigger)" — CF-V0-E8-07.

    Both, not either. Grants alone are bypassed by a superuser connection,
    which is exactly what a migration tool runs as.
    """
    rendered = "\n".join(renderer.render_schema(BRONZE_SCHEMA)).upper()
    assert "REVOKE" in rendered and "UPDATE" in rendered
    assert "CREATE OR REPLACE FUNCTION" in rendered
    assert "CREATE TRIGGER" in rendered or "CREATE OR REPLACE TRIGGER" in rendered


@pytest.mark.parametrize("renderer", RENDERERS)
def test_the_drop_ledger_constraint_reaches_the_database(
    renderer: PostgresDdlRenderer,
) -> None:
    """INVARIANTS.md marks this a SCHEMA-LEVEL constraint. A rule the pipeline
    respects is not the same as a rule the database enforces."""
    rendered = "\n".join(renderer.render_schema(CONTROL_SCHEMA)).lower()
    assert "check" in rendered
    assert "'other'" in rendered and "'unknown'" in rendered


@pytest.mark.parametrize("renderer", RENDERERS)
def test_the_balance_equation_is_a_check_constraint(renderer: PostgresDdlRenderer) -> None:
    """ "rows_in == rows_out + quarantined + attributed_drops" — enforceable in
    one place, so an unbalanced row cannot be written at all."""
    rendered = "\n".join(renderer.render_schema(CONTROL_SCHEMA)).lower()
    assert "records_in = records_out + quarantined + attributed_drops" in rendered


@pytest.mark.parametrize("renderer", RENDERERS)
def test_rendering_is_deterministic(renderer: PostgresDdlRenderer) -> None:
    """A migration that renders differently on two runs is a migration nobody
    can review."""
    assert renderer.render_schema(CONTROL_SCHEMA) == renderer.render_schema(CONTROL_SCHEMA)


@pytest.mark.parametrize("renderer", RENDERERS)
def test_rendering_is_idempotent_by_construction(renderer: PostgresDdlRenderer) -> None:
    """The installer is idempotent: "one command still stands up a complete
    fresh environment", run twice."""
    for statement in renderer.render_schema(CONTROL_SCHEMA):
        head = statement.strip().upper()
        if head.startswith(("CREATE SCHEMA", "CREATE TABLE", "CREATE INDEX")):
            assert "IF NOT EXISTS" in head, statement[:80]


@pytest.mark.parametrize("renderer", RENDERERS)
def test_the_introspection_signature_matches_the_spec_signature(
    renderer: PostgresDdlRenderer,
) -> None:
    """This is the comparison the conformance kit makes: each engine's
    introspected schema against the SPEC — never engine against engine."""
    for table in CONTROL_SCHEMA.tables:
        assert renderer.expected_signature(table) == table.signature
