"""CF-V1-E5-01 — step 1 of the wizard, over the wire and over the pins.

    Task pack: Data/pipeline (adapter only) -> Metadata/schema -> Backend API
    (status) -> Eval/test (replay & restart proofs)
    — CINQFLOW_MVP_Backlog.csv, CF-V1-E5-01

Contract layer, so it runs the REAL composition: a real storage adapter holding
real bytes, the real profiler, the real metadata store. What is asserted here
cannot be asserted in a unit test — that the storage pin, the metadata pin and
the permission matrix agree.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.local.localfs_storage import LocalFsStorage
from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.api.app import create_app
from cinqflow.core.profiling import profile_bytes
from cinqflow.ports.metadata_db import FileProfileRecord, ObjectNotFoundError
from cinqflow.workers.profiler import Profiler, ProfileTargetMissingError

pytestmark = pytest.mark.contract

FEED = "fidelis-downstate-roster"
KEY = "enrollments/fidelis_downstate/roster/incoming/2026-08-01/_ROSTER_202608.csv"

ROSTER = (
    b"MemberID,First_Name,Last_Name,DOB,LOB\n"
    b"MBR000001,FIRST000001,LAST000001,19360201,MEDICAID\n"
    b"MBR000002,,LAST000002,19370302,MEDICARE\n"
    b"MBR000003,FIRST000003,LAST000003,19380403,DUAL\n"
)

#: From `profiles/dev-users.yaml` and `adapters/mock/authn.py` — kept in step.
BA = "dev-ba@cinqcare.test"
READER = "dev-analyst@cinqcare.test"
STEWARD = "dev-steward@cinqcare.test"


@pytest.fixture
def storage(tmp_path) -> LocalFsStorage:  # type: ignore[no-untyped-def]
    store = LocalFsStorage(root=str(tmp_path))
    store.place(KEY, ROSTER)
    return store


@pytest.fixture
def metadata() -> MemMetadataDb:
    return MemMetadataDb()


@pytest.fixture
def client(storage: LocalFsStorage, metadata: MemMetadataDb) -> TestClient:
    app = create_app(authn=StaticAuthn(), metadata_db=metadata, storage=storage)
    return TestClient(app)


def _as(client: TestClient, subject: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {subject}"}


# ── the worker, over real pins ───────────────────────────────────────────────


def test_the_profiler_reads_through_the_storage_pin_and_stores_through_metadata(
    storage: LocalFsStorage, metadata: MemMetadataDb
) -> None:
    record = Profiler(storage=storage, metadata=metadata).profile(
        feed_id=FEED, file_key=KEY, file_format="csv", profiled_by=BA
    )

    assert record.profile.structure.data_rows == 3
    assert record.profile.source_fingerprint == storage.fingerprint(KEY)
    assert metadata.get_profile(record.profile_id, FEED) == record


def test_re_profiling_an_unchanged_file_returns_the_original_record(
    storage: LocalFsStorage, metadata: MemMetadataDb
) -> None:
    """THE REPLAY PROOF, as a database property rather than a test assertion.

    The profile's id is the digest of its facts, so a second run collides by
    construction and the first row keeps its timestamp. Evidence that has not
    changed must not look newer for having been recomputed, or a stale-evidence
    gate starts passing submissions it should hold.
    """
    profiler = Profiler(storage=storage, metadata=metadata)
    first = profiler.profile(
        feed_id=FEED,
        file_key=KEY,
        file_format="csv",
        profiled_by=BA,
        now=datetime(2026, 8, 1, tzinfo=UTC),
    )
    second = profiler.profile(
        feed_id=FEED,
        file_key=KEY,
        file_format="csv",
        profiled_by=BA,
        now=datetime(2026, 8, 30, tzinfo=UTC),
    )

    assert second.profile_id == first.profile_id
    assert second.profiled_ts == first.profiled_ts, "the evidence did not change"
    assert len(metadata.list_profiles(feed_id=FEED)) == 1


def test_a_changed_file_profiles_to_a_second_record(
    storage: LocalFsStorage, metadata: MemMetadataDb
) -> None:
    """A payer's file changing shape between samples must be visible as two
    profiles, not as one that quietly overwrote the other."""
    profiler = Profiler(storage=storage, metadata=metadata)
    first = profiler.profile(feed_id=FEED, file_key=KEY, file_format="csv", profiled_by=BA)
    storage.place(KEY, ROSTER + b"MBR000004,FIRST000004,LAST000004,19390504,DUAL\n")
    second = profiler.profile(feed_id=FEED, file_key=KEY, file_format="csv", profiled_by=BA)

    assert second.profile_id != first.profile_id
    assert len(metadata.list_profiles(feed_id=FEED)) == 2
    assert metadata.list_profiles(feed_id=FEED)[0].profile.structure.data_rows == 4


def test_a_missing_file_is_a_different_incident_from_an_unreadable_one(
    storage: LocalFsStorage, metadata: MemMetadataDb
) -> None:
    """ "We read your file and could not make sense of it" and "there is no such
    file" send a BA to two different places. Reporting the second as the first
    sends them to the payer over a typo."""
    with pytest.raises(ProfileTargetMissingError):
        Profiler(storage=storage, metadata=metadata).profile(
            feed_id=FEED, file_key="nothing/here.csv", file_format="csv", profiled_by=BA
        )


def test_an_unreadable_file_is_still_stored_as_a_profile(
    storage: LocalFsStorage, metadata: MemMetadataDb
) -> None:
    """A profiling attempt that left no row would be the one class of file
    nobody can see they tried."""
    bad = "enrollments/fidelis_downstate/roster/incoming/2026-08-01/bad.csv"
    storage.place(bad, "MemberID,Name\nMBR000001,JOSÉ\n".encode("latin-1"))

    record = Profiler(storage=storage, metadata=metadata).profile(
        feed_id=FEED, file_key=bad, file_format="csv", profiled_by=BA
    )
    assert record.profile.readable is False
    assert record.profile.refusal is not None
    assert metadata.get_profile(record.profile_id, FEED).profile.refusal is not None


def test_the_same_content_under_a_new_name_is_recognised(
    storage: LocalFsStorage, metadata: MemMetadataDb
) -> None:
    """A payer who re-sends the same month under `_RESEND.csv` has not given us
    a new file, and re-reading 50MB to discover that is waste."""
    profiler = Profiler(storage=storage, metadata=metadata)
    profiler.profile(feed_id=FEED, file_key=KEY, file_format="csv", profiled_by=BA)
    resend = KEY.replace(".csv", "_RESEND.csv")
    storage.place(resend, ROSTER)

    already = profiler.already_profiled(feed_id=FEED, file_key=resend)
    assert already is not None
    assert already.structure.data_rows == 3


# ── the routes ───────────────────────────────────────────────────────────────


def test_a_ba_profiles_a_sample_and_gets_the_facts(client: TestClient) -> None:
    response = client.post(
        f"/api/feeds/{FEED}/profile",
        json={"file_key": KEY, "file_format": "csv"},
        headers=_as(client, BA),
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["readable"] is True
    assert body["would_load"] is True
    assert body["structure"]["data_rows"] == 3
    assert next(c["name"] for c in body["columns"]) == "MemberID"
    assert body["citation_id"].startswith("profile:sha256-")
    assert body["route"].startswith("/data/intake/profile/")


def test_every_column_carries_a_citation_that_opens_it(client: TestClient) -> None:
    """CF-V1-E5-02 must cite the number it interprets, so the number needs an
    address before the agent that uses it exists."""
    body = client.post(
        f"/api/feeds/{FEED}/profile",
        json={"file_key": KEY, "file_format": "csv"},
        headers=_as(client, BA),
    ).json()

    dob = next(c for c in body["columns"] if c["name"] == "DOB")
    assert dob["citation_id"] == f"{body['profile_id'].join(['profile:', '#DOB'])}"
    assert dob["route"] == f"/data/intake/profile/{body['profile_id']}?column=DOB"


def test_a_read_only_user_sees_the_statistics_and_not_the_values(client: TestClient) -> None:
    """ "Send sample data anywhere except storage the BA's role can access."

    And the property that makes the redaction safe rather than merely careful:
    the profile_id is UNCHANGED, so the two readers are provably looking at one
    piece of evidence.
    """
    authored = client.post(
        f"/api/feeds/{FEED}/profile",
        json={"file_key": KEY, "file_format": "csv"},
        headers=_as(client, BA),
    ).json()

    seen = client.get(
        f"/api/feeds/{FEED}/profiles/{authored['profile_id']}", headers=_as(client, READER)
    ).json()

    assert seen["values_redacted"] is True
    assert all(column["examples"] == [] for column in seen["columns"])
    assert all(column["min_value"] is None for column in seen["columns"])
    assert all(key["examples"] == [] for key in seen["key_candidates"])
    assert seen["profile_id"] == authored["profile_id"], "same evidence, different view"
    assert [c["distinct_count"] for c in seen["columns"]] == [
        c["distinct_count"] for c in authored["columns"]
    ]


def test_a_steward_reviewing_the_evidence_also_sees_it_redacted(client: TestClient) -> None:
    """The steward approves the CONTRACT, and does not need the member values
    to do it. Least privilege applies to approvers too."""
    authored = client.post(
        f"/api/feeds/{FEED}/profile",
        json={"file_key": KEY, "file_format": "csv"},
        headers=_as(client, BA),
    ).json()
    seen = client.get(
        f"/api/feeds/{FEED}/profiles/{authored['profile_id']}", headers=_as(client, STEWARD)
    ).json()
    assert seen["values_redacted"] is True


def test_a_read_only_user_may_not_profile(client: TestClient) -> None:
    """Profiling reads a file and writes a row. Read-Only is refused at the
    SERVER, not hidden in the menu."""
    response = client.post(
        f"/api/feeds/{FEED}/profile",
        json={"file_key": KEY, "file_format": "csv"},
        headers=_as(client, READER),
    )
    assert response.status_code == 403


def test_an_anonymous_caller_may_not_profile(client: TestClient) -> None:
    response = client.post(
        f"/api/feeds/{FEED}/profile", json={"file_key": KEY, "file_format": "csv"}
    )
    assert response.status_code in {401, 403}


def test_profiling_leaves_an_audit_row(client: TestClient, metadata: MemMetadataDb) -> None:
    client.post(
        f"/api/feeds/{FEED}/profile",
        json={"file_key": KEY, "file_format": "csv"},
        headers=_as(client, BA),
    )
    actions = [entry.action for entry in metadata.read_audit(object_id=FEED)]
    assert "profile_file" in actions


def test_a_missing_file_is_a_404_with_the_key_in_it(client: TestClient) -> None:
    response = client.post(
        f"/api/feeds/{FEED}/profile",
        json={"file_key": "no/such/file.csv", "file_format": "csv"},
        headers=_as(client, BA),
    )
    assert response.status_code == 404
    assert "no/such/file.csv" in response.json()["detail"]


def test_an_unreadable_file_is_a_200_with_a_refusal_not_a_500(client: TestClient) -> None:
    """An unreadable file is a FACT ABOUT THE FILE, not a failure of the
    request — and a 500 tells the BA nothing they can act on."""
    storage: LocalFsStorage = client.app.state.storage  # type: ignore[attr-defined]
    bad = "enrollments/fidelis_downstate/roster/incoming/2026-08-01/bad.csv"
    storage.place(bad, "MemberID,Name\nMBR000001,JOSÉ\n".encode("latin-1"))

    response = client.post(
        f"/api/feeds/{FEED}/profile",
        json={"file_key": bad, "file_format": "csv"},
        headers=_as(client, BA),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["readable"] is False
    assert body["refusal"]["reason"] == "undecodable"
    assert "encoding" in body["refusal"]["ask_the_payer"]


def test_the_status_list_is_newest_first(client: TestClient) -> None:
    storage: LocalFsStorage = client.app.state.storage  # type: ignore[attr-defined]
    client.post(
        f"/api/feeds/{FEED}/profile",
        json={"file_key": KEY, "file_format": "csv"},
        headers=_as(client, BA),
    )
    storage.place(KEY, ROSTER + b"MBR000004,FIRST000004,LAST000004,19390504,DUAL\n")
    client.post(
        f"/api/feeds/{FEED}/profile",
        json={"file_key": KEY, "file_format": "csv"},
        headers=_as(client, BA),
    )

    listed = client.get(f"/api/feeds/{FEED}/profiles", headers=_as(client, BA)).json()
    assert len(listed) == 2
    assert listed[0]["structure"]["data_rows"] == 4


def test_a_deployment_with_no_landing_zone_says_so(metadata: MemMetadataDb) -> None:
    """Rather than 500ing or pretending. A profiler pointed at nothing would
    answer "file not found" for every real sample."""
    client = TestClient(create_app(authn=StaticAuthn(), metadata_db=metadata))
    response = client.post(
        f"/api/feeds/{FEED}/profile",
        json={"file_key": KEY, "file_format": "csv"},
        headers={"Authorization": f"Bearer {BA}"},
    )
    assert response.status_code == 503
    assert "landing zone" in response.json()["detail"]


# ── CF-V3-E5-05 · complex formats, over the wire ─────────────────────────────

_EOB_KEY = "enrollments/fidelis_downstate/eob/incoming/2026-08-01/eob.ndjson"
_EOB_NDJSON = (
    b'{"resourceType":"ExplanationOfBenefit","id":"A100","status":"active",'
    b'"item":[{"sequence":1,"adjudication":[{"category":"eligible"},'
    b'{"category":"paid"}]}]}\n'
)
_FIXED_WIDTH_KEY = "enrollments/fidelis_downstate/cclf1/incoming/2026-08-01/cclf1.txt"


def test_an_ndjson_sample_returns_a_structure_tree_and_flattening_proposals(
    client: TestClient,
) -> None:
    storage: LocalFsStorage = client.app.state.storage  # type: ignore[attr-defined]
    storage.place(_EOB_KEY, _EOB_NDJSON)

    response = client.post(
        f"/api/feeds/{FEED}/profile",
        json={"file_key": _EOB_KEY, "file_format": "ndjson"},
        headers=_as(client, BA),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["readable"] is True
    paths = {p["path"]: p for p in body["structure_paths"]}
    assert paths["status"]["fill_rate"] == 1.0
    assert paths["item"]["is_array"] is True
    proposed = {p["source_path"] for p in body["flatten_proposals"]}
    assert proposed == {"item", "item.adjudication"}


def test_a_fixed_width_cclf1_sample_returns_the_layout_and_the_real_ambiguity(
    client: TestClient,
) -> None:
    storage: LocalFsStorage = client.app.state.storage  # type: ignore[attr-defined]
    line = ("A" * 292 + "\n").encode()
    storage.place(_FIXED_WIDTH_KEY, line * 3)

    response = client.post(
        f"/api/feeds/{FEED}/profile",
        json={"file_key": _FIXED_WIDTH_KEY, "file_format": "fixed_width"},
        headers=_as(client, BA),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["readable"] is True
    assert body["fixed_width_layout"]["source"] == "statistical"
    assert body["fixed_width_layout"]["columns"]
    ambiguous = [f for f in body["findings"] if f["quirk"] == "ambiguous_fixed_width_boundary"]
    assert ambiguous
    assert any("CCLF1" in f["detail"] for f in ambiguous)


# ── the store's own contract ─────────────────────────────────────────────────


def test_an_unknown_profile_is_not_found_rather_than_empty(metadata: MemMetadataDb) -> None:
    with pytest.raises(ObjectNotFoundError):
        metadata.get_profile("sha256-nothing", FEED)


def test_two_feeds_may_profile_the_same_file_independently(metadata: MemMetadataDb) -> None:
    """The primary key is (profile_id, feed_id): the same bytes profiled for a
    Medicaid feed and its Medicare clone are one set of facts attached to two
    feeds, not one row that the second write silently steals."""
    profile = profile_bytes(ROSTER, file_format="csv", source_fingerprint="sha256-aaa")
    now = datetime(2026, 8, 30, tzinfo=UTC)
    for feed in ("centene-medicaid", "centene-medicare"):
        metadata.record_profile(
            FileProfileRecord(feed_id=feed, profile=profile, profiled_by=BA, profiled_ts=now)
        )

    assert len(metadata.list_profiles()) == 2
    assert metadata.get_profile(profile.profile_id, "centene-medicare").feed_id == (
        "centene-medicare"
    )
