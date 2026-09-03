"""Versioning and immutability, enforced in the store against real Postgres."""

from __future__ import annotations

import pytest

from cinqflow.workflow.models import MappingField, MappingSpec
from cinqflow.workflow.store import (
    DraftAlreadyOpen,
    NotEditable,
    UnknownMappingVersion,
    WorkflowStore,
)
from tests.conftest import requires_db

pytestmark = requires_db

FEED = "test_mapping_feed"


def spec(*targets: str, table: str = "silver_raw.members") -> MappingSpec:
    return MappingSpec(
        target_table=table,
        fields=[
            MappingField(source=f"src_{i}", target=target) for i, target in enumerate(targets)
        ],
    )


@pytest.fixture
def store(conn, settings):
    return WorkflowStore(conn, settings)


def test_first_version_is_draft_v1_with_its_origin(store, conn):
    created = store.create_mapping_version(
        feed=FEED,
        domain="enrollment",
        spec=spec("members.first_name"),
        created_by="analyst@cinqcare.com",
        origin_proposal_id=None,
    )
    conn.commit()

    assert created.version == 1
    assert created.status == "draft"
    assert created.derived_from is None
    assert created.origin == "analyst_created"
    assert created.editable is True


def test_drafts_are_mutable(store, conn):
    store.create_mapping_version(
        feed=FEED, domain="enrollment", spec=spec("members.first_name"), created_by="a"
    )
    conn.commit()

    updated = store.update_draft_spec(
        feed=FEED, version=1, spec=spec("members.first_name", "members.last_name")
    )
    conn.commit()

    assert len(updated.spec.fields) == 2
    assert updated.updated_ts is not None
    assert store.get_mapping_version(FEED, 1).spec.targets == [
        "members.first_name",
        "members.last_name",
    ]


def test_an_approved_version_cannot_be_edited(store, conn):
    """The guard lives in the store, so no caller can bypass it."""
    store.create_mapping_version(
        feed=FEED, domain="enrollment", spec=spec("members.first_name"), created_by="a"
    )
    store.set_mapping_status(feed=FEED, version=1, status="approved")
    conn.commit()

    with pytest.raises(NotEditable) as exc:
        store.update_draft_spec(feed=FEED, version=1, spec=spec("members.last_name"))
    assert exc.value.status == "approved"

    conn.rollback()
    # nothing changed
    assert store.get_mapping_version(FEED, 1).spec.targets == ["members.first_name"]


def test_a_superseded_version_cannot_be_edited(store, conn):
    store.create_mapping_version(
        feed=FEED, domain="enrollment", spec=spec("members.first_name"), created_by="a"
    )
    store.set_mapping_status(feed=FEED, version=1, status="superseded")
    conn.commit()

    with pytest.raises(NotEditable):
        store.update_draft_spec(feed=FEED, version=1, spec=spec("members.last_name"))


def test_a_previewed_version_is_still_editable_and_returns_to_draft(store, conn):
    """Editing after a preview invalidates it: the preview no longer describes the spec."""
    store.create_mapping_version(
        feed=FEED, domain="enrollment", spec=spec("members.first_name"), created_by="a"
    )
    store.set_mapping_status(feed=FEED, version=1, status="previewed")
    conn.commit()

    updated = store.update_draft_spec(feed=FEED, version=1, spec=spec("members.last_name"))
    conn.commit()
    assert updated.status == "draft"


def test_deriving_the_next_version_copies_the_spec_and_records_its_parent(store, conn):
    store.create_mapping_version(
        feed=FEED,
        domain="enrollment",
        spec=spec("members.first_name", "members.last_name"),
        created_by="a",
    )
    store.set_mapping_status(feed=FEED, version=1, status="approved")
    conn.commit()

    v2 = store.create_mapping_version(
        feed=FEED,
        domain="enrollment",
        spec=store.get_mapping_version(FEED, 1).spec,
        created_by="analyst@cinqcare.com",
        derived_from=1,
    )
    conn.commit()

    assert v2.version == 2
    assert v2.status == "draft"
    assert v2.derived_from == 1
    assert v2.spec.targets == ["members.first_name", "members.last_name"]
    # the parent is untouched and still approved until a later G2 supersedes it
    assert store.get_mapping_version(FEED, 1).status == "approved"


def test_only_one_draft_per_feed(store, conn):
    store.create_mapping_version(
        feed=FEED, domain="enrollment", spec=spec("members.first_name"), created_by="a"
    )
    conn.commit()

    with pytest.raises(DraftAlreadyOpen) as exc:
        store.create_mapping_version(
            feed=FEED, domain="enrollment", spec=spec("members.last_name"), created_by="a"
        )
    assert exc.value.version == 1


def test_versions_are_numbered_per_feed(store, conn):
    store.create_mapping_version(
        feed=FEED, domain="enrollment", spec=spec("members.first_name"), created_by="a"
    )
    store.set_mapping_status(feed=FEED, version=1, status="approved")
    store.create_mapping_version(
        feed="other_feed", domain="enrollment", spec=spec("members.last_name"), created_by="a"
    )
    conn.commit()

    assert store.latest_mapping_version(FEED).version == 1
    assert store.latest_mapping_version("other_feed").version == 1
    assert [v.version for v in store.list_mapping_versions(FEED)] == [1]


def test_latest_approved_is_findable_for_diffing(store, conn):
    for targets in (["members.first_name"], ["members.last_name"], ["members.race"]):
        version = store.create_mapping_version(
            feed=FEED, domain="enrollment", spec=spec(*targets), created_by="a"
        )
        store.set_mapping_status(
            feed=FEED,
            version=version.version,
            status="approved" if version.version < 3 else "draft",
        )
    conn.commit()

    assert store.latest_mapping_version(FEED).version == 3
    assert store.latest_mapping_version(FEED, status="approved").version == 2


def test_editing_an_unknown_version_is_refused(store):
    with pytest.raises(UnknownMappingVersion):
        store.update_draft_spec(feed=FEED, version=99, spec=spec("members.first_name"))


def test_origin_explains_where_a_version_came_from(store, conn):
    """A derived version is not 'analyst_created': it says what it came from."""
    store.create_mapping_version(
        feed=FEED,
        domain="enrollment",
        spec=spec("members.first_name"),
        created_by="a",
        origin_proposal_id=None,
    )
    store.set_mapping_status(feed=FEED, version=1, status="approved")
    v2 = store.create_mapping_version(
        feed=FEED, domain="enrollment", spec=spec("members.first_name"),
        created_by="a", derived_from=1,
    )
    conn.commit()

    assert store.get_mapping_version(FEED, 1).origin == "analyst_created"
    assert v2.origin == "derived from v1"
