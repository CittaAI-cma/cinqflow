"""The `notification` pin — alert by severity.

    verb: alert_by_severity   mock: console   dev: slack_webhook
    target: slack|email|ticket|webhook
    — docs/architecture/plates/04-pin-out-map.md

Slack, Teams, ticketing systems and generic webhooks are all an HTTP POST, so
no vendor SDK earns its place behind this pin.

The verb is `alert_by_severity`, not `send_message`, because the routing
decision (who hears about a Critical, who hears about a Warning) is platform
policy that belongs in the profile — not a channel name chosen at a call site.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, unique
from typing import Protocol, runtime_checkable

from cinqflow.core.citations import CitationId


@unique
class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Alert:
    """An alert that explains itself.

    The incident library's standing complaint about the incumbent platform is
    that alerts classify and route but never explain: "nothing explains why a
    feed is late, what is affected, or who to contact. Every alert becomes an
    investigation task."

    So `citations` is not optional decoration — it is the field that makes an
    alert openable instead of investigable.
    """

    severity: Severity
    summary: str
    detail: str = ""
    citations: tuple[CitationId, ...] = field(default_factory=tuple)


@runtime_checkable
class NotificationPort(Protocol):
    def alert(self, alert: Alert) -> None:
        """Dispatch by severity, per the profile's routing. Never raises into
        the caller: a pipeline must not fail because Slack is down."""
        ...
