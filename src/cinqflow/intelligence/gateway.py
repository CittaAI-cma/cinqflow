"""CF-V0-E16-01 — the one door to a model.

    "no model credentials exist outside the LLM gateway"
    "every model call is logged with prompt hash, model version, cost and
     caller identity"
    "only endpoints declared in the connection profile may be called"
    "PHI is scrubbed before ANY prompt"
    — docs/architecture/INVARIANTS.md, intelligence

The six stages run in `CALL_PIPELINE` order, and the order is CHECKED at
runtime by `_Stages`, not merely implied by the sequence of statements below.
That indirection buys one specific thing: a future refactor that moves prompt
assembly above the scrub raises `PipelineOrderError` on the first call instead
of quietly disclosing PHI. The ordering also has its own test, asserted without
reference to what either component does.

What this module deliberately does NOT do:

  • assemble prompts — `core/prompts` owns the order, and a second assembler
    would be a second place prompts live;
  • decide what a tool may do — the action gateway holds a whitelist, and at
    R0 that whitelist contains read tools only;
  • hold a credential — the adapter behind the `llm` pin does, and it is
    reachable only from here.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from cinqflow.core.intelligence import CALL_PIPELINE, Budget, CallStage, PipelineOrderError, Routing
from cinqflow.core.intelligence.validate import validate
from cinqflow.core.model.agent_action import ActionOutcome, AgentAction
from cinqflow.core.model.governed import Actor, ObjectType
from cinqflow.core.model.llm import (
    BudgetExhaustedError,
    Completion,
    CompletionFailedError,
    LlmError,
)
from cinqflow.core.prompts import AssembledPrompt, assemble, executable
from cinqflow.ports.llm import LlmPort
from cinqflow.ports.metadata_db import MetadataDbPort
from cinqflow.ports.observability import ObservabilityPort
from cinqflow.ports.phi_scrub import PhiScrubPort

#: One. Not "a few" — a retry loop that can run twice can run forever on a
#: model having a bad day, and the budget is the only thing that would stop it.
MAX_REPAIRS = 1


class ManualPathRequiredError(LlmError):
    """The model could not be reached, could not be afforded, or could not
    produce a valid response. Either way, the feature degrades.

        "the feature degrades to its manual path, and Operations sees the
         event — never a silent hang or a surprise bill."

    Named for what the user does next, not for what the model did wrong.
    THE ONE exception every calling agent catches: `_call` normalises a
    caught `BudgetExhaustedError` (the call was refused before it was made)
    and a caught `CompletionFailedError` (the call was made and the
    transport failed) into this same type, and `_parse_or_repair` does the
    same for a schema failure that survives the one bounded repair. A caller
    does not need to know which of the three happened to do the right thing
    next — only `outcome` on the audit row does.
    """


@dataclass(frozen=True)
class GatewayResult:
    """A completed call, and everything the audit row already holds."""

    value: Any
    text: str
    prompt: AssembledPrompt
    completion: Completion
    scrubbed_entities: tuple[str, ...]
    repairs: int
    stages: tuple[CallStage, ...]

    @property
    def cost_usd(self) -> Decimal:
        return self.completion.cost_usd


class _Stages:
    """Runtime proof that the six stages ran in order.

    Cheap, and it converts the platform's most consequential ordering rule from
    a comment into a raise.
    """

    def __init__(self) -> None:
        self.seen: list[CallStage] = []

    def enter(self, stage: CallStage) -> None:
        if self.seen and stage < self.seen[-1]:
            raise PipelineOrderError(
                f"{stage.label} ran after {self.seen[-1].label}. The pipeline is "
                f"{' -> '.join(s.label for s in CALL_PIPELINE)} and PHI is scrubbed "
                "before ANY prompt — there is nothing to degrade to once it is not."
            )
        self.seen.append(stage)

    @property
    def order(self) -> tuple[CallStage, ...]:
        return tuple(self.seen)


@dataclass
class _Spend:
    """Per-agent-per-day and per-run accounting, in Decimal."""

    by_agent_day: dict[tuple[str, str], Decimal] = field(default_factory=dict)
    by_run: dict[str, Decimal] = field(default_factory=dict)

    def today(self, agent: str, day: str) -> Decimal:
        return self.by_agent_day.get((agent, day), Decimal("0"))

    def run(self, run_id: str) -> Decimal:
        return self.by_run.get(run_id, Decimal("0"))

    def add(self, *, agent: str, day: str, run_id: str, amount: Decimal) -> None:
        self.by_agent_day[(agent, day)] = self.today(agent, day) + amount
        self.by_run[run_id] = self.run(run_id) + amount


class LlmGateway:
    """The only place a prompt becomes a model call.

    Constructed with pins, never with an SDK. A caller holding this object can
    ask for a completion; it cannot reach an endpoint, name a model, or spend
    past a cap.
    """

    def __init__(
        self,
        *,
        llm: LlmPort,
        phi_scrub: PhiScrubPort,
        metadata_db: MetadataDbPort,
        observability: ObservabilityPort,
        budget: Budget,
        routing: Routing,
        estimate_usd: Decimal = Decimal("0.01"),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._llm = llm
        self._scrub = phi_scrub
        self._store = metadata_db
        self._obs = observability
        self._budget = budget
        self._routing = routing
        self._estimate = estimate_usd
        self._clock = clock or (lambda: datetime.now(UTC))
        self._spend = _Spend()

    # ── the six stages ───────────────────────────────────────────────────────

    def complete(
        self,
        *,
        agent: str,
        run_id: str,
        prompt_id: str,
        caller: Actor,
        context: str = "",
        input_text: str = "",
        few_shots: str | None = None,
        prompt_version: int | None = None,
    ) -> GatewayResult:
        stages = _Stages()
        now = self._clock()
        day = now.date().isoformat()

        # 1 · context_assembly — the caller's grounding, as gathered.
        stages.enter(CallStage.CONTEXT_ASSEMBLY)

        # 2 · phi_scrub — BEFORE anything is assembled, and before the template
        #     is even read. Nothing that has touched a model can be un-touched.
        stages.enter(CallStage.PHI_SCRUB)
        grounding_scrub = self._scrub.scrub(context)
        input_scrub = self._scrub.scrub(input_text)
        entities = tuple(
            sorted(
                {f.entity_type for f in grounding_scrub.findings}
                | {f.entity_type for f in input_scrub.findings}
            )
        )
        if entities:
            self._obs.log("phi.scrubbed", agent=agent, run_id=run_id, entities=list(entities))

        # 3 · prompt_assembly — published templates only, fixed order.
        stages.enter(CallStage.PROMPT_ASSEMBLY)
        template = executable(self._store.get(ObjectType.PROMPT, prompt_id, prompt_version))
        prompt = assemble(
            template,
            grounding=grounding_scrub.text,
            input_text=input_scrub.text,
            few_shots=few_shots,
        )

        # 4 · llm_gateway — budget first, then the call.
        stages.enter(CallStage.LLM_GATEWAY)
        completion = self._call(
            agent=agent, run_id=run_id, caller=caller, day=day, prompt=prompt, now=now
        )

        # 5 · schema_validation — parse or reject, one bounded repair.
        stages.enter(CallStage.SCHEMA_VALIDATION)
        value, completion, repairs = self._parse_or_repair(
            agent=agent,
            run_id=run_id,
            caller=caller,
            day=day,
            prompt=prompt,
            completion=completion,
            now=now,
        )

        # 6 · action_gateway — at R0 there is nothing to permit. The stage runs
        #     anyway so the trace shows six, and so T15 populates a seat rather
        #     than inserting one.
        stages.enter(CallStage.ACTION_GATEWAY)

        self._record(
            agent=agent,
            run_id=run_id,
            caller=caller,
            prompt=prompt,
            completion=completion,
            outcome=ActionOutcome.COMPLETED,
            now=now,
        )
        return GatewayResult(
            value=value,
            text=completion.text,
            prompt=prompt,
            completion=completion,
            scrubbed_entities=entities,
            repairs=repairs,
            stages=stages.order,
        )

    # ── stage 4 ──────────────────────────────────────────────────────────────

    def _call(
        self,
        *,
        agent: str,
        run_id: str,
        caller: Actor,
        day: str,
        prompt: AssembledPrompt,
        now: datetime,
    ) -> Completion:
        try:
            self._budget.check(
                agent=agent,
                spent_today=self._spend.today(agent, day),
                spent_this_run=self._spend.run(run_id),
                estimated=self._estimate,
            )
        except BudgetExhaustedError as exhausted:
            # Recorded before it is raised. A budget event Operations cannot
            # see is a surprise bill with extra steps.
            self._record(
                agent=agent,
                run_id=run_id,
                caller=caller,
                prompt=prompt,
                completion=None,
                outcome=ActionOutcome.REFUSED_BUDGET,
                now=now,
                detail=str(exhausted),
            )
            self._obs.metric("llm.budget.refused", 1.0, agent=agent)
            # Re-raised as the ONE thing every caller already catches — a
            # bare `BudgetExhaustedError` here would be a sibling of
            # `ManualPathRequiredError`, not that error itself, and every
            # `except ManualPathRequiredError` in mapping_suggestion,
            # fingerprint_match and alert_enrichment would let it through
            # uncaught. Budget exhaustion is DESIGNED FOR, not a freak
            # accident — it degrades exactly like a schema failure.
            raise ManualPathRequiredError(
                f"{agent}'s budget is exhausted — {exhausted}. The manual path is unaffected."
            ) from exhausted

        try:
            completion = self._llm.complete(
                prompt=prompt.text,
                task_class=prompt.task_class,
                response_schema=prompt.response_schema,
                max_tokens=prompt.max_tokens,
                temperature=prompt.temperature,
            )
        except CompletionFailedError as failed:
            # The call itself failed — a timeout, a dropped connection, a
            # rate limit — as opposed to a bad ANSWER (schema_validation's
            # job) or an exhausted budget (above). Same degrade, same
            # single exception type every caller already catches.
            # `UndeclaredEndpointError` is a THIRD sibling of `LlmError` and
            # is deliberately NOT caught here: a misconfigured endpoint is
            # not something to degrade past, and must keep failing loudly.
            self._record(
                agent=agent,
                run_id=run_id,
                caller=caller,
                prompt=prompt,
                completion=None,
                outcome=ActionOutcome.FAILED_COMPLETION,
                now=now,
                detail=str(failed),
            )
            self._obs.metric("llm.completion.failed", 1.0, agent=agent)
            raise ManualPathRequiredError(
                f"{agent}'s completion call failed — {failed}. The manual path is unaffected."
            ) from failed

        self._spend.add(agent=agent, day=day, run_id=run_id, amount=completion.cost_usd)
        return completion

    # ── stage 5 ──────────────────────────────────────────────────────────────

    def _parse_or_repair(
        self,
        *,
        agent: str,
        run_id: str,
        caller: Actor,
        day: str,
        prompt: AssembledPrompt,
        completion: Completion,
        now: datetime,
    ) -> tuple[Any, Completion, int]:
        if prompt.response_schema is None:
            return completion.text, completion, 0

        for attempt in range(MAX_REPAIRS + 1):
            problems = _problems(prompt.response_schema, completion.text)
            if not problems:
                return json.loads(completion.text), completion, attempt

            self._record(
                agent=agent,
                run_id=run_id,
                caller=caller,
                prompt=prompt,
                completion=completion,
                outcome=ActionOutcome.FAILED_SCHEMA,
                now=now,
                detail="; ".join(problems),
            )
            if attempt == MAX_REPAIRS:
                break

            # The repair names the PATHS AND re-states the schema. Naming the
            # paths alone (CF-V0-E16-02's original design) is enough to tell a
            # model its output was wrong, but not what shape is right — a real
            # model observed inventing a plausible wrapper key (nesting
            # feed_id/batch_id under "identifiers") on every attempt because it
            # was never shown the flat contract, only an English paraphrase of
            # it in `prompt.text`. The schema is repeated here, in the
            # UNGOVERNED repair string, rather than added to the versioned
            # PromptTemplate — that would change every prompt's hash and
            # Lane-2 cassette key for a fix that only the repair path needs.
            try:
                completion = self._llm.complete(
                    prompt=f"{prompt.text}\n\n# repair\n"
                    f"The previous response was rejected:\n- "
                    + "\n- ".join(problems)
                    + "\nReturn only JSON matching this exact schema — no other keys, no wrapper "
                    "object:\n" + json.dumps(prompt.response_schema),
                    task_class=prompt.task_class,
                    response_schema=prompt.response_schema,
                    max_tokens=prompt.max_tokens,
                    temperature=prompt.temperature,
                )
            except CompletionFailedError as failed:
                # The ONE bounded repair attempt is a second, easily-missed
                # call site with the identical bug: the completion above this
                # loop is guarded by `_call`, but this one is made directly,
                # and a transport failure here must degrade exactly the same
                # way — never crash a run that a schema retry was already
                # trying to save.
                self._record(
                    agent=agent,
                    run_id=run_id,
                    caller=caller,
                    prompt=prompt,
                    completion=completion,
                    outcome=ActionOutcome.FAILED_COMPLETION,
                    now=now,
                    detail=str(failed),
                )
                self._obs.metric("llm.completion.failed", 1.0, agent=agent)
                raise ManualPathRequiredError(
                    f"{agent}'s repair call failed — {failed}. The manual path is unaffected."
                ) from failed
            self._spend.add(agent=agent, day=day, run_id=run_id, amount=completion.cost_usd)

        self._record(
            agent=agent,
            run_id=run_id,
            caller=caller,
            prompt=prompt,
            completion=completion,
            outcome=ActionOutcome.ESCALATED_TO_MANUAL,
            now=now,
        )
        raise ManualPathRequiredError(
            f"{agent} could not produce a response matching {prompt.reference}'s schema after "
            f"{MAX_REPAIRS} repair. The manual path is unaffected."
        )

    # ── the ledger ───────────────────────────────────────────────────────────

    def _record(
        self,
        *,
        agent: str,
        run_id: str,
        caller: Actor,
        prompt: AssembledPrompt,
        completion: Completion | None,
        outcome: ActionOutcome,
        now: datetime,
        detail: str = "",
    ) -> None:
        self._store.append_agent_action(
            AgentAction(
                run_id=run_id,
                agent=agent,
                action=f"llm:{prompt.task_class.value}",
                outcome=outcome,
                actor=caller,
                occurred_ts=now,
                prompt_ref=prompt.reference,
                prompt_hash=prompt.prompt_hash,
                model=completion.model
                if completion
                else self._routing.model_for(prompt.task_class),
                model_version=completion.model_version if completion else "",
                prompt_tokens=completion.prompt_tokens if completion else 0,
                completion_tokens=completion.completion_tokens if completion else 0,
                cost_usd=completion.cost_usd if completion else Decimal("0"),
                latency_ms=completion.latency_ms if completion else 0,
                detail=detail,
            )
        )

    def spent_today(self, agent: str, *, day: str | None = None) -> Decimal:
        return self._spend.today(agent, day or self._clock().date().isoformat())

    def spent_this_run(self, run_id: str) -> Decimal:
        """The sum of every model call charged to this run so far.

        A run's cost is not one call's cost — route, plan_tools and answer
        each spend. The `RunBudget` gate must see all three, or a $0.09 run
        across three calls reports as the last call's $0.04.
        """
        return self._spend.run(run_id)


def _problems(schema: dict[str, Any], text: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as broken:
        return (f"$: not JSON ({broken.msg} at position {broken.pos})",)
    return validate(schema, parsed)
