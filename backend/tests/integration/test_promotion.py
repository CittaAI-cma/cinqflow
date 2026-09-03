"""Promotion against real Postgres: Silver fan-out, quarantine, balance, replay.

The mapping here spans four canonical entities, because one roster row genuinely
populates several of them - that fan-out is the thing Stage 6 has to get right.
"""

from __future__ import annotations

from datetime import date

import psycopg
import pytest

from cinqflow.dataplane.contract import bronze_table, quarantine_table, silver_table
from cinqflow.dataplane.filestore import FileStore, Folder, fingerprint_bytes, landing_key
from cinqflow.dataplane.pg import PostgresDataPlane
from cinqflow.engine.mapping_exec import spec_fingerprint
from cinqflow.engine.runner import PipelineRunner, PromotionFailure
from cinqflow.knowledge.canonical import load_canonical
from cinqflow.knowledge.yaml_provider import YamlKnowledgeProvider
from cinqflow.workers import interpret_upload, land_bronze, profile_upload, promote_silver
from cinqflow.workflow.models import MappingField, MappingSpec, Transform
from cinqflow.workflow.states import RunState, UploadStatus
from cinqflow.workflow.store import WorkflowStore
from tests.conftest import requires_db

pytestmark = requires_db

FEED = "test_promote_feed"

#: Five rows, each making a different point.
ROSTER = (
    b"member_id,member_first_name,member_dob,member_sex,member_email,member_city,member_phone\n"
    b"M001,DANIELLE,1997-11-04,F,d@example.org,ALBANY,5550100\n"      # writes 4 entities
    b"M002,KEVIN,13/45/1990,M,k@example.org,TROY,5550101\n"           # unparseable date
    b",ALEX,2000-01-01,M,a@example.org,UTICA,5550102\n"               # no member id
    b"M004,SAM,2001-02-03,X,s@example.org,ROME,5550103\n"             # sex not in the map
    b"M005,JO,2002-03-04,F,,,\n"                                      # member only
)

ENTITIES = ("members", "members_addresses", "members_emails", "members_phones")


def _spec() -> MappingSpec:
    return MappingSpec(
        target_table="silver_raw.members",
        fields=[
            MappingField(source="member_id", target="members.source_system_id", on_null="reject"),
            MappingField(source="member_first_name", target="members.first_name"),
            MappingField(
                source="member_dob",
                target="members.date_of_birth",
                cast="timestamp",
                transform=Transform(op="parse_date", args={"format": "%Y-%m-%d"}),
            ),
            MappingField(
                source="member_sex",
                target="members.sex",
                value_map={"M": "male", "F": "female"},
                on_unmapped_value="quarantine",
            ),
            MappingField(source="member_email", target="members_emails.email_address"),
            MappingField(source="member_city", target="members_addresses.city"),
            MappingField(source="member_phone", target="members_phones.phone_number"),
        ],
    )


@pytest.fixture(autouse=True)
def drop_feed_tables(conn, settings):
    """The bronze table is feed-named and shared; silver lives in a per-test schema."""
    yield
    conn.rollback()
    table = bronze_table(FEED)
    with conn.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS {table.schema}."{table.name}" CASCADE')
    conn.commit()


@pytest.fixture
def approved(conn, settings):
    """A landed batch and a G2-approved mapping version over it."""
    key = landing_key(
        domain="enrollments",
        source_system="fidelis_ny_upstate",
        feed=FEED,
        folder=Folder.INCOMING,
        business_date="2026-06-01",
        filename="roster.csv",
    )
    FileStore(settings).place(key, ROSTER)
    store = WorkflowStore(conn, settings)
    upload = store.create_upload(
        fingerprint=fingerprint_bytes(ROSTER),
        filename="roster.csv",
        file_type="csv",
        size_bytes=len(ROSTER),
        uploader="analyst@cinqcare.com",
        source_system="fidelis_ny_upstate",
        feed=FEED,
        domain="enrollments",
        business_date=date(2026, 6, 1),
        landing_key=key,
    )
    profile_upload.handle(conn, {"upload_id": upload.upload_id}, settings)
    interpret_upload.handle(conn, {"upload_id": upload.upload_id}, settings)
    interpretation = store.get_interpretation(upload.upload_id)
    store.put_approval(
        gate="G1",
        artifact_type="interpretation",
        artifact_id=interpretation.interpretation_id,
        artifact_version=interpretation.version,
        upload_id=upload.upload_id,
        decision="approved",
        approver="analyst@cinqcare.com",
    )
    store.set_status(upload.upload_id, UploadStatus.APPROVED)
    conn.commit()
    landed = land_bronze.handle(conn, {"upload_id": upload.upload_id}, settings)
    mapping = store.create_mapping_version(
        feed=FEED, domain="enrollment", spec=_spec(), created_by="analyst@cinqcare.com"
    )
    store.approve_mapping_version(
        feed=FEED,
        version=mapping.version,
        upload_id=upload.upload_id,
        approver="analyst@cinqcare.com",
    )
    conn.commit()
    return landed["batch_id"], upload


def _promote(conn, settings, batch_id: str) -> dict:
    return promote_silver.handle(
        conn, {"feed": FEED, "version": 1, "batch_id": batch_id}, settings
    )


def _silver_rows_visible(settings, entity: str) -> int:
    """Rows visible on a separate connection - 0 if the table never survived.

    A failed promotion rolls back the transaction that created its tables, so
    "nothing was written" and "there is nothing to write to" are the same answer.
    """
    with psycopg.connect(settings.database_url) as check, check.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS present", (f'{settings.silver_schema}."{entity}"',))
        if cur.fetchone()[0] is None:
            return 0
        cur.execute(f'SELECT count(*) FROM {settings.silver_schema}."{entity}"')
        return cur.fetchone()[0]


def _silver(conn, settings, entity: str, batch_id: str) -> list[dict]:
    table = silver_table(
        entity,
        load_canonical(YamlKnowledgeProvider(settings), "enrollment").fields_of(entity),
        schema=settings.silver_schema,
    )
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT * FROM {table.schema}."{table.name}" WHERE batch_id = %s '
            "ORDER BY source_system_id",
            (batch_id,),
        )
        return list(cur.fetchall())


def test_promotion_writes_every_entity_the_mapping_targets(conn, settings, approved):
    batch_id, upload = approved
    result = _promote(conn, settings, batch_id)
    conn.commit()

    assert result["promoted"] is True
    assert result["counts"] == {
        "records_in": 5,
        "records_out": 2,       # M001 and M005
        "quarantined": 3,       # bad date, missing id, unmapped sex
        "attributed_drops": 0,
    }
    assert result["silver_tables"] == {
        f"{settings.silver_schema}.members": 2,
        f"{settings.silver_schema}.members_addresses": 1,
        f"{settings.silver_schema}.members_emails": 1,
        f"{settings.silver_schema}.members_phones": 1,
    }

    members = _silver(conn, settings, "members", batch_id)
    assert [m["source_system_id"] for m in members] == ["M001", "M005"]
    assert members[0]["first_name"] == "DANIELLE"
    assert members[0]["sex"] == "female"                    # the value map ran
    assert members[0]["date_of_birth"].date().isoformat() == "1997-11-04"
    assert members[0]["source_system"] == upload.source_system
    assert members[0]["batch_id"] == batch_id
    assert members[0]["record_hash"] and members[0]["created_ts"]
    assert members[0]["ingestion_ts"]

    # A row that carried no email produced no email record at all.
    assert [e["email_address"] for e in _silver(conn, settings, "members_emails", batch_id)] == [
        "d@example.org"
    ]


def test_child_rows_carry_the_member_key_they_belong_to(conn, settings, approved):
    """`members_addresses` is declared with source_system_id in its key, and the
    mapping does not map it, so the member's own identifier travels with the row."""
    batch_id, _ = approved
    _promote(conn, settings, batch_id)
    conn.commit()

    for entity in ("members_addresses", "members_emails", "members_phones"):
        rows = _silver(conn, settings, entity, batch_id)
        assert [r["source_system_id"] for r in rows] == ["M001"]


def test_refused_rows_are_quarantined_with_the_rule_that_refused_them(
    conn, settings, approved
):
    batch_id, _ = approved
    _promote(conn, settings, batch_id)
    conn.commit()

    table = quarantine_table(FEED, schema=settings.silver_schema)
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT * FROM {table.schema}."{table.name}" WHERE batch_id = %s ORDER BY row_number',
            (batch_id,),
        )
        rows = list(cur.fetchall())

    assert [r["row_number"] for r in rows] == [2, 3, 4]
    assert [r["outcome"] for r in rows] == ["failure", "rejected", "quarantined"]
    assert all(r["mapping_version"] == 1 for r in rows)

    by_rule = {r["row_number"]: r["reasons"][0] for r in rows}
    assert by_rule[2]["source"] == "member_dob" and by_rule[2]["rule"] == "parse_date"
    assert by_rule[3]["source"] == "member_id" and by_rule[3]["rule"] == "on_null"
    assert by_rule[4]["source"] == "member_sex"
    assert by_rule[4]["rule"] == "on_unmapped_value"
    assert "13/45/1990" in by_rule[2]["reason"]
    # The source row is kept, so a quarantined row can be re-examined or replayed.
    assert rows[0]["raw_row"]["member_first_name"] == "KEVIN"


def test_the_run_records_the_promotion_and_balances(conn, settings, approved):
    batch_id, _ = approved
    _promote(conn, settings, batch_id)
    conn.commit()

    store = WorkflowStore(conn, settings)
    runs = {r.kind: r for r in store.list_batch_runs(batch_id)}
    assert set(runs) == {"land_bronze", "promote_silver"}

    promotion = runs["promote_silver"]
    assert promotion.state is RunState.COMPLETED
    assert promotion.balanced is True
    assert promotion.mapping_version == 1
    assert promotion.counts.records_in == 5
    assert promotion.finished_ts is not None
    # The landing run is untouched by the promotion of the same batch.
    assert runs["land_bronze"].counts.records_out == 5


def test_lineage_reaches_from_the_file_to_silver(conn, settings, approved):
    batch_id, upload = approved
    _promote(conn, settings, batch_id)
    conn.commit()

    lineage = WorkflowStore(conn, settings).get_lineage(batch_id)
    assert lineage.upload_id == upload.upload_id
    assert lineage.fingerprint == upload.fingerprint
    assert lineage.landing_key.endswith(".csv")
    assert lineage.bronze_table == f"bronze.{FEED}_raw"   # not erased by promotion
    assert lineage.mapping_version == 1
    assert lineage.silver_table == f"{settings.silver_schema}.members"
    assert len(lineage.silver_tables) == 4


def test_replaying_a_promotion_rebuilds_only_this_batch_and_hashes_the_same(
    conn, settings, approved
):
    batch_id, _ = approved
    first = _promote(conn, settings, batch_id)
    conn.commit()
    before = [(m["source_system_id"], m["record_hash"]) for m in _silver(
        conn, settings, "members", batch_id
    )]

    second = _promote(conn, settings, batch_id)
    conn.commit()
    after = [(m["source_system_id"], m["record_hash"]) for m in _silver(
        conn, settings, "members", batch_id
    )]

    assert first["rebuilt"] is False and second["rebuilt"] is True
    assert second["counts"] == first["counts"]
    # Rebuilt, not appended: the same rows with the same content-derived hashes.
    assert after == before
    assert len(after) == 2

    with conn.cursor() as cur:
        table = quarantine_table(FEED, schema=settings.silver_schema)
        cur.execute(f'SELECT count(*) AS n FROM {table.schema}."{table.name}"')
        assert cur.fetchone()["n"] == 3


def test_replay_does_not_touch_bronze(conn, settings, approved):
    batch_id, _ = approved
    bronze = bronze_table(FEED)
    plane = PostgresDataPlane(conn)

    _promote(conn, settings, batch_id)
    conn.commit()
    assert plane.count_rows(bronze, batch_id) == 5
    _promote(conn, settings, batch_id)
    conn.commit()
    assert plane.count_rows(bronze, batch_id) == 5

    # And Bronze refuses to be rebuilt at all, which is why replay is safe.
    with pytest.raises(ValueError, match="append-only"):
        plane.delete_batch(bronze, batch_id)


def test_a_run_that_does_not_balance_is_failed_and_writes_nothing(
    conn, settings, approved, monkeypatch
):
    """The balance equation is enforced, not reported: a promotion that loses rows
    leaves no Silver behind and a failed run to explain itself."""
    batch_id, upload = approved
    store = WorkflowStore(conn, settings)
    canonical = load_canonical(YamlKnowledgeProvider(settings), "enrollment")
    runner = PipelineRunner(conn, settings)

    real = runner.plane.write_rows

    def lossy(table, rows, **kwargs):
        if table.name == "members":
            rows = rows[:1]  # lose a row on purpose
        return real(table, rows, **kwargs)

    monkeypatch.setattr(runner.plane, "write_rows", lossy)
    with pytest.raises(PromotionFailure, match="balance"):
        runner.promote_silver(
            batch_id=batch_id,
            mapping=store.get_mapping_version(FEED, 1),
            canonical=canonical,
            upload=store.get_upload(upload.upload_id),
        )
    conn.commit()

    run = store.get_run(batch_id, kind="promote_silver")
    assert run.state is RunState.FAILED
    assert "balance failed" in run.error
    assert "wrote 1 of 2" in run.error
    assert _silver_rows_visible(settings, "members") == 0


def test_promotion_refuses_a_version_that_is_not_approved(conn, settings, approved):
    batch_id, _ = approved
    store = WorkflowStore(conn, settings)
    store.set_mapping_status(feed=FEED, version=1, status="draft")
    conn.commit()

    result = _promote(conn, settings, batch_id)
    assert result["promoted"] is False
    assert result["reason"] == "draft"
    assert store.get_run(batch_id, kind="promote_silver") is None


def test_g2_freezes_the_version_and_supersedes_the_one_before_it(conn, settings, approved):
    store = WorkflowStore(conn, settings)
    v2 = store.create_mapping_version(
        feed=FEED,
        domain="enrollment",
        spec=_spec(),
        created_by="analyst@cinqcare.com",
        derived_from=1,
    )
    approval, frozen = store.approve_mapping_version(
        feed=FEED, version=v2.version, upload_id=approved[1].upload_id, approver="lead@x.org"
    )
    conn.commit()

    assert approval.gate == "G2"
    assert approval.artifact_type == "mapping_version"
    assert approval.artifact_version == 2
    assert frozen.status == "approved"
    assert frozen.editable is False
    assert store.get_mapping_version(FEED, 1).status == "superseded"
    assert store.approval_for_mapping(feed=FEED, version=2).approver == "lead@x.org"


def test_the_approved_mapping_becomes_knowledge(conn, settings, approved):
    """A G2 decision is exported as a knowledge document the provider can read,
    so the next feed's proposal has this feed's decisions as an exemplar."""
    batch_id, _ = approved
    result = _promote(conn, settings, batch_id)
    conn.commit()

    exported = settings.knowledge_root / "mappings" / "approved" / f"{FEED}.yaml"
    assert result["knowledge"] == str(exported)

    import yaml

    document = yaml.safe_load(exported.read_text())
    assert document["version"] == 1
    assert document["domain"] == "enrollment"
    decisions = document["decision_sets"][0]
    assert decisions["mapping_version"] == 1
    assert decisions["approved_by"] == "analyst@cinqcare.com"
    assert decisions["batch_id"] == batch_id
    targets = {d["source_field"]: d["target"] for d in decisions["decisions"]}
    assert targets["member_city"] == "members_addresses.city"
    # No values from the file are exported - decisions only.
    assert "DANIELLE" not in exported.read_text()

    # The provider merges it into the domain's approved knowledge.
    merged = YamlKnowledgeProvider(settings).get_approved_mappings("enrollment")
    assert any(f"{FEED}.yaml@1" == f for f in merged.content["files"])


def test_promotion_needs_no_model_at_all(conn, settings, approved, monkeypatch):
    """Silver is written by the executor, not by reasoning. If a model were
    reachable from this path, poisoning every entry point would break it."""
    import cinqflow.intelligence.llm as llm
    import cinqflow.intelligence.runtime as runtime

    def explode(*args, **kwargs):
        raise AssertionError("promotion must not reach a model")

    monkeypatch.setattr(runtime.AgentRuntime, "__init__", explode)
    monkeypatch.setattr(runtime.AgentRuntime, "run", explode)
    monkeypatch.setattr(llm.StubClient, "complete_json", explode)

    batch_id, _ = approved
    assert _promote(conn, settings, batch_id)["promoted"] is True


def test_the_promotion_path_imports_no_intelligence():
    """Structural, not behavioural: nothing on the write path imports a model.

    Asserted over the parsed imports rather than the file text, so a docstring may
    say the word `AgentRuntime` while the module still cannot reach one.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src/cinqflow"
    for module in ("engine/runner.py", "engine/mapping_exec.py", "workers/promote_silver.py"):
        imported: set[str] = set()
        for node in ast.walk(ast.parse((root / module).read_text())):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported.update(f"{node.module}.{a.name}" for a in node.names)
        assert not any(
            forbidden in name
            for name in imported
            for forbidden in ("intelligence", "langgraph", "anthropic")
        ), f"{module} imports a model: {sorted(imported)}"


def test_promotion_of_an_unlanded_batch_fails_loudly(conn, settings, approved):
    with pytest.raises(RuntimeError, match="unknown batch"):
        promote_silver.handle(conn, {"feed": FEED, "version": 1, "batch_id": "nope"}, settings)


def test_a_preview_is_still_required_before_g2(conn, settings, approved):
    """Store-level check of the gate's precondition, independent of the API."""
    store = WorkflowStore(conn, settings)
    mapping = store.get_mapping_version(FEED, 1)
    assert store.get_current_preview(FEED, 1, spec_fingerprint(mapping.spec)) is None
