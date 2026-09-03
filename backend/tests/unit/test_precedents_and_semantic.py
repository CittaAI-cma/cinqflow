"""Gap-closing coverage for the two knowledge paths added alongside the
existing deterministic lookup:

- `knowledge/decisions.py`: the analyst decision register, wired in as a
  preferred precedent over a model's own guess.
- `knowledge/semantic.py`: the lexical fallback, used only where deterministic
  lookup (glossary, canonical name, a precedent) placed nothing at all.

Both are exercised at their own level (pure functions) and through the graph,
so a change to either shows up where it would actually bite: `_validate`'s
deterministic enforcement.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cinqflow.engine import profiler
from cinqflow.engine.parsers import parse
from cinqflow.intelligence.context import ContextBuilder
from cinqflow.intelligence.graphs.recommend_mapping import RecommendMappingGraph
from cinqflow.intelligence.llm import StubClient
from cinqflow.knowledge.decisions import hints_for_columns, parse_decision_hints
from cinqflow.knowledge.semantic import build_concept_index, find_matches
from cinqflow.knowledge.yaml_provider import YamlKnowledgeProvider
from cinqflow.settings import Settings

KNOWLEDGE_ROOT = Path(__file__).resolve().parents[3] / "knowledge"
DOMAIN = "enrollment"


@pytest.fixture
def s(tmp_path) -> Settings:
    return Settings(landing_root=tmp_path, knowledge_root=KNOWLEDGE_ROOT, llm_provider="stub")


def graph_with(llm, s) -> RecommendMappingGraph:
    return RecommendMappingGraph(
        context_builder=ContextBuilder(YamlKnowledgeProvider(s)), llm=llm
    )


def facts_for(content: bytes, s: Settings):
    return profiler.profile(parse(content, "csv"), s)


# =============================================================================
# knowledge/decisions.py - pure parsing, no I/O
# =============================================================================


class TestDecisionHints:
    def test_extracts_the_source_to_target_relationship(self):
        records = [
            {
                "decision_id": "DR-1",
                "title": "t",
                "trigger": "member_id and medicaid_id both plausible",
                "decision": "member_id -> members.source_system_id with on_null reject",
                "rationale": "the payer's own id is the natural key",
                "reversibility": "reversible",
                "generalisable": False,
            }
        ]
        hints = parse_decision_hints(records)
        assert len(hints) == 1
        assert hints[0].source_column == "member_id"
        assert hints[0].target == "members.source_system_id"
        assert hints[0].decision_id == "DR-1"

    def test_ignores_records_with_no_column_routing_relationship(self):
        """A constant assignment or a value-map decision names no `source ->
        table.field` relationship, so it contributes nothing - there is no
        column for a hint like this to attach to."""
        records = [
            {
                "decision_id": "DR-2",
                "decision": "source_system = 'ACO_REACH_D0284' (constant).",
            },
            {
                "decision_id": "DR-3",
                "decision": "value_map F->female, M->male, U->unknown.",
            },
        ]
        assert parse_decision_hints(records) == []

    def test_hints_for_columns_matches_case_insensitively_and_only_named_columns(self):
        records = [
            {"decision_id": "DR-1", "decision": "Member_ID -> members.source_system_id"},
        ]
        hints = parse_decision_hints(records)
        selected = hints_for_columns(hints, ["member_id", "member_first_name"])
        assert set(selected) == {"member_id"}
        assert selected["member_id"].decision_id == "DR-1"

    def test_first_matching_record_wins_when_two_decisions_disagree(self):
        records = [
            {"decision_id": "DR-old", "decision": "member_id -> members.source_system_id"},
            {"decision_id": "DR-new", "decision": "member_id -> members.source_system_id_type"},
        ]
        hints = parse_decision_hints(records)
        selected = hints_for_columns(hints, ["member_id"])
        assert selected["member_id"].decision_id == "DR-old"


# =============================================================================
# knowledge/semantic.py - pure scoring, no I/O
# =============================================================================


GLOSSARY_TERMS = [
    {
        "term": "member_dob",
        "aliases": ["dob", "birthdate"],
        "means": "date of birth",
        "maps_toward": "members.date_of_birth",
    },
    {
        "term": "member_first_name",
        "aliases": ["first_name", "fname"],
        "means": "first name",
        "maps_toward": "members.first_name",
    },
]
CANONICAL_ENTITIES = [
    {
        "table": "members",
        "fields": [
            {"name": "date_of_birth", "means": "Member's date of birth"},
            {"name": "first_name", "means": "Member's first name"},
        ],
    }
]


class TestSemanticIndex:
    def test_merged_compound_matches_the_underscored_canonical_name(self):
        """`dateofbirth` (no separators) is a spelling difference from
        `date_of_birth`, not a different concept - lexical similarity should
        find it even though token overlap alone would see zero shared words."""
        entries = build_concept_index(
            glossary_terms=GLOSSARY_TERMS, canonical_entities=CANONICAL_ENTITIES
        )
        matches = find_matches(columns=["dateofbirth"], entries=entries)
        assert "dateofbirth" in matches
        best = matches["dateofbirth"][0]
        assert best.target == "members.date_of_birth"
        assert best.score >= 0.9

    def test_columns_sharing_only_a_naming_convention_stem_do_not_match(self):
        """`member_phone` and `member_first_name` share the `member_` prefix
        and nothing else - whole-string character similarity used to conflate
        that shared stem with a real match. Multi-token columns must be judged
        on vocabulary overlap, not raw spelling, or every column in one feed
        would look similar to every other."""
        entries = build_concept_index(
            glossary_terms=GLOSSARY_TERMS, canonical_entities=CANONICAL_ENTITIES
        )
        matches = find_matches(columns=["member_phone"], entries=entries)
        assert matches == {}

    def test_truly_unrelated_column_stays_unmatched(self):
        """Structured lookup found nothing, and neither does the fallback -
        this is the case the platform must leave `unknown`, not guess at."""
        entries = build_concept_index(
            glossary_terms=GLOSSARY_TERMS, canonical_entities=CANONICAL_ENTITIES
        )
        matches = find_matches(columns=["provider_taxonomy_code"], entries=entries)
        assert matches == {}


# =============================================================================
# Through the graph: precedents and semantic hints, enforced deterministically
# =============================================================================


class TestPrecedentsThroughTheGraph:
    def test_a_governed_decision_is_applied_even_when_the_model_ignores_it(self, s):
        """The stub itself already knows about `context.precedents` (see
        `intelligence/llm.py`), but this asserts the deterministic side: even a
        model that proposed nothing for this column would still get the
        precedent's target, because `_validate` applies it regardless."""

        class IgnoresPrecedents:
            model_id = "test"

            def complete_json(self, *, system, user, response_model=None):
                return {"fields": [], "notes": []}

        content = (
            b"member_id,member_first_name\nM001,DANIELLE\nM002,KEVIN\n"
        )
        facts = facts_for(content, s)
        content_out = graph_with(IgnoresPrecedents(), s).run(
            facts=facts, source_system="fidelis_ny_upstate", feed="member_roster", domain=DOMAIN
        )["content"]

        member_id = next(f for f in content_out.fields if f.source == "member_id")
        assert member_id.status == "candidate"
        assert member_id.target == "members.source_system_id"
        assert any(e.startswith("precedent:") for e in member_id.evidence)
        assert member_id.confidence >= 0.9

    def test_precedent_never_overwrites_an_invalid_rejection(self, s):
        """A model that fabricates a target outside the canonical model must
        stay visibly `invalid` - a precedent is surfaced as a note for the
        analyst to apply by hand, never used to quietly paper over the model's
        own fabrication."""

        class FabricatesATarget:
            model_id = "test"

            def complete_json(self, *, system, user, response_model=None):
                return {
                    "fields": [
                        {
                            "source": "member_id",
                            "target": "members.not_a_real_field",
                            "confidence": 0.9,
                            "evidence": ["looks right"],
                            "status": "candidate",
                        }
                    ],
                    "notes": [],
                }

        content = b"member_id\nM001\nM002\n"
        facts = facts_for(content, s)
        result = graph_with(FabricatesATarget(), s).run(
            facts=facts, source_system="fidelis_ny_upstate", feed="member_roster", domain=DOMAIN
        )
        member_id = next(f for f in result["content"].fields if f.source == "member_id")
        assert member_id.status == "invalid"
        assert member_id.rejected_target == "members.not_a_real_field"
        assert any("DR-20260902-001" in note for note in result["content"].notes)

    def test_model_and_precedent_disagreement_is_surfaced_not_silently_resolved(self, s):
        class DisagreesWithThePrecedent:
            model_id = "test"

            def complete_json(self, *, system, user, response_model=None):
                return {
                    "fields": [
                        {
                            "source": "member_id",
                            "target": "members.source_system_id_type",
                            "confidence": 0.7,
                            "evidence": ["name match"],
                            "status": "candidate",
                        }
                    ],
                    "notes": [],
                }

        content = b"member_id\nM001\nM002\n"
        facts = facts_for(content, s)
        result = graph_with(DisagreesWithThePrecedent(), s).run(
            facts=facts, source_system="fidelis_ny_upstate", feed="member_roster", domain=DOMAIN
        )
        member_id = next(f for f in result["content"].fields if f.source == "member_id")
        assert member_id.status == "ambiguous"
        assert "DR-20260902-001" in (member_id.reason or "")
        assert any("analyst must resolve" in note for note in result["content"].notes)


class TestSemanticFallbackThroughTheGraph:
    def test_semantic_candidate_surfaces_as_ambiguous_never_candidate(self, s):
        """`dateofbirth` has no glossary alias and no exact canonical field
        name - deterministic lookup finds nothing. The lexical fallback finds
        `members.date_of_birth`, but must never hand it over as an accepted
        mapping: only a human decision (via `MappingStudio`'s manual-add path)
        can turn this into part of a draft."""

        class SaysUnknown:
            model_id = "test"

            def complete_json(self, *, system, user, response_model=None):
                return {
                    "fields": [
                        {
                            "source": "dateofbirth",
                            "target": None,
                            "confidence": 0.0,
                            "evidence": ["no evidence"],
                            "status": "unknown",
                        }
                    ],
                    "notes": [],
                }

        content = b"dateofbirth\n1997-11-04\n2013-11-04\n"
        facts = facts_for(content, s)
        result = graph_with(SaysUnknown(), s).run(
            facts=facts, source_system="fidelis_ny_upstate", feed="member_roster", domain=DOMAIN
        )
        field = next(f for f in result["content"].fields if f.source == "dateofbirth")
        assert field.status == "ambiguous"
        assert field.target == "members.date_of_birth"
        assert field.confidence <= 0.4
        assert any(e.startswith("semantic:") for e in field.evidence)

    def test_a_column_nothing_can_place_stays_unknown(self, s):
        class SaysUnknown:
            model_id = "test"

            def complete_json(self, *, system, user, response_model=None):
                return {
                    "fields": [
                        {
                            "source": "provider_taxonomy_code",
                            "target": None,
                            "confidence": 0.0,
                            "evidence": ["no evidence"],
                            "status": "unknown",
                        }
                    ],
                    "notes": [],
                }

        content = b"provider_taxonomy_code\n207Q00000X\n363LP0808X\n"
        facts = facts_for(content, s)
        result = graph_with(SaysUnknown(), s).run(
            facts=facts, source_system="fidelis_ny_upstate", feed="member_roster", domain=DOMAIN
        )
        field = next(f for f in result["content"].fields if f.source == "provider_taxonomy_code")
        assert field.status == "unknown"
        assert field.target is None


# =============================================================================
# Provenance: the new sources are cited like every other governed source.
# =============================================================================


def test_precedents_and_semantic_citations_are_recorded_in_provenance(s):
    content = b"member_id,dateofbirth\nM001,1997-11-04\nM002,2013-11-04\n"
    facts = facts_for(content, s)
    result = graph_with(StubClient(), s).run(
        facts=facts, source_system="fidelis_ny_upstate", feed="member_roster", domain=DOMAIN
    )
    knowledge = result["knowledge"]
    assert any(c.startswith("decisions/analyst_decisions.yaml@") for c in knowledge)
    assert any(c.startswith("semantic/lexical_v1") for c in knowledge)
