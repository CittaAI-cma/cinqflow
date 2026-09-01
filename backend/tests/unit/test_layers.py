"""core/layers — the spine, and the masking that lets a screen show rows.

Pure. No connection, no adapter, no server. What is asserted here is the two
decisions the module makes: WHAT a layer is, and WHAT a viewer may see of a
value. Everything about going and fetching it belongs to the adapter suites.
"""

from __future__ import annotations

import json

import pytest

from cinqflow.core.layers import (
    MASKED,
    PHI_REASON,
    UNCLASSIFIED_REASON,
    LayerStatus,
    built_layers,
    mask_cell,
    mask_row,
    phi_columns,
    spec_of,
    spine,
    table_of,
    tables_of,
)
from cinqflow.core.model.vocabulary import Gate, Layer
from cinqflow.core.schema_spec import Column, Table, TypeName

pytestmark = [pytest.mark.unit, pytest.mark.lane1]


# ── the spine ────────────────────────────────────────────────────────────────
def test_the_spine_covers_the_closed_layer_set_exactly() -> None:
    """A seventh layer cannot be introduced by forgetting one here.

    `Layer` is the closed set the architecture names; SPINE is the join of that
    set to the plane. If the two ever disagree, a layer is either invisible on
    the screen or invented by it — and this assertion is the only thing that
    makes the difference impossible rather than unlikely.
    """
    assert tuple(s.layer for s in spine()) == tuple(Layer)


def test_the_spine_is_in_promotion_order() -> None:
    """Order is load-bearing: the screen renders it as a numbered sequence, and
    "data cannot skip a layer" is a claim about this order."""
    for current, following in zip(spine(), spine()[1:], strict=False):
        assert Layer.after(current.layer) is following.layer


def test_every_layer_but_landing_names_the_gate_that_guards_it() -> None:
    """Landing has no entry gate, and that is a fact about the architecture
    rather than a gap: arrival is not a promotion."""
    landing, *promoted = spine()
    assert landing.entry_gate is None
    assert [s.entry_gate for s in promoted] == list(Gate)
    for spec in promoted:
        assert spec.entry_gate is not None
        assert spec.entry_gate.between[1] is spec.layer


def test_three_layers_are_built_and_three_are_not() -> None:
    """The screen's whole claim, asserted. If a later wave builds Identity and
    nobody updates SPINE, this fails — which is the point: the honest map has
    to be maintained, and the alternative is a screen that quietly lies."""
    assert [s.layer.value for s in built_layers()] == ["landing", "bronze", "silver_raw"]


def test_an_unbuilt_layer_says_why_and_a_built_one_does_not_guess() -> None:
    """`absence_reason` is required for the two statuses that need one and
    EMPTY for BUILT. A built layer's emptiness is a data question, and
    answering it in the spec would be a guess printed as a fact."""
    for spec in spine():
        if spec.status is LayerStatus.BUILT:
            assert spec.absence_reason == ""
        else:
            assert spec.absence_reason, spec.layer
            assert spec.wave > 0, spec.layer


def test_provisioned_empty_and_not_built_are_different_answers() -> None:
    """`silver_ods` HAS a schema and no tables, on purpose; `gold` has no
    schema at all. Collapsing them would tell an operator the same thing about
    a deliberate contract and an absent one."""
    ods = spec_of(Layer.SILVER_ODS)
    gold = spec_of(Layer.GOLD)
    assert ods.status is LayerStatus.PROVISIONED_EMPTY
    assert ods.exists_on_plane and tables_of(ods) == ()
    assert gold.status is LayerStatus.NOT_BUILT
    assert not gold.exists_on_plane


def test_a_layer_name_that_is_not_one_raises_with_the_six_that_are() -> None:
    """The message is the API's 404 body. A caller who mistyped is told what
    the options are, rather than told "not found"."""
    with pytest.raises(KeyError) as raised:
        spec_of("sliver_raw")
    for name in (s.layer.value for s in spine()):
        assert name in str(raised.value)


def test_spec_of_accepts_the_enum_and_the_wire_name() -> None:
    assert spec_of(Layer.BRONZE) is spec_of("bronze")


def test_tables_come_from_the_contract_not_from_a_second_list() -> None:
    """`schema_spec` is the one declaration. A screen reading its own list
    would be the hand-maintained data dictionary this platform exists to
    retire, embedded in the tool that replaced it."""
    bronze = spec_of(Layer.BRONZE)
    assert [t.name for t in tables_of(bronze)] == ["members_raw"]
    assert table_of(bronze, "members_raw").append_only is True
    with pytest.raises(KeyError):
        table_of(bronze, "members_cooked")


# ── masking ──────────────────────────────────────────────────────────────────
_PHI = Column("last_name", TypeName.STRING, is_phi=True)
_CLEAR = Column("line_of_business", TypeName.STRING)
_PHI_DATE = Column("date_of_birth", TypeName.DATE, is_phi=True)
_RAW = Column("raw_row", TypeName.JSON, is_phi=True)


def test_a_flagged_column_is_masked_and_an_unflagged_one_is_not() -> None:
    masked = mask_cell(_PHI, "Okafor")
    assert masked.masked and masked.value == MASKED and masked.reason == PHI_REASON
    clear = mask_cell(_CLEAR, "MEDICAID")
    assert not clear.masked and clear.value == "MEDICAID"


def test_the_flag_decides_never_the_column_name() -> None:
    """A column called `last_name` with no flag is NOT masked, and one called
    `x` with the flag IS. Driving masking from names would make a rename a
    disclosure; the flag is a contract term whose change needs approval."""
    assert not mask_cell(Column("last_name", TypeName.STRING), "Okafor").masked
    assert mask_cell(Column("x", TypeName.STRING, is_phi=True), "Okafor").masked


def test_the_masked_rendering_carries_no_shape() -> None:
    """Not the length, not an initial, not the year.

    An earlier rendering kept an initial and a length hint because it read
    better on screen, and that version let a reader tell two members apart and
    re-identify a known one from a roster they already had.
    """
    short = mask_cell(_PHI, "Vo")
    long = mask_cell(_PHI, "Featherstonehaugh")
    assert short.value == long.value
    assert "V" not in str(short.value) and "F" not in str(long.value)
    assert mask_cell(_PHI_DATE, "1936-02-01").value == MASKED


def test_a_null_is_not_masked_even_in_a_flagged_column() -> None:
    """A real decision, not an oversight. "Absent" is not protected
    information, and hiding it would make a completeness screen unable to show
    that a required identifier was missing — the defect Bronze exists to keep
    visible. Every null also looks like every other null."""
    cell = mask_cell(_PHI, None)
    assert cell.value is None and not cell.masked


def test_a_flagged_json_column_keeps_its_keys_and_loses_every_value() -> None:
    """Bronze's whole source record is one flagged column. Masking it whole
    would hide the source's COLUMN NAMES, which is what an engineer opening
    Bronze is looking for; the values are the member."""
    cell = mask_cell(_RAW, json.dumps({"Last_Name": "Okafor", "MemberID": "M0003"}))
    assert cell.masked
    assert cell.value == f"{{Last_Name: {MASKED}, MemberID: {MASKED}}}"
    assert "Okafor" not in str(cell.value) and "M0003" not in str(cell.value)


def test_a_json_column_that_is_not_a_mapping_masks_whole() -> None:
    """No keys to preserve, so there is nothing to trade legibility for."""
    assert mask_cell(_RAW, "[1, 2, 3]").value == MASKED
    assert mask_cell(_RAW, "not json at all").value == MASKED
    assert mask_cell(_RAW, "{}").value == "{}"


def test_a_json_column_masks_a_dict_as_well_as_a_string() -> None:
    """psycopg hands back `jsonb` already parsed; a replay fixture hands back
    the string. Both arrive at this function and both must mask."""
    assert mask_cell(_RAW, {"MemberID": "M0001"}).value == f"{{MemberID: {MASKED}}}"


_TABLE = Table(
    name="members",
    columns=(
        Column("member_row_id", TypeName.UUID, nullable=False),
        _PHI,
        _CLEAR,
        Column("is_active", TypeName.BOOL, nullable=False),
    ),
    primary_key=("member_row_id",),
)


def test_a_row_is_decided_cell_by_cell_from_its_own_column() -> None:
    masked = mask_row(
        _TABLE,
        {
            "member_row_id": "abc",
            "last_name": "Okafor",
            "line_of_business": "MEDICAID",
            "is_active": True,
        },
    )
    assert masked["member_row_id"].value == "abc"
    assert masked["last_name"].masked
    assert masked["line_of_business"].value == "MEDICAID"
    # Rendered once, here, rather than in two adapters and a browser.
    assert masked["is_active"].value == "true"


def test_a_column_absent_from_the_contract_is_masked() -> None:
    """Unclassified is masked, never public. A plane that has drifted ahead of
    the spec carries columns nothing has classified, and defaulting those to
    visible would make a forgotten contract update a disclosure."""
    masked = mask_row(_TABLE, {"member_row_id": "abc", "ssn_we_never_declared": "078-05-1120"})
    cell = masked["ssn_we_never_declared"]
    assert cell.masked and cell.value == MASKED and cell.reason == UNCLASSIFIED_REASON
    assert "078" not in str(cell.value)


def test_an_unclassified_null_is_still_not_masked() -> None:
    """Consistent with the flagged case — there is nothing to protect, and
    rendering it as bullets would claim a value exists."""
    masked = mask_row(_TABLE, {"member_row_id": "abc", "undeclared": None})
    assert masked["undeclared"].value is None and not masked["undeclared"].masked


def test_the_masked_value_object_carries_no_original() -> None:
    """A serializer cannot leak what a renderer hid if the field is not there.

    Asserted on the dataclass itself rather than on an instance: a future
    convenience field called `original` or `raw` would pass every other test in
    this file and defeat the entire design.
    """
    fields = set(mask_cell(_PHI, "Okafor").__dataclass_fields__)
    assert fields == {"value", "masked", "reason"}


def test_phi_columns_lists_what_a_screen_is_hiding() -> None:
    assert phi_columns(_TABLE) == ("last_name",)
