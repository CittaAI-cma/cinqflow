"""CF-V1-E7-01 and CF-V1-E7-02 through the API.

The routes cannot be talked past the two properties that matter: the platform
renders the SQL (the model never sends one), and the preview masks before it
serialises.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.local.localfs_storage import LocalFsStorage
from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.llm import ScriptedLlm
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.api.app import create_app
from cinqflow.core.agents.rule_authoring.prompts import TEMPLATES
from cinqflow.core.intelligence import Budget, Routing
from cinqflow.core.model.governed import Actor, LifecycleState
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.profiling import profile_bytes
from cinqflow.core.registry.contract import ContractColumn, SchemaContract, contract_as_governed
from cinqflow.core.rules import Check, CheckKind, RuleSpec, rule_as_governed
from cinqflow.core.rules.preview import MASKED
from cinqflow.core.schema_spec import TypeName
from cinqflow.intelligence.agents.rule_authoring import RuleAuthoringAgent
from cinqflow.intelligence.gateway import LlmGateway
from cinqflow.ports.metadata_db import FileProfileRecord

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
FEED = "fidelis-downstate-roster"
BA = "dev-ba@cinqcare.test"
READ_ONLY = "dev-analyst@cinqcare.test"
KEY = "enrollments/fidelis_downstate/roster/incoming/2026-08-01/roster.csv"

ROSTER = (
    b"source_member_id,first_name,line_of_business\n"
    b"MBR000001,Ada,MEDICAID\n"
    b"MBR000002,,MEDICARE\n"
    b"MBR000003,Grace,COMMERCIAL\n"
)

CONTRACT = SchemaContract(
    feed_id=FEED,
    version=3,
    columns=(
        ContractColumn("source_member_id", TypeName.STRING, nullable=False, is_phi=True),
        ContractColumn("first_name", TypeName.STRING, is_phi=True),
        ContractColumn("line_of_business", TypeName.STRING),
    ),
)

RULES = (
    RuleSpec(
        rule_id="DQ-002",
        name="Member First Name Not Null",
        stated="Member first name must be populated for all active members",
        check=Check(kind=CheckKind.NOT_NULL, column="first_name"),
    ),
    RuleSpec(
        rule_id="DQ-031",
        name="Line of Business Code Set",
        stated="LOB must be one of the published product lines",
        check=Check(
            kind=CheckKind.IN_SET,
            column="line_of_business",
            allowed=("MEDICAID", "MEDICARE", "DUAL"),
        ),
    ),
    RuleSpec(
        rule_id="DQ-070",
        name="Member Exists In Roster",
        stated="Every claim member must already exist in the roster",
        check=Check(
            kind=CheckKind.EXISTS_IN,
            column="source_member_id",
            reference_table="members",
            reference_column="member_id",
        ),
    ),
)


def _as(subject: str) -> dict[str, str]:
    return {"authorization": f"Bearer {subject}"}


def _published(obj):  # type: ignore[no-untyped-def]
    return replace(
        obj,
        lifecycle_state=LifecycleState.PUBLISHED,
        approved_by=Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN),
        approved_ts=NOW,
    )


@pytest.fixture
def landing(tmp_path):  # type: ignore[no-untyped-def]
    root = tmp_path / "landing"
    (root / KEY).parent.mkdir(parents=True, exist_ok=True)
    (root / KEY).write_bytes(ROSTER)
    return LocalFsStorage(root=root)


@pytest.fixture
def store() -> MemMetadataDb:
    memory = MemMetadataDb()
    author = Actor(subject=BA, actor_type=ActorType.HUMAN)
    for template in TEMPLATES:
        memory.save(_published(template.as_governed(author=author, now=NOW)))
    memory.save(contract_as_governed(CONTRACT, author=author))
    memory.save(rule_as_governed(FEED, RULES, author=author, created_ts=NOW))
    memory.record_profile(
        FileProfileRecord(
            feed_id=FEED,
            profile=profile_bytes(
                ROSTER, file_format="csv", source_key=KEY, source_fingerprint="sha256-a"
            ),
            profiled_by=BA,
            profiled_ts=NOW,
        )
    )
    return memory


def _agent(metadata: MemMetadataDb) -> RuleAuthoringAgent:
    answer = (
        '{"rules": [{"stated": "Member first name must be populated for all active members", '
        '"name": "Member First Name Not Null", "check_kind": "not_null", "column_ref": 2, '
        '"dimension": "completeness", "severity": "high", "confidence": 0.95, '
        '"rationale": "A mandatory-field rule."}]}'
    )
    gateway = LlmGateway(
        llm=ScriptedLlm(lambda p, t: answer),
        phi_scrub=PatternPhiScrub(),
        metadata_db=metadata,
        observability=NoopObservability(),
        budget=Budget(per_run_usd=Decimal("0.25"), per_agent_per_day_usd=Decimal("5")),
        routing=Routing(small="small-model", large="large-model"),
        estimate_usd=Decimal("0.01"),
        clock=lambda: NOW,
    )
    return RuleAuthoringAgent(llm=gateway, metadata=metadata)


@pytest.fixture
def client(store: MemMetadataDb, landing: LocalFsStorage) -> Iterator[TestClient]:
    app = create_app(
        authn=StaticAuthn(),
        metadata_db=store,
        storage=landing,
        rule_authoring_factory=_agent,
    )
    with TestClient(app) as test_client:
        yield test_client


# ── authoring ───────────────────────────────────────────────────────────────


def test_the_route_writes_a_proposal_carrying_platform_rendered_sql(
    client: TestClient,
) -> None:
    """ "plain English -> SQL/PySpark" — and the SQL on the wire is rendered by
    the platform from the stored check, never read from the model's answer."""
    proposed = client.post(
        f"/api/feeds/{FEED}/author-rules",
        json={"stated": ["Member first name must be populated for all active members"]},
        headers=_as(BA),
    )
    assert proposed.status_code == 200, proposed.text
    body = proposed.json()

    assert body["state"] == "pending_review"
    rule = body["rules"][0]
    assert rule["sql"] == (
        "SELECT * FROM silver_raw WHERE first_name IS NULL OR TRIM(first_name) = ''"
    )
    assert "F.col('first_name')" in rule["pyspark"]
    assert rule["stated"] == "Member first name must be populated for all active members"
    assert rule["explanation"] == "first_name must be present and not blank."


def test_authoring_needs_a_contract(client: TestClient, store: MemMetadataDb) -> None:
    """A rule checks a contracted column. A rule about a column nobody agreed
    exists quarantines nothing and looks like it is working."""
    empty = MemMetadataDb()
    app = create_app(authn=StaticAuthn(), metadata_db=empty, rule_authoring_factory=_agent)
    with TestClient(app) as bare:
        refused = bare.post(
            f"/api/feeds/{FEED}/author-rules", json={"stated": ["x"]}, headers=_as(BA)
        )
    assert refused.status_code == 404
    assert "no schema contract" in refused.text


def test_an_empty_request_is_refused(client: TestClient) -> None:
    refused = client.post(
        f"/api/feeds/{FEED}/author-rules", json={"stated": ["  "]}, headers=_as(BA)
    )
    assert refused.status_code == 400
    assert "nothing to write a rule from" in refused.text


def test_a_read_only_user_cannot_author_rules(client: TestClient) -> None:
    refused = client.post(
        f"/api/feeds/{FEED}/author-rules", json={"stated": ["x"]}, headers=_as(READ_ONLY)
    )
    assert refused.status_code == 403


# ── the preview ─────────────────────────────────────────────────────────────


def test_the_preview_reports_tested_passed_and_failed(client: TestClient) -> None:
    """The counts are what a BA reads before deciding whether to submit."""
    pack = client.post(
        f"/api/feeds/{FEED}/preview-rules", json={"stated": []}, headers=_as(BA)
    ).json()

    assert pack["sample_rows"] == 3
    by_id = {p["rule_id"]: p for p in pack["previews"]}
    assert (by_id["DQ-002"]["tested"], by_id["DQ-002"]["failed"]) == (3, 1)
    assert by_id["DQ-031"]["failed"] == 1


def test_the_failing_rows_are_masked_on_the_wire(client: TestClient) -> None:
    """THE PROPERTY, asserted on the RESPONSE BODY — which is what a browser,
    a log and an evidence pack all see."""
    response = client.post(f"/api/feeds/{FEED}/preview-rules", json={"stated": []}, headers=_as(BA))
    body = response.text

    for value in ("Ada", "Grace", "MBR000001", "MBR000003"):
        assert value not in body, f"{value} reached the wire unmasked"
    pack = response.json()
    by_id = {p["rule_id"]: p for p in pack["previews"]}
    assert by_id["DQ-002"]["masked_columns"] == ["first_name"]
    assert by_id["DQ-002"]["failing_rows"][0]["values"]["first_name"] == MASKED


def test_an_unprotected_column_is_still_readable(client: TestClient) -> None:
    """A preview that masked everything would be a preview nobody could read."""
    pack = client.post(
        f"/api/feeds/{FEED}/preview-rules", json={"stated": []}, headers=_as(BA)
    ).json()
    by_id = {p["rule_id"]: p for p in pack["previews"]}
    assert by_id["DQ-031"]["failing_rows"][0]["values"]["line_of_business"] == "COMMERCIAL"


def test_a_rule_that_cannot_be_previewed_says_so_rather_than_reporting_zero(
    client: TestClient,
) -> None:
    """Reporting no failures for a check that never ran is the most misleading
    green a preview can show."""
    pack = client.post(
        f"/api/feeds/{FEED}/preview-rules", json={"stated": []}, headers=_as(BA)
    ).json()
    by_id = {p["rule_id"]: p for p in pack["previews"]}

    assert by_id["DQ-070"]["not_previewable"]
    assert pack["rules_not_previewable"] == 1
    assert "could not be previewed" in by_id["DQ-070"]["summary"]


def test_a_read_only_user_may_preview(client: TestClient) -> None:
    """Seeing what a rule catches is READING. A reviewer who cannot preview the
    rule they are approving is being asked to approve prose."""
    allowed = client.post(
        f"/api/feeds/{FEED}/preview-rules", json={"stated": []}, headers=_as(READ_ONLY)
    )
    assert allowed.status_code == 200


def test_previewing_a_feed_with_no_rules_says_so(client: TestClient) -> None:
    missing = client.post(
        "/api/feeds/nothing-here/preview-rules", json={"stated": []}, headers=_as(BA)
    )
    assert missing.status_code == 404
    assert "no rules yet" in missing.text
