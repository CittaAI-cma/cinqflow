"""Profile in, gateway out. The ONLY construction path for a model call.

    "all environment difference lives in the connection profile, nowhere else"
    "climbing a socket rung changes ONLY the profile"
    — docs/architecture/INVARIANTS.md, chip discipline

Every value the adapter needs — endpoint, credential, model names, prices,
budgets — is read HERE, from the profile, through the secrets pin. Two
consequences:

  • rung 3 is a profile edit. Azure AI Foundry serves the same request shape,
    so `endpoint` and `api_key` move and nothing else does.
  • there is no second way to build an `OpenAiCompatibleLlm` in the running
    system. A call site that wanted its own would have to invent an endpoint
    and a key, and the structural test in
    `tests/unit/test_credentials_live_only_in_adapters.py` fails the moment it
    imports the SDK to try.

The mock and cassette adapters are built from the same function, because the
profile is what says which rung we are on — not an `if TESTING` somewhere.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from cinqflow.adapters.mock.llm import ScriptedLlm
from cinqflow.adapters.openai_compatible.llm import OpenAiCompatibleLlm
from cinqflow.adapters.replay.llm import CassetteLlm
from cinqflow.core.intelligence import Budget, Routing
from cinqflow.core.model.llm import LlmError, TaskClass
from cinqflow.core.model.profile import Profile, ProfileError
from cinqflow.ports.llm import LlmPort
from cinqflow.ports.secrets import SecretsPort


def routing_from(profile: Profile, secrets: SecretsPort) -> Routing:
    config = _llm_config(profile)
    table = config.get("routing", {})
    for task_class in TaskClass:
        if task_class.value not in table:
            raise ProfileError(
                f"{profile.source}: llm.routing has no {task_class.value} model. Routing is by "
                "TASK from the profile — a call site naming a model is a call site that has "
                "to be edited when the tenant's catalogue differs."
            )
    return Routing(
        small=secrets.resolve(str(table["small"])),
        large=secrets.resolve(str(table["large"])),
        pin_versions=bool(config.get("pin_versions", True)),
    )


def budget_from(profile: Profile) -> Budget:
    budgets = _llm_config(profile).get("budgets", {})
    if not budgets:
        raise ProfileError(
            f"{profile.source}: llm.budgets is absent. An unbudgeted agent is a surprise bill "
            "waiting for a bad week."
        )
    return Budget(
        # str() before Decimal: Decimal(float) inherits the float's error, and
        # a cap of 0.25 that is really 0.2500000000000001 is a cap that lets
        # one more call through than it says.
        per_run_usd=Decimal(str(budgets["per_run_usd"])),
        per_agent_per_day_usd=Decimal(str(budgets["per_agent_per_day_usd"])),
    )


def llm_from(profile: Profile, secrets: SecretsPort) -> LlmPort:
    """Fit the `llm` pin as the profile says, and no other way."""
    config = _llm_config(profile)
    adapter = profile.adapter_for("llm")
    routing = routing_from(profile, secrets)
    models = {TaskClass.SMALL: routing.small, TaskClass.LARGE: routing.large}

    match adapter:
        case "scripted" | "mock":
            # Rung 0. No endpoint and no key exist here BY CONSTRUCTION, which
            # is what makes "lanes 1 and 2 hold no live credentials" a fact
            # about the profile rather than a discipline.
            return ScriptedLlm()
        case "cassette":
            return CassetteLlm(str(config.get("directory", "goldensets/cassettes")), models=models)
        case "openai-compatible":
            endpoint = secrets.resolve(str(config.get("endpoint", "")))
            if not endpoint:
                raise ProfileError(f"{profile.source}: llm.endpoint is empty")
            return OpenAiCompatibleLlm(
                endpoint=endpoint,
                api_key=secrets.resolve(str(config.get("api_key", ""))),
                models=models,
                prices=_prices(profile, config),
                declared_endpoints=frozenset({endpoint}),
                embedding_model=secrets.resolve(str(config.get("embedding_model", ""))),
            )
        case unknown:
            raise ProfileError(
                f"{profile.source}: {unknown!r} is not an adapter for the llm pin "
                "(scripted, cassette, openai-compatible)"
            )


def _llm_config(profile: Profile) -> dict[str, Any]:
    return profile.pins.get("llm", {})


def _prices(profile: Profile, config: dict[str, Any]) -> dict[str, tuple[Decimal, Decimal]]:
    declared = config.get("prices")
    if not declared:
        raise LlmError(
            f"{profile.source}: llm.prices is absent. A model priced at zero makes every "
            "budget cap non-binding and every cost figure a lie."
        )
    return {
        model: (Decimal(str(prompt)), Decimal(str(completion)))
        for model, (prompt, completion) in declared.items()
    }
