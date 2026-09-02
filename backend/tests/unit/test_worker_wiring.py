"""`workers.wiring.build` — one iteration's real adapters, and the profile
check that stands in front of them.

`build()` never touches the database at construction time — every Postgres
adapter here stores a connection and does nothing else until a method is
called — so this file proves the WIRING (right types, both topics
registered, a mismatched profile refused loudly) with a placeholder
connection that is never queried, and an all-mock intelligence profile so
no real LLM/PHI-scrub endpoint is needed either.
"""

from __future__ import annotations

import pytest

from cinqflow.adapters.local.localfs_storage import LocalFsStorage
from cinqflow.adapters.local.pg_control_tables import PostgresControlTables
from cinqflow.adapters.local.pg_metadata_db import PostgresMetadataDb
from cinqflow.adapters.mock.secrets import MemSecrets
from cinqflow.core.model.profile import Profile, ProfileError
from cinqflow.core.model.vocabulary import Mode
from cinqflow.workers import wiring
from cinqflow.workers.agent_task import AGENT_TASK_TOPIC
from cinqflow.workers.scheduler import RUN_FEED_TOPIC

pytestmark = pytest.mark.unit


def _profile(tmp_path, **overrides: object) -> Profile:
    base: dict[str, object] = {
        "metadata_db": {"adapter": "postgres"},
        "control_tables": {"adapter": "pg-control"},
        "queue": {"adapter": "pg-skip-locked"},
        "orchestration": {"adapter": "pg-scheduler"},
        "storage": {"adapter": "localfs", "root": str(tmp_path)},
        "connector": {"routes": {}},
        "llm": {
            "adapter": "scripted",
            "routing": {"small": "s", "large": "l"},
            "budgets": {"per_run_usd": 0.25, "per_agent_per_day_usd": 5.0},
            "prices": {"s": [0.1, 0.2], "l": [1.0, 2.0]},
        },
        "phi_scrub": {"adapter": "mock"},
        "vector": {"adapter": "mock"},
        "agent_runtime": {"adapter": "inproc"},
    }
    base.update(overrides)
    return Profile(source="test.yaml", rung=0.5, socket="test", mode=Mode.FULL, pins=base)


def test_build_wires_both_topics_and_the_right_adapter_types(tmp_path) -> None:
    profile = _profile(tmp_path)
    storage = LocalFsStorage(root=str(tmp_path))

    built = wiring.build(profile, MemSecrets(), connection=object(), storage=storage)

    assert isinstance(built.metadata, PostgresMetadataDb)
    assert isinstance(built.control, PostgresControlTables)
    assert built.storage is storage
    assert built.connectors == {}
    # White-box: `Consumer` has no public "is this topic registered" verb,
    # and asserting on it here is cheaper than a full drain to prove it.
    assert RUN_FEED_TOPIC in built.consumer._handlers
    assert AGENT_TASK_TOPIC in built.consumer._handlers


def test_a_mismatched_adapter_is_refused_before_anything_is_built(tmp_path) -> None:
    profile = _profile(tmp_path, queue={"adapter": "sqs"})

    with pytest.raises(ProfileError, match="queue"):
        wiring.build(profile, MemSecrets(), connection=object(), storage=LocalFsStorage(root=str(tmp_path)))


def test_every_expected_pin_is_checked(tmp_path) -> None:
    for pin in ("metadata_db", "control_tables", "queue", "orchestration", "storage"):
        profile = _profile(tmp_path, **{pin: {"adapter": "not-a-real-adapter"}})
        with pytest.raises(ProfileError, match=pin):
            wiring.build(
                profile, MemSecrets(), connection=object(), storage=LocalFsStorage(root=str(tmp_path))
            )
