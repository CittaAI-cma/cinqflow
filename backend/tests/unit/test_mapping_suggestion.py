"""CF-V1-E6-02's grounding — what the estate already decided, and what it did not.

    "AI source→target mapping with confidence + exemplars from the golden
     workbooks; UNMAPPED flagged, never guessed"
    "new payer's MBR_DOB maps like Fidelis date_of_birth did"

The three-way distinction this suite exists to protect:

  1. a GLOSSARY SYNONYM settles the line — no model;
  2. THIS FEED'S OWN published mapping settles the line — no model, because it
     is the approved decision rather than evidence about one;
  3. ANOTHER FEED'S published mapping is an EXEMPLAR — shown to the model, and
     never applied by the platform, because two payers can spell two different
     concepts the same way.

`tests/contract/test_mapping_suggestion_agent.py` runs the wired agent on Lane
1; `tests/evaluation/test_lane3_mapping_suggestion.py` measures blind
re-derivation against a real model.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from cinqflow.core.agents.mapping_suggestion import Exemplar, ground
from cinqflow.core.mapping import FeedMapping, MappingLine
from cinqflow.core.registry.canonical import build
from cinqflow.core.registry.contract import ContractColumn, SchemaContract
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm
from cinqflow.core.schema_spec import Column, Schema, Table, TypeName

pytestmark = pytest.mark.unit

DEPLOYED = Schema(
    name="silver_ods",
    description="test",
    tables=(
        Table(
            name="members",
            comment="The member, canonically.",
            columns=(
                Column("member_row_id", TypeName.UUID, nullable=False),
                Column("date_of_birth", TypeName.DATE, is_phi=True),
                Column("line_of_business", TypeName.STRING),
                Column("relationship_code", TypeName.STRING),
            ),
            primary_key=("member_row_id",),
        ),
    ),
)

GLOSSARY = Glossary(
    terms=(
        GlossaryTerm(
            glossary_id="BG-004",
            term="Member Date of Birth",
            definition="Date of birth of the member, used for age and eligibility.",
            mapped_domains=("Enrollment",),
            mapped_tables=("members",),
            mapped_columns_original=("DOB", "Patient_dob", "MBR_DOB"),
            mapped_columns_corrected=("date_of_birth",),
            is_phi=True,
        ),
        GlossaryTerm(
            glossary_id="BG-050",
            term="Line of Business",
            definition="The product line a member is enrolled under.",
            mapped_domains=("Enrollment",),
            # TWO tables, deliberately: the term does not say which one a
            # column lands in, so the platform declines to settle it.
            mapped_tables=("members", "claim_ipheader"),
            mapped_columns_original=("LOB",),
            mapped_columns_corrected=("line_of_business",),
        ),
    )
)

MODEL = build((DEPLOYED,), GLOSSARY)


def _contract(*columns: str) -> SchemaContract:
    return SchemaContract(
        feed_id="uhc-optum-ny",
        version=1,
        columns=tuple(
            ContractColumn(name.lower(), TypeName.STRING, source_name=name) for name in columns
        ),
    )


def _published(feed_id: str, source: str, entity: str, field: str) -> FeedMapping:
    return FeedMapping(
        feed_id=feed_id,
        version=2,
        lines=(MappingLine(target_entity=entity, target_field=field, source_columns=(source,)),),
    )


def _ground(contract: SchemaContract, **kwargs: object):  # type: ignore[no-untyped-def]
    return ground(
        contract,
        feed_id="uhc-optum-ny",
        glossary=GLOSSARY,
        model=MODEL,
        **kwargs,  # type: ignore[arg-type]
    )


# ── 1 · the glossary settles, and costs nothing ─────────────────────────────


def test_a_glossary_synonym_settles_a_column_with_no_model() -> None:
    """`BG-004` records that DOB, Patient_dob and MBR_DOB all carry Member Date
    of Birth. Written by the client's own analysts — there is nothing to ask."""
    grounding = _ground(_contract("MBR_DOB"))
    column = grounding.column("MBR_DOB")

    assert column is not None
    assert column.settled and column.settled_by == "glossary"
    assert (column.target_entity, column.target_field) == ("members", "date_of_birth")
    assert grounding.needs_no_model


def test_a_feed_the_glossary_names_entirely_costs_zero_tokens() -> None:
    """The economics of the story on an invoice rather than in a docstring."""
    assert _ground(_contract("MBR_DOB", "DOB")).needs_no_model


def test_a_term_naming_two_tables_does_not_settle_where_the_column_lands() -> None:
    """`BG-050` names members AND claim_ipheader. Picking the first would map a
    claims column into the roster — so the platform declines and the model is
    asked, with the definition in front of it."""
    grounding = _ground(_contract("LOB"))
    column = grounding.column("LOB")

    assert column is not None
    assert not column.settled
    assert any("BG-050" in line for line in column.evidence)
    assert not grounding.needs_no_model


def test_a_column_two_terms_claim_is_an_open_question_not_a_coin_toss() -> None:
    ambiguous = Glossary(
        terms=(
            GlossaryTerm(
                glossary_id="BG-100",
                term="Status",
                definition="Enrollment status.",
                mapped_tables=("members",),
                mapped_columns_original=("STATUS",),
                mapped_columns_corrected=("enrollment_status",),
            ),
            GlossaryTerm(
                glossary_id="BG-101",
                term="Claim Status",
                definition="Adjudication status of the claim.",
                mapped_tables=("members",),
                mapped_columns_original=("STATUS",),
                mapped_columns_corrected=("claim_status",),
            ),
        )
    )
    grounding = ground(
        _contract("STATUS"),
        feed_id="uhc-optum-ny",
        glossary=ambiguous,
        model=MODEL,
    )
    column = grounding.column("STATUS")
    assert column is not None and not column.settled
    assert any("2 glossary terms claim" in line for line in column.evidence)


# ── 2 · this feed's own approved mapping IS the decision ────────────────────


def test_this_feeds_own_published_mapping_settles_the_line() -> None:
    """Not evidence — the decision. Re-proposing it would ask a steward to
    re-sign what they signed in March."""
    grounding = _ground(
        _contract("SUBSCR_REL_CD"),
        published_mappings=(
            _published("uhc-optum-ny", "SUBSCR_REL_CD", "members", "relationship_code"),
        ),
    )
    column = grounding.column("SUBSCR_REL_CD")

    assert column is not None
    assert column.settled and column.settled_by == "published_mapping"
    assert column.target_field == "relationship_code"
    assert "already mapped and approved" in column.evidence[0]
    assert grounding.needs_no_model


def test_the_feeds_own_mapping_is_cited_so_a_reviewer_can_open_it() -> None:
    grounding = _ground(
        _contract("SUBSCR_REL_CD"),
        published_mappings=(
            _published("uhc-optum-ny", "SUBSCR_REL_CD", "members", "relationship_code"),
        ),
    )
    column = grounding.column("SUBSCR_REL_CD")
    assert column is not None
    assert [str(c) for c in column.citations] == ["mapping:uhc-optum-ny@v2"]


# ── 3 · another feed's mapping is an exemplar, never an answer ──────────────


def test_another_feeds_mapping_becomes_an_exemplar_not_a_target() -> None:
    """THE STORY'S HEADLINE, and its limit. That Fidelis maps SUBSCR_REL_CD to
    relationship_code is strong evidence about UnitedHealth's column and is not
    proof — two payers can spell two different concepts the same way. So the
    line stays OPEN and the precedent goes to the model."""
    grounding = _ground(
        _contract("SUBSCR_REL_CD"),
        published_mappings=(
            _published("fidelis-downstate-roster", "SUBSCR_REL_CD", "members", "relationship_code"),
        ),
        approvers={"fidelis-downstate-roster": "ola@cinqcare.test"},
    )
    column = grounding.column("SUBSCR_REL_CD")

    assert column is not None
    assert not column.settled, "a precedent from another feed must not auto-apply"
    assert len(column.exemplars) == 1
    assert column.exemplars[0].feed_id == "fidelis-downstate-roster"
    assert column.exemplars[0].target_field == "relationship_code"


def test_an_exemplar_carries_who_approved_it_to_the_reviewer_only() -> None:
    """What makes it PRECEDENT rather than similarity: a reviewer reading "Ola
    approved this on the Fidelis feed" can go and ask Ola.

    And the model is told only that it WAS approved. The gateway's scrubber
    found this before I did — it redacted the address out of the prompt and the
    test asserting the name arrived failed. It was right: a colleague's
    identifier in a vendor prompt buys nothing.
    """
    grounding = _ground(
        _contract("SUBSCR_REL_CD"),
        published_mappings=(
            _published("fidelis-downstate-roster", "SUBSCR_REL_CD", "members", "relationship_code"),
        ),
        approvers={"fidelis-downstate-roster": "ola@cinqcare.test"},
    )
    column = grounding.column("SUBSCR_REL_CD")
    assert column is not None
    assert "approved by ola@cinqcare.test" in column.exemplars[0].as_review_line()
    assert "ola@cinqcare.test" not in column.exemplars[0].as_line()
    assert column.exemplars[0].as_line().endswith("approved")
    assert "ola@cinqcare.test" not in grounding.as_prompt_grounding()


def test_an_unmapped_line_on_another_feed_is_not_an_exemplar() -> None:
    """Somebody deciding a field has no source is a decision about THAT feed.
    Offering it as precedent would propagate a gap."""
    grounding = _ground(
        _contract("SUBSCR_REL_CD"),
        published_mappings=(
            FeedMapping(
                feed_id="fidelis-downstate-roster",
                lines=(
                    MappingLine(
                        target_entity="members",
                        target_field="relationship_code",
                        unmapped_reason="Fidelis does not send a relationship code.",
                    ),
                ),
            ),
        ),
    )
    column = grounding.column("SUBSCR_REL_CD")
    assert column is not None and column.exemplars == ()


def test_exemplars_match_the_exact_spelling_not_a_similar_one() -> None:
    """A precedent's whole value is that it is the same decision about the same
    thing. "These two names are 80% similar" is a guess wearing precedent's
    clothes."""
    grounding = _ground(
        _contract("SUBSCRIBER_REL"),
        published_mappings=(
            _published("fidelis-downstate-roster", "SUBSCR_REL_CD", "members", "relationship_code"),
        ),
    )
    column = grounding.column("SUBSCRIBER_REL")
    assert column is not None and column.exemplars == ()


# ── the grounding shows a target vocabulary, and no values ──────────────────


def test_the_canonical_model_is_in_the_grounding() -> None:
    """A model asked where MBR_DOB goes, with no target list in front of it,
    invents `patient.dob`. Showing the list is what "cited exemplars" means."""
    text = _ground(_contract("LOB")).as_prompt_grounding()
    assert "[members] [date_of_birth]" in text
    assert "[members] [relationship_code]" in text


def test_deployed_fields_are_listed_before_designed_ones() -> None:
    """The list is truncated for long models. A target that exists today is the
    better default, and burying it below three hundred designed ones would
    invert that."""
    designed = Glossary(
        terms=(
            *GLOSSARY.terms,
            GlossaryTerm(
                glossary_id="BG-900",
                term="Care Gap Code",
                definition="An open quality measure.",
                mapped_tables=("care_gap",),
                mapped_columns_corrected=("gap_code",),
            ),
        )
    )
    entries = ground(
        _contract("LOB"), feed_id="f", glossary=designed, model=build((DEPLOYED,), designed)
    ).vocabulary.entries
    deployed_last = max(i for i, (e, _, _) in enumerate(entries) if e == "members")
    designed_first = min(i for i, (e, _, _) in enumerate(entries) if e == "care_gap")
    assert deployed_last < designed_first


def test_the_grounding_carries_no_sample_values() -> None:
    """Unlike CF-V1-E5-02's, this grounding has no `examples` line — and the
    reason is not only protection. Mapping is a name-to-concept decision;
    values do not help, so carrying them would leak for nothing."""
    text = _ground(_contract("LOB", "MBR_DOB")).as_prompt_grounding()
    for leak in ("example", "sample", "value:"):
        assert leak not in text.lower(), f"the mapping grounding must not carry {leak!r}"


def test_the_settled_columns_are_named_for_consistency_but_not_re_asked() -> None:
    text = _ground(_contract("MBR_DOB", "LOB")).as_prompt_grounding()
    assert "[MBR_DOB] -> [members] [date_of_birth]" in text
    assert "Source columns needing a target:\n- source column [LOB]" in text


# ── the deterministic nodes reach no model ──────────────────────────────────


def test_the_deterministic_nodes_never_reach_the_gateway() -> None:
    """Asserted by walking the AST, not by reading the code. A comment saying
    "this node calls no model" is a comment; a test that fails when someone
    adds `self.llm.complete` is a control."""
    from cinqflow.intelligence.agents import mapping_suggestion as wired

    tree = ast.parse(inspect.getsource(wired))
    bodies = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in {"_ground", "_assemble"}
    }
    assert set(bodies) == {"_ground", "_assemble"}, "both deterministic nodes must exist"

    for name, node in bodies.items():
        attributes = {child.attr for child in ast.walk(node) if isinstance(child, ast.Attribute)}
        assert "llm" not in attributes, f"{name} reaches the gateway"
        assert "complete" not in attributes, f"{name} calls a model"


def test_the_agent_module_never_saves_a_governed_object() -> None:
    """ "No agent path writes production state." The strongest form available
    without running it: the wired module contains no call to `metadata.save`,
    so there is no path — not a guarded one, not a dead one."""
    from cinqflow.intelligence.agents import mapping_suggestion as wired

    source = inspect.getsource(wired)
    assert "metadata.save" not in source
    assert ".save(" not in source


# ── the grounding must survive the scrubber that stands in front of it ──────


def test_no_address_in_the_grounding_is_written_with_a_dot() -> None:
    """THE BUG THE LANE-3 GATE FOUND, as a cheap deterministic test.

    The PHI scrubber sits between every prompt and the model, and `a.b` is the
    shape of a hostname: Presidio rewrote `claim_header.source_claim_id` to
    `claim_<URL>urce_claim_id`. The model then faithfully copied back the
    mangled name, and the platform refused every single one of its targets as
    "not in the canonical model" — two correct components and one unusable
    agent.

    Asserted on the RENDERED grounding rather than on the helper, so a new line
    added to `as_prompt_grounding` that reintroduces the dot fails here. The
    numbering in `TargetVocabulary` is what makes a redaction survivable; this
    is what keeps them rare.
    """
    grounding = _ground(
        _contract("SUBSCR_REL_CD"),
        published_mappings=(
            _published("fidelis-downstate-roster", "SUBSCR_REL_CD", "members", "relationship_code"),
        ),
    )
    text = grounding.as_prompt_grounding()

    for entity in ("members", "claim_ipheader"):
        assert f"{entity}." not in text, (
            f"{entity}.<field> is a hostname to a URL recogniser; write "
            f"'{entity} / <field>' instead"
        )
    assert "[members] [relationship_code]" in text
    assert "[members] [date_of_birth]" in text


def test_the_reviewers_rendering_keeps_the_dotted_address() -> None:
    """Nothing stands between that string and a person, and `entity.field` is
    what an engineer reads. The two renderings differ on purpose."""
    exemplar = Exemplar(
        feed_id="fidelis-downstate-roster",
        source_column="SUBSCR_REL_CD",
        target_entity="members",
        target_field="relationship_code",
        transform="direct",
        approved_by="ola@cinqcare.test",
    )
    assert "members.relationship_code" in exemplar.as_review_line()
    assert "[members] [relationship_code]" in exemplar.as_line()


def test_the_target_list_is_numbered_and_the_number_resolves() -> None:
    """THE GUARANTEE, after two renderings failed to be one.

    No layout keeps `date_of_birth` intact beside the sentence "Date of birth
    of the member." — the scrubber is right about it, and it is the estate's
    most important field name. So the answer the model gives is a NUMBER, and a
    redacted name costs a little context instead of every proposal for the
    whole feed.
    """
    vocabulary = _ground(_contract("LOB")).vocabulary
    text = vocabulary.as_text()

    assert "1 = [" in text, "the list a model chooses from must be numbered"
    for index, (entity, field, _) in enumerate(vocabulary.entries, start=1):
        assert vocabulary.target(index) == (entity, field)
    assert vocabulary.target(0) is None
    assert vocabulary.target(len(vocabulary.entries) + 1) is None
