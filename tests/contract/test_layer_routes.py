"""GET /api/layers · /api/layers/{layer} · .../tables/{table}/rows — W3-01.

The three routes the medallion screen reads. What this suite is for, beyond
"they answer 200": every one of the design decisions that made the screen
publishable at all is a claim about a RESPONSE, and a claim about a response is
only true if something asserts it on the wire.

  · the unbuilt layers are PRESENT, not omitted and not 404;
  · a flagged column never leaves the server in the clear;
  · a layer name off the wire cannot reach an identifier;
  · reading a masked layer needs no permission a read-only user lacks.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.api import create_app
from cinqflow.intelligence.demo import layer_reader_for, plane

pytestmark = [pytest.mark.contract, pytest.mark.lane1]

ENGINEER = "dev-engineer@cinqcare.test"
READ_ONLY = "dev-analyst@cinqcare.test"
NO_GROUP = "dev-nogroup@cinqcare.test"

#: Every value the seed invents, so a leak can be caught by looking for the
#: cleartext rather than by trusting that the bullets are in the right cells.
#: A test that only checks for "•••" passes against a response that masks one
#: column and publishes the next.
INVENTED_PHI = ("Okafor", "Byron", "Nunez", "Roca", "M0001", "M0003", "1936-02-01", "19360201")


@pytest.fixture
def client() -> Iterator[TestClient]:
    store, control = plane()
    app = create_app(
        authn=StaticAuthn(),
        metadata_db=store,
        control_tables=control,
        layer_reader=layer_reader_for(),
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def planeless() -> Iterator[TestClient]:
    """A deployment with NO data plane fitted. Not a broken one — a real shape,
    and the routes must answer honestly rather than 500."""
    store, control = plane()
    app = create_app(authn=StaticAuthn(), metadata_db=store, control_tables=control)
    with TestClient(app) as test_client:
        yield test_client


def _as(subject: str) -> dict[str, str]:
    return {"authorization": f"Bearer {subject}"}


# ── the spine on the wire ────────────────────────────────────────────────────
def test_all_six_layers_are_listed_including_the_three_not_built(client: TestClient) -> None:
    """The screen's main claim. A response that omitted them would tell a
    caller the spine was complete."""
    body = client.get("/api/layers", headers=_as(ENGINEER)).json()
    assert [layer["layer"] for layer in body] == [
        "landing",
        "bronze",
        "silver_raw",
        "identity",
        "silver_ods",
        "gold",
    ]
    statuses = {layer["layer"]: layer["status"] for layer in body}
    assert statuses["silver_raw"] == "built"
    assert statuses["silver_ods"] == "provisioned_empty"
    assert statuses["gold"] == "not_built"


def test_an_unbuilt_layer_carries_its_wave_and_its_reason(client: TestClient) -> None:
    """ "Not built" without a reason is indistinguishable from a bug."""
    body = client.get("/api/layers", headers=_as(ENGINEER)).json()
    gold = next(layer for layer in body if layer["layer"] == "gold")
    assert gold["wave"] == 4
    assert "Wave 4" in gold["absence_reason"]
    assert gold["row_count"] is None


def test_a_count_is_null_and_never_zero_when_nothing_is_on_the_plane(
    client: TestClient, planeless: TestClient
) -> None:
    """A deployment reporting "Bronze: 0 rows" would be a lie that reads like a
    healthy empty platform. Both the unbuilt-layer case and the no-plane case
    answer null."""
    fitted = client.get("/api/layers", headers=_as(ENGINEER)).json()
    assert next(x for x in fitted if x["layer"] == "bronze")["row_count"] == 6
    assert next(x for x in fitted if x["layer"] == "gold")["row_count"] is None

    unfitted = planeless.get("/api/layers", headers=_as(ENGINEER)).json()
    assert all(layer["row_count"] is None for layer in unfitted)
    assert [layer["layer"] for layer in unfitted] == [x["layer"] for x in fitted]


def test_an_unbuilt_layer_answers_200_with_its_reason_not_404(client: TestClient) -> None:
    """404 would make the screen unable to tell "not in this architecture" from
    "not built yet". The second is true; the first is not."""
    response = client.get("/api/layers/identity", headers=_as(ENGINEER))
    assert response.status_code == 200
    body = response.json()
    assert body["layer"]["status"] == "not_built"
    assert "Wave 3" in body["layer"]["absence_reason"]
    assert body["tables"] == []


def test_a_layer_name_that_is_not_one_is_404_and_names_the_six(client: TestClient) -> None:
    response = client.get("/api/layers/sliver_raw", headers=_as(ENGINEER))
    assert response.status_code == 404
    assert "silver_raw" in response.json()["detail"]


def test_a_layer_detail_carries_both_types_for_every_column(client: TestClient) -> None:
    """Contract and engine, side by side. A screen showing only one of them
    cannot show a drift, which is the comparison the conformance kit makes."""
    body = client.get("/api/layers/silver_raw", headers=_as(ENGINEER)).json()
    (table,) = body["tables"]
    assert table["name"] == "members"
    columns = {column["name"]: column for column in table["columns"]}
    assert columns["ingestion_ts"]["declared_type"] == "timestamp_utc"
    assert columns["ingestion_ts"]["engine_type"]
    assert columns["first_name"]["is_phi"] is True
    assert columns["line_of_business"]["is_phi"] is False


def test_the_gate_evidence_belongs_to_the_layer_the_gate_feeds(client: TestClient) -> None:
    """Quarantine and reconciliation are G2's evidence. Attaching them to every
    layer would show Landing a drop count for a rule that runs downstream."""
    silver = client.get("/api/layers/silver_raw", headers=_as(ENGINEER)).json()
    assert [q["rule_id"] for q in silver["quarantine"]] == ["DQ-002", "CAST-date_of_birth"]
    assert [line["stage"] for line in silver["reconciliation"]] == ["silver_raw"]

    landing = client.get("/api/layers/landing", headers=_as(ENGINEER)).json()
    assert landing["quarantine"] == []
    assert landing["reconciliation"] == []


def test_unattributed_is_derived_and_travels_beside_the_recorded_verdict(
    client: TestClient,
) -> None:
    """A green tick with unexplained rows behind it has to be visible rather
    than trusted — so the ledger's `balanced` and the derived `unattributed`
    both ship, and the screen shows both."""
    body = client.get("/api/layers/silver_raw", headers=_as(ENGINEER)).json()
    (line,) = body["reconciliation"]
    assert line["balanced"] is True
    assert line["unattributed"] == (
        line["records_in"] - line["records_out"] - line["quarantined"] - line["attributed_drops"]
    )
    assert line["route"].startswith("/operations/control/batch/")


def test_the_quarantine_summary_carries_no_row_at_all(client: TestClient) -> None:
    """Not even masked. This answers "what is wrong and how much of it", a
    question that needs a rule id and a count and nothing about any member."""
    body = client.get("/api/layers/silver_raw", headers=_as(ENGINEER)).json()
    serialized = repr(body["quarantine"])
    for value in INVENTED_PHI:
        assert value not in serialized
    assert set(body["quarantine"][0]) == {"rule_id", "reason", "stage", "row_count"}


# ── rows ─────────────────────────────────────────────────────────────────────
def test_rows_are_masked_and_the_cleartext_is_nowhere_in_the_response(
    client: TestClient,
) -> None:
    """The strongest assertion in the suite, and it is deliberately negative:
    it searches the WHOLE serialized response for every invented value rather
    than checking that named cells contain bullets. A response that masks
    `first_name` and publishes `last_name` passes the positive test."""
    response = client.get("/api/layers/silver_raw/tables/members/rows", headers=_as(ENGINEER))
    assert response.status_code == 200
    body = response.text
    for value in INVENTED_PHI:
        assert value not in body, f"{value} reached the wire in the clear"

    page = response.json()
    assert page["masked_columns"] == [
        "source_member_id",
        "first_name",
        "last_name",
        "date_of_birth",
    ]
    assert all(page["rows"][0][column]["masked"] for column in page["masked_columns"])
    assert page["rows"][0]["line_of_business"]["masked"] is False


def test_a_masked_cell_says_why(client: TestClient) -> None:
    """The rule is readable at the point of the hiding, not in a policy
    document the reader does not have."""
    page = client.get("/api/layers/silver_raw/tables/members/rows", headers=_as(ENGINEER)).json()
    assert "is_phi" in page["rows"][0]["first_name"]["reason"]


def test_bronzes_json_column_keeps_the_source_column_names(client: TestClient) -> None:
    """The keys are what an engineer opening Bronze is looking for; the values
    are the member. So: keys shown, values gone."""
    page = client.get("/api/layers/bronze/tables/members_raw/rows", headers=_as(ENGINEER)).json()
    raw = page["rows"][0]["raw_row"]
    assert raw["masked"] is True
    assert "MemberID" in str(raw["value"]) and "First_Name" in str(raw["value"])
    assert "Byron" not in str(raw["value"])


def test_the_page_reports_the_total_so_a_screen_cannot_imply_it_is_all(
    client: TestClient,
) -> None:
    page = client.get(
        "/api/layers/bronze/tables/members_raw/rows?limit=2", headers=_as(ENGINEER)
    ).json()
    assert len(page["rows"]) == 2
    assert page["total_rows"] == 6
    assert page["truncated"] is True


def test_column_order_is_the_contracts_not_the_engines(client: TestClient) -> None:
    """Reading the engine's order would let a plane's migration history decide
    what a screen looks like."""
    page = client.get("/api/layers/silver_raw/tables/members/rows", headers=_as(ENGINEER)).json()
    assert page["columns"][:3] == ["member_row_id", "feed_id", "source_member_id"]
    assert page["columns"][-1] == "updated_ts"


def test_rows_from_an_unbuilt_layer_are_409_with_the_reason(client: TestClient) -> None:
    """Not 404: the layer is real and the request is well-formed. What is
    absent is the SCHEMA, and the reason is worth sending."""
    response = client.get("/api/layers/gold/tables/anything/rows", headers=_as(ENGINEER))
    assert response.status_code == 409
    assert "Wave 4" in response.json()["detail"]


def test_a_table_name_off_the_wire_never_reaches_an_identifier(client: TestClient) -> None:
    """The injection guard, asserted as a 404 rather than as an escaped string.

    A name that is not in the schema contract is rejected by
    `core.layers.table_of` BEFORE the adapter is called, which is why the
    reader's signature takes a contract `Table` and not a string.
    """
    for attempt in ("members; DROP TABLE bronze.members_raw", 'members" --', "pg_shadow"):
        response = client.get(
            f"/api/layers/silver_raw/tables/{attempt}/rows", headers=_as(ENGINEER)
        )
        assert response.status_code == 404, attempt


def test_rows_with_no_plane_fitted_say_so_rather_than_500(planeless: TestClient) -> None:
    response = planeless.get("/api/layers/silver_raw/tables/members/rows", headers=_as(ENGINEER))
    assert response.status_code == 503
    assert "no data plane is fitted" in response.json()["detail"]


# ── who may read ─────────────────────────────────────────────────────────────
def test_a_read_only_user_sees_the_layers_exactly_as_an_engineer_does(
    client: TestClient,
) -> None:
    """Masking is not a permission tier. There is ONE answer for everyone,
    which is the answer nobody has to be trusted with — and that is only true
    if the two responses are byte-identical."""
    engineer = client.get("/api/layers/silver_raw/tables/members/rows", headers=_as(ENGINEER))
    analyst = client.get("/api/layers/silver_raw/tables/members/rows", headers=_as(READ_ONLY))
    assert analyst.status_code == 200
    assert analyst.json() == engineer.json()


@pytest.mark.parametrize(
    "path",
    [
        "/api/layers",
        "/api/layers/bronze",
        "/api/layers/bronze/tables/members_raw/rows",
    ],
)
def test_no_layer_route_serves_an_anonymous_caller(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/api/layers",
        "/api/layers/bronze",
        "/api/layers/bronze/tables/members_raw/rows",
    ],
)
def test_a_user_in_no_group_is_refused(client: TestClient, path: str) -> None:
    assert client.get(path, headers=_as(NO_GROUP)).status_code == 403
