"""The egress check ADR-0018 owes: proves the telemetry kill-switch fired.

langgraph pulls `langchain-core`, which pulls `langsmith`. That telemetry
client is inert unless `LANGSMITH_TRACING` is set — but "inert unless" is a
default, not a guarantee, and this platform's invariant is *"only endpoints
declared in the connection profile may be called"* (ADR-0018).

`cinqflow.adapters.langgraph.agent_runtime` calls `_silence_telemetry()` at
IMPORT time, before any langchain module loads, setting the kill variables
directly on `os.environ`. This check imports that adapter and then reads the
same variables back out of the CURRENT process environment — proving the
switch actually fired, not merely that the adapter's docstring claims it does.

SCOPE, STATED HONESTLY. "No outbound host outside the profile is reachable" is
a network property, and asserting it for real means a live network probe.
That does not belong in a check that runs at unit speed on every commit — a
flaky or slow assertion here is a check nobody trusts, which is worse than no
check. What this function proves instead is the precondition that makes the
network property true by construction: the client is disarmed at the switch.
It is deliberately an environment-variable assertion, not a network probe —
that is a scoping decision, not a shortcut, and it is the honest version of
what ADR-0018 asks for from a check that has to run in CI.
"""

from __future__ import annotations

import os

from conformance.kit import Check, Verdict

#: Names langgraph/langchain honor, mirroring
#: `cinqflow.adapters.langgraph.agent_runtime._silence_telemetry` exactly —
#: verified here rather than trusted, because a kill-switch nobody checks is a
#: comment, not a control.
_MUST_BE_FALSE = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2")
_MUST_BE_EMPTY = ("LANGCHAIN_ENDPOINT", "LANGSMITH_ENDPOINT", "LANGCHAIN_API_KEY")


def check_egress() -> Check:
    """PASS iff the langgraph adapter's telemetry kill-switch has fired.

    Importing the adapter IS the assertion setup: `_silence_telemetry()` runs
    as a side effect of the import, before any langchain module can act on a
    variable it might otherwise have found already set by an operator's shell.
    """
    import cinqflow.adapters.langgraph  # noqa: F401  (import triggers the kill-switch)

    unset_false = [n for n in _MUST_BE_FALSE if os.environ.get(n, "").lower() != "false"]
    unset_empty = [n for n in _MUST_BE_EMPTY if os.environ.get(n, "")]
    offenders = unset_false + unset_empty

    if offenders:
        return Check(
            "law:egress-silenced",
            Verdict.FAIL,
            f"telemetry kill-switch did not fire for: {', '.join(offenders)}",
        )
    return Check(
        "law:egress-silenced",
        Verdict.PASS,
        f"{len(_MUST_BE_FALSE) + len(_MUST_BE_EMPTY)} langsmith/langchain telemetry "
        "variables pinned off after importing the langgraph adapter (ADR-0018)",
    )
