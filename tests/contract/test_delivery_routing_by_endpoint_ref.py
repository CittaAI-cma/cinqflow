"""CF-V1-E8-09 through the API — a feed's OWN `endpoint_ref` picks its route.

    "Configure every connector entirely from the feed registry's delivery
     section plus profile secrets — zero code per source."
    — CF-V1-E8-09, acceptance criteria

Two connectors are fitted on one deployment — `default` and a NAMED route,
`fidelis-sftp` — and this asserts a feed's delivery travels through the one
its OWN `operations.endpoint_ref` names, not whichever one happens to be the
deployment's default. That is the join `installer.connectors.connectors_from`
and `api.app._connector_for` make real; `test_connectors_wiring.py` already
covers the profile-parsing half in isolation, so this exercises the other
half — the request path — end to end.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.authn import StaticAuthn
from cinqflow.adapters.mock.connector import ScriptedConnector
from cinqflow.adapters.mock.control_tables import MemStoreControlTables
from cinqflow.adapters.mock.metadata_db import MemMetadataDb
from cinqflow.adapters.mock.storage import MemFsStorage
from cinqflow.api import create_app
from cinqflow.core.model.governed import Actor, LifecycleState
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry.golden_fidelis import FEED
from cinqflow.core.registry.operations import FeedOperations

pytestmark = pytest.mark.contract

NOW = datetime(2026, 9, 1, 6, 0, tzinfo=UTC)
BA = "dev-ba@cinqcare.test"
ENGINEER = "dev-engineer@cinqcare.test"
DATE = "2026-09-01"
GOOD_NAME = "_CINQDOWNSTATE_Member_Roster_202609.csv"
ROSTER = b"MemberID,First_Name\nM900001,Ada\n"


def _feed_store(*, endpoint_ref: str) -> MemMetadataDb:
    memory = MemMetadataDb()
    author = Actor(subject=BA, actor_type=ActorType.HUMAN)
    governed = FEED.as_governed(author=author, created_ts=NOW)
    body = {**governed.body, "operations": FeedOperations(endpoint_ref=endpoint_ref).as_body()}
    memory.save(
        replace(
            governed,
            body=body,
            lifecycle_state=LifecycleState.PUBLISHED,
            approved_by=Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN),
            approved_ts=NOW,
        )
    )
    return memory


def _app(
    store: MemMetadataDb, connectors: dict[str, ScriptedConnector], storage: MemFsStorage
) -> object:
    # `DeliveryWorker.storage` and the resolved connector's OWN storage must
    # be the SAME object: the worker moves a file post-classification inside
    # the storage it holds, and a connector that landed the bytes somewhere
    # else would leave the worker moving a key that is not there.
    return create_app(
        authn=StaticAuthn(),
        metadata_db=store,
        control_tables=MemStoreControlTables(),
        storage=storage,
        connectors=connectors,  # type: ignore[arg-type]
    )


def _connector(storage: MemFsStorage, *, source: str, reachable: bool = True) -> ScriptedConnector:
    return ScriptedConnector(storage, source=source, reachable=reachable)


def _upload(client: TestClient) -> object:
    return client.post(
        f"/api/feeds/{FEED.feed_id}/deliveries",
        headers={"Authorization": f"Bearer {ENGINEER}"},
        files={"file": (GOOD_NAME, ROSTER, "text/csv")},
        data={"business_date": DATE},
    )


def test_a_feed_with_a_named_endpoint_ref_delivers_through_that_route() -> None:
    store = _feed_store(endpoint_ref="fidelis-sftp")
    storage = MemFsStorage()
    connectors = {
        "default": _connector(storage, source="upload-endpoint"),
        "fidelis-sftp": _connector(storage, source="fidelis-sftp"),
    }
    with TestClient(_app(store, connectors, storage)) as client:  # type: ignore[arg-type]
        body = _upload(client).json()  # type: ignore[attr-defined]
    assert body["source"] == "fidelis-sftp"


def test_a_feed_with_no_endpoint_ref_falls_back_to_default() -> None:
    store = _feed_store(endpoint_ref="")
    storage = MemFsStorage()
    connectors = {
        "default": _connector(storage, source="upload-endpoint"),
        "fidelis-sftp": _connector(storage, source="fidelis-sftp"),
    }
    with TestClient(_app(store, connectors, storage)) as client:  # type: ignore[arg-type]
        body = _upload(client).json()  # type: ignore[attr-defined]
    assert body["source"] == "upload-endpoint"


def test_a_feed_whose_endpoint_ref_names_no_fitted_route_falls_back_to_default() -> None:
    """A feed onboarded before its named connector was configured must still
    deliver — falling back rather than refusing is what keeps that possible."""
    store = _feed_store(endpoint_ref="payer-nobody-configured-yet")
    storage = MemFsStorage()
    connectors = {"default": _connector(storage, source="upload-endpoint")}
    with TestClient(_app(store, connectors, storage)) as client:  # type: ignore[arg-type]
        body = _upload(client).json()  # type: ignore[attr-defined]
    assert body["source"] == "upload-endpoint"


def test_delivery_source_reachability_is_asked_of_the_feeds_own_route() -> None:
    store = _feed_store(endpoint_ref="fidelis-sftp")
    storage = MemFsStorage()
    connectors = {
        "default": _connector(storage, source="upload-endpoint"),
        "fidelis-sftp": _connector(storage, source="fidelis-sftp", reachable=False),
    }
    with TestClient(_app(store, connectors, storage)) as client:  # type: ignore[arg-type]
        response = client.get(
            f"/api/feeds/{FEED.feed_id}/deliveries/source",
            headers={"Authorization": f"Bearer {ENGINEER}"},
        )
    body = response.json()
    assert body["source"] == "fidelis-sftp"
    assert body["reachable"] is False
