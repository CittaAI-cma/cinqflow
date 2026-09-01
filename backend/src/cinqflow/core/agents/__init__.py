"""Agent graphs, declared as DATA. No runtime is imported here — ever.

    "agent graphs are declared in core, never bound to a runtime"
    — .importlinter, the `graphs-are-data` contract

A graph is nodes plus an edge spec. The node functions are pure over state and
call the platform's own services; the runtime merely walks the edges. That is
what makes LangGraph a Wave-2 ADAPTER SWAP (ADR-0018) rather than a rewrite,
and it is enforced mechanically: this package may not import
`cinqflow.adapters` or `langgraph`.
"""

from cinqflow.core.agents.alert_enrichment.prompts import TEMPLATES as ALERT_ENRICHMENT
from cinqflow.core.agents.fingerprint_match.prompts import TEMPLATES as FINGERPRINT_MATCH
from cinqflow.core.agents.mapping_suggestion.prompts import TEMPLATES as MAPPING_SUGGESTION
from cinqflow.core.agents.merge_evidence.prompts import TEMPLATES as MERGE_EVIDENCE
from cinqflow.core.agents.phi_detection.prompts import TEMPLATES as PHI_DETECTION
from cinqflow.core.agents.pipeline_insight.prompts import TEMPLATES as PIPELINE_INSIGHT
from cinqflow.core.agents.rule_authoring.prompts import TEMPLATES as RULE_AUTHORING
from cinqflow.core.agents.schema_inference.prompts import TEMPLATES as SCHEMA_INFERENCE
from cinqflow.core.prompts import PromptTemplate

#: EVERY prompt this platform can run, in ONE list.
#:
#:     "no agent is ever built outside the registry"
#:     "Allow any prompt text to live outside the registry — inline prompt
#:      strings in code are a defect class." — don't
#:     — CF-V0-E16-02
#:
#: THE GAP THIS CLOSES IS AN INSTALLER GAP, NOT AN AUTHORING ONE. Every
#: template below was already versioned in git beside its agent, exactly as
#: E16-02 requires. What did not exist was a list of them: `intelligence.demo`
#: enumerated SEVEN by hand for the seeded mock plane, `rule_authoring` was
#: missing from that hand-written tuple, and the REAL Postgres plane had no
#: seeding path at all — so `cinqflow install` produced a plane on which the
#: first model call any agent made raised `ObjectNotFoundError: prompt:
#: pipeline-insight.route`. A registry nothing loads is a registry the running
#: platform does not have.
#:
#: One list, so adding an agent means adding a line HERE and every seeding
#: path gains its prompts at once — rather than three call sites that drift.
ALL_TEMPLATES: tuple[PromptTemplate, ...] = (
    *PIPELINE_INSIGHT,
    *SCHEMA_INFERENCE,
    *PHI_DETECTION,
    *MAPPING_SUGGESTION,
    *RULE_AUTHORING,
    *FINGERPRINT_MATCH,
    *ALERT_ENRICHMENT,
    *MERGE_EVIDENCE,
)

__all__ = ["ALL_TEMPLATES"]
