"""CF-V1-E3-05 through the API. Upload a file, and see what the platform did.

    "1. Upload sample, …"  — CF-V1-E4-01's first step, which until now named a
    capability no route provided.

The route is a CONNECTOR call, not a write. What that buys is asserted here:
the same landing controls run over an uploaded file as over a polled one, the
file is registered whatever the outcome, and a rejection comes back as a
finding a person can act on rather than an HTTP error they retry.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.local.localfs_storage import LocalFsStorage
from cinqflow.adapters.local.upload_connector import UploadConnector
from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.api import create_app
from cinqflow.core.delivery import fingerprint_of
from cinqflow.core.model.governed import Actor, LifecycleState
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry.golden_fidelis import FEED

pytestmark = pytest.mark.contract

NOW = datetime(2026, 8, 30, 6, 0, tzinfo=UTC)
BA = "dev-ba@cinqcare.test"
ENGINEER = "dev-engineer@cinqcare.test"
READER = "dev-analyst@cinqcare.test"
DATE = "2026-10-01"
GOOD_NAME = "_CINQDOWNSTATE_Member_Roster_202610.csv"

ROSTER = (
    b"MemberID,First_Name,Last_Name,DOB,Gender,LOB,Effective_Date,Termination_Date\n"
    b"M900001,Ada,Lovelace,19901215,F,MEDICAID,2026-10-01,\n"
    b"M900002,,Hopper,19850302,F,MEDICAID,2026-10-01,\n"
)


@pytest.fixture
def landing(tmp_path: Path) -> LocalFsStorage:
    return LocalFsStorage(root=str(tmp_path / "landing"))


@pytest.fixture
def store() -> MemMetadataDb:
    memory = MemMetadataDb()
    author = Actor(subject=BA, actor_type=ActorType.HUMAN)
    from dataclasses import replace

    governed = FEED.as_governed(author=author, created_ts=NOW)
    memory.save(
        replace(
            governed,
            lifecycle_state=LifecycleState.PUBLISHED,
            approved_by=Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN),
            approved_ts=NOW,
        )
    )
    return memory


@pytest.fixture
def client(store: MemMetadataDb, landing: LocalFsStorage) -> Iterator[TestClient]:
    app = create_app(
        authn=StaticAuthn(),
        metadata_db=store,
        control_tables=MemStoreControlTables(),
        storage=landing,
        connector=UploadConnector(landing),
    )
    with TestClient(app) as test_client:
        yield test_client


def _upload(
    client: TestClient,
    *,
    name: str = GOOD_NAME,
    content: bytes = ROSTER,
    who: str = ENGINEER,
    business_date: str = DATE,
    **form: object,
):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/feeds/{FEED.feed_id}/deliveries",
        headers={"Authorization": f"Bearer {who}"},
        files={"file": (name, content, "text/csv")},
        data={"business_date": business_date, **form},
    )


# ── the happy path a BA actually walks ───────────────────────────────────────


def test_a_delivered_file_is_accepted_and_lands_under_the_layout(client: TestClient) -> None:
    response = _upload(client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["outcome"] == "ACCEPTED"
    assert body["key"] == (f"{FEED.landing_path}/incoming/{DATE}/{GOOD_NAME}")
    assert body["fingerprint"] == fingerprint_of(ROSTER)


def test_the_receipt_says_where_the_file_is_now_not_where_it_was_put(
    client: TestClient, landing: LocalFsStorage
) -> None:
    """Landing MOVES an accepted file out of `incoming/` before this returns.

    A receipt naming the delivered key sent somebody to look in an empty
    directory for a file the platform had already accepted — so the key a
    person is shown is asserted against the filesystem, not against the
    composition that produced it.
    """
    body = _upload(client).json()
    assert body["landed_key"] == f"{FEED.landing_path}/processed/{DATE}/{GOOD_NAME}"
    assert landing.exists(body["landed_key"])
    assert not landing.exists(body["key"])


def test_a_parked_file_is_reported_where_a_person_can_go_and_look_at_it(
    client: TestClient, landing: LocalFsStorage
) -> None:
    """The unexpected ones matter most here — parked is only better than lost
    if somebody can be told where the parking is."""
    body = _upload(client, name="something_nobody_registered.csv").json()
    assert "/parked/" in body["landed_key"]
    assert landing.exists(body["landed_key"])


def test_the_receipt_cites_the_file_so_the_explorer_can_open_it(client: TestClient) -> None:
    """No new citation kind was needed — a delivery IS a file."""
    body = _upload(client).json()
    assert body["citation_id"] == f"file:{fingerprint_of(ROSTER)}"
    assert body["route"].startswith("/data/explorer/landing/")


def test_an_accepted_delivery_is_profiled_by_computation(client: TestClient) -> None:
    """The insight on upload is ARITHMETIC. No model is called, which is what
    makes every fact on the next screen citable."""
    body = _upload(client).json()
    assert body["profile_id"], "an accepted file must be profiled"


def test_the_receipt_says_what_to_do_next(client: TestClient) -> None:
    body = _upload(client).json()
    assert "Approve the schema next" in body["next_step"]


def test_the_delivery_records_both_the_person_and_the_connector(
    client: TestClient,
) -> None:
    """Two different questions. An approver asking "who put this content in the
    estate" wants the person; an operator asking "how did it arrive" wants the
    connector, and a poller has one and not the other."""
    body = _upload(client).json()
    assert body["delivered_by"] == ENGINEER
    assert body["source"] == "upload-endpoint"


# ── a rejection is a finding, not an HTTP error ──────────────────────────────


def test_a_file_matching_no_pattern_is_parked_and_reported_at_201(
    client: TestClient,
) -> None:
    """The request SUCCEEDED — the bytes arrived, the row exists, the file is
    parked where somebody can look at it. A 400 would blame the caller for
    something the payer did."""
    response = _upload(client, name="something_nobody_registered.csv")
    assert response.status_code == 201
    body = response.json()
    assert body["outcome"] == "UNEXPECTED"
    assert body["check_name"] == "feed_pattern"
    assert "parked, not lost" in body["next_step"]


def test_a_rejection_always_names_the_check_that_made_it(client: TestClient) -> None:
    """A rejection with no named check is an unattributed drop wearing a
    different hat."""
    body = _upload(client, name="something_nobody_registered.csv").json()
    assert body["reason"]
    assert body["check_name"]


def test_an_unaccepted_file_is_not_profiled(client: TestClient) -> None:
    """Facts about bytes the platform declined to load would be cited later as
    though they described the feed."""
    body = _upload(client, name="something_nobody_registered.csv").json()
    assert body["profile_id"] is None


def test_delivering_the_same_content_twice_is_skipped_by_fingerprint(
    client: TestClient,
) -> None:
    """Exactly-once ingestion, demonstrated through the door a person uses."""
    assert _upload(client).json()["outcome"] == "ACCEPTED"
    second = _upload(client, business_date="2026-11-01").json()
    assert second["outcome"] == "SKIPPED"
    assert "already processed" in (second["reason"] or "")
    assert "already been processed" in second["next_step"]


# ── the door refuses what is not a delivery ──────────────────────────────────


@pytest.mark.parametrize("hostile", ["../../etc/passwd", "sub/dir/roster.csv", ".."])
def test_a_filename_that_is_a_path_is_refused_at_400(client: TestClient, hostile: str) -> None:
    """A caller choosing the platform's write path. THIS one is the caller's
    mistake, so it IS a 400."""
    response = _upload(client, name=hostile)
    assert response.status_code == 400
    assert "path" in response.json()["detail"].lower()


def test_a_checksum_that_disagrees_with_the_bytes_is_refused_at_400(
    client: TestClient,
) -> None:
    response = _upload(client, checksum="sha256-" + "0" * 32)
    assert response.status_code == 400
    assert "damaged" in response.json()["detail"]


def test_a_business_date_that_is_not_one_is_refused(client: TestClient) -> None:
    response = _upload(client, business_date="2026-9-1")
    assert response.status_code == 400
    assert "two folders" in response.json()["detail"]


def test_nothing_is_landed_when_the_checksum_is_wrong(
    client: TestClient, landing: LocalFsStorage
) -> None:
    _upload(client, checksum="sha256-" + "0" * 32)
    assert not landing.exists(f"{FEED.landing_path}/incoming/{DATE}/{GOOD_NAME}")


# ── permissions ──────────────────────────────────────────────────────────────


def test_a_reader_cannot_deliver(client: TestClient) -> None:
    """A delivery changes what the platform holds. A reader who could deliver
    could put content into the estate."""
    assert _upload(client, who=READER).status_code == 403


def test_an_anonymous_caller_cannot_deliver(client: TestClient) -> None:
    response = client.post(
        f"/api/feeds/{FEED.feed_id}/deliveries",
        files={"file": (GOOD_NAME, ROSTER, "text/csv")},
        data={"business_date": DATE},
    )
    assert response.status_code in {401, 403}


def test_delivering_to_a_feed_that_does_not_exist_is_a_404(client: TestClient) -> None:
    response = client.post(
        "/api/feeds/nobody-registered-this/deliveries",
        headers={"Authorization": f"Bearer {ENGINEER}"},
        files={"file": (GOOD_NAME, ROSTER, "text/csv")},
        data={"business_date": DATE},
    )
    assert response.status_code == 404


# ── a deployment with no connector says so ───────────────────────────────────


def test_a_deployment_with_no_connector_refuses_honestly(
    store: MemMetadataDb, landing: LocalFsStorage
) -> None:
    """503 and a sentence naming the profile key to fit — not a 500, and not a
    silent success into nowhere."""
    app = create_app(authn=StaticAuthn(), metadata_db=store, storage=landing)
    with TestClient(app) as client:
        response = client.post(
            f"/api/feeds/{FEED.feed_id}/deliveries",
            headers={"Authorization": f"Bearer {ENGINEER}"},
            files={"file": (GOOD_NAME, ROSTER, "text/csv")},
            data={"business_date": DATE},
        )
    assert response.status_code == 503
    assert "connector" in response.json()["detail"]


def test_the_source_check_reports_reachability(client: TestClient) -> None:
    response = client.get(
        f"/api/feeds/{FEED.feed_id}/deliveries/source",
        headers={"Authorization": f"Bearer {ENGINEER}"},
    )
    assert response.status_code == 200
    assert response.json()["reachable"] is True


def test_the_source_check_says_when_no_connector_is_fitted(
    store: MemMetadataDb, landing: LocalFsStorage
) -> None:
    app = create_app(authn=StaticAuthn(), metadata_db=store, storage=landing)
    with TestClient(app) as client:
        body = client.get(
            f"/api/feeds/{FEED.feed_id}/deliveries/source",
            headers={"Authorization": f"Bearer {ENGINEER}"},
        ).json()
    assert body["reachable"] is False
    assert "no connector pin" in body["detail"]
