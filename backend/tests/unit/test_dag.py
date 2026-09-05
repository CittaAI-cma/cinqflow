"""The workflow declaration is a DAG, names real topics, and answers the two
questions the worker loop asks of it: which step is this message, and which
scope is it about."""

from __future__ import annotations

import pytest

from cinqflow.queue import worker
from cinqflow.settings import Settings
from cinqflow.workers import (
    analyze_bronze,
    interpret_upload,
    land_bronze,
    profile_upload,
    promote_silver,
    reject_upload,
    run_preview,
)
from cinqflow.workflow import dag
from cinqflow.workflow.dag import (
    STEP_ORDER,
    STEPS,
    WORKFLOW,
    StepDef,
    downstream_gate,
    feed_version_scope,
    scope_id_for,
    step_for_topic,
)


def test_the_declared_workflow_validates_and_is_ordered():
    dag.validate()  # import already ran it; this makes the intent explicit
    assert [step.key for step in WORKFLOW] == [
        "profile",
        "interpret",
        "gate_g1",
        "land",
        "analyze",
        "preview",
        "gate_g2",
        "promote",
    ]
    # Every dependency points backwards in the declared order.
    for step in WORKFLOW:
        for dep in step.depends_on:
            assert STEP_ORDER[dep] < STEP_ORDER[step.key], (step.key, dep)


def test_every_worker_step_names_a_topic_the_worker_actually_serves(tmp_path):
    registry = worker.handlers(Settings(llm_provider="stub", landing_root=tmp_path))
    for step in WORKFLOW:
        if step.gate:
            assert step.topic is None
        else:
            assert step.topic in registry, step.key


def test_topics_match_the_workers_own_constants():
    assert STEPS["profile"].topic == profile_upload.TOPIC
    assert STEPS["interpret"].topic == interpret_upload.TOPIC
    assert STEPS["land"].topic == land_bronze.TOPIC
    assert STEPS["analyze"].topic == analyze_bronze.TOPIC
    assert STEPS["preview"].topic == run_preview.TOPIC
    assert STEPS["promote"].topic == promote_silver.TOPIC


def test_housekeeping_topics_are_not_steps():
    # Rejecting moves a file out of `incoming`; it is what the G1 decision
    # caused, not a step the run performs.
    assert step_for_topic(reject_upload.TOPIC) is None
    assert step_for_topic("bronze.analyze") is STEPS["analyze"]
    assert step_for_topic("no.such.topic") is None


def test_scope_ids_come_from_the_payload_each_worker_already_receives():
    assert scope_id_for(STEPS["profile"], {"upload_id": "u1"}) == "u1"
    assert scope_id_for(STEPS["land"], {"upload_id": "u1"}) == "u1"
    assert scope_id_for(STEPS["analyze"], {"batch_id": "b1"}) == "b1"
    assert scope_id_for(STEPS["promote"], {"feed": "f", "version": 2, "batch_id": "b1"}) == "b1"
    assert scope_id_for(STEPS["preview"], {"feed": "roster", "version": "3"}) == "roster:v3"
    assert feed_version_scope("roster", 3) == "roster:v3"


def test_gates_open_after_the_step_they_depend_on_in_the_same_scope():
    assert downstream_gate(STEPS["interpret"]) is STEPS["gate_g1"]
    assert downstream_gate(STEPS["preview"]) is STEPS["gate_g2"]
    assert downstream_gate(STEPS["profile"]) is None
    assert downstream_gate(STEPS["land"]) is None
    # A gate depending on a step in another scope would not open from it.
    foreign = StepDef("x", "x", "batch", "t", ())
    assert downstream_gate(foreign) is None


@pytest.mark.parametrize(
    ("workflow", "message"),
    [
        (
            (StepDef("a", "a", "upload", "t"), StepDef("a", "a", "upload", "t")),
            "duplicate step keys",
        ),
        ((StepDef("a", "a", "upload", "t", ("zzz",)),), "unknown step"),
        (
            (
                StepDef("a", "a", "upload", "t1", ("b",)),
                StepDef("b", "b", "upload", "t2", ("a",)),
            ),
            "cycle",
        ),
        ((StepDef("g", "g", "upload", "t", (), gate=True),), "must not have a topic"),
        ((StepDef("w", "w", "upload", None, ()),), "has no topic"),
    ],
)
def test_malformed_declarations_are_refused(workflow, message):
    with pytest.raises(ValueError, match=message):
        dag.validate(workflow)


def test_as_dicts_is_plain_data():
    exported = dag.as_dicts()
    assert exported[0] == {
        "key": "profile",
        "label": "Parse and profile",
        "scope": "upload",
        "topic": "upload.profile",
        "depends_on": (),
        "gate": False,
    }
