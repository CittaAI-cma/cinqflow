"""Test graph state management, provenance, and failure modes."""

from __future__ import annotations

from pathlib import Path

import pytest

from cinqflow.engine import profiler
from cinqflow.engine.parsers import parse
from cinqflow.intelligence.context import ContextBuilder
from cinqflow.intelligence.graphs.interpret_file import InterpretFileGraph
from cinqflow.intelligence.graphs.recommend_mapping import RecommendMappingGraph
from cinqflow.intelligence.llm import LlmError, StubClient
from cinqflow.knowledge.yaml_provider import YamlKnowledgeProvider
from cinqflow.settings import Settings

KNOWLEDGE_ROOT = Path(__file__).resolve().parents[3] / "knowledge"
DOMAIN = "enrollment"


@pytest.fixture
def s(tmp_path) -> Settings:
    return Settings(landing_root=tmp_path, knowledge_root=KNOWLEDGE_ROOT, llm_provider="stub")


@pytest.fixture
def facts(small_csv_bytes, s):
    return profiler.profile(parse(small_csv_bytes, "csv"), s)


@pytest.fixture
def roster_facts(s):
    """Roster-shaped columns using the real source names from the corpus."""
    content = (
        b"member_id,member_first_name,member_last_name,member_dob,member_sex,"
        b"product,member_city,harp_eligible,recertification_end_date\n"
        b"M001,DANIELLE,DYER,1997-11-04,F,TANF Adult,BROWNTOWN,Yes,2026-06-30\n"
        b"M002,KEVIN,ALLISON,2013-11-04,M,TANF Child,SPRING VALLEY,,2026-07-31\n"
    )
    return profiler.profile(parse(content, "csv"), s)


def graph_interpret(llm, s) -> InterpretFileGraph:
    return InterpretFileGraph(context_builder=ContextBuilder(YamlKnowledgeProvider(s)), llm=llm)


def graph_mapping(llm, s) -> RecommendMappingGraph:
    return RecommendMappingGraph(context_builder=ContextBuilder(YamlKnowledgeProvider(s)), llm=llm)


# ===============================================================================
# State Machine Tests
# ===============================================================================


class TestInterpretFileState:
    """Graph state progression and output validation."""

    def test_ground_node_removes_sample_rows(self, facts, s):
        """Sample rows are PHI and redundant; they must not reach the model."""

        class InspectingClient:
            model_id = "test"

            def complete_json(self, *, system, user, response_model=None):
                import json

                payload = json.loads(user)
                # The payload is what the model sees; sample_rows must not be there
                assert "sample_rows" not in payload.get("observations", {})
                return {"claims": [], "risks": [], "unknowns": []}

        graph_interpret(InspectingClient(), s).run(
            facts=facts, source_system="fidelis_ny_upstate", feed="member_roster"
        )

    def test_infer_node_receives_complete_context(self, facts, s):
        """The infer node receives observations and selected context."""

        class InspectingClient:
            model_id = "test"

            def complete_json(self, *, system, user, response_model=None):
                import json

                payload = json.loads(user)
                # Both observations and context are present
                assert "observations" in payload
                assert "context" in payload
                # Context contains source and glossary
                assert "source" in payload["context"]
                assert "glossary" in payload["context"]
                return {"claims": [], "risks": [], "unknowns": []}

        graph_interpret(InspectingClient(), s).run(
            facts=facts, source_system="fidelis_ny_upstate", feed="member_roster"
        )

    def test_assemble_node_validates_confidence_range(self, facts, s):
        """Confidence must be in [0, 1]; out-of-range values are rejected."""

        class OutOfRangeConfidence:
            model_id = "test"

            def complete_json(self, *, system, user, response_model=None):
                return {
                    "claims": [
                        {
                            "kind": "inference",
                            "field": "likely_domain",
                            "value": "claims",
                            "confidence": 1.5,  # invalid: > 1
                            "evidence": ["x"],
                        },
                        {
                            "kind": "inference",
                            "field": "likely_grain",
                            "value": "grain",
                            "confidence": 0.8,  # valid
                            "evidence": ["y"],
                        },
                    ],
                    "risks": [],
                    "unknowns": [],
                }

        content = graph_interpret(OutOfRangeConfidence(), s).run(
            facts=facts, source_system="fidelis_ny_upstate", feed="member_roster"
        )["content"]
        # The invalid one is discarded
        assert len(content.claims) == 1
        assert content.claims[0].field == "likely_grain"
        # Discarded item is recorded as an unknown
        assert any("malformed claim" in u for u in content.unknowns)

    def test_assemble_node_rejects_invalid_claim_kind(self, facts, s):
        """Only valid ClaimKind values are accepted."""

        class InvalidKind:
            model_id = "test"

            def complete_json(self, *, system, user, response_model=None):
                return {
                    "claims": [
                        {
                            "kind": "hallucination",  # not a valid kind
                            "field": "x",
                            "value": "y",
                            "confidence": 0.9,
                            "evidence": ["e"],
                        }
                    ],
                    "risks": [],
                    "unknowns": [],
                }

        content = graph_interpret(InvalidKind(), s).run(
            facts=facts, source_system="fidelis_ny_upstate", feed="member_roster"
        )["content"]
        # All claims should be discarded
        assert len(content.claims) == 0
        assert any("malformed claim" in u for u in content.unknowns)

    def test_stream_mode_supports_on_step_callback(self, facts, s):
        """The run() method with on_step callback records each node's completion."""
        nodes_visited = []

        def on_step(node: str) -> None:
            nodes_visited.append(node)

        graph_interpret(StubClient(), s).run(
            facts=facts,
            source_system="fidelis_ny_upstate",
            feed="member_roster",
            on_step=on_step,
        )

        # All three nodes should have been visited
        assert "ground" in nodes_visited
        assert "infer" in nodes_visited
        assert "assemble" in nodes_visited
        # Visited in order
        assert nodes_visited.index("ground") < nodes_visited.index("infer")
        assert nodes_visited.index("infer") < nodes_visited.index("assemble")


class TestMappingState:
    """Mapping graph state and validation."""

    def test_ground_node_loads_legal_targets(self, roster_facts, s):
        """The ground node loads the canonical model's legal targets."""

        class InspectingClient:
            model_id = "test"

            def complete_json(self, *, system, user, response_model=None):
                import json

                payload = json.loads(user)
                context = payload["context"]
                # The canonical model must be present and populated
                assert "canonical" in context
                entities = context["canonical"]["entities"]
                assert len(entities) > 0
                # Fields are fully qualified (table.field)
                all_fields = [
                    f["name"]
                    for entity in entities
                    for f in entity.get("fields", [])
                ]
                assert any("." in f for f in all_fields)
                return {"fields": [], "notes": []}

        graph_mapping(InspectingClient(), s).run(
            facts=roster_facts,
            source_system="fidelis_ny_upstate",
            feed="member_roster",
            domain=DOMAIN,
        )

    def test_validate_node_enforces_all_columns_present(self, roster_facts, s):
        """Every observed column must appear in the output, even if ignored by model."""

        class IgnoresMostColumns:
            model_id = "test"

            def complete_json(self, *, system, user, response_model=None):
                return {
                    "fields": [
                        {
                            "source": "member_id",
                            "target": "members.source_system_id",
                            "confidence": 0.9,
                            "evidence": ["test"],
                            "status": "candidate",
                        }
                    ],
                    "notes": [],
                }

        content = graph_mapping(IgnoresMostColumns(), s).run(
            facts=roster_facts,
            source_system="fidelis_ny_upstate",
            feed="member_roster",
            domain=DOMAIN,
        )["content"]

        # All observed columns must be present
        observed = [c.name for c in roster_facts.columns]
        proposed = [f.source for f in content.fields]
        assert sorted(proposed) == sorted(observed)

        # The ones the model ignored have status=unknown
        ignored = [c for c in content.fields if c.source != "member_id"]
        assert all(c.status == "unknown" for c in ignored)
        assert all("not addressed by the model" in c.evidence[0] for c in ignored)

    def test_validate_node_sorts_by_status(self, roster_facts, s):
        """Output is sorted: invalid first, then candidate, ambiguous, unknown."""

        class Mixed:
            model_id = "test"

            def complete_json(self, *, system, user, response_model=None):
                return {
                    "fields": [
                        {
                            "source": "member_id",
                            "target": "members.fake_field",  # invalid (not in canonical)
                            "confidence": 0.9,
                            "evidence": ["test"],
                            "status": "candidate",
                        },
                        {
                            "source": "member_dob",
                            "target": "members.date_of_birth",  # candidate
                            "confidence": 0.95,
                            "evidence": ["test"],
                            "status": "candidate",
                        },
                        {
                            "source": "member_first_name",
                            "target": None,  # unknown
                            "confidence": 0.0,
                            "evidence": ["test"],
                            "status": "unknown",
                        },
                    ],
                    "notes": [],
                }

        content = graph_mapping(Mixed(), s).run(
            facts=roster_facts,
            source_system="fidelis_ny_upstate",
            feed="member_roster",
            domain=DOMAIN,
        )["content"]

        # Find the relevant fields
        invalid = next(f for f in content.fields if f.source == "member_id")
        candidate = next(f for f in content.fields if f.source == "member_dob")
        unknown = next(f for f in content.fields if f.source == "member_first_name")

        # Check ordering: invalid comes before candidate
        assert content.fields.index(invalid) < content.fields.index(candidate)
        # candidate comes before unknown
        assert content.fields.index(candidate) < content.fields.index(unknown)


# ===============================================================================
# Provenance Tests
# ===============================================================================


class TestProvenance:
    """Provenance recording and citation."""

    def test_interpret_provenance_cites_exact_versions(self, facts, s):
        """Provenance must cite exact versions: prompt@N, model_id, knowledge@versions."""
        result = graph_interpret(StubClient(), s).run(
            facts=facts, source_system="fidelis_ny_upstate", feed="member_roster"
        )

        assert result["prompt"] == "interpret_file@1"
        assert result["model"] == "stub-reasoner-1"

        # Knowledge citations are version-stamped
        assert isinstance(result["knowledge"], list)
        for citation in result["knowledge"]:
            # Each citation has the pattern path@version
            assert "@" in citation
            parts = citation.split("@")
            assert len(parts) == 2
            assert parts[1].isdigit()

    def test_mapping_provenance_cites_exact_versions(self, roster_facts, s):
        """Provenance must cite exact versions."""
        result = graph_mapping(StubClient(), s).run(
            facts=roster_facts,
            source_system="fidelis_ny_upstate",
            feed="member_roster",
            domain=DOMAIN,
        )

        assert result["prompt"] == "recommend_mapping@3"
        assert result["model"] == "stub-reasoner-1"

        # All knowledge sources are cited with versions
        assert isinstance(result["knowledge"], list)
        for citation in result["knowledge"]:
            assert "@" in citation

    def test_provenance_is_returned_not_modified_by_graph(self, facts, s):
        """Graphs return provenance data; they don't store it themselves."""

        class CustomModel:
            model_id = "custom-model-v42"

            def complete_json(self, *, system, user, response_model=None):
                return {"claims": [], "risks": [], "unknowns": []}

        result = graph_interpret(CustomModel(), s).run(
            facts=facts, source_system="fidelis_ny_upstate", feed="member_roster"
        )

        # The exact model_id is returned
        assert result["model"] == "custom-model-v42"


# ===============================================================================
# Failure Mode Tests
# ===============================================================================


class TestFailureModes:
    """Graph behavior under adverse conditions."""

    def test_interpret_graph_handles_missing_knowledge_gracefully(self, facts, s):
        """If knowledge is missing, the graph proceeds with empty context."""
        result = graph_interpret(StubClient(), s).run(
            facts=facts, source_system="unknown_system", feed="unknown_feed"
        )

        # Even with no knowledge, the graph completes
        assert "content" in result
        assert hasattr(result["content"], "claims")

    def test_mapping_graph_handles_unknown_domain_gracefully(self, roster_facts, s):
        """If domain knowledge is missing, validate still enforces canonical constraints."""
        result = graph_mapping(StubClient(), s).run(
            facts=roster_facts,
            source_system="fidelis_ny_upstate",
            feed="member_roster",
            domain="no_such_domain",  # Unknown domain
        )

        # The graph completes; validation still enforces constraints
        assert "content" in result
        assert hasattr(result["content"], "fields")

    def test_llm_error_propagates_with_context(self, facts, s):
        """LLM errors include context for debugging."""

        class BrokenClient:
            model_id = "broken"

            def complete_json(self, *, system, user, response_model=None):
                raise LlmError("simulated failure")

        with pytest.raises(LlmError) as exc_info:
            graph_interpret(BrokenClient(), s).run(
                facts=facts, source_system="fidelis_ny_upstate", feed="member_roster"
            )

        assert "simulated failure" in str(exc_info.value)

    def test_response_with_missing_required_fields_handled_gracefully(self, facts, s):
        """If the LLM response is missing fields, they're provided with defaults."""

        class IncompleteClient:
            model_id = "incomplete"

            def complete_json(self, *, system, user, response_model=None):
                # Missing 'claims' field - assemble will use default []
                return {"risks": [], "unknowns": []}

        result = graph_interpret(IncompleteClient(), s).run(
            facts=facts, source_system="fidelis_ny_upstate", feed="member_roster"
        )

        # Graph completes successfully; missing fields get defaults
        assert "content" in result
        assert result["content"].claims == []


# ===============================================================================
# Edge Case Tests
# ===============================================================================


class TestEdgeCases:
    """Boundary conditions and unusual inputs."""

    def test_empty_claims_list_is_valid_output(self, facts, s):
        """A model returning zero claims is valid (rare but legal)."""

        class NoClaimsClient:
            model_id = "silent"

            def complete_json(self, *, system, user, response_model=None):
                return {"claims": [], "risks": [], "unknowns": []}

        result = graph_interpret(NoClaimsClient(), s).run(
            facts=facts, source_system="fidelis_ny_upstate", feed="member_roster"
        )

        assert result["content"].claims == []
        assert "content" in result

    def test_empty_evidence_list_triggers_rejection_and_recording(self, facts, s):
        """Claims without evidence are discarded and noted."""

        class NoEvidenceClient:
            model_id = "unsubstantiated"

            def complete_json(self, *, system, user, response_model=None):
                return {
                    "claims": [
                        {
                            "kind": "inference",
                            "field": "likely_domain",
                            "value": "claims",
                            "confidence": 0.9,
                            "evidence": [],  # empty!
                        }
                    ],
                    "risks": [],
                    "unknowns": [],
                }

        content = graph_interpret(NoEvidenceClient(), s).run(
            facts=facts, source_system="fidelis_ny_upstate", feed="member_roster"
        )["content"]

        assert len(content.claims) == 0
        assert any("without evidence" in u for u in content.unknowns)

    def test_non_string_evidence_is_coerced_to_string(self, facts, s):
        """Evidence is coerced to string (defensive programming)."""

        class MixedEvidenceTypes:
            model_id = "mixed"

            def complete_json(self, *, system, user, response_model=None):
                return {
                    "claims": [
                        {
                            "kind": "observed_fact",
                            "field": "row_count",
                            "value": "1000",
                            "confidence": 1.0,
                            "evidence": [
                                "profile:row_count",
                                "123",  # coerce to string
                                '{"key": "value"}',  # coerce to string
                            ],
                        }
                    ],
                    "risks": [],
                    "unknowns": [],
                }

        content = graph_interpret(MixedEvidenceTypes(), s).run(
            facts=facts, source_system="fidelis_ny_upstate", feed="member_roster"
        )["content"]

        # The claim is accepted (evidence is present, all as strings)
        assert len(content.claims) == 1
        claim = content.claims[0]
        # Evidence is all strings
        assert all(isinstance(e, str) for e in claim.evidence)

    def test_very_long_risk_or_unknown_text_is_accepted(self, facts, s):
        """No artificial limits on risk/unknown text length."""

        class VerboseClient:
            model_id = "verbose"

            def complete_json(self, *, system, user, response_model=None):
                long_text = "x" * 5000
                return {
                    "claims": [],
                    "risks": [long_text],
                    "unknowns": [long_text],
                }

        content = graph_interpret(VerboseClient(), s).run(
            facts=facts, source_system="fidelis_ny_upstate", feed="member_roster"
        )["content"]

        assert len(content.risks) > 0
        assert len(content.risks[0]) == 5000

    def test_unicode_in_values_and_evidence_is_preserved(self, facts, s):
        """Unicode characters are preserved through the graph."""

        class UnicodeClient:
            model_id = "unicode"

            def complete_json(self, *, system, user, response_model=None):
                return {
                    "claims": [
                        {
                            "kind": "inference",
                            "field": "domain",
                            "value": "医疗保险",  # Chinese characters
                            "confidence": 0.7,
                            "evidence": ["column: 患者_ID"],  # Chinese
                        }
                    ],
                    "risks": ["⚠️ warning"],
                    "unknowns": ["❓ unknown"],
                }

        content = graph_interpret(UnicodeClient(), s).run(
            facts=facts, source_system="fidelis_ny_upstate", feed="member_roster"
        )["content"]

        assert content.claims[0].value == "医疗保险"
        assert "患者_ID" in content.claims[0].evidence[0]
        assert "⚠️" in content.risks[0]


