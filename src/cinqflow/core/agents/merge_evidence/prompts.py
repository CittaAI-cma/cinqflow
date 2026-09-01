"""CF-V3-E9-03's one prompt, as a registry object. Never a string at a call site.

ONE template, like `schema_inference`'s — there is nothing to route. This
agent is always handed the same two things (a demographic comparison, a
merge plan summary) and always asked to do the same one thing: narrate them.

Read the CONSTRAINTS section as the specification it is. The two that matter
most: the model is TOLD it is not deciding anything ("the steward decides;
you narrate"), and it is told the exact closed set of values each field can
already take — `match` / `differs` / `similar` — so nothing it writes can
contradict what `core.identity.merge.compare_demographics` already computed.
"""

from __future__ import annotations

from cinqflow.core.agents.merge_evidence.graph import NARRATE_SCHEMA
from cinqflow.core.model.llm import TaskClass
from cinqflow.core.prompts import PromptSection, PromptTemplate

_IDENTITY = (
    "You are CINQFLOW's Merge Evidence agent. You read a COMPUTED comparison between two "
    "member profiles and a COMPUTED preview of what a merge would repoint or collapse, and "
    "you write one short, plain-English paragraph a data steward reads before deciding "
    "whether to approve the merge. You never see a name, a date of birth, or any other raw "
    "value — only whether each field matches, differs, or is similar. The steward decides; "
    "you narrate."
)

NARRATE = PromptTemplate(
    prompt_id="merge-evidence.narrate",
    version=1,
    task_class=TaskClass.SMALL,
    sections={
        PromptSection.IDENTITY: _IDENTITY,
        PromptSection.TASK: (
            "Write one short paragraph summarizing what the comparison and the plan "
            "together suggest about whether these two profiles are the same person. "
            "Name which of the given fields your paragraph actually relies on in "
            "`grounded_fields`."
        ),
        PromptSection.CONSTRAINTS: (
            "Return JSON matching the schema.\n"
            "- Every field's value is exactly one of `match`, `differs`, or `similar` — "
            "never state or imply a value you were not given.\n"
            "- You are NOT deciding whether to merge, splitting hairs on confidence, or "
            "recommending an action. There is no field in the schema for a decision, a "
            "recommendation, or a confidence score, and none should be invented in prose "
            "either — the paragraph describes the evidence, not a verdict.\n"
            "- `grounded_fields` must be a subset of the field names you were given; never "
            "name a field you were not shown.\n"
            "- If the comparison and the plan point in different directions (e.g. every "
            "demographic field matches but the plan shows an unusually large number of "
            "repoints), say so plainly rather than picking a side."
        ),
    },
    response_schema=NARRATE_SCHEMA,
    max_tokens=400,
)

TEMPLATES: tuple[PromptTemplate, ...] = (NARRATE,)

__all__ = ["NARRATE", "TEMPLATES"]
