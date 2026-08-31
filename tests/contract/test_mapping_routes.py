"""CF-V1-E6-03 through the API — the manual mapping editor.

    "Humans must be able to do by hand everything the AI proposes — the editor
     is the fallback and the correction surface."

`tests/unit/test_mapping_taxonomy.py` proves the taxonomy's rules. This proves
the routes cannot be talked past them: that a half-authored mapping SAVES while
a self-contradictory one does not, that findings travel with the GET so a
mapping invalidated by a change at either end shows it immediately, and that a
mapping arrives as a DRAFT routed to a steward like everything else.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.api import create_app

# W1-36 regression: `_own_published_mapping` is the exact function the
# adversarial review flagged alongside `installer/cli.py`'s inline lookup as
# needing the SAME "highest EXECUTABLE version, not highest version number"
# fix. Every other test in this file goes through the HTTP surface only; this
# one private import is deliberate, because nothing routes this function's
# return value back out over HTTP in an observable way — the regression can
# only be pinned by calling it directly.
from cinqflow.api.app import _own_published_mapping
from cinqflow.core.mapping import FeedMapping, MappingLine, mapping_as_governed
from cinqflow.core.model.governed import Actor, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry.contract import ContractColumn, SchemaContract, contract_as_governed
from cinqflow.core.registry.glossary import GlossaryTerm
from cinqflow.core.schema_spec import TypeName

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

BA = "dev-ba@cinqcare.test"
STEWARD = "dev-steward@cinqcare.test"
READ_ONLY = "dev-analyst@cinqcare.test"

FEED_ID = "fidelis-downstate-roster"

#: The glossary defines the canonical target. `members` is the one entity the
#: estate has deployed, so a mapping against it is runnable today.
TERMS = (
    GlossaryTerm(
        glossary_id="BG-001",
        term="Member First Name",
        definition="The member's given name.",
        mapped_domains=("Enrollment",),
        mapped_tables=("members",),
        mapped_columns_original=("First_Name",),
        mapped_columns_corrected=("first_name",),
        is_phi=True,
    ),
    GlossaryTerm(
        glossary_id="BG-050",
        term="Line of Business",
        definition="The product line a member is enrolled under.",
        mapped_domains=("Enrollment",),
        mapped_tables=("members",),
        mapped_columns_original=("LOB",),
        mapped_columns_corrected=("line_of_business",),
    ),
)

CONTRACT = SchemaContract(
    feed_id=FEED_ID,
    version=1,
    columns=(
        ContractColumn("first_name", TypeName.STRING, source_name="First_Name", is_phi=True),
        ContractColumn("line_of_business", TypeName.STRING, source_name="LOB"),
    ),
)


def _as(subject: str) -> dict[str, str]:
    return {"authorization": f"Bearer {subject}"}


def _line(**kwargs: Any) -> dict[str, Any]:
    return {
        "target_entity": "members",
        "target_field": "first_name",
        "source_columns": ["First_Name"],
        **kwargs,
    }


@pytest.fixture
def store() -> MemMetadataDb:
    memory = MemMetadataDb()
    author = Actor(subject="seed@cinqcare.test", actor_type=ActorType.HUMAN)
    for term in TERMS:
        memory.save(term.as_governed(author=author, now=datetime.now(UTC)))
    memory.save(contract_as_governed(CONTRACT, author=author))
    return memory


@pytest.fixture
def client(store: MemMetadataDb) -> Iterator[TestClient]:
    with TestClient(create_app(authn=StaticAuthn(), metadata_db=store)) as test_client:
        yield test_client


# ── save is permissive; contradictions are not ──────────────────────────────


def test_a_half_authored_mapping_saves(client: TestClient) -> None:
    """A BA who has settled one line and is waiting on the payer to explain the
    second needs somewhere to keep the first."""
    saved = client.put(
        f"/api/feeds/{FEED_ID}/mapping",
        json={"contract_version": 1, "lines": [_line()]},
        headers=_as(BA),
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["version"] == 1
    assert body["lifecycle_state"] == "draft"
    assert (body["mapped_count"], body["total_count"]) == (1, 1)


def test_an_unmapped_line_needs_a_reason_and_the_route_says_so(client: TestClient) -> None:
    """The client's own `NO MAP Fields` sheet has a Reason column. A 422 from
    the model layer would be a schema complaint; this is a 400 that explains."""
    refused = client.put(
        f"/api/feeds/{FEED_ID}/mapping",
        json={
            "lines": [{"target_entity": "members", "target_field": "hicn_id", "source_columns": []}]
        },
        headers=_as(BA),
    )
    assert refused.status_code == 400
    assert "no source and no reason" in refused.text


def test_an_unmapped_line_with_a_reason_saves_and_is_counted(client: TestClient) -> None:
    saved = client.put(
        f"/api/feeds/{FEED_ID}/mapping",
        json={
            "lines": [
                _line(),
                {
                    "target_entity": "members",
                    "target_field": "hicn_id",
                    "source_columns": [],
                    "unmapped_reason": "Fidelis stopped sending HICN when MBI replaced it.",
                },
            ]
        },
        headers=_as(BA),
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["unmapped_count"] == 1


def test_mapping_the_same_target_twice_is_refused_at_the_route(client: TestClient) -> None:
    """Not a gap — a contradiction. Which line wins would depend on ordering,
    so storing it produces a review screen nobody can act on."""
    refused = client.put(
        f"/api/feeds/{FEED_ID}/mapping",
        json={"lines": [_line(), _line(notes="the same field again")]},
        headers=_as(BA),
    )
    assert refused.status_code == 400
    assert "mapped 2 times" in refused.text


def test_a_read_only_user_cannot_save_a_mapping(client: TestClient) -> None:
    refused = client.put(
        f"/api/feeds/{FEED_ID}/mapping", json={"lines": [_line()]}, headers=_as(READ_ONLY)
    )
    assert refused.status_code == 403


# ── findings travel with the GET, not only with the save ────────────────────


def test_findings_are_recomputed_on_read(client: TestClient, store: MemMetadataDb) -> None:
    """A mapping approved months ago can be invalidated by a change at either
    end. A reviewer opening it must see that, which they would not if findings
    were computed once at save and stored."""
    client.put(
        f"/api/feeds/{FEED_ID}/mapping",
        json={"lines": [_line(source_columns=["MBR_FNAME"], notes="a column nobody has")]},
        headers=_as(BA),
    )
    body = client.get(f"/api/feeds/{FEED_ID}/mapping", headers=_as(BA)).json()
    assert body["blocking_count"] == 1
    finding = body["findings"][0]
    assert finding["key"] == "unknown_source"
    assert finding["how_to_fix"]
    assert finding["why_it_matters"]


def test_phi_carried_into_an_unflagged_target_is_reported(client: TestClient) -> None:
    """`First_Name` is flagged PHI by BG-001; `line_of_business` is not flagged
    by BG-050. Landing the first in the second takes the value out of the
    masking policy without breaking a rule anywhere — and only a mapping,
    which is the crossing, can see it."""
    client.put(
        f"/api/feeds/{FEED_ID}/mapping",
        json={"lines": [_line(target_field="line_of_business")]},
        headers=_as(BA),
    )
    body = client.get(f"/api/feeds/{FEED_ID}/mapping", headers=_as(BA)).json()
    assert [f["key"] for f in body["findings"] if f["blocks"]] == ["phi_laundering"]


def test_reading_a_feed_with_no_mapping_says_where_to_start(client: TestClient) -> None:
    missing = client.get(f"/api/feeds/{FEED_ID}/mapping", headers=_as(BA))
    assert missing.status_code == 404
    assert "/api/canonical" in missing.text


# ── a mapping is a governed object, routed to a steward ─────────────────────


def test_saving_again_creates_the_next_version_never_an_edit(client: TestClient) -> None:
    client.put(f"/api/feeds/{FEED_ID}/mapping", json={"lines": [_line()]}, headers=_as(BA))
    second = client.put(
        f"/api/feeds/{FEED_ID}/mapping",
        json={"lines": [_line(), _line(target_field="line_of_business", source_columns=["LOB"])]},
        headers=_as(BA),
    )
    assert second.json()["version"] == 2
    first = client.get(f"/api/feeds/{FEED_ID}/mapping?version=1", headers=_as(BA)).json()
    assert first["total_count"] == 1, "v1 must be exactly what was approved as v1"


def test_a_mapping_travels_the_one_lifecycle(client: TestClient) -> None:
    """No private state machine (ADR-0006), and no publish route on the editor:
    the same transition endpoint that carries a feed carries this."""
    client.put(f"/api/feeds/{FEED_ID}/mapping", json={"lines": [_line()]}, headers=_as(BA))
    submitted = client.post(
        f"/api/objects/{ObjectType.MAPPING.value}/{FEED_ID}/submit",
        json={"comment": "ready for review"},
        headers=_as(BA),
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["lifecycle_state"] == "pending_review"


def test_a_mapping_routes_to_the_steward_not_the_platform_engineer(client: TestClient) -> None:
    """`APPROVAL_ROUTING` sends mapping and dq_rule to the data steward, and
    config and contracts to the platform engineer. A mapping is a business
    decision about what a field MEANS."""
    client.put(f"/api/feeds/{FEED_ID}/mapping", json={"lines": [_line()]}, headers=_as(BA))
    client.post(
        f"/api/objects/{ObjectType.MAPPING.value}/{FEED_ID}/submit",
        json={"comment": "ready"},
        headers=_as(BA),
    )
    approved = client.post(
        f"/api/objects/{ObjectType.MAPPING.value}/{FEED_ID}/approve",
        json={"comment": "the two lines match the glossary"},
        headers=_as(STEWARD),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved_by_subject"] == STEWARD


def test_a_steward_cannot_author_the_mapping_they_would_approve(client: TestClient) -> None:
    """SEGREGATION CLOSES ONE LAYER EARLIER THAN THE LIFECYCLE.

    A data steward holds APPROVE and not EDIT_FEED, so the person who signs a
    mapping cannot be the person who wrote it — the permission matrix refuses
    before the lifecycle is reached. Written as its own test because a reader
    finding only the lifecycle negative below would reasonably conclude that a
    steward CAN author, and that authoring is caught later.
    """
    refused = client.put(
        f"/api/feeds/{FEED_ID}/mapping", json={"lines": [_line()]}, headers=_as(STEWARD)
    )
    assert refused.status_code == 403
    assert "edit_feed" in refused.text


def test_the_author_of_a_mapping_cannot_approve_it(
    client: TestClient, store: MemMetadataDb
) -> None:
    """Universal negative #1, on a type built long after it was written.

    Authored THROUGH THE STORE rather than the route, deliberately: no role in
    the current matrix holds both EDIT_FEED and APPROVE, so the only way to
    reach the lifecycle's own refusal is to construct the object the matrix
    would not let anyone construct. That is the point — the guarantee must hold
    if a later wave ever grants one role both, and this is the test that would
    catch it the day somebody does.
    """
    author = Actor(subject=STEWARD, actor_type=ActorType.HUMAN)
    store.save(
        mapping_as_governed(
            FeedMapping(
                feed_id=FEED_ID,
                lines=(
                    MappingLine(
                        target_entity="members",
                        target_field="first_name",
                        source_columns=("First_Name",),
                    ),
                ),
            ),
            author=author,
        )
    )
    client.post(
        f"/api/objects/{ObjectType.MAPPING.value}/{FEED_ID}/submit",
        json={"comment": "ready"},
        headers=_as(BA),
    )
    refused = client.post(
        f"/api/objects/{ObjectType.MAPPING.value}/{FEED_ID}/approve",
        json={"comment": "looks fine to me"},
        headers=_as(STEWARD),
    )
    assert refused.status_code == 403
    assert "never approves it" in refused.text


def test_the_stored_mapping_reaches_the_glossary_through_lineage(
    client: TestClient, store: MemMetadataDb
) -> None:
    """`core.impact.REFERENCES` says a MAPPING's `glossary_ids` point at terms.
    Writing that key means a steward changing BG-001 is told which mappings
    they are about to affect."""
    client.put(
        f"/api/feeds/{FEED_ID}/mapping",
        json={"lines": [_line(glossary_id="BG-001")]},
        headers=_as(BA),
    )
    obj = store.get(ObjectType.MAPPING, FEED_ID)
    assert obj.body["glossary_ids"] == ["BG-001"]
    assert obj.body["feed_id"] == FEED_ID


# ── CF-V1-E6-04 · version compare, and the loss that does not announce itself ─


def _publish(client: TestClient, comment: str = "the mapping matches the glossary") -> None:
    client.post(
        f"/api/objects/{ObjectType.MAPPING.value}/{FEED_ID}/submit",
        json={"comment": "ready"},
        headers=_as(BA),
    )
    client.post(
        f"/api/objects/{ObjectType.MAPPING.value}/{FEED_ID}/approve",
        json={"comment": comment},
        headers=_as(STEWARD),
    )
    client.post(
        f"/api/objects/{ObjectType.MAPPING.value}/{FEED_ID}/publish",
        json={},
        headers=_as(STEWARD),
    )


BOTH_LINES = [_line(), _line(target_field="line_of_business", source_columns=["LOB"])]


def test_the_diff_is_by_target_field_not_by_json(client: TestClient) -> None:
    """`diff_bodies` would render a mapping change as `lines: [...] -> [...]`
    — technically complete, and useless to the person signing it."""
    client.put(f"/api/feeds/{FEED_ID}/mapping", json={"lines": BOTH_LINES}, headers=_as(BA))
    client.put(f"/api/feeds/{FEED_ID}/mapping", json={"lines": [_line()]}, headers=_as(BA))

    diff = client.get(f"/api/feeds/{FEED_ID}/mapping/diff", headers=_as(BA)).json()

    assert (diff["from_version"], diff["to_version"]) == (1, 2)
    assert [line["address"] for line in diff["lines"]] == ["members.line_of_business"]
    assert diff["lines"][0]["change"] == "removed"
    assert "arrive empty on every row" in diff["lines"][0]["explanation"]


def test_reordering_the_lines_is_not_reported_as_a_change(client: TestClient) -> None:
    client.put(f"/api/feeds/{FEED_ID}/mapping", json={"lines": BOTH_LINES}, headers=_as(BA))
    client.put(
        f"/api/feeds/{FEED_ID}/mapping",
        json={"lines": list(reversed(BOTH_LINES))},
        headers=_as(BA),
    )
    diff = client.get(f"/api/feeds/{FEED_ID}/mapping/diff", headers=_as(BA)).json()
    assert diff["lines"] == []
    assert "identical" in diff["summary"]


def test_comparing_a_feed_with_one_version_says_so(client: TestClient) -> None:
    client.put(f"/api/feeds/{FEED_ID}/mapping", json={"lines": [_line()]}, headers=_as(BA))
    nothing = client.get(f"/api/feeds/{FEED_ID}/mapping/diff", headers=_as(BA))
    assert nothing.status_code == 404
    assert "nothing to compare" in nothing.text


def test_emptying_a_live_field_is_refused_unless_the_approver_names_it(
    client: TestClient,
) -> None:
    """THE GATE THE STORY EXISTS FOR. Nothing fails when a mapping drops a
    line: the batch runs, the counts reconcile, and one column arrives NULL on
    every record from that day."""
    client.put(f"/api/feeds/{FEED_ID}/mapping", json={"lines": BOTH_LINES}, headers=_as(BA))
    _publish(client)

    client.put(f"/api/feeds/{FEED_ID}/mapping", json={"lines": [_line()]}, headers=_as(BA))
    client.post(
        f"/api/objects/{ObjectType.MAPPING.value}/{FEED_ID}/submit",
        json={"comment": "dropping LOB"},
        headers=_as(BA),
    )
    refused = client.post(
        f"/api/objects/{ObjectType.MAPPING.value}/{FEED_ID}/approve",
        json={"comment": "looks fine"},
        headers=_as(STEWARD),
    )

    assert refused.status_code == 403
    assert "members.line_of_business" in refused.text
    assert "the batch will run" in refused.text.lower()


def test_naming_the_field_lets_the_change_through(client: TestClient) -> None:
    """Removing a source is sometimes exactly right — a payer stops sending a
    field. What must not happen is removing one by accident."""
    client.put(f"/api/feeds/{FEED_ID}/mapping", json={"lines": BOTH_LINES}, headers=_as(BA))
    _publish(client)

    client.put(f"/api/feeds/{FEED_ID}/mapping", json={"lines": [_line()]}, headers=_as(BA))
    client.post(
        f"/api/objects/{ObjectType.MAPPING.value}/{FEED_ID}/submit",
        json={"comment": "Fidelis stopped sending LOB"},
        headers=_as(BA),
    )
    approved = client.post(
        f"/api/objects/{ObjectType.MAPPING.value}/{FEED_ID}/approve",
        json={
            "comment": "Fidelis confirmed LOB is gone from the August layout",
            "accepts_loss": ["members.line_of_business"],
        },
        headers=_as(STEWARD),
    )

    assert approved.status_code == 200, approved.text
    assert approved.json()["lifecycle_state"] == "approved"


def test_the_refusal_leaves_a_row(client: TestClient) -> None:
    """A guardrail nobody can see fired is a comment."""
    client.put(f"/api/feeds/{FEED_ID}/mapping", json={"lines": BOTH_LINES}, headers=_as(BA))
    _publish(client)
    client.put(f"/api/feeds/{FEED_ID}/mapping", json={"lines": [_line()]}, headers=_as(BA))
    client.post(
        f"/api/objects/{ObjectType.MAPPING.value}/{FEED_ID}/submit",
        json={"comment": "dropping LOB"},
        headers=_as(BA),
    )
    client.post(
        f"/api/objects/{ObjectType.MAPPING.value}/{FEED_ID}/approve",
        json={"comment": "looks fine"},
        headers=_as(STEWARD),
    )

    trail = client.get("/api/audit", headers=_as(STEWARD)).json()
    assert any(entry["action"] == "refused:silent_row_loss" for entry in trail)


def test_a_first_mapping_needs_no_acknowledgement(client: TestClient) -> None:
    """There is no published predecessor, so there is nothing a field can stop
    being. Asking here would train people to acknowledge everything."""
    client.put(f"/api/feeds/{FEED_ID}/mapping", json={"lines": BOTH_LINES}, headers=_as(BA))
    client.post(
        f"/api/objects/{ObjectType.MAPPING.value}/{FEED_ID}/submit",
        json={"comment": "ready"},
        headers=_as(BA),
    )
    approved = client.post(
        f"/api/objects/{ObjectType.MAPPING.value}/{FEED_ID}/approve",
        json={"comment": "both lines match the glossary"},
        headers=_as(STEWARD),
    )
    assert approved.status_code == 200, approved.text


def test_adding_a_field_to_a_published_mapping_needs_no_acknowledgement(
    client: TestClient,
) -> None:
    """The gate is about LOSS. A change that only adds must not cost the
    approver a ritual, or the ritual is what they will learn."""
    client.put(f"/api/feeds/{FEED_ID}/mapping", json={"lines": [_line()]}, headers=_as(BA))
    _publish(client)

    client.put(f"/api/feeds/{FEED_ID}/mapping", json={"lines": BOTH_LINES}, headers=_as(BA))
    client.post(
        f"/api/objects/{ObjectType.MAPPING.value}/{FEED_ID}/submit",
        json={"comment": "adding LOB"},
        headers=_as(BA),
    )
    approved = client.post(
        f"/api/objects/{ObjectType.MAPPING.value}/{FEED_ID}/approve",
        json={"comment": "LOB is new in the August layout"},
        headers=_as(STEWARD),
    )
    assert approved.status_code == 200, approved.text


def test_the_gate_compares_against_the_published_version_not_the_previous_one(
    client: TestClient,
) -> None:
    """The pipeline reads published metadata and nothing else, so what matters
    is what changes for IT. A draft in between is not what is running."""
    client.put(f"/api/feeds/{FEED_ID}/mapping", json={"lines": BOTH_LINES}, headers=_as(BA))
    _publish(client)

    # v2 — a draft that also drops LOB, never submitted.
    client.put(f"/api/feeds/{FEED_ID}/mapping", json={"lines": [_line()]}, headers=_as(BA))
    # v3 — drops it too, and this is the one submitted.
    client.put(f"/api/feeds/{FEED_ID}/mapping", json={"lines": [_line()]}, headers=_as(BA))
    client.post(
        f"/api/objects/{ObjectType.MAPPING.value}/{FEED_ID}/submit",
        json={"comment": "dropping LOB"},
        headers=_as(BA),
    )
    refused = client.post(
        f"/api/objects/{ObjectType.MAPPING.value}/{FEED_ID}/approve",
        json={"comment": "looks fine"},
        headers=_as(STEWARD),
    )

    assert refused.status_code == 403, (
        "v2 and v3 are identical, so a previous-version comparison would have found no "
        "loss — and the field would have gone dark against what is actually running"
    )
    assert "members.line_of_business" in refused.text


def test_own_published_mapping_survives_an_unpublished_draft_on_top(
    client: TestClient, store: MemMetadataDb
) -> None:
    """W1-36: `metadata.get(ObjectType.MAPPING, feed_id)` with no `version`
    returns the highest VERSION NUMBER, full stop — not "whichever version is
    currently PUBLISHED". A v2 draft opened the instant anyone starts editing
    a mapping (or accepts ANY mapping-suggestion proposal, which always lands
    as a fresh draft) would make `_own_published_mapping` return nothing, even
    though v1 is validly PUBLISHED and is exactly what the pipeline is
    running. `_own_published_mapping` must walk the object's full history and
    find the highest version that is ACTUALLY executable."""
    client.put(f"/api/feeds/{FEED_ID}/mapping", json={"lines": [_line()]}, headers=_as(BA))
    _publish(client)

    # v2 — a draft opened on top of the published v1, never submitted.
    client.put(f"/api/feeds/{FEED_ID}/mapping", json={"lines": BOTH_LINES}, headers=_as(BA))

    # Sanity check: the naive "latest version, full stop" read is a draft.
    naive = store.get(ObjectType.MAPPING, FEED_ID)
    assert naive.version == 2
    assert not naive.is_executable

    resolved = _own_published_mapping(store, FEED_ID)
    assert resolved, "the published v1 must still be found despite the unpublished v2 on top"
    assert resolved[0].version == 1
    assert [line.target_field for line in resolved[0].lines] == ["first_name"]
