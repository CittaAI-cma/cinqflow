"""The pin-out map, asserted.

    "every port has real | dev_standin | mock, all passing ONE contract suite"
    — docs/architecture/INVARIANTS.md, chip discipline

The failure this guards against is the one the invariant names: three
implementations drifting onto three test suites. So the registry is the single
place a port is declared, and these tests assert that the declaration matches
plate 04 — twenty pins, each with a business verb and a ladder of
implementations — and that no adapter can be fitted without a contract suite.
"""

from __future__ import annotations

import pytest

from cinqflow.ports import PIN_GROUPS, PORTS, PortSpec, fitted, port

PLATE_04_PINS = {
    # group A · data plane
    "compute_job",
    "orchestration",
    "storage",
    "control_tables",
    "catalog",
    "sql_query",
    "identity",
    "legacy_readonly",
    # group B · platform
    "metadata_db",
    "queue",
    "cache",
    "authn",
    "secrets",
    "llm",
    "vector",
    "phi_scrub",
    "notification",
    "observability",
    "http_edge",
    "agent_runtime",
}


@pytest.mark.contract
def test_there_are_exactly_twenty_pins() -> None:
    """FIG 04: ports.count == 20. A twenty-first pin needs a plate change."""
    assert set(PORTS) == PLATE_04_PINS
    assert len(PORTS) == 20


@pytest.mark.contract
def test_every_pin_belongs_to_exactly_one_group() -> None:
    grouped = [name for names in PIN_GROUPS.values() for name in names]
    assert sorted(grouped) == sorted(PORTS)
    assert len(grouped) == len(set(grouped)), "a pin in two groups has no owner"


@pytest.mark.contract
def test_every_pin_speaks_a_business_verb() -> None:
    """ "Every touch of the outside world goes through a port, IN BUSINESS LANGUAGE."

    A verb like `execute_sql` describes the adapter; `governed_readonly_query`
    describes what the platform wants. The second survives a socket climb.
    """
    for name, spec in PORTS.items():
        assert spec.verb, name
        assert spec.verb.islower(), f"{name}: a verb, not a class name"


@pytest.mark.contract
def test_every_pin_declares_its_ladder_including_the_target_seat() -> None:
    """A pin with no named target is a pin nobody has thought through."""
    for name, spec in PORTS.items():
        assert spec.mock, f"{name}: rung 0 has no stand-in"
        assert spec.target, f"{name}: no target adapter named — even a seat must be named"


@pytest.mark.contract
def test_a_fitted_adapter_is_always_reachable_by_its_port() -> None:
    """`fitted()` is what the one contract suite iterates over."""
    for name in PORTS:
        for adapter_name, factory in fitted(name).items():
            assert callable(factory), f"{name}/{adapter_name}"


@pytest.mark.contract
def test_the_mock_socket_fits_every_pin() -> None:
    """Rung 0 proves the core's logic in CI in seconds — with nothing running.

    A pin with no mock is a pin that forces a service into the unit lane.
    """
    for name in PORTS:
        assert "mock" in fitted(name), f"{name} has no mock adapter; rung 0 is incomplete"


@pytest.mark.contract
def test_registering_a_duplicate_adapter_is_refused() -> None:
    """Two adapters answering to one name is how a test silently runs twice."""

    @port("storage", "duplicate-probe")
    class _First:  # pragma: no cover - registration is the behaviour under test
        pass

    with pytest.raises(ValueError, match="already fitted"):

        @port("storage", "duplicate-probe")
        class _Second:  # pragma: no cover
            pass

    PORTS["storage"].adapters.pop("duplicate-probe")


@pytest.mark.contract
def test_registering_an_adapter_for_an_unknown_pin_is_refused() -> None:
    """A pin is declared on plate 04 or it does not exist."""
    with pytest.raises(KeyError, match="not a pin"):

        @port("telepathy", "mock")
        class _Nope:  # pragma: no cover
            pass


@pytest.mark.contract
def test_a_port_spec_is_immutable() -> None:
    """The pin-out map is a plate, not a runtime setting."""
    with pytest.raises((AttributeError, TypeError)):
        PORTS["storage"].verb = "something_else"  # type: ignore[misc]


@pytest.mark.contract
def test_port_specs_carry_the_plate_they_come_from() -> None:
    """ "Cite, don't paraphrase." A spec that cannot name its source has drifted."""
    for name, spec in PORTS.items():
        assert isinstance(spec, PortSpec)
        assert spec.plate.startswith("docs/architecture/plates/"), name
