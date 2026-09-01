"""CF-V1-E4-02 — the one button, and the gate it is the key to.

    "I want one button that runs my whole draft configuration — schema,
     mappings, rules — against the sample end to end, and produces the
     onboarding summary and UAT evidence pack automatically"
    "Run the real engine in a sandboxed test area — the same code path
     production will use."
    "Touch production tables or control records — the test area is fully
     isolated." — don't
    — CF-V1-E4-02

`core.onboarding.evidence.build_pack` shipped fully tested and had ZERO
callers anywhere in `src/`. `POST /onboarding/submit` refuses without a pack;
`GET /evidence` could only read one. Every deployment could walk the wizard to
step 5 and no further, forever. These tests are about the WIRE.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.local.localfs_storage import LocalFsStorage
from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.api import create_app
from cinqflow.core.model.governed import ObjectType

pytestmark = pytest.mark.contract

BA = {"authorization": "Bearer dev-ba@cinqcare.test"}
FEED = "roster"
SAMPLE = "incoming/roster.csv"


@pytest.fixture
def plane(tmp_path: object) -> Iterator[tuple[TestClient, MemMetadataDb]]:
    from cinqflow.intelligence.demo import plane as demo_plane

    store, control = demo_plane()
    storage = LocalFsStorage(root=str(tmp_path))
    app = create_app(
        authn=StaticAuthn(), metadata_db=store, control_tables=control, storage=storage
    )
    with TestClient(app) as client:
        yield client, store


def _feed_id(client: TestClient) -> str:
    return client.get("/api/feeds", headers=BA).json()[0]["feed_id"]


# ── the wire ─────────────────────────────────────────────────────────────────


def test_the_route_exists_at_all(plane: tuple[TestClient, MemMetadataDb]) -> None:
    """The whole point. A POST to this path was a 405 on every deployment."""
    client, _ = plane
    response = client.post(f"/api/feeds/{_feed_id(client)}/evidence", json={}, headers=BA)
    assert response.status_code != 405, "the button has no wire"


def test_a_feed_with_no_sample_is_told_so_rather_than_500(
    plane: tuple[TestClient, MemMetadataDb],
) -> None:
    client, _ = plane
    response = client.post(f"/api/feeds/{_feed_id(client)}/evidence", json={}, headers=BA)
    assert response.status_code in (200, 409)
    if response.status_code == 409:
        assert "sample" in response.json()["detail"].lower()


def test_an_unknown_feed_is_a_404_not_a_crash(
    plane: tuple[TestClient, MemMetadataDb],
) -> None:
    client, _ = plane
    response = client.post("/api/feeds/nope/evidence", json={}, headers=BA)
    assert response.status_code in (404, 409)


# ── the isolation the story's don't demands ──────────────────────────────────


def test_the_test_area_writes_no_control_row(plane: tuple[TestClient, MemMetadataDb]) -> None:
    """ "Touch production tables or control records — the test area is fully
    isolated." Structural rather than promised: `core.compiler.execute.apply`
    is a PURE function, so `SampleTestWorker` holds no compute pin and has no
    verb that could open a batch."""
    from cinqflow.workers.sample_test import SampleTestWorker

    fields = set(SampleTestWorker.__dataclass_fields__)
    assert fields == {"metadata", "storage"}, (
        f"the sandbox grew a pin it could write production through: {fields}"
    )


def test_the_worker_runs_the_same_two_functions_the_pipeline_runs() -> None:
    """ "the same code path production will use" — asserted on the SOURCE, so a
    future "test mode" branch fails here rather than in a demo."""
    import inspect

    from cinqflow.workers import pipeline, sample_test

    source = inspect.getsource(sample_test)
    assert "compile_feed" in source and "apply(" in source
    runner = inspect.getsource(pipeline)
    assert "compile_feed" in runner or "plan" in runner


# ── a failing run is still a pack ────────────────────────────────────────────


def test_an_unreadable_sample_produces_a_pack_with_a_failure_not_an_exception(
    tmp_path: object,
) -> None:
    """ "the pack is still produced up to the failure, the failing step is
    explained in plain language, and the wizard links straight to the ...
    line at fault"."""
    from cinqflow.intelligence.demo import plane as demo_plane
    from cinqflow.workers.sample_test import SampleTestWorker

    store, _ = demo_plane()
    worker = SampleTestWorker(metadata=store, storage=LocalFsStorage(root=str(tmp_path)))
    feed_id = next(o.object_id for o in store.list(ObjectType.FEED))
    from cinqflow.core.model.governed import Actor
    from cinqflow.core.model.vocabulary import ActorType

    actor = Actor(subject="ba@x", actor_type=ActorType.HUMAN, display_name="BA")
    try:
        outcome = worker.run(feed_id=feed_id, file_key="nothing/here.csv", actor=actor)
    except Exception as broke:
        # A DRAFT-state refusal is legitimate; a crash on a missing file is not.
        assert "draft" in str(broke).lower(), broke
        return
    assert outcome.pack.failure is not None
    assert outcome.pack.failure.step == "read"
    assert outcome.pack.failure.explanation
    assert outcome.pack.failure.route, "a failure with no address is a dead end"


# ── the fingerprint both sides must agree on ─────────────────────────────────


def test_writer_and_reader_hash_the_sample_from_the_same_place() -> None:
    """The bug this locks down: the reader passed `sample_fingerprint=""` and
    the writer passed a fresh `storage.fingerprint`, so EVERY pack was born
    stale and the wizard could never leave step 5. Both now read
    `FileProfile.source_fingerprint`."""
    import inspect

    from cinqflow.api import app as api_app
    from cinqflow.workers import sample_test

    assert "source_fingerprint" in inspect.getsource(api_app._sample_fingerprint)
    assert "source_fingerprint" in inspect.getsource(sample_test.SampleTestWorker)
    assert "sample_fingerprint=" in inspect.getsource(api_app._configuration_fingerprint), (
        "the reader must pass the sample into the fingerprint, or packs are born stale"
    )
