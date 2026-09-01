"""Wave-0 validation audit (2026-08-29) — the confirmed gaps, as executable tests.

Each test encodes ONE defect found by the Wave-0 completeness audit and is
marked `xfail(strict=True)`: today it fails, which documents the defect in the
suite itself; the day the defect is fixed the test XPASSes and strict mode
turns that into a suite failure, forcing the marker's removal. The file
therefore cannot go stale in either direction.

The assertions state the REQUIRED behaviour (the acceptance criterion), never
the broken behaviour — "acceptance criteria are the tests, written first."
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.api import create_app
from cinqflow.core.citations import CitationId, CitationKind
from cinqflow.core.schema_spec import all_schemas
from cinqflow.intelligence.evals import numeric_fidelity
from cinqflow.intelligence.tools import ToolContext, invoke
from cinqflow.ports.authn import Principal, Role, Scopes
from tests.contract.seeded_plane import BATCH_ID, build_plane

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

#: `parent.parent.parent` is `backend/`, not the repo root — the Python tree
#: moved one level down when the frontend became a sibling of it rather than a
#: subdirectory. The UI is up and across.
BACKEND = Path(__file__).parent.parent.parent
REPO = BACKEND.parent
UI_APP = REPO / "frontend" / "app"


# ── FIXED · the numeric-fidelity eval gate now sees a dropped zero ──────────
#
# intelligence/evals.py used to canonicalise with `rstrip(".0")`, which strips
# ALL trailing '.' and '0' characters, not a ".0" suffix: "21820" -> "2182",
# "22000" -> "22", "100" -> "1". The gate now strips trailing zeros only after
# an actual decimal point, so a bare integer is never touched.
def test_numeric_fidelity_rejects_a_dropped_zero() -> None:
    verdict = numeric_fidelity("2,182 rows reached Silver Raw", grounding="records_out: 21820")
    assert not verdict.passes, "an answer that drops a zero must fail the gate"


def test_numeric_fidelity_rejects_an_added_zero() -> None:
    verdict = numeric_fidelity("218,200 rows reached Silver Raw", grounding="records_out: 21820")
    assert not verdict.passes, "an answer that adds a zero must fail the gate"


# ── FIXED · every citation kind deep-links to a page that exists ────────────
#
# "clicking a citation opens that registry row" — CF-V0-E16-10, happy path.
# Six kinds (contract, plan, rule, term, error, file) are now real pages
# backed by certified tools through the generic /api/tools/{name} route
# (get_schema_contract, get_compiled_plan, lookup_reference,
# get_error_by_hash, get_file_by_fingerprint). `mapping` — the one kind no
# Wave-0 tool emits, since mapping authoring is Wave 1 — gets an honest
# "not yet available" page instead of fabricated data.
def _page_exists(route: str) -> bool:
    path = route.split("?", 1)[0]
    node = UI_APP
    for segment in (s for s in path.split("/") if s):
        literal = node / segment
        if literal.is_dir():
            node = literal
            continue
        dynamic = sorted(d for d in node.iterdir() if d.is_dir() and d.name.startswith("["))
        if dynamic:
            node = dynamic[0]
            continue
        return False
    return (node / "page.tsx").exists()


@pytest.mark.parametrize("kind", list(CitationKind))
def test_every_citation_kind_opens_a_real_ui_page(kind: CitationKind) -> None:
    citation = CitationId(kind, "x1")
    assert _page_exists(citation.route), (
        f"{citation} routes to {citation.route}, which has no page under frontend/app — "
        "a well-formed citation that resolves to a 404 reads as evidence and opens nothing"
    )


# ── FIXED · the connection profile names only adapters that exist ───────────
#
# Law 3: "climbing a rung changes only the profile". That is only true if a
# profile's chosen adapter NAMES a registered adapter. local.yaml, ci.yaml and
# mock.yaml used to name ~11 adapters apiece with no registration anywhere in
# src/ — pins genuinely unfitted at rung 0.5 now say `none`, matching the
# ADR-0014 pattern `cache` already used, rather than naming vaporware.
def _registered_adapters() -> None:
    import cinqflow.adapters.local
    import cinqflow.adapters.mock
    import cinqflow.adapters.openai_compatible
    import cinqflow.adapters.replay  # noqa: F401


@pytest.mark.parametrize("filename", ["local.yaml", "ci.yaml", "mock.yaml"])
def test_every_shipped_profile_names_only_registered_adapters(filename: str) -> None:
    from cinqflow.installer.profile import load
    from cinqflow.ports import PIN_GROUPS, fitted

    _registered_adapters()
    profile = load(BACKEND / "profiles" / filename)
    missing = {
        pin: chosen
        for pins in PIN_GROUPS.values()
        for pin in pins
        if (chosen := profile.adapter_for(pin)) not in {"none", None} and chosen not in fitted(pin)
    }
    assert not missing, (
        f"{filename} names adapters that are not registered: {missing} — "
        "the platform cannot know which environment it is actually in"
    )


# ── FIXED · the conformance kit now fails a pin whose chosen adapter is fiction
#
# kit.check_pin used to verify whatever adapters happened to be registered,
# using the profile's choice only for the `none` case. It now fails a pin
# whose profile-chosen adapter is not among the registered ones — that GREEN
# was unearned.
def test_conformance_kit_flags_a_profile_choosing_an_unregistered_adapter() -> None:
    from conformance.kit import Verdict, check_pin

    from cinqflow.core.model.profile import Profile
    from cinqflow.core.model.vocabulary import Mode

    _registered_adapters()
    # No shipped profile names a fictional adapter any more (that IS the fix);
    # this constructs one synthetically to prove the kit would catch it if one
    # did. ('presidio' was the synthetic fiction until Wave 1 fitted it for
    # real — the fiction has to stay fictional for the test to keep proving
    # anything.)
    profile = Profile(
        source="synthetic",
        rung=0.5,
        socket="postgres_plane",
        mode=Mode.FULL,
        pins={"phi_scrub": {"adapter": "an-adapter-nobody-wrote"}},
    )
    check = check_pin("phi_scrub", profile)
    assert check.verdict is Verdict.FAIL, (
        "a profile naming 'an-adapter-nobody-wrote', which is not registered, must FAIL "
        "this pin — certifying it PASS or UNFITTED both grade a socket that does not exist"
    )


# ── FIXED · the flagship citation `recon:<batch>#<rule>` highlights its row ──
#
# get_drop_ledger emits citations whose fragment is a rule_id; the route opens
# the recon panel and highlights rows where row.rule_id == fragment.
# get_reconciliation now emits one row per named drop (rule_id populated)
# alongside its stage-balance rows (rule_id None), so the highlight fires.
def _tool_context() -> ToolContext:
    store, control = build_plane()
    return ToolContext(
        principal=Principal(
            subject="auditor@cinqcare.test",
            display_name="Auditor",
            roles=frozenset({Role.ENGINEER}),
            scopes=Scopes(feeds=frozenset({"*"}), domains=frozenset({"*"})),
        ),
        control=control,
        metadata=store,
        run_id="audit-run",
    )


def test_the_recon_panel_can_satisfy_a_drop_ledger_citation_fragment() -> None:
    context = _tool_context()
    ledger = invoke(context, "get_drop_ledger", {"batch_id": BATCH_ID})
    panel = invoke(context, "get_reconciliation", {"batch_id": BATCH_ID})

    fragments = {c.fragment for c in ledger.citations if c.fragment}
    assert fragments, "seeded plane must yield at least one drop-ledger citation"
    for fragment in fragments:
        assert any(str(row.get("rule_id")) == fragment for row in panel.rows), (
            f"recon:{BATCH_ID}#{fragment} opens the recon panel, but no recon-panel row "
            f"carries rule_id={fragment!r} — the highlight the citation promises cannot fire"
        )


# ── GAP 6 · the audit trail and the registry have no persistent home ─────────
#
# "Write every tool invocation to audit.agent_action" — CF-V0-E16-09. The DDL
# provisions no audit schema and no registry schema: every agent action, feed,
# DQ rule and governance row lives only in process memory and evaporates on
# exit, on every rung including 0.5.
def _provisioned_tables() -> set[str]:
    return {f"{schema.name}.{table.name}" for schema in all_schemas() for table in schema.tables}


# ── FIXED · the audit trail and the registry now have a persistent home ─────
#
# schema_spec now provisions the three plane objects core/registry/wave0.py
# already declared by object_id: registry.governed_object (one table, every
# object type — ADR-0006's "one lifecycle" means one table, not a table per
# type), governance.audit_ledger, and audit.agent_action. PostgresMetadataDb
# implements the MetadataDbPort against all three and passes the same
# contract suite MemMetadataDb does (tests/contract/test_platform_contracts.py).
def test_ddl_provisions_the_agent_action_audit_table() -> None:
    tables = _provisioned_tables()
    assert any(table.endswith(".agent_action") for table in tables), (
        f"no agent_action table in any provisioned schema ({sorted(tables)}) — "
        "'every tool invocation is audited' is only true for the life of a Python object"
    )


def test_ddl_provisions_the_registry_and_governance_tables() -> None:
    tables = _provisioned_tables()
    for required in ("registry.governed_object", "governance.audit_ledger"):
        assert required in tables, (
            f"{required} is not provisioned — governed metadata does not survive a process restart"
        )


# ── GAP 7 · declared control tables that no code reads or writes ─────────────
#
# The control plane is declared as 11 tables; several existed only in DDL.
# Every drift finding — blocking or not — is recorded to schema_drift_log
# (workers/pipeline.py, ControlTablesPort.record_schema_drift). `feed_sla_config`,
# `sla_instance` and `sla_alerts` were the last three, and CF-V2-E12-01/05
# (W2-14, `ports.control_tables.upsert_sla_instance` and friends, in both the
# mock and pg-control adapters) is what closes this GAP — this test is now a
# PERMANENT REGRESSION GUARD rather than a documented deficiency.
#
# Still declared and unwritten: `landing_ctl.landing_event`. Wave 2's clock
# does not touch it; it is not this gap's concern.
_CONTROL_TABLE_WRITERS_MUST_EXIST = (
    "sla_instance",
    "sla_alerts",
    "feed_sla_config",
    # CF-V2-E7-05: provisioned and written in the same commit — the
    # provisioned-and-unwritten era is what this test exists to end.
    "rule_results",
)


@pytest.mark.parametrize("table", _CONTROL_TABLE_WRITERS_MUST_EXIST)
def test_every_declared_control_table_is_used_by_some_code(table: str) -> None:
    # The name may appear in declaration lists and tool descriptions; what
    # matters is the layer that executes SQL: adapters and workers.
    sql_layers = (BACKEND / "src" / "cinqflow" / "adapters", BACKEND / "src" / "cinqflow" / "workers")
    users = [
        path
        for layer in sql_layers
        for path in layer.rglob("*.py")
        if table in path.read_text(encoding="utf-8")
    ]
    assert users, (
        f"control.{table} is provisioned but no adapter or worker reads or writes it — "
        "a control table nothing writes is a façade, not a control"
    )


# ── FIXED · uninstall now validates manifest identifiers before dropping ────
#
# installer/cli.py used to build `DROP <KIND> IF EXISTS <identifier> CASCADE`
# by f-string straight from the user-writable installation manifest. It now
# refuses (UnsafeManifestIdentifierError) any identifier that isn't a bare
# name or `schema.table` — the only shapes this installer ever writes.
def test_uninstall_refuses_a_malicious_manifest_identifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cinqflow.installer import cli

    manifest = {
        "profile": "profiles/local.yaml",
        "rung": 0.5,
        "socket": "postgres_plane",
        "spec_fingerprints": {},
        "installed_ts": "2026-08-29T00:00:00+00:00",
        "manifest_version": 1,
        "objects": [
            {
                "kind": "table",
                "identifier": "control.batch_control; DROP DATABASE cinqflow; --",
                "created_ts": "2026-08-29T00:00:00+00:00",
            }
        ],
    }
    manifest_path = tmp_path / "installation-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    executed: list[str] = []
    monkeypatch.setattr(cli, "_execute", lambda _profile, statements: executed.extend(statements))

    try:
        cli.uninstall(
            profile=BACKEND / "profiles" / "local.yaml", manifest_path=manifest_path, yes=True
        )
    except Exception:
        executed.clear()

    assert not any("DROP DATABASE" in statement for statement in executed), (
        "a tampered manifest identifier reached the DROP statement verbatim — "
        "uninstall must validate identifiers before interpolating them into SQL"
    )


# ── FIXED · /api/batches now checks a query-param feed_id's scope ───────────
#
# require(Action.VIEW) used to read feed_id from PATH params only;
# list_batches takes it as a QUERY param, so the scope check saw feed_id=None
# and always passed. api/deps.py:require now reads both path and query params.
def test_a_caller_scoped_to_another_feed_cannot_list_this_feeds_batches() -> None:
    from cinqflow.adapters.mock.metadata_db import MemMetadataDb

    _store, control = build_plane()
    scoped_user = Principal(
        subject="scoped@cinqcare.test",
        display_name="Scoped Viewer",
        roles=frozenset({Role.READ_ONLY}),
        scopes=Scopes(
            feeds=frozenset({"some-other-feed"}),
            domains=frozenset({"*"}),
            environments=frozenset({"dev"}),
        ),
    )
    app = create_app(
        authn=StaticAuthn({"scoped@cinqcare.test": scoped_user}),
        metadata_db=MemMetadataDb(),
        control_tables=control,
    )
    with TestClient(app) as client:
        response = client.get(
            "/api/batches",
            params={"feed_id": "fidelis-downstate-roster"},
            headers={"authorization": "Bearer scoped@cinqcare.test"},
        )
    leaked = response.json() if response.status_code == 200 else []
    assert response.status_code in {403, 404} or leaked == [], (
        f"a viewer scoped to 'some-other-feed' listed {len(leaked)} batches of "
        "'fidelis-downstate-roster' — the scope must live in the query, not the menu"
    )
