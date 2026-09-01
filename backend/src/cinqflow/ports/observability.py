"""The `observability` pin — logs, metrics and traces.

    verb: logs_metrics_traces   mock: noop   dev: otel_grafana
    target: otel_log_analytics
    — docs/architecture/plates/04-pin-out-map.md

The app emits plain OTLP at EVERY rung. A collector — a service in the same
Helm chart — forwards to Grafana in the twin and Azure Monitor in the tenant,
so the environment difference lands in chart values where it belongs and this
pin costs ZERO Python dependencies at rung 3.

That was measured, not assumed: the obvious rung-3 move (a vendor OTel
exporter) hard-pins the OTel SDK, which would weld the whole platform's
telemetry version to one vendor's release cadence — in the DEVELOPMENT image,
for a package only rungs 3-4 would ever use.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ObservabilityPort(Protocol):
    def log(self, event: str, /, **fields: Any) -> None:
        """A structured event. Never a formatted sentence.

        PHI never reaches here: logs are one of the classic leak routes, and
        masking is structural rather than procedural in every rendering path.
        """
        ...

    def metric(self, name: str, value: float, /, **labels: str) -> None: ...

    @contextmanager
    def span(self, name: str, /, **attributes: Any) -> Iterator[None]:
        """Trace one unit of work. Used by the agent runtime to trace each
        node's entry and exit, which is what makes a run reproducible from the
        prompt hashes alone."""
        ...
