"""The conformance kit — one check per energized pin, each naming its pin.

    "conformance kit with one check per energized pin, each naming its pin"
    "climbing a socket rung changes ONLY the profile"
    — docs/architecture/INVARIANTS.md, chip discipline

The kit is what makes plug-and-play a MEASUREMENT rather than a hope. Fitting a
new adapter is a CERTIFICATION — run the kit, read the pin names — instead of a
migration nobody can score.

Two design decisions worth stating:

  • Every check NAMES ITS PIN in its result, so a failure says
    `storage: no move verb` rather than `AssertionError`. A conformance report
    a person cannot read is a conformance report nobody runs twice.

  • A pin the profile says is `none` is REPORTED as unfitted, not skipped
    silently. "cache: adapter none (ADR-0014 — no implementation until
    measured)" is information; an absent line is an oversight that looks like a
    pass.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from enum import StrEnum, unique
from importlib import import_module

from cinqflow.core.model.profile import Profile
from cinqflow.ports import PIN_GROUPS, PORTS, fitted


@unique
class Verdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNFITTED = "UNFITTED"


@dataclass(frozen=True)
class Check:
    pin: str
    verdict: Verdict
    detail: str

    def line(self) -> str:
        mark = {Verdict.PASS: "✓", Verdict.FAIL: "✗", Verdict.UNFITTED: "·"}[self.verdict]
        return f"  {mark} {self.pin:<16} {self.verdict.value:<9} {self.detail}"


def _protocol_verbs(pin: str) -> frozenset[str]:
    """The verbs the port declares — read off the Protocol itself.

    Read rather than listed, so the kit cannot drift from the ports. Adding a
    verb to a port immediately makes every fitted adapter answer for it, which
    is precisely the pressure that keeps a stand-in honest.
    """
    module = import_module(f"cinqflow.ports.{pin}")
    protocol = next(
        (
            member
            for name, member in vars(module).items()
            if name.endswith("Port") and isinstance(member, type)
        ),
        None,
    )
    if protocol is None:
        return frozenset()
    return frozenset(
        name
        for name in dir(protocol)
        if not name.startswith("_") and callable(getattr(protocol, name, None))
    )


def _adapters_chosen(pin: str, profile: Profile | None) -> tuple[str, ...]:
    """Every DISTINCT adapter a profile actually fits to this pin.

    Almost every pin names one adapter (`profile.adapter_for`). `connector` is
    the exception CF-V1-E8-09 introduced: it is chosen PER ROUTE
    (`endpoint_ref` -> adapter), because two feeds can arrive by different
    methods on one socket. A pin config carrying `routes` is read as the set
    of adapters across every route rather than one — climbing a rung has to
    check every adapter a profile actually fits, or a route nobody wired
    a working adapter for would certify GREEN by never being asked about.
    """
    if profile is None:
        return ()
    config = profile.pins.get(pin, {})
    routes = config.get("routes")
    if isinstance(routes, dict):
        names = {
            str(route.get("adapter", "none"))
            for route in routes.values()
            if isinstance(route, dict)
        }
        return tuple(sorted(names - {"none"}))
    single = profile.adapter_for(pin)
    return () if single in ("none", None) else (single,)


def check_pin(pin: str, profile: Profile | None) -> Check:
    """One pin, certified against ONE contract — the port's own protocol."""
    adapters = fitted(pin)
    if not adapters:
        return Check(pin, Verdict.UNFITTED, "no adapter registered for this pin")

    chosen = _adapters_chosen(pin, profile) if profile else None
    if chosen == () and profile is not None:
        return Check(pin, Verdict.UNFITTED, f"profile says `none` — {PORTS[pin].verb}")

    if chosen is not None:
        unknown = [name for name in chosen if name not in adapters]
        if unknown:
            # "climbing a rung changes only the profile" is only true if the
            # profile's CHOICE is checked, not merely whatever adapter happens
            # to be registered. Without this, a profile naming a fictional
            # adapter (`pg-compute`, `presidio`, ...) certifies GREEN against
            # whichever real adapter is fitted for someone ELSE's reason — the
            # platform cannot know which environment it is actually in.
            return Check(
                pin,
                Verdict.FAIL,
                f"profile names {', '.join(f'`{name}`' for name in unknown)}, which "
                f"{'is' if len(unknown) == 1 else 'are'} not registered — fitted: "
                f"{', '.join(sorted(adapters)) or 'none'}",
            )

    verbs = _protocol_verbs(pin)
    if not verbs:
        return Check(pin, Verdict.FAIL, "the port declares no verb")

    missing: list[str] = []
    for name, adapter in sorted(adapters.items()):
        absent = [verb for verb in verbs if not hasattr(adapter, verb)]
        if absent:
            missing.append(f"{name} lacks {', '.join(sorted(absent))}")

    if missing:
        return Check(pin, Verdict.FAIL, "; ".join(missing))
    return Check(
        pin,
        Verdict.PASS,
        f"{PORTS[pin].verb} · {len(verbs)} verbs · adapters: {', '.join(sorted(adapters))}",
    )


#: Checks that are not about one pin but about the platform's own laws. They
#: run in the same report because a person certifying a socket needs one answer,
#: not two commands.
def _law_checks() -> list[Check]:
    from cinqflow.core.agents.pipeline_insight.graph import RISK_CLASS
    from cinqflow.core.prompts import ASSEMBLY_ORDER, PromptSection
    from cinqflow.core.tools import CATALOGUE, FORBIDDEN_READS
    from cinqflow.intelligence.action_gateway import ActionGateway

    checks: list[Check] = []

    leaks = sorted(spec.name for spec in CATALOGUE.values() if spec.reads & FORBIDDEN_READS)
    checks.append(
        Check(
            "law:no-member-rows",
            Verdict.FAIL if leaks else Verdict.PASS,
            f"{', '.join(leaks)} read a data layer"
            if leaks
            else f"{len(CATALOGUE)} certified tools, none reaching a data layer",
        )
    )

    writes = [
        verb
        for verb in ("retry_batch", "pause_feed", "edit_mapping", "delete_audit")
        if ActionGateway().permit(verb)
    ]
    checks.append(
        Check(
            "law:r0-read-only",
            Verdict.FAIL if writes else Verdict.PASS,
            f"the action gateway permitted {', '.join(writes)}"
            if writes
            else f"risk class {RISK_CLASS}: every write verb refused",
        )
    )

    ordered = list(ASSEMBLY_ORDER)
    correct = ordered.index(PromptSection.CONSTRAINTS) < ordered.index(PromptSection.INPUT)
    checks.append(
        Check(
            "law:input-last",
            Verdict.PASS if correct else Verdict.FAIL,
            "untrusted input is assembled after every constraint"
            if correct
            else "INPUT precedes CONSTRAINTS — an injection would be the instruction",
        )
    )
    return checks


def _egress_check() -> Check:
    """ADR-0018's telemetry kill-switch, checked rather than trusted.

    A local import: `conformance.checks.egress` imports `Check`/`Verdict` FROM
    this module, so importing it at module scope here would be a real cycle.
    Deferred exactly like `_law_checks()`'s own imports, for the same reason.

    This module is also run directly (`python conformance/kit.py`, per
    README.md, ci.yml and scripts/wave0-demo.sh) rather than only via
    `python -m conformance.kit`. Run that way, Python puts THIS file's own
    directory on `sys.path`, not its parent — so the `conformance` PACKAGE
    (needed for the absolute import below) is not importable yet. Fix that
    narrowly, here, rather than depending on the caller's invocation style.
    """
    from pathlib import Path

    repo_root = str(Path(__file__).resolve().parent.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    from conformance.checks.egress import check_egress

    return check_egress()


def run(profile: Profile | None = None) -> list[Check]:
    checks: list[Check] = []
    for group, pins in PIN_GROUPS.items():
        _ = group
        checks.extend(check_pin(pin, profile) for pin in pins)
    checks.extend(_law_checks())
    checks.append(_egress_check())
    return checks


def report(profile: Profile | None = None) -> tuple[str, bool]:
    checks = run(profile)
    lines: list[str] = []
    for group, pins in PIN_GROUPS.items():
        lines.append(f"\n{group}")
        lines.extend(c.line() for c in checks if c.pin in pins)
    lines.append("\nplatform laws")
    lines.extend(c.line() for c in checks if c.pin.startswith("law:"))

    passed = sum(1 for c in checks if c.verdict is Verdict.PASS)
    failed = sum(1 for c in checks if c.verdict is Verdict.FAIL)
    unfitted = sum(1 for c in checks if c.verdict is Verdict.UNFITTED)
    lines.append(f"\n{passed} pass · {failed} fail · {unfitted} unfitted")
    return "\n".join(lines), failed == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Certify a socket, pin by pin.")
    parser.add_argument("--profile", help="path to a connection profile")
    arguments = parser.parse_args(argv)

    loaded: Profile | None = None
    if arguments.profile:
        from cinqflow.installer.profile import load

        loaded = load(arguments.profile)

    _register_adapters()
    text, green = report(loaded)
    print(text)
    print("\nconformance:", "GREEN" if green else "RED")
    return 0 if green else 1


def _register_adapters() -> None:
    """Import the adapter packages so the @port decorator has run.

    Explicit rather than implicit: a kit that silently reported on whatever
    happened to be imported would grade a different socket depending on how it
    was invoked.
    """
    import cinqflow.adapters.langgraph
    import cinqflow.adapters.local
    import cinqflow.adapters.mock
    import cinqflow.adapters.openai_compatible
    import cinqflow.adapters.replay  # noqa: F401
    import cinqflow.adapters.sftp  # noqa: F401


if __name__ == "__main__":
    sys.exit(main())
