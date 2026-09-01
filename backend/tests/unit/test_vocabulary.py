"""The programme's fixed vocabulary, asserted as data.

Vocabulary is not a style preference here — it is the mechanism that stops the
platform growing per-screen dialects, which is the failure the seven status
words exist to prevent:

    "Avoid 'Perfect' and avoid creating different terms on every screen."
    — clientdata/CINQFlow_Final_Navigation_and_Screen_Blueprint

These tests pin every closed set the architecture names. A set that grows
without a plate changing is a drift bug, and it fails here first.
"""

from __future__ import annotations

import pytest

from cinqflow.core.model.vocabulary import (
    BatchState,
    ErrorCategory,
    FileState,
    Gate,
    LandingFolder,
    Layer,
    Mode,
    RiskClass,
    StatusWord,
    TestLane,
)


@pytest.mark.unit
def test_users_see_exactly_seven_status_words() -> None:
    """ "Users see seven words only." — memory/05-ground-truth/00-control-plane.md"""
    assert [s.value for s in StatusWord] == [
        "Expected",
        "Received",
        "Processing",
        "Completed",
        "Needs Review",
        "Needs Attention",
        "Missing",
    ]


@pytest.mark.unit
@pytest.mark.parametrize("banned", ["Perfect", "OK", "Success", "Failed", "Error", "Warning"])
def test_no_synonym_may_enter_the_status_lexicon(banned: str) -> None:
    """No synonyms, no per-screen dialects, no 'Perfect'."""
    assert banned not in {s.value for s in StatusWord}


@pytest.mark.unit
def test_the_medallion_spine_is_six_stages_in_order() -> None:
    """FIG 06: stages: [landing, bronze, silver_raw, identity, silver_ods, gold]"""
    assert [layer.value for layer in Layer] == [
        "landing",
        "bronze",
        "silver_raw",
        "identity",
        "silver_ods",
        "gold",
    ]


@pytest.mark.unit
def test_every_layer_transition_has_exactly_one_gate() -> None:
    """ "Data cannot advance a layer until that layer's gate passes." — FIG 06"""
    assert [g.value for g in Gate] == ["G1", "G2", "G3", "G4", "G5"]
    assert len(list(Gate)) == len(list(Layer)) - 1
    for gate in Gate:
        assert gate.between[1] == Layer.after(gate.between[0])


@pytest.mark.unit
def test_gates_name_the_transition_they_guard() -> None:
    assert Gate.G1.between == (Layer.LANDING, Layer.BRONZE)
    assert Gate.G3.between == (Layer.SILVER_RAW, Layer.IDENTITY)
    assert Gate.G5.between == (Layer.SILVER_ODS, Layer.GOLD)


@pytest.mark.unit
def test_error_categories_are_a_fixed_set() -> None:
    """A category outside this set is an unattributed failure by another name."""
    assert {e.value for e in ErrorCategory} == {
        "FILE_ERROR",
        "SCHEMA_ERROR",
        "VALIDATION_ERROR",
        "TRANSFORMATION_ERROR",
        "INTEGRATION_ERROR",
        "SYSTEM_ERROR",
    }


@pytest.mark.unit
def test_batch_and_file_state_machines_match_the_control_plane() -> None:
    assert {b.value for b in BatchState} == {
        "RECEIVED",
        "VALIDATED",
        "IN_PROGRESS",
        "COMPLETED",
        "FAILED",
        "RESTARTED",
        "WAITING_DEPENDENCY",
        "BLOCKED",
    }
    assert {f.value for f in FileState} == {"RECEIVED", "ACCEPTED", "REJECTED", "PROCESSED"}


@pytest.mark.unit
def test_unexpected_files_are_parked_never_ignored() -> None:
    """ "Every arriving file is registered — including unexpected ones." — FIG 09"""
    assert LandingFolder.PARKED in set(LandingFolder)


@pytest.mark.unit
def test_risk_class_gates_capability_and_r4_is_never_automatable() -> None:
    """ "R4 is human-always, never automated, not configurable." — INVARIANTS.md"""
    assert [r.name for r in RiskClass] == ["R0", "R1", "R2", "R3", "R4"]
    assert RiskClass.R0.always_allowed is True
    assert RiskClass.R4.automatable is False
    assert RiskClass.R4.configurable is False
    # And no other class may claim R4's exemption by accident.
    assert [r.name for r in RiskClass if not r.automatable] == ["R4"]


@pytest.mark.unit
def test_confidence_can_never_raise_a_risk_class() -> None:
    """ "risk class gates capability; confidence only routes WITHIN the class"."""
    for cls in RiskClass:
        for confidence in (0.0, 0.5, 0.99, 1.0):
            assert cls.at_confidence(confidence) is cls


@pytest.mark.unit
def test_the_three_modes_are_a_profile_field() -> None:
    """ "partial permission is a mode, not a failure" — INVARIANTS.md"""
    assert {m.value for m in Mode} == {"full", "propose_only", "observe_only"}


@pytest.mark.unit
def test_three_test_lanes_and_only_lane_three_may_claim_quality() -> None:
    assert [lane.value for lane in TestLane] == [1, 2, 3]
    assert [lane for lane in TestLane if lane.may_claim_quality] == [TestLane.REAL]
    assert TestLane.MOCK.holds_credentials is False
    assert TestLane.REPLAY.holds_credentials is False
