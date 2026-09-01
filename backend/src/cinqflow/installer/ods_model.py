"""CF-V3-E10-01 — provision a PUBLISHED ODS model's real tables.

    "Given the claims model review resolved all differences, when the model
     deploys, then the ODS structures exist with audit columns and version
     tags."
    — CF-V3-E10-01

WHY THIS LIVES BESIDE `cli.py`, NOT INSIDE IT. `cinqflow install` provisions
the platform's STATIC schemas — `all_schemas()`, a Python literal, unchanged
until someone edits a plate. `silver_ods` is deliberately NOT filled in
there (`schema_spec.SILVER_ODS_SCHEMA` stays empty, by its own docstring):
its real content is a GOVERNED, VERSIONED value living in `metadata_db`, so
"what tables exist" can change without a code deploy, and so a draft with
undecided discrepancies never reaches a database at all. This module is the
installer's OWN rendering path (`PostgresDdlRenderer`, the same one `install`
uses), applied to whatever model a caller hands it — a published governed
object today, and unchanged if a future story sources that model from
somewhere else.

IDEMPOTENT BY THE SAME MECHANISM EVERY OTHER SCHEMA ALREADY IS. `render()`
turns an `OdsModel` into the exact `Schema`/`Table`/`Column` vocabulary
`PostgresDdlRenderer` already knows how to make safe — `CREATE ... IF NOT
EXISTS` — so deploying the same version twice, or resuming after a crash
mid-provision, costs nothing and duplicates nothing.
"""

from __future__ import annotations

from cinqflow.adapters.local.pg_control import Connection
from cinqflow.adapters.local.pg_ddl import PostgresDdlRenderer
from cinqflow.adapters.local.pg_ods_load import sequence_name
from cinqflow.core.registry.ods_model import OdsModel, render


def provision_ods_model(connection: Connection, model: OdsModel) -> tuple[str, ...]:
    """Render and execute one model version's DDL, plus one surrogate-key
    sequence per entity (CF-V3-E8-05 — `PostgresOdsLoad.next_surrogate_key`
    reads exactly this sequence, named the same way). Returns every
    statement run, so a caller can log or dry-run them the same way
    `cinqflow install --dry-run` already lets someone read before writing.

    `CREATE SEQUENCE IF NOT EXISTS` is idempotent the same way `CREATE
    TABLE IF NOT EXISTS` is: re-running this on an already-provisioned
    model costs nothing, and — because a sequence is never reset — a
    surrogate key already minted is never handed out again.
    """
    statements = PostgresDdlRenderer().render_schema(render(model))
    for statement in statements:
        connection.execute(statement)
    sequence_statements = tuple(
        f'CREATE SEQUENCE IF NOT EXISTS "silver_ods"."{sequence_name(entity.name)}";'
        for entity in model.entities
    )
    for statement in sequence_statements:
        connection.execute(statement)
    return (*statements, *sequence_statements)
