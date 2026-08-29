"""The action gateway — stage six, and the thing a runtime may never own.

    "never_owns: [model_call, prompt_text, tool_whitelist, risk_class,
     lifecycle_state]"
    — docs/architecture/plates/11-agent-runtime-and-the-risk-router.md

    "R0: the action gateway whitelist contains read tools only"
    — CF-V0-E16-10

Non-bypassable by construction: the tool executor is reached only through
`permit()`, and `permit()` consults a whitelist the agent does not carry and
cannot widen. An agent asking to retry a batch does not get a smaller answer —
it gets a refusal, a reason, and a row.

The refusal EXPLAINS ITSELF and names the story that will answer the request.
A dead end teaches a user to stop asking; a roadmap answer teaches them when to
ask again.
"""

from __future__ import annotations

from dataclasses import dataclass

from cinqflow.core.agents.pipeline_insight.graph import DECLINED_CAPABILITIES
from cinqflow.core.model.agent_action import ActionOutcome
from cinqflow.core.tools import CATALOGUE, READ_ONLY_WHITELIST

#: Verbs that are WRITES no matter what they are called. Matched against a
#: requested action name so a model that invents `retry_batch` is refused by
#: the gateway rather than by `UnknownToolError` — the difference matters,
#: because one of those is a governance event and the other is a typo.
WRITE_VERBS: frozenset[str] = frozenset(
    {
        "retry",
        "rerun",
        "reprocess",
        "pause",
        "resume",
        "edit",
        "update",
        "delete",
        "approve",
        "publish",
        "create",
        "load",
        "write",
        "set",
        "override",
    }
)


@dataclass(frozen=True)
class Permission:
    allowed: bool
    reason: str = ""
    outcome: ActionOutcome = ActionOutcome.COMPLETED
    capability: str = ""

    def __bool__(self) -> bool:
        return self.allowed


@dataclass(frozen=True)
class ActionGateway:
    """One whitelist, consulted once, owned by the platform.

    `risk_class` is carried for the audit row and is NOT a dial: R0 is what
    this agent is, and there is no confidence at which it becomes something
    else. A gateway with a confidence threshold is a gateway that eventually
    lets something through on a good day.
    """

    whitelist: frozenset[str] = READ_ONLY_WHITELIST
    risk_class: str = "R0"

    def __post_init__(self) -> None:
        if outside := sorted(self.whitelist - set(CATALOGUE)):
            raise ValueError(
                f"{', '.join(outside)} are not certified tools. A whitelist entry that is "
                "not in the catalogue is a permission for something nobody reviewed."
            )

    def permit(self, action: str) -> Permission:
        """The one question. Asked before anything runs."""
        if action in self.whitelist:
            return Permission(True)

        verb = action.split("_", 1)[0].lower()
        if verb in WRITE_VERBS or action in CATALOGUE:
            return Permission(
                False,
                _write_refusal(action),
                ActionOutcome.REFUSED_NOT_WHITELISTED,
                capability="write_action",
            )
        return Permission(
            False,
            f"{action!r} is not a certified tool. This agent may call: "
            f"{', '.join(sorted(self.whitelist))}.",
            ActionOutcome.REFUSED_NOT_WHITELISTED,
        )


def _write_refusal(action: str) -> str:
    return (
        f"{action!r} would change something, and this agent runs at R0 — it observes and "
        f"explains. {DECLINED_CAPABILITIES['write_action']}"
    )


def declined(capability: str) -> str:
    """The named refusal for a capability Wave 0 does not have."""
    return DECLINED_CAPABILITIES.get(
        capability, "That is not something this agent can do in Wave 0."
    )
