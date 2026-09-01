"""CF-V0-E16-01 · CF-V1-E5-03 · E6-02 · E7-01 · E16-04/05 — the plane, fitted.

    "the intelligence is real from the first commit, swapping to the client's
     tenant is a profile line plus a re-baseline"
    — CF-V0-E16-01

The gap these tests close is not a bug in an agent. It is that four Wave-1
capabilities were built, routed and unit-tested, and then constructed by NO
server — `POST /detect-phi`, `POST /suggest-mapping`, `POST /author-rules` and
every publish hook's embed step answered 503 or degraded silently on both
`api/dev.py` and `api/local.py`. An agent nothing constructs is an agent the
platform does not have, so what is asserted here is CONSTRUCTION and PROFILE
FIDELITY, not model quality — that is Lane 3's job.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cinqflow.adapters.mock.llm import ScriptedLlm
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.adapters.mock.secrets import MemSecrets
from cinqflow.adapters.mock.vector import ListVector
from cinqflow.adapters.openai_compatible.llm import OpenAiCompatibleLlm
from cinqflow.api.dev import build
from cinqflow.core.model.profile import Profile, ProfileError
from cinqflow.core.model.vocabulary import Mode
from cinqflow.intelligence.plane import IntelligencePlane, phi_scrub_from, vector_from

pytestmark = pytest.mark.contract


def _profile(**pins: object) -> Profile:
    base: dict[str, object] = {
        "llm": {
            "adapter": "scripted",
            "routing": {"small": "s", "large": "l"},
            "budgets": {"per_run_usd": 0.25, "per_agent_per_day_usd": 5.0},
            "prices": {"s": [0.1, 0.2], "l": [1.0, 2.0]},
        },
        "phi_scrub": {"adapter": "mock"},
        "vector": {"adapter": "mock"},
    }
    base.update(pins)
    return Profile(source="test.yaml", rung=0.0, socket="mock", mode=Mode.FULL, pins=base)


# ── the pins come from the profile, and nowhere else ─────────────────────────


def test_the_llm_adapter_is_whatever_the_profile_names() -> None:
    plane = IntelligencePlane.from_profile(_profile(), MemSecrets())
    assert isinstance(plane.llm, ScriptedLlm)

    real = _profile(
        llm={
            "adapter": "openai-compatible",
            "endpoint": "https://example.invalid/v1",
            "api_key": "k",
            "routing": {"small": "s", "large": "l"},
            "budgets": {"per_run_usd": 0.25, "per_agent_per_day_usd": 5.0},
            "prices": {"s": [0.1, 0.2], "l": [1.0, 2.0]},
        }
    )
    assert isinstance(IntelligencePlane.from_profile(real, MemSecrets()).llm, OpenAiCompatibleLlm)


def test_the_scrubber_is_a_pin_not_a_default() -> None:
    assert isinstance(phi_scrub_from(_profile()), PatternPhiScrub)


def test_a_scrubber_the_profile_does_not_name_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ProfileError, match="phi_scrub"):
        phi_scrub_from(_profile(phi_scrub={"adapter": "invented"}))


def test_an_unfitted_vector_pin_is_none_not_a_throwaway_store() -> None:
    """A `ListVector` fitted where the profile said nothing would accept
    embeddings into a store that vanishes on restart — which reads as a
    working knowledge plane and is not one."""
    assert vector_from(_profile(vector={"adapter": "none"})) is None
    assert isinstance(vector_from(_profile()), ListVector)


def test_pgvector_without_a_connection_refuses_rather_than_inventing_one() -> None:
    with pytest.raises(ProfileError, match="connection"):
        vector_from(_profile(vector={"adapter": "pgvector"}))


# ── every agent the platform claims to have, constructible ───────────────────


def test_the_plane_constructs_every_wave_0_and_wave_1_agent() -> None:
    from cinqflow.adapters.mock.control_tables import MemStoreControlTables
    from cinqflow.adapters.mock.metadata_db import MemMetadataDb
    from cinqflow.core.model.identity import Principal

    plane = IntelligencePlane.from_profile(_profile(), MemSecrets())
    store, control, secrets = MemMetadataDb(), MemStoreControlTables(), MemSecrets()
    caller = Principal(subject="ba@x", display_name="BA")

    assert plane.pipeline_insight(caller, control, store, secrets) is not None
    assert plane.schema_inference(store, secrets) is not None
    assert plane.phi_detection(store, secrets) is not None
    assert plane.mapping_suggestion(store, secrets) is not None
    assert plane.rule_authoring(store, secrets) is not None
    assert plane.merge_evidence(store, secrets) is not None
    assert plane.fingerprint_match(control, store, secrets) is not None
    assert plane.alert_enrichment(control, store, secrets) is not None


def test_every_proposing_agent_grounds_through_the_one_retrieval_service() -> None:
    """CF-V1-E16-05's don't: "Let any agent read the vector store directly,
    around the service." """
    from cinqflow.adapters.mock.metadata_db import MemMetadataDb

    plane = IntelligencePlane.from_profile(_profile(), MemSecrets())
    store, secrets = MemMetadataDb(), MemSecrets()
    for agent in (
        plane.schema_inference(store, secrets),
        plane.mapping_suggestion(store, secrets),
        plane.rule_authoring(store, secrets),
    ):
        assert agent.retrieval is not None, f"{type(agent).__name__} grounds privately"
        assert agent.retrieval.vector is plane.vector, "a second store is a second truth"


# ── and the routes that used to answer 503 ───────────────────────────────────


@pytest.fixture
def client(tmp_path: object) -> TestClient:
    return TestClient(build(str(tmp_path)))


HEADERS = {"authorization": "Bearer dev-ba@cinqcare.test"}


def test_the_dev_server_fits_the_four_capabilities_no_server_used_to_fit(
    client: TestClient,
) -> None:
    """Not a quality assertion — a WIRING one. Each of these returned 503
    ("no LLM pin is fitted on this deployment") on every deployment that
    existed, because no `build()` passed the factory."""
    app = client.app
    assert app.state.phi_detection_factory is not None
    assert app.state.mapping_suggestion_factory is not None
    assert app.state.rule_authoring_factory is not None
    assert app.state.knowledge_ingest_factory is not None


def test_suggest_mapping_answers_for_real_rather_than_503(client: TestClient) -> None:
    feed_id = client.get("/api/feeds", headers=HEADERS).json()[0]["feed_id"]
    response = client.post(f"/api/feeds/{feed_id}/suggest-mapping", json={}, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["agent"] == "mapping-suggestion"
