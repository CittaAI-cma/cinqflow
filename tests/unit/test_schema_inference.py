"""CF-V1-E5-02 — AI schema inference, and the facts it is not allowed to touch.

    "eval red until >= 90% fields accepted without correction · ungroundable
     column -> 'needs your input', never silently typed · contract emitted
     machine-enforceable · corrections captured to eval set"
    "PHI scrub verified pre-prompt · human approval always"
    — CINQFLOW_Wave_Implementation_Blueprint.md §4.1

The grounding half is pure arithmetic over a real profile and the client's real
glossary rows, so it is tested here with no model anywhere. The wired agent is
tested with a scripted model (Lane 1) — which proves MACHINERY and, per the
standing rule, no quality claim whatsoever.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from cinqflow.core.agents.schema_inference import (
    AGENT,
    CONFIDENCE_FLOOR,
    DETERMINISTIC_NODES,
    NODE_ASSEMBLE,
    NODE_GROUND,
    NODE_INFER,
    RISK_CLASS,
    ground,
    merge,
)
from cinqflow.core.model.vocabulary import RiskClass
from cinqflow.core.profiling import profile_bytes
from cinqflow.core.registry.glossary import Glossary, GlossaryTerm
from cinqflow.core.schema_spec import TypeName

pytestmark = pytest.mark.unit

#: The Fidelis roster's real column names, with the values the simulator
#: generates. `DOB` is the interesting one: eight digits that are also a date.
ROSTER = (
    b"MemberID,First_Name,DOB,LOB,SUBSCR_REL_CD\n"
    b"MBR000001,FIRST000001,19360201,MEDICAID,01\n"
    b"MBR000002,,19370302,MEDICARE,02\n"
    b"MBR000003,FIRST000003,19380403,DUAL,01\n"
)

#: Real rows, copied from `Data lake data model.xlsx`. BG-004 genuinely records
#: `DOB` among its spellings, which is why the platform can name that column
#: without asking anybody.
BG_004 = GlossaryTerm(
    glossary_id="BG-004",
    term="Member Date of Birth",
    definition="Date of birth of the member, used for age calculations and eligibility.",
    mapped_columns_original=("Date_of_Birth", "Patient_dob", "DOB", "MemberDateOfBirth"),
    mapped_columns_corrected=("Member_Date_Of_Birth",),
    is_phi=True,
)
BG_001 = GlossaryTerm(
    glossary_id="BG-001",
    term="Member Internal Identifier",
    definition="The platform's internal identifier for a member.",
    mapped_columns_original=("MemberID", "Member_Id"),
    mapped_columns_corrected=("Source_Member_Id",),
    is_phi=True,
)
BG_050 = GlossaryTerm(
    glossary_id="BG-050",
    term="Line of Business",
    definition="The product line a member is enrolled under.",
    mapped_columns_original=("LOB",),
    mapped_columns_corrected=("Line_Of_Business",),
    is_phi=False,
)

GLOSSARY = Glossary(terms=(BG_001, BG_004, BG_050))


@pytest.fixture
def grounding():  # type: ignore[no-untyped-def]
    profile = profile_bytes(ROSTER, file_format="csv", source_fingerprint="sha256-aaa")
    return ground(profile, feed_id="fidelis-downstate-roster", glossary=GLOSSARY)


# ── the risk class, which never moves ────────────────────────────────────────


def test_the_agent_is_r2_and_a_config_proposal() -> None:
    """R2 · config_proposal: proposes a change as a reviewable diff. Not R3
    (acts within an envelope) and not R1 (suggests and is ignored)."""
    assert RISK_CLASS is RiskClass.R2
    assert RISK_CLASS.automatable is True
    assert RISK_CLASS.at_confidence(0.99) is RiskClass.R2


# ── the deterministic half ───────────────────────────────────────────────────


def test_a_column_the_glossary_names_and_the_types_settle_needs_no_model(grounding) -> None:  # type: ignore[no-untyped-def]
    """`LOB` is a string in every row and `BG-050` names it. There is nothing
    to ask, so nothing is asked — and nothing is paid."""
    lob = grounding.column("LOB")
    assert lob is not None
    assert lob.settled is True
    assert lob.type is TypeName.STRING
    assert lob.name == "line_of_business"
    assert lob.glossary_id == "BG-050"


def test_a_compact_date_stays_an_open_question_even_though_the_glossary_names_it(
    grounding,  # type: ignore[no-untyped-def]
) -> None:
    """`DOB` is named by BG-004 but `19360201` fits both `date` and `int64`.
    Half-settled is still a question — and the model is told exactly which
    half."""
    dob = grounding.column("DOB")
    assert dob is not None
    assert dob.name == "member_date_of_birth", "the glossary settled the NAME"
    assert dob.type is None, "the arithmetic did not settle the TYPE"
    assert dob.settled is False
    assert dob.missing == ("type",)


def test_a_column_no_glossary_term_claims_is_an_open_question(grounding) -> None:  # type: ignore[no-untyped-def]
    """`SUBSCR_REL_CD` is exactly the column this agent exists for: a payer's
    abbreviation nobody has written down."""
    column = grounding.column("SUBSCR_REL_CD")
    assert column is not None
    assert column.name is None
    assert column.glossary_id is None
    assert "name" in column.missing


def test_the_phi_flag_comes_from_the_glossary_on_the_way_down(grounding) -> None:  # type: ignore[no-untyped-def]
    """Three things read this flag — the masking policy, the detection gate and
    the vector store's PHI-absence guarantee. None of them should be reading a
    model's opinion."""
    assert grounding.column("MemberID").is_phi is True  # type: ignore[union-attr]
    assert grounding.column("DOB").is_phi is True  # type: ignore[union-attr]
    assert grounding.column("LOB").is_phi is False  # type: ignore[union-attr]


def test_nullability_is_not_guessed_from_the_sample(grounding) -> None:
    """A sample cannot establish a NOT NULL constraint.

    The obvious rule — no nulls in the sample, so propose NOT NULL — is wrong
    in the expensive direction: the pipeline quarantines every row arriving
    with that field empty, so a constraint inferred from 200 rows starts
    dropping real members the first month a payer omits a middle name. The
    null COUNT travels as evidence; the constraint arrives with the key columns
    the approver declares.
    """
    assert all(c.nullable is True for c in grounding.columns)
    member = grounding.column("MemberID")
    assert member is not None
    assert any("nulls 0" in line for line in member.evidence), "the count is still reported"


def test_every_grounded_column_carries_an_openable_citation(grounding) -> None:  # type: ignore[no-untyped-def]
    """The agent interprets facts; a reviewer must be able to open the fact."""
    dob = grounding.column("DOB")
    assert dob is not None
    addresses = [str(c) for c in dob.citations]
    assert any(a.startswith("profile:sha256-") and a.endswith("#DOB") for a in addresses)
    assert "term:member-date-of-birth" in addresses


def test_two_glossary_terms_claiming_one_column_is_a_question_not_a_coin_toss() -> None:
    """Resolving it silently would pick a business meaning on somebody's
    behalf — the exact thing CF-V1-E6-02 is also forbidden to do."""
    rival = GlossaryTerm(
        glossary_id="BG-999",
        term="Date Of Birth Reported",
        definition="A self-reported date of birth, unverified.",
        mapped_columns_original=("DOB",),
        is_phi=True,
    )
    profile = profile_bytes(ROSTER, file_format="csv")
    grounded = ground(
        profile,
        feed_id="f",
        glossary=Glossary(terms=(BG_001, BG_004, BG_050, rival)),
    )
    dob = grounded.column("DOB")
    assert dob is not None
    assert dob.name is None, "no single term settles it"
    assert any("2 glossary terms claim this column" in line for line in dob.evidence)


def test_the_grounding_text_carries_only_the_open_questions(grounding) -> None:  # type: ignore[no-untyped-def]
    """Sending the settled columns would pay tokens for answers the platform
    already has, and hand the model a chance to disagree with arithmetic."""
    text = grounding.as_prompt_grounding()
    assert "SUBSCR_REL_CD" in text
    assert "'LOB'" not in text, "a settled column is not an open question"
    assert "LOB->line_of_business" in text, "though it is listed for naming consistency"


def test_a_file_of_wholly_settled_columns_asks_nothing() -> None:
    """The economics of the story, as a property: a payer who names things
    sensibly costs zero tokens."""
    settled = b"MemberID,LOB\nMBR000001,MEDICAID\nMBR000002,MEDICARE\n"
    grounded = ground(profile_bytes(settled, file_format="csv"), feed_id="f", glossary=GLOSSARY)
    assert grounded.needs_no_model is True
    assert grounded.open_questions == ()


# ── what the platform refuses to take from a model ───────────────────────────


def test_a_model_may_not_clear_a_glossary_phi_flag(grounding) -> None:  # type: ignore[no-untyped-def]
    """ "Never downgrade a glossary-flagged PHI field." Enforced here, where the
    downgrade first becomes possible — and REPORTED, because an agent that
    tried is a governance event, not a log line."""
    dob = grounding.column("DOB")
    assert dob is not None
    merged, refusals = merge(dob, {"type": "date", "is_phi": False, "confidence": 0.99})

    assert merged.is_phi is True
    assert any("clearing the PHI flag" in r for r in refusals)


def test_a_model_may_raise_a_phi_flag(grounding) -> None:  # type: ignore[no-untyped-def]
    """Requiring approval to protect MORE data would be a control that punishes
    caution."""
    column = grounding.column("SUBSCR_REL_CD")
    assert column is not None
    merged, refusals = merge(column, {"name": "subscriber_relationship_code", "is_phi": True})
    assert merged.is_phi is True
    assert refusals == ()


def test_the_computation_wins_when_a_model_contradicts_it() -> None:
    """ "NEVER contradict a computed fact" is in the prompt; this is what
    happens when the model does it anyway."""
    profile = profile_bytes(b"amt\n12.50\n3.75\n", file_format="csv")
    grounded = ground(profile, feed_id="f", glossary=GLOSSARY)
    amount = grounded.column("amt")
    assert amount is not None
    assert amount.type is TypeName.DECIMAL

    merged, refusals = merge(amount, {"name": "amount", "type": "string", "confidence": 0.9})
    assert merged.type is TypeName.DECIMAL
    assert any("The computation wins" in r for r in refusals)


# ── the shape's own guarantees ───────────────────────────────────────────────


def test_two_of_the_three_nodes_are_declared_deterministic() -> None:
    assert {NODE_GROUND, NODE_ASSEMBLE} == DETERMINISTIC_NODES
    assert NODE_INFER not in DETERMINISTIC_NODES


def test_the_deterministic_nodes_never_reach_the_gateway() -> None:
    """Asserted by walking the AST, not by reading the code.

    A comment saying "this node calls no model" is a comment; a test that
    fails when someone adds `self.llm.complete` is a control.
    """
    from cinqflow.intelligence.agents import schema_inference as wired

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
    """ "No agent path writes production state."

    The strongest form available without running it: the wired module contains
    no call to `metadata.save` at all, so there is no path — not a guarded
    one, not a dead one.
    """
    source = Path(
        Path(__file__).parent.parent.parent
        / "src"
        / "cinqflow"
        / "intelligence"
        / "agents"
        / "schema_inference.py"
    ).read_text()
    assert ".save(" not in source
    assert "record_transition" not in source
    assert "record_proposal" in source, "the one thing it does write"


def test_the_confidence_floor_lives_in_the_platform_not_the_prompt() -> None:
    """A model asked to self-censor at a number will report that number. The
    threshold has to be somewhere the model cannot see."""
    from cinqflow.core.agents.schema_inference.prompts import INFER

    assert 0 < CONFIDENCE_FLOOR < 1
    assert str(CONFIDENCE_FLOOR) not in "".join(INFER.sections.values())


def test_the_prompt_tells_the_model_that_declining_is_correct() -> None:
    """An agent with no way to decline has one way to answer a column it cannot
    read, and that way is a guess."""
    from cinqflow.core.agents.schema_inference.prompts import INFER
    from cinqflow.core.prompts import PromptSection

    constraints = INFER.sections[PromptSection.CONSTRAINTS]
    assert "DECLINING IS A CORRECT ANSWER" in constraints
    assert "NEVER contradict a computed fact" in constraints
    assert "never set `is_phi` to false" in constraints.lower().replace(
        "you may never set it to false", "never set `is_phi` to false"
    ), "the PHI-downgrade refusal must be stated in the prompt, not only enforced in code"


def test_the_prompt_is_a_registry_object_with_constraints() -> None:
    """A prompt with no constraints is a prompt with no refusals — refused by
    `PromptTemplate` itself, and asserted here so the agent's own template
    cannot regress to one."""
    from cinqflow.core.agents.schema_inference.prompts import TEMPLATES
    from cinqflow.core.prompts import PromptSection

    assert len(TEMPLATES) == 1, "one call, one template — a router here would buy nothing"
    for template in TEMPLATES:
        assert template.sections[PromptSection.CONSTRAINTS].strip()
        assert template.temperature == 0.0, "structured output at temperature is a coin toss"
        assert template.response_schema is not None
        assert template.prompt_id.startswith(AGENT.replace("-", "-"))


def test_the_platform_spells_the_name_from_the_term_the_model_cited(grounding) -> None:  # type: ignore[no-untyped-def]
    """THE MODEL PICKS THE CONCEPT; THE PLATFORM SPELLS THE NAME.

    Left to spell it, a model returns "Member Date of Birth" where the estate
    says `member_date_of_birth` — and a whole class of naming variance
    disappears the moment the platform does the spelling. Same rule that makes
    the Pipeline Insight Agent take identifiers from routing rather than from a
    model's output.
    """
    dob = grounding.column("DOB")
    assert dob is not None
    merged, refusals = merge(
        dob,
        {
            "name": "Member Date of Birth",
            "type": "date",
            "glossary_id": "BG-004",
            "confidence": 0.9,
        },
        glossary=GLOSSARY,
    )
    assert merged.name == "member_date_of_birth", "the term's own column name won"
    assert merged.glossary_id == "BG-004"
    assert any("the estate's vocabulary spells it" in r for r in refusals)


def test_citing_a_term_raises_its_phi_flag(grounding) -> None:  # type: ignore[no-untyped-def]
    """A model that recognises a column as a PHI-flagged concept has said so,
    whatever it put in `is_phi`."""
    column = grounding.column("SUBSCR_REL_CD")
    assert column is not None
    merged, _ = merge(
        column,
        {"type": "string", "glossary_id": "BG-004", "is_phi": False, "confidence": 0.9},
        glossary=GLOSSARY,
    )
    assert merged.is_phi is True


def test_a_cited_term_that_does_not_exist_is_simply_ignored(grounding) -> None:  # type: ignore[no-untyped-def]
    """A model reaching for grounding it does not have. The confidence floor
    and the "needs your input" path already handle it, so this need not raise."""
    column = grounding.column("SUBSCR_REL_CD")
    assert column is not None
    merged, refusals = merge(
        column,
        {
            "name": "relationship_code",
            "type": "string",
            "glossary_id": "BG-NOPE",
            "confidence": 0.9,
        },
        glossary=GLOSSARY,
    )
    assert merged.name == "relationship_code"
    assert refusals == ()


def test_the_canonical_vocabulary_travels_to_the_model(grounding) -> None:  # type: ignore[no-untyped-def]
    """A model asked to name a column with no target vocabulary in front of it
    invents one — defensible, and not what this estate calls things."""
    text = grounding.as_prompt_grounding()
    assert "canonical vocabulary" in text
    assert "BG-004: column `member_date_of_birth`" in text
    assert grounding.vocabulary.entries, "the vocabulary is built from the glossary, not typed"


def test_the_vocabulary_says_when_it_has_been_truncated(grounding) -> None:  # type: ignore[no-untyped-def]
    """A model told "choose from this list" and shown a third of it would
    decline perfectly nameable columns. No silent caps."""
    assert "more terms not listed here" in grounding.vocabulary.as_text(limit=1)
