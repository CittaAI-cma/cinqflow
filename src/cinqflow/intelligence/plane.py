"""Profile in, the WHOLE intelligence plane out. One construction path.

    "all environment difference lives in the connection profile, nowhere else"
    "climbing a socket rung changes ONLY the profile"
    — docs/architecture/INVARIANTS.md, chip discipline

`intelligence.wiring` already fits the `llm` pin from a profile and refuses
any second way of doing it. This module is the same idea one level up: an
`LlmGateway` needs FOUR pins fitted together — `llm`, `phi_scrub`,
`metadata_db`, `observability` — and every agent in the platform needs that
same gateway plus the store it writes proposals to. Before this module every
server assembled those four by hand, which is how `api/local.py` came to run
a REAL Postgres data plane behind a SCRIPTED model while `profiles/local.yaml`
named a real endpoint two directories away.

THE KNOWLEDGE-INGEST WORKER IS NOT HERE, AND THAT IS THE LAYERING, NOT AN
OMISSION. `CF-V1-E16-04`'s worker lives in `cinqflow.workers`, which
`.importlinter`'s layer contract places ABOVE `cinqflow.intelligence` — an
Archetype-B pipeline stage, not an agent. This module therefore exposes the
three pins that worker needs (`phi_scrub`, a `gateway`, `vector`) and the
SERVER composes it, which is the same shape `api/local.py` already uses for
`ods_model_provisioner`.

WHY THE FACTORIES LIVE HERE AND NOT IN `api/`. `create_app` takes callables
(`SchemaInferenceFactory`, `PhiDetectionFactory`, ...) precisely so it never
constructs an agent itself — an agent's tool context carries a principal, and
a shared agent would be a shared scope. This module is the other end of that
contract: it answers "what is a schema-inference agent on THIS deployment"
once, and every server asks it rather than each writing the same six-line
`LlmGateway(...)` block with its own idea of which scrubber to fit.

WHAT WAS UNREACHABLE BEFORE THIS FILE, AND IS NOT ANY MORE. Four Wave-1
capabilities were built, tested and wired to routes, and then fitted by NO
server: `POST /detect-phi` (CF-V1-E5-03), `POST /suggest-mapping`
(CF-V1-E6-02), `POST /author-rules` (CF-V1-E7-01) and the knowledge-ingest
worker every publish hook calls (CF-V1-E16-04) each answered 503 or degraded
silently on both `api/dev.py` and `api/local.py`, because neither passed the
factory. An agent nothing constructs is an agent the platform does not have,
whatever its test suite says.

THE PHI SCRUBBER IS A PIN, NOT A DEFAULT. `PatternPhiScrub` and
`PresidioPhiScrub` are both real adapters behind `phi_scrub`; which one a
deployment gets is `profiles/*.yaml`'s decision, exactly as the LLM adapter
is. `presidio` degrades to a NAMED refusal when `requirements/ai.txt` is not
installed, never to the pattern scrubber — silently substituting a weaker
scrubber for the one the profile asked for is precisely the class of
substitution `conformance.kit.check_pin` exists to catch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from cinqflow.adapters.mock.agent_runtime import InProcAgentRuntime
from cinqflow.adapters.mock.observability import NoopObservability
from cinqflow.adapters.mock.phi_scrub import PatternPhiScrub
from cinqflow.adapters.mock.vector import ListVector
from cinqflow.core.agents.alert_enrichment.graph import AGENT as ALERT_ENRICHMENT_AGENT
from cinqflow.core.agents.fingerprint_match.graph import AGENT as FINGERPRINT_MATCH_AGENT
from cinqflow.core.model.identity import Principal, Scopes
from cinqflow.core.model.profile import Profile, ProfileError
from cinqflow.core.retrieval import platform_index
from cinqflow.intelligence.agents.alert_enrichment import AlertEnrichmentAgent
from cinqflow.intelligence.agents.fingerprint_match import FingerprintMatchAgent
from cinqflow.intelligence.agents.mapping_suggestion import MappingSuggestionAgent
from cinqflow.intelligence.agents.merge_evidence import MergeEvidenceAgent
from cinqflow.intelligence.agents.phi_detection import PhiDetectionAgent
from cinqflow.intelligence.agents.pipeline_insight import PipelineInsightAgent
from cinqflow.intelligence.agents.rule_authoring import RuleAuthoringAgent
from cinqflow.intelligence.agents.schema_inference import SchemaInferenceAgent
from cinqflow.intelligence.gateway import LlmGateway
from cinqflow.intelligence.retrieval import RetrievalService
from cinqflow.intelligence.tools import ToolContext
from cinqflow.intelligence.wiring import budget_from, llm_from, routing_from
from cinqflow.ports.control_tables import ControlTablesPort
from cinqflow.ports.metadata_db import MetadataDbPort
from cinqflow.ports.observability import ObservabilityPort
from cinqflow.ports.phi_scrub import PhiScrubPort
from cinqflow.ports.secrets import SecretsPort
from cinqflow.ports.vector import VectorPort

if TYPE_CHECKING:  # pragma: no cover - typing only
    from cinqflow.adapters.local.pg_control import Connection

__all__ = ["IntelligencePlane", "phi_scrub_from", "vector_from"]


#: The scope a platform-triggered agent runs its certified tool calls under.
#: A batch failing is the platform's own signal, not a request a person made,
#: so there is no caller principal to inherit — the same reasoning
#: `intelligence.demo.PLATFORM_PRINCIPAL` states for the dev plane.
PLATFORM_PRINCIPAL = Principal(
    subject="platform@cinqflow",
    display_name="platform",
    scopes=Scopes(feeds=frozenset({"*"})),
)


def phi_scrub_from(profile: Profile) -> PhiScrubPort:
    """Fit the `phi_scrub` pin as the profile says, and no other way.

    `presidio` is the profile's word for "the real NER scrubber", and
    importing it is what tells us whether `requirements/ai.txt` is actually
    installed. It raises rather than falling back, for the reason this
    module's docstring gives: a deployment that asked for Presidio and
    quietly got regexes is a deployment whose PHI guarantee is a different,
    weaker guarantee than the one its profile records.
    """
    adapter = profile.adapter_for("phi_scrub")
    match adapter:
        case "mock" | "pattern" | "scripted":
            return PatternPhiScrub()
        case "presidio":
            from cinqflow.adapters.local import presidio_scrub as module

            if not module._PRESIDIO_AVAILABLE:
                raise ProfileError(
                    f"{profile.source}: phi_scrub is `presidio`, but presidio-analyzer is not "
                    "installed — `pip install -r requirements/ai.txt` to energize the pin. "
                    "Falling back to the pattern scrubber is NOT done here: a deployment "
                    "that asked for NER and quietly got regexes has a weaker PHI guarantee "
                    "than the one its profile records."
                )
            return module.PresidioPhiScrub()
        case unknown:
            raise ProfileError(
                f"{profile.source}: {unknown!r} is not an adapter for the phi_scrub pin "
                "(mock, presidio)"
            )


def vector_from(profile: Profile, *, connection: Connection | None = None) -> VectorPort | None:
    """Fit the `vector` pin. `None` when the profile fits nothing.

    `None` rather than an empty stand-in, and that is the same honest shape
    `connectors_from` uses for an unfitted route: `workers.knowledge` and
    `intelligence.tools._search_knowledge` both already read "no vector pin
    on this deployment" and degrade by SAYING SO. A `ListVector` fitted where
    the profile said nothing would instead accept embeddings into a store
    that vanishes on restart, which reads as a working knowledge plane and is
    not one.
    """
    adapter = profile.adapter_for("vector")
    match adapter:
        case "none":
            return None
        case "mock" | "list":
            return ListVector()
        case "pgvector":
            if connection is None:
                raise ProfileError(
                    f"{profile.source}: vector is pgvector, which needs the live Postgres "
                    "connection passed to `vector_from(..., connection=...)` — there is no "
                    "second way to reach the database from here."
                )
            from cinqflow.adapters.local.pg_vector import PgVectorStore

            return PgVectorStore(connection)
        case unknown:
            raise ProfileError(
                f"{profile.source}: {unknown!r} is not an adapter for the vector pin "
                "(none, mock, pgvector)"
            )


@dataclass(frozen=True)
class IntelligencePlane:
    """Every agent this deployment has, and the one gateway they share.

    Constructed from a profile — never from an endpoint, a key or a model
    name — so that a rung climb is a profile edit here exactly as it is for
    `llm_from`. Holds the pins, not the agents: an agent is built per call
    because its tool context carries the caller's scope, and a cached agent
    would be a cached scope.
    """

    llm: object
    phi_scrub: PhiScrubPort
    observability: ObservabilityPort
    vector: VectorPort | None
    profile: Profile

    @classmethod
    def from_profile(
        cls,
        profile: Profile,
        secrets: SecretsPort,
        *,
        connection: Connection | None = None,
        observability: ObservabilityPort | None = None,
    ) -> IntelligencePlane:
        return cls(
            llm=llm_from(profile, secrets),
            phi_scrub=phi_scrub_from(profile),
            observability=observability or NoopObservability(),
            vector=vector_from(profile, connection=connection),
            profile=profile,
        )

    # ── the one gateway ──────────────────────────────────────────────────────

    def gateway(self, metadata: MetadataDbPort, secrets: SecretsPort) -> LlmGateway:
        """The ONLY way an agent on this deployment gets a gateway.

        `metadata` is a parameter rather than a field because the store is
        per-app wiring (`create_app` holds one) while this plane is per-
        profile; binding them here would make the plane un-shareable between
        the app and a worker that legitimately holds the same store.
        """
        return LlmGateway(
            llm=self.llm,  # type: ignore[arg-type]
            phi_scrub=self.phi_scrub,
            metadata_db=metadata,
            observability=self.observability,
            budget=budget_from(self.profile),
            routing=routing_from(self.profile, secrets),
        )

    # ── the agents, one method per capability ────────────────────────────────

    def pipeline_insight(
        self,
        principal: Principal,
        control: ControlTablesPort,
        metadata: MetadataDbPort,
        secrets: SecretsPort,
    ) -> PipelineInsightAgent:
        """CF-V0-E16-10. One agent per caller — a shared agent is a shared scope."""
        return PipelineInsightAgent(
            llm=self.gateway(metadata, secrets),
            tools=ToolContext(
                principal=principal,
                control=control,
                metadata=metadata,
                vector=self.vector,
                llm=self.gateway(metadata, secrets),
                phi_scrub=self.phi_scrub,
            ),
            runtime=InProcAgentRuntime(),
        )

    def retrieval(self, metadata: MetadataDbPort, secrets: SecretsPort) -> RetrievalService:
        """CF-V1-E16-05. ONE service, every agent, no private stores.

        The lexical index is the generated platform vocabulary plus whatever
        client corpus has been seeded; the semantic half is live only where
        the `vector` pin is fitted AND something has been embedded. A
        deployment with neither still gets a real service — it simply returns
        lexical results and says so in its notes.
        """
        return RetrievalService(
            index=platform_index(),
            vector=self.vector,
            llm=self.gateway(metadata, secrets),
            phi_scrub=self.phi_scrub,
        )

    def schema_inference(
        self, metadata: MetadataDbPort, secrets: SecretsPort
    ) -> SchemaInferenceAgent:
        """CF-V1-E5-02."""
        return SchemaInferenceAgent(
            llm=self.gateway(metadata, secrets),
            metadata=metadata,
            retrieval=self.retrieval(metadata, secrets),
        )

    def phi_detection(self, metadata: MetadataDbPort, secrets: SecretsPort) -> PhiDetectionAgent:
        """CF-V1-E5-03. The scrubber is passed as well as held: this agent
        classifies WITH it, it does not merely have its prompts cleaned by it."""
        return PhiDetectionAgent(
            llm=self.gateway(metadata, secrets), scrub=self.phi_scrub, metadata=metadata
        )

    def mapping_suggestion(
        self, metadata: MetadataDbPort, secrets: SecretsPort
    ) -> MappingSuggestionAgent:
        """CF-V1-E6-02."""
        return MappingSuggestionAgent(
            llm=self.gateway(metadata, secrets),
            metadata=metadata,
            retrieval=self.retrieval(metadata, secrets),
        )

    def rule_authoring(self, metadata: MetadataDbPort, secrets: SecretsPort) -> RuleAuthoringAgent:
        """CF-V1-E7-01 / CF-V1-E7-04."""
        return RuleAuthoringAgent(
            llm=self.gateway(metadata, secrets),
            metadata=metadata,
            retrieval=self.retrieval(metadata, secrets),
        )

    def merge_evidence(self, metadata: MetadataDbPort, secrets: SecretsPort) -> MergeEvidenceAgent:
        """CF-V3-E9-03."""
        return MergeEvidenceAgent(llm=self.gateway(metadata, secrets))

    def fingerprint_match(
        self, control: ControlTablesPort, metadata: MetadataDbPort, secrets: SecretsPort
    ) -> FingerprintMatchAgent:
        """CF-V2-E12-04. Platform-triggered, so `PLATFORM_PRINCIPAL` rather
        than a caller's — see that constant's own note."""
        return FingerprintMatchAgent(
            llm=self.gateway(metadata, secrets),
            tools=ToolContext(
                principal=PLATFORM_PRINCIPAL,
                control=control,
                metadata=metadata,
                agent=FINGERPRINT_MATCH_AGENT,
                vector=self.vector,
                llm=self.gateway(metadata, secrets),
                phi_scrub=self.phi_scrub,
            ),
            runtime=InProcAgentRuntime(),
        )

    def alert_enrichment(
        self, control: ControlTablesPort, metadata: MetadataDbPort, secrets: SecretsPort
    ) -> AlertEnrichmentAgent:
        """CF-V2-E12-05."""
        return AlertEnrichmentAgent(
            llm=self.gateway(metadata, secrets),
            tools=ToolContext(
                principal=PLATFORM_PRINCIPAL,
                control=control,
                metadata=metadata,
                agent=ALERT_ENRICHMENT_AGENT,
                vector=self.vector,
                llm=self.gateway(metadata, secrets),
                phi_scrub=self.phi_scrub,
            ),
            runtime=InProcAgentRuntime(),
        )
