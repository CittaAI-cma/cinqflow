"""conformance/checks/ — checks that are not "does a pin's protocol exist".

`conformance/kit.py:check_pin` certifies a PIN against its own Protocol — one
check, twenty-one pins. This subpackage holds checks about a platform LAW that
a pin check cannot express, starting with the one ADR-0018 owes: proving the
langgraph adapter's telemetry kill-switch actually fired, not merely that its
docstring claims it did.
"""

from __future__ import annotations

from conformance.checks.egress import check_egress

__all__ = ["check_egress"]
