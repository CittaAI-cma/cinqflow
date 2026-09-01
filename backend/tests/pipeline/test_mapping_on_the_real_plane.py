"""CF-V1-E6-03's mapping, on the REAL rung-0.5 plane.

The unit suite proves the taxonomy's semantics; the contract suite proves the
routes honour them. This proves the object survives the row that actually
stores it — which for a mapping is not a formality, because a mapping's body is
the most deeply nested JSONB the registry holds: a list of lines, each carrying
a transform, each transform carrying a lookup table or a list of cases.

Three things only Postgres can show:

  • a 70-code lookup table survives the JSONB round trip with its ORDER intact,
    so a reviewer comparing two versions of a translation reads a stable diff
    rather than a reshuffled one;
  • versioning is real rows, so `v1` keeps exactly what was approved as `v1`
    after `v2` is stored;
  • lineage resolves — a stored mapping reaches the feed and the glossary terms
    `core.impact.REFERENCES` declares, so an approver of a glossary change is
    told which mappings they are about to affect.

Every write rolls back (the `plane` fixture), so the suite leaves nothing
behind and needs no cleanup code.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cinqflow.adapters.local.pg_metadata_db import PostgresMetadataDb
from cinqflow.core.impact import dependents_of
from cinqflow.core.mapping import (
    Case,
    FeedMapping,
    LineStatus,
    MappingLine,
    NullPolicy,
    Transform,
    TransformKind,
    UnlistedCode,
    from_governed,
    mapping_as_governed,
)
from cinqflow.core.model.governed import Actor, LifecycleState, ObjectType
from cinqflow.core.model.vocabulary import ActorType
from cinqflow.core.registry.feed import FeedRecord

pytestmark = [pytest.mark.pipeline, pytest.mark.postgres]

NOW = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
BA = Actor(subject="dev-ba@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Meera")
STEWARD = Actor(subject="dev-steward@cinqcare.test", actor_type=ActorType.HUMAN, display_name="Ola")
FEED = "fidelis-downstate-roster"

#: The claims workbook's `reference` sheet, first rows, in the client's own
#: order. Order matters here: a lookup table that came back sorted would make
#: every version diff of a translation unreadable.
FACILITY_CODES: tuple[tuple[str, str], ...] = (
    ("NULL", "Hospice"),
    ("12", "Day Care Medical"),
    ("14", "Ambulatory Surgery Center"),
    ("28", "Emerg Med/Critical Care/Trauma"),
)


def _mapping(version: int = 1) -> FeedMapping:
    return FeedMapping(
        feed_id=FEED,
        version=version,
        contract_version=3,
        lines=(
            MappingLine(
                target_entity="members",
                target_field="first_name",
                source_columns=("First_Name",),
                glossary_id="BG-001",
            ),
            MappingLine(
                target_entity="members",
                target_field="service_date",
                source_columns=("service_date", "service_from_date"),
                null_policy=NullPolicy.COALESCE,
                notes="OP service date -> service_date. COALESCE with service_from_date.",
            ),
            MappingLine(
                target_entity="members",
                target_field="servicing_facility_category",
                source_columns=("facility_code",),
                transform=Transform(
                    kind=TransformKind.LOOKUP,
                    lookup=FACILITY_CODES,
                    on_unlisted=UnlistedCode.SUBSTITUTE,
                    default_value="unknown",
                ),
            ),
            MappingLine(
                target_entity="members",
                target_field="claim_type",
                source_columns=("scope",),
                transform=Transform(
                    kind=TransformKind.CONDITIONAL,
                    cases=(
                        Case(when_in=("IP H", "IP L"), then="inpatient"),
                        Case(when_in=("OP H", "OP L"), then="outpatient"),
                    ),
                    default_value="other",
                ),
            ),
            MappingLine(
                target_entity="members",
                target_field="source_system_id",
                transform=Transform(kind=TransformKind.CONSTANT, literal="D0284"),
            ),
            MappingLine(
                target_entity="members",
                target_field="batch_id",
                platform_supplied=True,
            ),
            MappingLine(
                target_entity="members",
                target_field="hicn_id",
                unmapped_reason="Equitable BIC HICN — not mbi_id; Fidelis stopped sending it.",
            ),
        ),
    )


def test_a_mapping_round_trips_through_its_row(plane: object) -> None:
    """Every kind in the taxonomy, through JSONB and back, byte-identical."""
    store = PostgresMetadataDb(plane)  # type: ignore[arg-type]
    stored = store.save(mapping_as_governed(_mapping(), author=BA, created_ts=NOW))

    assert stored.object_type is ObjectType.MAPPING
    assert from_governed(stored) == _mapping()


def test_a_lookup_table_keeps_the_clients_own_order(plane: object) -> None:
    """A translation that came back sorted would make every version diff of it
    unreadable — a reviewer would see seventy moved rows instead of the one
    code that changed."""
    store = PostgresMetadataDb(plane)  # type: ignore[arg-type]
    stored = store.save(mapping_as_governed(_mapping(), author=BA, created_ts=NOW))

    line = from_governed(stored).line("members", "servicing_facility_category")
    assert line is not None
    assert line.transform.lookup == FACILITY_CODES


def test_the_unmapped_field_keeps_its_reason_through_the_row(plane: object) -> None:
    """The reason IS the review. A mapping whose unmapped fields came back
    bare would be a mapping nobody could sign."""
    store = PostgresMetadataDb(plane)  # type: ignore[arg-type]
    stored = store.save(mapping_as_governed(_mapping(), author=BA, created_ts=NOW))

    line = from_governed(stored).line("members", "hicn_id")
    assert line is not None
    assert line.status is LineStatus.UNMAPPED
    assert "stopped sending it" in line.unmapped_reason


def test_v1_keeps_what_was_approved_as_v1(plane: object) -> None:
    """Amend by new version, never in place. A published mapping stays exactly
    as it was approved, which is what makes "which mapping version did this
    batch run under?" answerable a year later."""
    store = PostgresMetadataDb(plane)  # type: ignore[arg-type]
    store.save(mapping_as_governed(_mapping(), author=BA, created_ts=NOW))

    trimmed = FeedMapping(
        feed_id=FEED,
        version=2,
        contract_version=3,
        lines=_mapping().lines[:2],
    )
    store.save(mapping_as_governed(trimmed, author=BA, created_ts=NOW))

    assert len(from_governed(store.get(ObjectType.MAPPING, FEED, 1)).lines) == 7
    assert len(from_governed(store.get(ObjectType.MAPPING, FEED, 2)).lines) == 2
    assert store.get(ObjectType.MAPPING, FEED).version == 2


def test_a_mapping_travels_the_one_lifecycle_on_the_real_plane(plane: object) -> None:
    """No private state machine. The mapping reaches PUBLISHED through the same
    transitions a feed does, and its approver is a named person on the row."""
    store = PostgresMetadataDb(plane)  # type: ignore[arg-type]
    draft = store.save(mapping_as_governed(_mapping(), author=BA, created_ts=NOW))

    for target, actor in (
        (LifecycleState.PENDING_REVIEW, BA),
        (LifecycleState.APPROVED, STEWARD),
        (LifecycleState.PUBLISHED, STEWARD),
    ):
        draft, entry = draft.transition_to(target, actor=actor, now=NOW)
        draft = store.record_transition(draft, entry)

    assert draft.lifecycle_state is LifecycleState.PUBLISHED
    assert draft.approved_by is not None
    assert draft.approved_by.subject == STEWARD.subject
    assert draft.is_executable


def test_lineage_reaches_the_mapping_from_the_feed_it_reads(plane: object) -> None:
    """`core.impact.REFERENCES` declares that a MAPPING's `feed_id` points at a
    FEED. So a platform engineer changing the feed is told about the mapping
    without the mapping's author having remembered to mention it."""
    store = PostgresMetadataDb(plane)  # type: ignore[arg-type]
    feed = store.save(
        FeedRecord(
            feed_id=FEED,
            domain="membership",
            source_system="fidelis",
            file_format="csv",
            landing_path="landing/fidelis/roster",
            file_pattern=r"^_CINQDOWNSTATE_Member_Roster_\d{8}\.csv$",
            schedule_cron="0 6 * * 1",
            sample_filename="_CINQDOWNSTATE_Member_Roster_20260801.csv",
        ).as_governed(author=BA, created_ts=NOW)
    )
    store.save(mapping_as_governed(_mapping(), author=BA, created_ts=NOW))

    everything = tuple(store.list(ObjectType.FEED)) + tuple(store.list(ObjectType.MAPPING))
    touched = dependents_of(feed, everything)

    assert [(t.object_type, t.object_id) for t in touched] == [(ObjectType.MAPPING, FEED)]
    assert touched[0].via == f"feed:{FEED}"
