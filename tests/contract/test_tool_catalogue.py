"""CF-V0-E16-09 — the guarantees, asserted over the CATALOGUE.

    "no tool in the catalogue can emit a data-layer row"
    "100% of tool results carry a resolvable citation; zero tools return
     member-level rows"
    — CF-V0-E16-09, guardrail and measurable

The canary test below is the one that matters. It seeds a plane whose member
data contains a marker string, invokes ALL SEVENTEEN tools with every plausible
argument, and asserts the marker never appears anywhere in any result. That is
a test that makes the attempt; a review of seventeen implementations is not.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest

from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.core.citations import parse
from cinqflow.core.model.agent_action import ActionOutcome
from cinqflow.core.model.governed import Actor
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry import feed as feed_registry
from cinqflow.core.tools import (
    CATALOGUE,
    FORBIDDEN_READS,
    READ_ONLY_WHITELIST,
    ArgumentError,
    ToolError,
    ToolSpec,
    signatures,
    spec_for,
)
from cinqflow.intelligence.tools import (
    OUT_OF_SCOPE,
    ToolContext,
    ToolNotWhitelistedError,
    invoke,
)
from cinqflow.ports.authn import Principal, Role, Scopes
from tests.contract.seeded_plane import (
    BATCH_ID,
    CANARY,
    ERROR_ID_HASH,
    FEED_ID,
    FINGERPRINT,
    NOW,
    build_plane,
)

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

AUTHOR = Actor(subject="arun@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Arun")


@pytest.fixture
def seeded() -> tuple[MemMetadataDb, MemStoreControlTables]:
    return build_plane()


def _context(
    seeded: tuple[MemMetadataDb, MemStoreControlTables], *, feeds: frozenset[str] = frozenset({"*"})
) -> ToolContext:
    store, control = seeded
    return ToolContext(
        principal=Principal(
            subject="priya@cinqcare.test",
            display_name="Priya Nair",
            roles=frozenset({Role.ENGINEER}),
            scopes=Scopes(feeds=feeds, domains=frozenset({"*"})),
        ),
        control=control,
        metadata=store,
        run_id="run-1",
        now=NOW + timedelta(hours=1),
    )


def _every_call() -> list[tuple[str, dict[str, Any]]]:
    """Every tool, with every plausible argument a caller could supply."""
    calls: list[tuple[str, dict[str, Any]]] = []
    for name, spec in CATALOGUE.items():
        arguments: dict[str, Any] = {}
        for parameter in spec.parameters:
            match parameter.name:
                case "feed_id":
                    arguments["feed_id"] = FEED_ID
                case "batch_id":
                    arguments["batch_id"] = BATCH_ID
                case "query":
                    arguments["query"] = "quarantine"
                case "window_days":
                    arguments["window_days"] = 400
                case "limit":
                    arguments["limit"] = 20
                case "error_id_hash":
                    arguments["error_id_hash"] = ERROR_ID_HASH
                case "fingerprint":
                    arguments["fingerprint"] = FINGERPRINT
        calls.append((name, arguments))
        # Also the unfiltered variant, where a filter is optional.
        if spec.name in {"list_feeds", "list_errors"}:
            calls.append((spec.name, {k: v for k, v in arguments.items() if k == "batch_id"}))
    return calls


# ── THE guardrail ────────────────────────────────────────────────────────────


def test_no_tool_in_the_catalogue_can_emit_a_member_level_row(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    context = _context(seeded)
    for name, arguments in _every_call():
        result = invoke(context, name, arguments)
        rendered = json.dumps(result.rows, default=str) + result.note + result.marker
        assert CANARY not in rendered, (
            f"{name} emitted member-level data. No tool in the catalogue may reach a data "
            "layer — operational truth reaches a model as counts, reasons, rule ids and "
            "column names."
        )


def test_the_error_log_tool_returns_the_rule_not_the_record_key(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    """`record_key` identifies a MEMBER. It is deliberately not projected."""
    result = invoke(_context(seeded), "list_errors", {"batch_id": BATCH_ID})
    (row,) = result.rows
    assert row["rule_id"] == "DQ-002"
    assert "record_key" not in row


def test_an_error_resolves_by_hash_alone_no_batch_id_required(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    """The `error:<hash>` citation's own lookup — GAP of the Wave-0 audit:
    `list_errors` is batch-scoped, but a citation carries only the hash."""
    result = invoke(_context(seeded), "get_error_by_hash", {"error_id_hash": ERROR_ID_HASH})
    (row,) = result.rows
    assert row["error_id_hash"] == ERROR_ID_HASH
    assert row["batch_id"] == BATCH_ID
    assert "record_key" not in row


def test_a_hash_with_no_matching_error_is_absent_not_an_error(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    result = invoke(_context(seeded), "get_error_by_hash", {"error_id_hash": "no-such-hash"})
    assert result.out_of_scope is True


def test_a_file_resolves_by_fingerprint_alone_no_feed_id_required(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    """The `file:<hash>` citation's own lookup — the mirror of the error fix,
    for the port verb (`find_input_by_fingerprint`) that already existed."""
    result = invoke(_context(seeded), "get_file_by_fingerprint", {"fingerprint": FINGERPRINT})
    (row,) = result.rows
    assert row["fingerprint"] == FINGERPRINT
    assert row["feed_id"] == FEED_ID
    # `key` — the storage path, which carries the canary in this fixture — is
    # deliberately not projected; only `filename` is.
    assert "key" not in row
    assert CANARY not in json.dumps(row, default=str)


def test_no_spec_declares_a_data_layer_read() -> None:
    for spec in CATALOGUE.values():
        assert not spec.reads & FORBIDDEN_READS, spec.name


def test_a_tool_declaring_a_data_layer_read_cannot_be_constructed() -> None:
    from cinqflow.core.citations import CitationKind

    with pytest.raises(ToolError, match="No tool in the catalogue may reach a data layer"):
        ToolSpec(
            name="peek_at_bronze",
            answers="whatever",
            reads=frozenset({"bronze.members_raw"}),
            cites=(CitationKind.BATCH,),
        )


# ── the catalogue, as a surface ──────────────────────────────────────────────


def test_there_are_exactly_seventeen_certified_tools() -> None:
    assert len(CATALOGUE) == 17
    assert set(CATALOGUE) == set(READ_ONLY_WHITELIST)


def test_every_tool_declares_a_citation_kind() -> None:
    for spec in CATALOGUE.values():
        assert spec.cites, f"{spec.name} cites nothing; uncited claims are a defect class"


def test_a_tool_that_cites_nothing_cannot_be_constructed() -> None:
    with pytest.raises(ToolError, match="cites nothing"):
        ToolSpec(name="silent", answers="x", reads=frozenset({"control.batch_control"}))


def test_every_signature_is_valid_json_schema_the_model_can_read() -> None:
    for schema in signatures().as_json_schemas():
        assert schema["name"] in CATALOGUE
        assert schema["parameters"]["type"] == "object"
        assert schema["parameters"]["additionalProperties"] is False


def test_no_tool_accepts_free_text_sql() -> None:
    """Text-to-tool, never text-to-SQL, until CF-V4-E14-04."""
    for spec in CATALOGUE.values():
        for parameter in spec.parameters:
            assert parameter.name not in {"sql", "query_sql", "where", "filter"}, spec.name


def test_every_result_carries_a_resolvable_citation(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    context = _context(seeded)
    for name, arguments in _every_call():
        result = invoke(context, name, arguments)
        for row in result.rows:
            citation = row.get("citation_id")
            assert citation, f"{name} returned an uncited row"
            assert parse(str(citation)).route.startswith("/"), f"{name}: {citation}"


# ── scope is applied inside the query ────────────────────────────────────────


def test_an_out_of_scope_feed_returns_empty_with_an_explicit_marker(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    context = _context(seeded, feeds=frozenset({"some-other-feed"}))
    result = invoke(context, "get_feed", {"feed_id": FEED_ID})
    assert result.rows == ()
    assert result.out_of_scope
    assert result.marker == OUT_OF_SCOPE


def test_an_out_of_scope_feed_is_indistinguishable_from_one_that_does_not_exist(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    context = _context(seeded, feeds=frozenset({"*"}))
    absent = invoke(context, "get_feed", {"feed_id": "no-such-feed"})
    narrow = invoke(
        _context(seeded, feeds=frozenset({"nothing"})), "get_feed", {"feed_id": FEED_ID}
    )
    assert (absent.rows, absent.marker) == (narrow.rows, narrow.marker)


def test_an_out_of_scope_batch_is_refused_by_resolving_its_feed_first(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    context = _context(seeded, feeds=frozenset({"some-other-feed"}))
    result = invoke(context, "get_reconciliation", {"batch_id": BATCH_ID})
    assert result.out_of_scope
    assert result.rows == ()


def test_lists_are_filtered_where_they_are_built(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    assert invoke(_context(seeded, feeds=frozenset({"other"})), "list_feeds", {}).rows == ()
    assert invoke(_context(seeded), "list_feeds", {}).row_count == 1


def test_the_out_of_scope_attempt_is_logged(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    store, _ = seeded
    invoke(_context(seeded, feeds=frozenset({"other"})), "get_feed", {"feed_id": FEED_ID})
    (row,) = store.read_agent_actions(run_id="run-1")
    assert row.outcome is ActionOutcome.REFUSED_PERMISSION
    assert row.action == "tool:get_feed"


# ── every invocation is audited ──────────────────────────────────────────────


def test_every_invocation_writes_an_audit_row_with_caller_tool_args_and_row_count(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    store, _ = seeded
    context = _context(seeded)
    invoke(context, "get_reconciliation", {"batch_id": BATCH_ID})
    (row,) = store.read_agent_actions(run_id="run-1")
    assert row.action == "tool:get_reconciliation"
    assert row.actor.subject == "priya@cinqcare.test"
    assert row.actor.actor_type is ActorType.HUMAN
    # One stage-balance row plus one row per named drop (the seeded plane's
    # one stage carries two) — see _get_reconciliation.
    assert "batch_id" in row.detail and "rows=3" in row.detail


def test_a_tool_outside_the_whitelist_is_refused_and_recorded(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    store, _ = seeded
    context = _context(seeded)
    context.whitelist = frozenset({"get_batch"})
    with pytest.raises(ToolNotWhitelistedError, match="R0"):
        invoke(context, "get_reconciliation", {"batch_id": BATCH_ID})
    (row,) = store.read_agent_actions(run_id="run-1")
    assert row.outcome is ActionOutcome.REFUSED_NOT_WHITELISTED


def test_an_unknown_tool_name_is_refused_naming_the_catalogue(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    from cinqflow.core.tools import UnknownToolError

    context = _context(seeded)
    context.whitelist = context.whitelist | {"retry_batch"}
    with pytest.raises(UnknownToolError, match="is not a certified tool"):
        invoke(context, "retry_batch", {})


# ── arguments ────────────────────────────────────────────────────────────────


def test_an_unbounded_window_is_refused(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    with pytest.raises(ArgumentError, match="full table scan"):
        invoke(_context(seeded), "list_batches", {"feed_id": FEED_ID, "window_days": 9999})


def test_an_undeclared_argument_is_refused(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    with pytest.raises(ArgumentError, match="has no parameter"):
        invoke(_context(seeded), "get_batch", {"batch_id": BATCH_ID, "sql": "DROP TABLE"})


def test_a_missing_required_argument_names_the_parameter(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    with pytest.raises(ArgumentError, match="requires feed_id"):
        invoke(_context(seeded), "get_feed", {})


def test_a_bad_enum_choice_lists_the_choices(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    with pytest.raises(ArgumentError, match="VALIDATION_ERROR"):
        invoke(_context(seeded), "list_errors", {"batch_id": BATCH_ID, "category": "vibes"})


# ── the happy path from the story ────────────────────────────────────────────


def test_the_drop_ledger_names_dq_002_and_the_structure_check(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    result = invoke(_context(seeded), "get_drop_ledger", {"batch_id": BATCH_ID})
    assert {row["rule_id"] for row in result.rows} == {"DQ-002", "STRUCTURE-001"}
    assert all(str(c).startswith("recon:8842#") for c in result.citations)


def test_reconciliation_reports_the_balance_equation(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    """The stage-balance row — `rule_id is None` — carries the equation. The
    rows after it are one per named drop, so `recon:<batch>#<rule>` can
    highlight the row it actually names (see GAP 5 of the Wave-0 audit)."""
    rows = invoke(_context(seeded), "get_reconciliation", {"batch_id": BATCH_ID}).rows
    (stage_row,) = [row for row in rows if row["rule_id"] is None]
    assert (
        stage_row["records_in"]
        == stage_row["records_out"] + stage_row["quarantined"] + stage_row["attributed_drops"]
    )
    assert stage_row["balances"] is True
    assert stage_row["unexplained"] == 0

    drop_rows = [row for row in rows if row["rule_id"] is not None]
    assert {row["rule_id"] for row in drop_rows} == {"DQ-002", "STRUCTURE-001"}
    assert all(row["citation_id"] == f"recon:{BATCH_ID}#{row['rule_id']}" for row in drop_rows)


def test_quarantine_summary_returns_counts_reasons_rules_and_columns_only(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    (row,) = invoke(_context(seeded), "get_quarantine_summary", {"batch_id": BATCH_ID}).rows
    assert set(row) == {"stage", "rule_id", "reason", "column_names", "record_count", "citation_id"}
    assert row["record_count"] == 175


def test_the_compiled_plan_is_returned_step_by_step(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    result = invoke(_context(seeded), "get_compiled_plan", {"feed_id": FEED_ID})
    steps = [row["step"] for row in result.rows]
    assert steps[0] == "read"
    assert steps[-1] == "reconcile"
    assert "evaluate_rules" in steps
    assert all(str(c) == f"plan:{FEED_ID}@v1" for c in result.citations)


def test_lookup_reference_finds_a_platform_term_lexically(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    result = invoke(_context(seeded), "lookup_reference", {"query": "balance equation"})
    assert result.rows[0]["term"] == "balance equation"
    assert result.rows[0]["citation_id"].startswith("term:")
    assert result.rows[0]["matched"]


def test_lookup_reference_needs_no_feed_scope(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    """A definition is not somebody's data."""
    context = _context(seeded, feeds=frozenset())
    assert invoke(context, "lookup_reference", {"query": "bronze"}).row_count > 0


def test_an_unpublished_feed_is_not_explained(
    seeded: tuple[MemMetadataDb, MemStoreControlTables],
) -> None:
    """The engine reads published metadata, so the agent explains published
    metadata. Explaining a draft would describe a pipeline that cannot run."""
    store, _ = seeded
    store.save(
        feed_registry.FeedRecord(
            feed_id="draft-feed",
            domain="membership",
            source_system="x",
            file_format="csv",
            landing_path="landing/x",
            file_pattern=r"^x_\d{8}\.csv$",
            schedule_cron="0 6 * * 1",
            sample_filename="x_20260801.csv",
        ).as_governed(author=AUTHOR)
    )
    assert invoke(_context(seeded), "get_feed", {"feed_id": "draft-feed"}).out_of_scope


def test_spec_for_refuses_a_name_that_is_not_in_the_catalogue() -> None:
    from cinqflow.core.tools import UnknownToolError

    with pytest.raises(UnknownToolError):
        spec_for("get_member")
