"""The mapping graph must be safe to listen to: governed knowledge decides what a
proposal may say, and anything the model invents is visible rather than silent."""

from __future__ import annotations

from pathlib import Path

import pytest

from cinqflow.engine import profiler
from cinqflow.engine.parsers import parse
from cinqflow.intelligence.context import ContextBuilder
from cinqflow.intelligence.graphs.recommend_mapping import RecommendMappingGraph
from cinqflow.intelligence.llm import StubClient
from cinqflow.knowledge.yaml_provider import YamlKnowledgeProvider
from cinqflow.settings import Settings

KNOWLEDGE_ROOT = Path(__file__).resolve().parents[3] / "knowledge"
DOMAIN = "enrollment"


@pytest.fixture
def s(tmp_path) -> Settings:
    return Settings(landing_root=tmp_path, knowledge_root=KNOWLEDGE_ROOT, llm_provider="stub")


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


def graph_with(llm, s) -> RecommendMappingGraph:
    return RecommendMappingGraph(
        context_builder=ContextBuilder(YamlKnowledgeProvider(s)), llm=llm
    )


# ------------------------------------------------------------------- knowledge


def test_legal_targets_come_from_the_canonical_ddl(s):
    targets = ContextBuilder(YamlKnowledgeProvider(s)).legal_targets(DOMAIN)

    # present in enrollment_silver_raw_model.sql
    assert "members.source_system_id" in targets
    assert "members.date_of_birth" in targets
    assert "members.sex" in targets
    assert "members_addresses.city" in targets
    assert "members_enrollment_segments.lob" in targets

    # the DDL calls it `sex`; `gender` is only a spreadsheet term
    assert "members.gender" not in targets
    # contested fields other documents use but the DDL lacks
    assert "members.guardian_first_name" not in targets
    assert "members.feed_name" not in targets
    # platform-populated columns are never mapping targets
    for system_column in ("record_hash", "batch_id", "created_at", "source_system"):
        assert f"members.{system_column}" not in targets


def test_context_carries_governed_knowledge_and_no_sample_rows(s, roster_facts):
    job = ContextBuilder(YamlKnowledgeProvider(s)).for_mapping(
        facts=roster_facts,
        source_system="fidelis_ny_upstate",
        feed="member_roster",
        domain=DOMAIN,
    )

    assert "sample_rows" not in job.observations
    assert {"canonical", "source", "glossary", "history", "domain_knowledge"} <= set(job.context)
    assert any(c.startswith("canonical/enrollment.yaml@") for c in job.citations)
    assert any(c.startswith("mappings/approved@") for c in job.citations)
    assert any(c.startswith("domains/enrollment.yaml@") for c in job.citations)
    # history is exemplars from the client's own spreadsheets
    sets = job.context["history"]["decision_sets"]
    assert any("MSSP" in str(entry.get("source", "")) for entry in sets)


def test_domain_knowledge_carries_grain_rules_and_known_gaps_not_targets(s, roster_facts):
    """Domain knowledge explains why a column may have no home; it must never
    itself look like a place to widen the legal target list."""
    job = ContextBuilder(YamlKnowledgeProvider(s)).for_mapping(
        facts=roster_facts,
        source_system="fidelis_ny_upstate",
        feed="member_roster",
        domain=DOMAIN,
    )
    domain_knowledge = job.context["domain_knowledge"]
    assert {"citation", "what_it_answers", "grain_rules", "known_gaps", "failure_modes"} == set(
        domain_knowledge
    )
    assert domain_knowledge["grain_rules"]
    # the real, grounded reason a status-like column has no canonical home
    assert any("care_management_program" in gap for gap in domain_knowledge["known_gaps"])
    assert "entities" not in domain_knowledge
    assert "fields" not in domain_knowledge


def test_domain_knowledge_is_absent_without_crashing_for_an_ungoverned_domain(s, roster_facts):
    job = ContextBuilder(YamlKnowledgeProvider(s)).for_mapping(
        facts=roster_facts,
        source_system="fidelis_ny_upstate",
        feed="member_roster",
        domain="no_such_domain",
    )
    assert "domain_knowledge" not in job.context


# -------------------------------------------------------------------- proposal


def test_proposal_maps_what_knowledge_supports_and_admits_the_rest(s, roster_facts):
    result = graph_with(StubClient(), s).run(
        facts=roster_facts,
        source_system="fidelis_ny_upstate",
        feed="member_roster",
        domain=DOMAIN,
    )
    assert result["status"] == "proposed"
    by_source = {f.source: f for f in result["content"].fields}

    # glossary-backed mappings land on real DDL fields
    assert by_source["member_id"].target == "members.source_system_id"
    assert by_source["member_dob"].target == "members.date_of_birth"
    assert by_source["member_sex"].target == "members.sex"
    assert by_source["member_city"].target == "members_addresses.city"
    assert by_source["product"].target == "members_enrollment_segments.lob"
    assert all(by_source[c].evidence for c in by_source)

    # columns with no canonical home are admitted, not invented
    assert by_source["harp_eligible"].target is None
    assert by_source["harp_eligible"].status == "unknown"
    assert by_source["recertification_end_date"].target is None
    assert result["content"].notes


def test_every_observed_column_appears_exactly_once(s, roster_facts):
    result = graph_with(StubClient(), s).run(
        facts=roster_facts,
        source_system="fidelis_ny_upstate",
        feed="member_roster",
        domain=DOMAIN,
    )
    proposed = [f.source for f in result["content"].fields]
    observed = [c.name for c in roster_facts.columns]
    assert sorted(proposed) == sorted(observed)


def test_date_into_timestamp_gets_a_named_transform(s, roster_facts):
    result = graph_with(StubClient(), s).run(
        facts=roster_facts,
        source_system="fidelis_ny_upstate",
        feed="member_roster",
        domain=DOMAIN,
    )
    dob = next(f for f in result["content"].fields if f.source == "member_dob")
    assert dob.transform is not None
    assert dob.transform.op == "parse_date"


def test_provenance_records_prompt_model_and_knowledge(s, roster_facts):
    result = graph_with(StubClient(), s).run(
        facts=roster_facts,
        source_system="fidelis_ny_upstate",
        feed="member_roster",
        domain=DOMAIN,
    )
    assert result["prompt"] == "recommend_mapping@3"
    assert result["model"] == "stub-reasoner-1"
    assert any("canonical/enrollment.yaml" in c for c in result["knowledge"])


def test_stub_reasoner_is_deterministic(s, roster_facts):
    kwargs = dict(
        facts=roster_facts,
        source_system="fidelis_ny_upstate",
        feed="member_roster",
        domain=DOMAIN,
    )
    a = graph_with(StubClient(), s).run(**kwargs)["content"]
    b = graph_with(StubClient(), s).run(**kwargs)["content"]
    assert a.model_dump() == b.model_dump()


# ------------------------------------------------------- adversarial validation


class _Fabricator:
    """A model that invents a canonical field that does not exist."""

    model_id = "adversarial"

    def complete_json(self, *, system, user, response_model=None):
        return {
            "fields": [
                {
                    "source": "member_id",
                    "target": "members.member_uuid",  # not in the DDL
                    "confidence": 0.97,
                    "evidence": ["looks like an identifier"],
                    "status": "candidate",
                },
                {
                    "source": "member_dob",
                    "target": "members.date_of_birth",  # real
                    "confidence": 0.9,
                    "evidence": ["glossary:DOB"],
                    "status": "candidate",
                },
            ],
            "notes": [],
        }


def test_fabricated_target_is_rejected_and_persisted_as_invalid(s, roster_facts):
    """The proposal fails validation and says so - it is not silently corrected."""
    result = graph_with(_Fabricator(), s).run(
        facts=roster_facts,
        source_system="fidelis_ny_upstate",
        feed="member_roster",
        domain=DOMAIN,
    )

    assert result["status"] == "invalid"
    fabricated = next(f for f in result["content"].fields if f.source == "member_id")
    assert fabricated.status == "invalid"
    assert fabricated.target is None  # nothing landable was kept
    assert fabricated.rejected_target == "members.member_uuid"  # but it is on the record
    assert "not a field in the canonical model" in fabricated.reason

    # the legitimate candidate in the same response survives
    good = next(f for f in result["content"].fields if f.source == "member_dob")
    assert good.status == "candidate"
    assert good.target == "members.date_of_birth"


def test_system_populated_target_is_refused(s, roster_facts):
    class TargetsAuditColumn:
        model_id = "adversarial"

        def complete_json(self, *, system, user, response_model=None):
            return {
                "fields": [
                    {
                        "source": "member_id",
                        "target": "members.record_hash",
                        "confidence": 0.8,
                        "evidence": ["it is a hash-like id"],
                        "status": "candidate",
                    }
                ],
                "notes": [],
            }

    result = graph_with(TargetsAuditColumn(), s).run(
        facts=roster_facts,
        source_system="fidelis_ny_upstate",
        feed="member_roster",
        domain=DOMAIN,
    )
    field = next(f for f in result["content"].fields if f.source == "member_id")
    assert result["status"] == "invalid"
    assert field.rejected_target == "members.record_hash"


def test_hallucinated_source_column_is_discarded(s, roster_facts):
    class InventsAColumn:
        model_id = "adversarial"

        def complete_json(self, *, system, user, response_model=None):
            return {
                "fields": [
                    {
                        "source": "patient_ssn",  # never observed in Bronze
                        "target": "members.source_system_id",
                        "confidence": 0.9,
                        "evidence": ["ssn is an identifier"],
                        "status": "candidate",
                    }
                ],
                "notes": [],
            }

    content = graph_with(InventsAColumn(), s).run(
        facts=roster_facts,
        source_system="fidelis_ny_upstate",
        feed="member_roster",
        domain=DOMAIN,
    )["content"]

    assert "patient_ssn" not in [f.source for f in content.fields]
    assert any("not in Bronze" in note for note in content.notes)
    # and the real columns are still all accounted for
    assert len(content.fields) == len(roster_facts.columns)


def test_unsupported_transform_is_dropped_but_mapping_survives(s, roster_facts):
    class ExoticTransform:
        model_id = "adversarial"

        def complete_json(self, *, system, user, response_model=None):
            return {
                "fields": [
                    {
                        "source": "member_dob",
                        "target": "members.date_of_birth",
                        "transform": {
                            "op": "exec_python",
                            "args": [{"key": "code", "value": "os.system('x')"}],
                        },
                        "confidence": 0.9,
                        "evidence": ["glossary:DOB"],
                        "status": "candidate",
                    }
                ],
                "notes": [],
            }

    content = graph_with(ExoticTransform(), s).run(
        facts=roster_facts,
        source_system="fidelis_ny_upstate",
        feed="member_roster",
        domain=DOMAIN,
    )["content"]

    field = next(f for f in content.fields if f.source == "member_dob")
    assert field.target == "members.date_of_birth"
    assert field.transform is None
    assert any("exec_python" in note for note in content.notes)


def test_two_columns_claiming_one_target_become_ambiguous(s):
    """Real roster ambiguity: member_id and medicaid_id are both identifiers, but
    members.source_system_id can only hold one of them."""
    content = (
        b"member_id,medicaid_id,member_first_name\n"
        b"96863747295-FCNY4487,FZ32654H,DANIELLE\n"
        b"85069922621-FCNY9812,EY26138X,KEVIN\n"
    )
    facts = profiler.profile(parse(content, "csv"), s)
    result = graph_with(StubClient(), s).run(
        facts=facts,
        source_system="fidelis_ny_upstate",
        feed="member_roster",
        domain=DOMAIN,
    )

    contested = [
        f for f in result["content"].fields if f.target == "members.source_system_id"
    ]
    assert len(contested) == 2
    assert {f.source for f in contested} == {"member_id", "medicaid_id"}
    for field in contested:
        assert field.status == "ambiguous"
        assert "also proposed for" in field.reason
    assert any("One must win" in note for note in result["content"].notes)


def test_a_single_claimant_stays_a_candidate(s, roster_facts):
    """The ambiguity check must not punish an uncontested mapping."""
    result = graph_with(StubClient(), s).run(
        facts=roster_facts,
        source_system="fidelis_ny_upstate",
        feed="member_roster",
        domain=DOMAIN,
    )
    dob = next(f for f in result["content"].fields if f.source == "member_dob")
    assert dob.status == "candidate"
    assert dob.reason is None


def test_candidate_without_evidence_loses_its_confidence(s, roster_facts):
    class NoEvidence:
        model_id = "adversarial"

        def complete_json(self, *, system, user, response_model=None):
            return {
                "fields": [
                    {
                        "source": "member_dob",
                        "target": "members.date_of_birth",
                        "confidence": 0.99,
                        "evidence": [],
                        "status": "candidate",
                    }
                ],
                "notes": [],
            }

    content = graph_with(NoEvidence(), s).run(
        facts=roster_facts,
        source_system="fidelis_ny_upstate",
        feed="member_roster",
        domain=DOMAIN,
    )["content"]

    field = next(f for f in content.fields if f.source == "member_dob")
    assert field.confidence == 0.0
    assert field.status == "ambiguous"


# --------------------------------------------------------------------- concept


def test_concept_passes_through_for_a_matched_candidate(s, roster_facts):
    result = graph_with(StubClient(), s).run(
        facts=roster_facts,
        source_system="fidelis_ny_upstate",
        feed="member_roster",
        domain=DOMAIN,
    )
    dob = next(f for f in result["content"].fields if f.source == "member_dob")
    assert dob.status == "candidate"
    assert dob.concept == "Member's date of birth"  # the target's own governed meaning


def test_concept_passes_through_even_when_the_target_is_rejected(s, roster_facts):
    class FabricatorWithConcept:
        model_id = "adversarial"

        def complete_json(self, *, system, user, response_model=None):
            return {
                "fields": [
                    {
                        "source": "member_id",
                        "target": "members.member_uuid",  # not in the DDL
                        "concept": "A unique identifier for the member",
                        "confidence": 0.97,
                        "evidence": ["looks like an identifier"],
                        "status": "candidate",
                    }
                ],
                "notes": [],
            }

    result = graph_with(FabricatorWithConcept(), s).run(
        facts=roster_facts,
        source_system="fidelis_ny_upstate",
        feed="member_roster",
        domain=DOMAIN,
    )
    fabricated = next(f for f in result["content"].fields if f.source == "member_id")
    assert fabricated.status == "invalid"
    assert fabricated.target is None
    # the AI's understanding survives even though the target it named did not
    assert fabricated.concept == "A unique identifier for the member"


def test_concept_passes_through_for_an_ambiguous_candidate(s, roster_facts):
    class AmbiguousWithConcept:
        model_id = "adversarial"

        def complete_json(self, *, system, user, response_model=None):
            return {
                "fields": [
                    {
                        "source": "member_dob",
                        "target": "members.date_of_birth",
                        "concept": "The member's birth date",
                        "confidence": 0.6,
                        "evidence": ["two plausible readings"],
                        "status": "ambiguous",
                    }
                ],
                "notes": [],
            }

    content = graph_with(AmbiguousWithConcept(), s).run(
        facts=roster_facts,
        source_system="fidelis_ny_upstate",
        feed="member_roster",
        domain=DOMAIN,
    )["content"]
    field = next(f for f in content.fields if f.source == "member_dob")
    assert field.status == "ambiguous"
    assert field.concept == "The member's birth date"


def test_concept_is_none_for_a_column_the_model_never_addressed(s, roster_facts):
    """The code-generated backfill for an ignored column has nothing to say - a
    real understanding is an AI statement, never a placeholder's."""

    class IgnoresMostColumns:
        model_id = "adversarial"

        def complete_json(self, *, system, user, response_model=None):
            return {
                "fields": [
                    {
                        "source": "member_dob",
                        "target": "members.date_of_birth",
                        "concept": "The member's date of birth",
                        "confidence": 0.9,
                        "evidence": ["glossary:DOB"],
                        "status": "candidate",
                    }
                ],
                "notes": [],
            }

    content = graph_with(IgnoresMostColumns(), s).run(
        facts=roster_facts,
        source_system="fidelis_ny_upstate",
        feed="member_roster",
        domain=DOMAIN,
    )["content"]
    backfilled = next(f for f in content.fields if f.source == "harp_eligible")
    assert backfilled.status == "unknown"
    assert backfilled.reason == "no candidate returned for this column"
    assert backfilled.concept is None


def test_intelligence_never_reads_knowledge_files_itself():
    """No module in intelligence/ parses YAML or opens a file: knowledge arrives
    through the provider, so YAML can become a database without touching graphs."""
    intelligence = Path(__file__).resolve().parents[2] / "src/cinqflow/intelligence"
    offenders = []
    for path in intelligence.rglob("*.py"):
        text = path.read_text()
        if "import yaml" in text or "yaml.safe_load" in text or "open(" in text:
            offenders.append(str(path.relative_to(intelligence)))
    assert offenders == []


def test_graphs_do_not_know_which_provider_they_are_given():
    """Only the runtime (the composition root) may name a concrete provider."""
    intelligence = Path(__file__).resolve().parents[2] / "src/cinqflow/intelligence"
    coupled = [
        str(path.relative_to(intelligence))
        for path in [*(intelligence / "graphs").rglob("*.py"), intelligence / "context.py"]
        if "YamlKnowledgeProvider" in path.read_text()
    ]
    assert coupled == []
    assert "YamlKnowledgeProvider" in (intelligence / "runtime.py").read_text()
