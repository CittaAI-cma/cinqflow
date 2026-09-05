"""Prompt v3 (PR-6): column roles and importance, and the deterministic anomaly
signals. The model classifies against the profiler's hints; `_assemble` keeps
only what was observed, bounds importance by knowledge, and falls back to the
hint - each correction on record as an `info` signal."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cinqflow.engine import profiler
from cinqflow.engine.parsers import parse
from cinqflow.intelligence.context import ContextBuilder
from cinqflow.intelligence.graphs.interpret_file import InterpretFileGraph
from cinqflow.intelligence.llm import StubClient
from cinqflow.knowledge.yaml_provider import YamlKnowledgeProvider
from cinqflow.settings import Settings

KNOWLEDGE_ROOT = Path(__file__).resolve().parents[3] / "knowledge"
SOURCE = {"source_system": "fidelis_ny_upstate", "feed": "member_roster"}


@pytest.fixture
def s(tmp_path) -> Settings:
    return Settings(landing_root=tmp_path, knowledge_root=KNOWLEDGE_ROOT, llm_provider="stub")


@pytest.fixture
def facts(small_csv_bytes, s):
    return profiler.profile(parse(small_csv_bytes, "csv"), s)


def graph_with(llm, s) -> InterpretFileGraph:
    return InterpretFileGraph(context_builder=ContextBuilder(YamlKnowledgeProvider(s)), llm=llm)


class Scripted:
    """A model that answers with exactly the dict it is given."""

    model_id = "scripted"

    def __init__(self, response: dict) -> None:
        self.response = response

    def complete_json(self, *, system, user, response_model=None):
        return self.response


def roles_of(content) -> dict[str, object]:
    return {r.name: r for r in content.column_roles}


def infos(content) -> list[str]:
    return [sig.claim for sig in content.signals if sig.severity == "info"]


# ------------------------------------------------------------- the stub and v3


def test_prompt_is_v3_and_the_domain_knowledge_reaches_the_model(facts, s):
    seen: dict = {}

    class Inspecting:
        model_id = "test"

        def complete_json(self, *, system, user, response_model=None):
            seen["payload"] = json.loads(user)
            seen["system"] = system
            return {"claims": [], "signals": [], "column_roles": []}

    result = graph_with(Inspecting(), s).run(facts=facts, domain="enrollments", **SOURCE)
    assert result["prompt"] == "interpret_file@3"
    assert "column_roles" in seen["system"]
    assert "denominator" in seen["payload"]["context"]["domain_knowledge"]["what_it_answers"]
    # The observations carry the v2 facts the prompt names.
    column = seen["payload"]["observations"]["columns"][0]
    assert {"hint", "null_ratio", "constant", "sentinel_count"} <= set(column)
    assert any(c.startswith("domains/") for c in result["knowledge"])


def test_stub_emits_one_role_per_observed_column_with_bounded_importance(facts, s):
    content = graph_with(StubClient(), s).run(facts=facts, **SOURCE)["content"]
    roles = roles_of(content)
    assert set(roles) == {c.name for c in facts.columns}
    assert all(r.source == "model" for r in roles.values())
    # member_id: candidate key, and the glossary maps it toward a canonical field.
    assert (roles["member_id"].role, roles["member_id"].importance) == ("identifier", "high")
    assert roles["product"].role == "dimension"
    assert roles["member_dob"].role == "date"
    # Reasons are structure and knowledge - never a value from the file.
    values = {v for c in facts.columns for v in c.sample_values} | {
        t.value for c in facts.columns for t in c.top_values
    }
    for role in roles.values():
        assert role.reason
        assert not any(v in role.reason for v in values if len(v) > 1), role.reason


def test_stub_is_deterministic_for_roles_too(facts, s):
    a = graph_with(StubClient(), s).run(facts=facts, **SOURCE)["content"]
    b = graph_with(StubClient(), s).run(facts=facts, **SOURCE)["content"]
    assert a.model_dump() == b.model_dump()


# ----------------------------------------------------------- _assemble rules


def test_roles_for_unobserved_columns_are_dropped_and_recorded(facts, s):
    llm = Scripted(
        {
            "claims": [],
            "signals": [],
            "column_roles": [
                {"name": "ghost", "role": "measure", "importance": "high", "reason": "x"},
                {"name": "product", "role": "dimension", "importance": "medium", "reason": "ok"},
            ],
        }
    )
    content = graph_with(llm, s).run(facts=facts, **SOURCE)["content"]
    roles = roles_of(content)
    assert "ghost" not in roles
    assert roles["product"].source == "model"
    assert any("unobserved column" in i and "ghost" in i for i in infos(content))


def test_an_unknown_role_becomes_unclassified(facts, s):
    llm = Scripted(
        {
            "claims": [],
            "signals": [],
            "column_roles": [
                {"name": "product", "role": "vibe", "importance": "medium", "reason": "hmm"}
            ],
        }
    )
    content = graph_with(llm, s).run(facts=facts, **SOURCE)["content"]
    assert roles_of(content)["product"].role == "unclassified"
    assert any("unknown role" in i for i in infos(content))


def test_columns_the_model_skipped_fall_back_to_their_hint(facts, s):
    llm = Scripted(
        {
            "claims": [],
            "signals": [],
            "column_roles": [
                {"name": "product", "role": "dimension", "importance": "medium", "reason": "ok"}
            ],
        }
    )
    content = graph_with(llm, s).run(facts=facts, **SOURCE)["content"]
    roles = roles_of(content)
    assert set(roles) == {c.name for c in facts.columns}  # one per column, always
    fallback = roles["member_id"]
    assert (fallback.source, fallback.role, fallback.reason) == (
        "hint",
        "identifier",
        "from profile hint",
    )
    assert fallback.importance == "high"  # glossary-mapped column
    assert roles["member_dob"].importance in ("high", "medium")


def test_a_technical_column_is_never_above_low(s):
    content_bytes = b"member_id,created_at\nM1,2026-01-01 10:00\nM2,2026-01-02 10:00\n"
    facts = profiler.profile(parse(content_bytes, "csv"), s)
    assert {c.name: c.hint for c in facts.columns}["created_at"] == "technical"
    llm = Scripted(
        {
            "claims": [],
            "signals": [],
            "column_roles": [
                {
                    "name": "created_at",
                    "role": "technical",
                    "importance": "high",
                    "reason": "looks important",
                }
            ],
        }
    )
    content = graph_with(llm, s).run(facts=facts, **SOURCE)["content"]
    assert roles_of(content)["created_at"].importance == "low"
    assert any("demoted to low" in i for i in infos(content))


def test_contradicting_the_hint_needs_a_reason(facts, s):
    silent = Scripted(
        {
            "claims": [],
            "signals": [],
            "column_roles": [
                {"name": "product", "role": "measure", "importance": "medium", "reason": ""}
            ],
        }
    )
    content = graph_with(silent, s).run(facts=facts, **SOURCE)["content"]
    assert roles_of(content)["product"].role == "dimension"  # the hint stood
    assert any("without a reason" in i for i in infos(content))

    argued = Scripted(
        {
            "claims": [],
            "signals": [],
            "column_roles": [
                {
                    "name": "product",
                    "role": "business_attribute",
                    "importance": "medium",
                    "reason": "plan product names are descriptive, not a fixed code set",
                }
            ],
        }
    )
    content = graph_with(argued, s).run(facts=facts, **SOURCE)["content"]
    assert roles_of(content)["product"].role == "business_attribute"


def test_malformed_role_entries_are_recorded_not_fatal(facts, s):
    llm = Scripted({"claims": [], "signals": [], "column_roles": ["nope", {"role": "x"}]})
    content = graph_with(llm, s).run(facts=facts, **SOURCE)["content"]
    assert len(content.column_roles) == len(facts.columns)
    assert sum("malformed column role" in i for i in infos(content)) == 2


# ------------------------------------------------ deterministic anomaly signals


def test_anomalies_are_raised_from_the_facts_whatever_the_model_says(s):
    content_bytes = (
        b"member_id,plan,empty,svc_date\n"
        b"M1,A,,2026-01-01\n"
        b"M2,A,,9999-12-31\n"
        b"M3,A,,9999-12-31\n"
        b"M3,A,,9999-12-31\n"
    )
    facts = profiler.profile(parse(content_bytes, "csv"), s)
    silent = Scripted({"claims": [], "signals": [], "column_roles": []})
    content = graph_with(silent, s).run(facts=facts, **SOURCE)["content"]
    risks = [sig.claim for sig in content.signals if sig.kind == "risk"]
    assert any("empty is empty in every row" in r for r in risks)
    assert any("plan holds one value" in r for r in risks)
    assert any("svc_date uses placeholder dates" in r for r in risks)
    assert any("fully duplicated rows" in r for r in risks)
    # Composed into the headline the same way model risks are.
    assert content.recommended_action == "approve"
    assert "risk" in content.headline


def test_null_rate_risk_survives_the_move_out_of_the_stub(facts, s):
    content = graph_with(StubClient(), s).run(facts=facts, **SOURCE)["content"]
    risks = [sig.claim for sig in content.signals if sig.kind == "risk"]
    assert sum("member_dob is null" in r for r in risks) == 1  # once, not twice


# ---------------------------------------------------------------- golden set


def test_real_roster_roles(roster_csv_bytes, s):
    facts = profiler.profile(parse(roster_csv_bytes, "csv"), s)
    content = graph_with(StubClient(), s).run(facts=facts, domain="enrollments", **SOURCE)[
        "content"
    ]
    roles = roles_of(content)
    assert len(roles) == 45
    assert (roles["member_id"].role, roles["member_id"].importance) == ("identifier", "high")
    assert roles["provider_npi"].role == "identifier"
    assert roles["member_dob"].role == "date"
    assert roles["member_age"].role == "measure"
    assert roles["product"].role == "dimension"
    high = [name for name, r in roles.items() if r.importance == "high"]
    assert 1 <= len(high) <= 20, high  # a short list, bounded by knowledge, not everything
    # PHI never leaks through a reason.
    values = {v for c in facts.columns for v in c.sample_values} | {
        t.value for c in facts.columns for t in c.top_values
    }
    for role in roles.values():
        assert not any(v in role.reason for v in values if len(v) > 2), role.reason
