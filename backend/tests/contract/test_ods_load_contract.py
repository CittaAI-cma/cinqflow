"""The ONE contract suite for `OdsLoadPort` — mock and Postgres, every test.

    "Generate surrogate keys ... apply history rules exactly as configured
     per entity (current-only updates vs full history with effective
     dates)."
    — CF-V3-E8-05

Mirrors `test_ddl_render_contract.py`'s own discipline: one suite, every
implementation, so a future rendering (or a second Postgres-shaped adapter)
is certified by these tests rather than trusted.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest

from cinqflow.adapters.local.pg_ods_load import PostgresOdsLoad
from cinqflow.adapters.mock.ods_load import MemOdsLoad
from cinqflow.core.registry.ods_model import HistoryMode, OdsEntity, OdsModel
from cinqflow.core.schema_spec import Column, TypeName
from cinqflow.installer.ods_model import provision_ods_model
from cinqflow.ports.ods_load import OdsLoadPort

pytestmark = pytest.mark.contract

_WIDGETS = OdsEntity(
    name="ContractTestWidgets",
    columns=(
        Column("WidgetId", TypeName.INT64, nullable=False),
        Column("Name", TypeName.STRING),
        Column("BatchId", TypeName.STRING, nullable=False),
    ),
    surrogate_key="WidgetId",
    history_mode=HistoryMode.CURRENT_ONLY,
)
_WIDGET_PARTS = OdsEntity(
    name="ContractTestWidgetParts",
    columns=(
        Column("PartRecordId", TypeName.UUID, nullable=False),
        Column("WidgetId", TypeName.INT64, nullable=False),
        Column("PartSourceId", TypeName.STRING, nullable=False),
        Column("PartName", TypeName.STRING),
        Column("EffectiveStartDate", TypeName.DATE, nullable=False),
        Column("EffectiveEndDate", TypeName.DATE),
        Column("BatchId", TypeName.STRING),
    ),
    surrogate_key="PartRecordId",
    history_mode=HistoryMode.EFFECTIVE_DATED,
    satellite_of="ContractTestWidgets",
    source_key_columns=("PartSourceId",),
)
_MODEL = OdsModel(version=1, entities=(_WIDGETS, _WIDGET_PARTS))


@pytest.fixture(params=["mock", "postgres"])
def ods(request: pytest.FixtureRequest) -> Iterator[OdsLoadPort]:
    if request.param == "mock":
        yield MemOdsLoad()
        return
    plane = request.getfixturevalue("plane")
    provision_ods_model(plane, _MODEL)
    yield PostgresOdsLoad(plane)


# ── surrogate keys ────────────────────────────────────────────────────────


def test_surrogate_keys_are_distinct_and_increasing(ods: OdsLoadPort) -> None:
    first = ods.next_surrogate_key("ContractTestWidgets")
    second = ods.next_surrogate_key("ContractTestWidgets")
    assert second > first


def test_surrogate_keys_are_scoped_per_entity(ods: OdsLoadPort) -> None:
    widget_key = ods.next_surrogate_key("ContractTestWidgets")
    part_key = ods.next_surrogate_key("ContractTestWidgetParts")
    assert widget_key == 1
    assert part_key == 1


# ── current-only (SCD-1) ──────────────────────────────────────────────────


def test_a_never_loaded_key_has_no_existing_row(ods: OdsLoadPort) -> None:
    assert ods.existing_current_row("ContractTestWidgets", "WidgetId", 999) is None


def test_upsert_then_read_round_trips_the_values(ods: OdsLoadPort) -> None:
    ods.upsert_current_row(
        "ContractTestWidgets",
        "WidgetId",
        {"WidgetId": 1, "Name": "Bolt", "BatchId": "b1"},
    )
    found = ods.existing_current_row("ContractTestWidgets", "WidgetId", 1)
    assert found is not None
    assert found["Name"] == "Bolt"
    assert found["BatchId"] == "b1"


def test_a_second_upsert_at_the_same_key_updates_in_place(ods: OdsLoadPort) -> None:
    ods.upsert_current_row(
        "ContractTestWidgets", "WidgetId", {"WidgetId": 2, "Name": "Bolt", "BatchId": "b1"}
    )
    ods.upsert_current_row(
        "ContractTestWidgets", "WidgetId", {"WidgetId": 2, "Name": "Bolt v2", "BatchId": "b2"}
    )
    found = ods.existing_current_row("ContractTestWidgets", "WidgetId", 2)
    assert found is not None
    assert found["Name"] == "Bolt v2"
    assert found["BatchId"] == "b2"


# ── effective-dated (SCD-2) ───────────────────────────────────────────────


def test_a_never_opened_key_has_no_current_open_row(ods: OdsLoadPort) -> None:
    found = ods.current_open_row(
        "ContractTestWidgetParts",
        {"WidgetId": 999, "PartSourceId": "src-1"},
        "EffectiveEndDate",
    )
    assert found is None


def test_an_inserted_row_is_the_current_open_row(ods: OdsLoadPort) -> None:
    ods.insert_effective_dated_row(
        "ContractTestWidgetParts",
        {
            "PartRecordId": "11111111-1111-1111-1111-111111111111",
            "WidgetId": 3,
            "PartSourceId": "src-1",
            "PartName": "Gasket",
            "EffectiveStartDate": "2026-01-01",
            "EffectiveEndDate": None,
        },
    )
    found = ods.current_open_row(
        "ContractTestWidgetParts", {"WidgetId": 3, "PartSourceId": "src-1"}, "EffectiveEndDate"
    )
    assert found is not None
    assert found["PartName"] == "Gasket"


def test_closing_a_row_removes_it_from_the_current_open_read(ods: OdsLoadPort) -> None:
    ods.insert_effective_dated_row(
        "ContractTestWidgetParts",
        {
            "PartRecordId": "22222222-2222-2222-2222-222222222222",
            "WidgetId": 4,
            "PartSourceId": "src-1",
            "PartName": "Gasket",
            "EffectiveStartDate": "2026-01-01",
            "EffectiveEndDate": None,
        },
    )
    ods.close_open_row(
        "ContractTestWidgetParts",
        {"WidgetId": 4, "PartSourceId": "src-1"},
        "EffectiveEndDate",
        date(2026, 6, 1),
    )
    found = ods.current_open_row(
        "ContractTestWidgetParts", {"WidgetId": 4, "PartSourceId": "src-1"}, "EffectiveEndDate"
    )
    assert found is None


def test_close_and_open_leaves_exactly_the_new_row_current(ods: OdsLoadPort) -> None:
    """ "An address change closes the old row and opens a new one" —
    both rows persist; only the NEW one reads back as current."""
    match = {"WidgetId": 5, "PartSourceId": "src-1"}
    ods.insert_effective_dated_row(
        "ContractTestWidgetParts",
        {
            "PartRecordId": "33333333-3333-3333-3333-333333333333",
            "WidgetId": 5,
            "PartSourceId": "src-1",
            "PartName": "Old Gasket",
            "EffectiveStartDate": "2026-01-01",
            "EffectiveEndDate": None,
        },
    )
    ods.close_open_row("ContractTestWidgetParts", match, "EffectiveEndDate", date(2026, 6, 1))
    ods.insert_effective_dated_row(
        "ContractTestWidgetParts",
        {
            "PartRecordId": "44444444-4444-4444-4444-444444444444",
            "WidgetId": 5,
            "PartSourceId": "src-1",
            "PartName": "New Gasket",
            "EffectiveStartDate": "2026-06-01",
            "EffectiveEndDate": None,
        },
    )
    found = ods.current_open_row("ContractTestWidgetParts", match, "EffectiveEndDate")
    assert found is not None
    assert found["PartName"] == "New Gasket"


# ── count_rows / orphans / count_orphans · CF-V3-E10-03 ──────────────────


def test_count_rows_reflects_what_was_loaded(ods: OdsLoadPort) -> None:
    ods.upsert_current_row(
        "ContractTestWidgets", "WidgetId", {"WidgetId": 10, "Name": "Bolt", "BatchId": "b1"}
    )
    ods.upsert_current_row(
        "ContractTestWidgets", "WidgetId", {"WidgetId": 11, "Name": "Nut", "BatchId": "b1"}
    )
    assert ods.count_rows("ContractTestWidgets") >= 2


def test_count_rows_is_scoped_to_a_batch_when_given(ods: OdsLoadPort) -> None:
    ods.upsert_current_row(
        "ContractTestWidgets", "WidgetId", {"WidgetId": 20, "Name": "Bolt", "BatchId": "scoped-1"}
    )
    assert ods.count_rows("ContractTestWidgets", batch_id="scoped-1") == 1
    assert ods.count_rows("ContractTestWidgets", batch_id="no-such-batch") == 0


def test_no_orphans_when_every_child_references_a_real_parent(ods: OdsLoadPort) -> None:
    ods.upsert_current_row(
        "ContractTestWidgets", "WidgetId", {"WidgetId": 30, "Name": "Bolt", "BatchId": "b1"}
    )
    ods.insert_effective_dated_row(
        "ContractTestWidgetParts",
        {
            "PartRecordId": "55555555-5555-5555-5555-555555555555",
            "WidgetId": 30,
            "PartSourceId": "src-ok",
            "PartName": "Gasket",
            "EffectiveStartDate": "2026-01-01",
            "EffectiveEndDate": None,
            "BatchId": "orphan-scope-1",
        },
    )
    assert (
        ods.count_orphans(
            "ContractTestWidgetParts",
            "WidgetId",
            "ContractTestWidgets",
            "WidgetId",
            batch_id="orphan-scope-1",
        )
        == 0
    )


def test_orphans_finds_a_child_row_with_no_matching_parent(ods: OdsLoadPort) -> None:
    ods.insert_effective_dated_row(
        "ContractTestWidgetParts",
        {
            "PartRecordId": "66666666-6666-6666-6666-666666666666",
            "WidgetId": 999999,
            "PartSourceId": "src-orphan",
            "PartName": "Dangling Gasket",
            "EffectiveStartDate": "2026-01-01",
            "EffectiveEndDate": None,
            "BatchId": "orphan-scope-2",
        },
    )
    found = ods.orphans(
        "ContractTestWidgetParts",
        "WidgetId",
        "ContractTestWidgets",
        "WidgetId",
        batch_id="orphan-scope-2",
    )
    assert len(found) == 1
    assert found[0]["WidgetId"] == 999999
    assert (
        ods.count_orphans(
            "ContractTestWidgetParts",
            "WidgetId",
            "ContractTestWidgets",
            "WidgetId",
            batch_id="orphan-scope-2",
        )
        == 1
    )


def test_orphans_are_capped_by_limit_but_count_orphans_reports_the_true_total(
    ods: OdsLoadPort,
) -> None:
    for index in range(3):
        ods.insert_effective_dated_row(
            "ContractTestWidgetParts",
            {
                "PartRecordId": f"7777777{index}-7777-7777-7777-777777777777",
                "WidgetId": 888880 + index,
                "PartSourceId": f"src-cap-{index}",
                "PartName": "Dangling Gasket",
                "EffectiveStartDate": "2026-01-01",
                "EffectiveEndDate": None,
                "BatchId": "orphan-scope-3",
            },
        )
    capped = ods.orphans(
        "ContractTestWidgetParts",
        "WidgetId",
        "ContractTestWidgets",
        "WidgetId",
        batch_id="orphan-scope-3",
        limit=2,
    )
    assert len(capped) == 2
    assert (
        ods.count_orphans(
            "ContractTestWidgetParts",
            "WidgetId",
            "ContractTestWidgets",
            "WidgetId",
            batch_id="orphan-scope-3",
        )
        == 3
    )


# ── column_values · CF-V3-E13-02 ─────────────────────────────────────────


def test_column_values_returns_every_distinct_value(ods: OdsLoadPort) -> None:
    ods.upsert_current_row(
        "ContractTestWidgets", "WidgetId", {"WidgetId": 40, "Name": "Bolt", "BatchId": "cv-1"}
    )
    ods.upsert_current_row(
        "ContractTestWidgets", "WidgetId", {"WidgetId": 41, "Name": "Nut", "BatchId": "cv-1"}
    )
    values = ods.column_values("ContractTestWidgets", "WidgetId")
    assert {40, 41} <= set(values)


def test_column_values_is_scoped_to_a_batch_when_given(ods: OdsLoadPort) -> None:
    ods.upsert_current_row(
        "ContractTestWidgets", "WidgetId", {"WidgetId": 50, "Name": "Bolt", "BatchId": "cv-scope-1"}
    )
    ods.upsert_current_row(
        "ContractTestWidgets", "WidgetId", {"WidgetId": 51, "Name": "Nut", "BatchId": "cv-scope-2"}
    )
    scoped = ods.column_values("ContractTestWidgets", "WidgetId", batch_id="cv-scope-1")
    assert scoped == (50,)


def test_column_values_for_an_empty_batch_is_empty(ods: OdsLoadPort) -> None:
    assert ods.column_values("ContractTestWidgets", "WidgetId", batch_id="no-such-batch") == ()
