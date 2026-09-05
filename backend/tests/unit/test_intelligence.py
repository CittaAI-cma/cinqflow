"""The graph must produce validated artifacts and refuse unusable model output."""

from __future__ import annotations

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


@pytest.fixture
def s(tmp_path) -> Settings:
    return Settings(landing_root=tmp_path, knowledge_root=KNOWLEDGE_ROOT, llm_provider="stub")


@pytest.fixture
def facts(small_csv_bytes, s):
    return profiler.profile(parse(small_csv_bytes, "csv"), s)


def graph_with(llm, s) -> InterpretFileGraph:
    return InterpretFileGraph(context_builder=ContextBuilder(YamlKnowledgeProvider(s)), llm=llm)


def test_context_is_selective_and_excludes_full_sample_rows(facts, s):
    job = ContextBuilder(YamlKnowledgeProvider(s)).for_interpretation(
        facts=facts, source_system="fidelis_ny_upstate", feed="member_roster"
    )
    assert "sample_rows" not in job.observations
    assert "source" in job.context
    # only glossary terms matching the observed columns are included
    terms = [t["term"] for t in job.context["glossary"]["terms"]]
    assert "member_dob" in terms
    assert "provider_npi" not in terms


def test_governed_knowledge_wins_for_domain(facts, s):
    result = graph_with(StubClient(), s).run(
        facts=facts, source_system="fidelis_ny_upstate", feed="member_roster"
    )
    domain = next(c for c in result["content"].claims if c.field == "likely_domain")
    assert domain.value == "enrollments"
    assert domain.kind == "governed_knowledge"
    assert any("sources/" in e for e in domain.evidence)


def test_provenance_records_prompt_model_and_knowledge_versions(facts, s):
    result = graph_with(StubClient(), s).run(
        facts=facts, source_system="fidelis_ny_upstate", feed="member_roster"
    )
    assert result["prompt"] == "interpret_file@3"
    assert result["model"] == "stub-reasoner-1"
    assert any(
        c.startswith("sources/fidelis_ny_upstate__member_roster.yaml@") for c in result["knowledge"]
    )


def test_unregistered_feed_yields_unknowns_not_guesses(facts, s):
    result = graph_with(StubClient(), s).run(
        facts=facts, source_system="unheard_of", feed="mystery"
    )
    content = result["content"]
    domain = next(c for c in content.claims if c.field == "likely_domain")
    assert domain.kind == "inference"
    assert domain.confidence < 0.9


def test_claims_without_evidence_are_discarded_not_stored(facts, s):
    class NoEvidence:
        model_id = "test"

        def complete_json(self, *, system, user, response_model=None):
            return {
                "claims": [
                    {
                        "kind": "inference",
                        "field": "likely_domain",
                        "value": "claims",
                        "confidence": 0.9,
                        "evidence": [],
                    },
                    {
                        "kind": "inference",
                        "field": "likely_grain",
                        "value": "one row per member",
                        "confidence": 0.8,
                        "evidence": ["candidate_key:member_id"],
                    },
                ],
                "signals": [],
            }

    content = graph_with(NoEvidence(), s).run(
        facts=facts, source_system="fidelis_ny_upstate", feed="member_roster"
    )["content"]
    fields = [c.field for c in content.claims]
    assert "likely_domain" not in fields
    assert "likely_grain" in fields
    unknowns = [sig.claim for sig in content.signals if sig.kind == "unknown"]
    assert any("without evidence" in u for u in unknowns)


def test_malformed_model_output_is_discarded_and_recorded(facts, s):
    class Malformed:
        model_id = "test"

        def complete_json(self, *, system, user, response_model=None):
            return {
                "claims": [
                    {
                        "kind": "nonsense",
                        "field": "x",
                        "value": "y",
                        "confidence": 5,
                        "evidence": ["e"],
                    },
                    "not even an object",
                ],
                "signals": [
                    {
                        "kind": "risk",
                        "claim": "ok",
                        "basis": "test basis",
                        "check": "test check",
                        "consequence": "test consequence",
                    }
                ],
            }

    content = graph_with(Malformed(), s).run(
        facts=facts, source_system="fidelis_ny_upstate", feed="member_roster"
    )["content"]
    assert content.claims == []
    dropped = [sig for sig in content.signals if sig.kind == "unknown" and sig.severity == "info"]
    assert len(dropped) == 2
    risks = [sig.claim for sig in content.signals if sig.kind == "risk"]
    # The model's risk survives; the deterministic anomaly risks (PR-6) join it.
    assert risks[0] == "ok"
    assert any("member_dob is null" in r for r in risks)


def test_stub_reasoner_is_deterministic(facts, s):
    a = graph_with(StubClient(), s).run(
        facts=facts, source_system="fidelis_ny_upstate", feed="member_roster"
    )["content"]
    b = graph_with(StubClient(), s).run(
        facts=facts, source_system="fidelis_ny_upstate", feed="member_roster"
    )["content"]
    assert a.model_dump() == b.model_dump()


def test_risks_report_real_null_rates(facts, s):
    content = graph_with(StubClient(), s).run(
        facts=facts, source_system="fidelis_ny_upstate", feed="member_roster"
    )["content"]
    risks = [sig.claim for sig in content.signals if sig.kind == "risk"]
    assert any("member_dob is null" in r for r in risks)
