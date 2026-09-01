"""console — alerts to a list, and to stdout in the twin."""

from __future__ import annotations

from cinqflow.ports import port
from cinqflow.ports.notification import Alert


@port("notification", "mock")
class ConsoleNotification:
    """Records what was dispatched, so a test can assert an alert HAPPENED.

    That assertion matters more than it looks: "Operations is alerted with the
    exact stage where the imbalance appeared" is an acceptance criterion, and
    an unasserted alert is how a pipeline fails silently in production.
    """

    def __init__(self, *, echo: bool = False) -> None:
        self.dispatched: list[Alert] = []
        self._echo = echo

    def alert(self, alert: Alert) -> None:
        # Never raises into the caller: a pipeline must not fail because the
        # notification channel is down.
        self.dispatched.append(alert)
        if self._echo:  # pragma: no cover - operator convenience in the twin
            cites = " ".join(str(c) for c in alert.citations)
            print(f"[{alert.severity.value.upper()}] {alert.summary} {cites}".rstrip())
