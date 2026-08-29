"""slack-webhook — alerts as an HTTP POST. No vendor SDK earns a place here.

    "notification: alert_by_severity   mock: console   dev: slack_webhook
     target: slack|email|ticket|webhook"
    — docs/architecture/plates/04-pin-out-map.md

Wave 1 energizes this pin for APPROVAL EVENTS (CF-V1-E11-01/02) and the
stop-pipeline rule's named recipient (CF-V1-E7-03). Slack, Teams and generic
webhooks all accept the same shape: a JSON POST.

Never raises into the caller — a pipeline must not fail because Slack is down —
and always records what it dispatched, because "Operations is alerted" is an
acceptance criterion and an unasserted alert is how a platform fails silently.
"""

from __future__ import annotations

import json
import urllib.request

from cinqflow.ports import port
from cinqflow.ports.notification import Alert


@port("notification", "slack-webhook")
class WebhookNotification:
    """Constructed with no URL it records without posting — the honest degrade,
    and what the contract suite exercises. The profile supplies the URL as a
    `secret://` reference the wiring resolves."""

    def __init__(self, webhook_url: str | None = None, *, timeout_s: int = 5) -> None:
        if webhook_url is not None and not webhook_url.startswith("https://"):
            raise ValueError("a webhook URL is https or it is not a webhook URL")
        self._url = webhook_url
        self._timeout_s = timeout_s
        self.dispatched: list[Alert] = []
        self.delivery_failures: int = 0

    def alert(self, alert: Alert) -> None:
        self.dispatched.append(alert)
        if self._url is None:
            return
        cites = " ".join(str(c) for c in alert.citations)
        body = json.dumps(
            {"text": f"[{alert.severity.value.upper()}] {alert.summary} {cites}".rstrip()}
        ).encode()
        request = urllib.request.Request(  # noqa: S310 — https enforced in __init__
            self._url, data=body, headers={"Content-Type": "application/json"}
        )
        try:
            urllib.request.urlopen(request, timeout=self._timeout_s)  # noqa: S310
        except Exception:
            # Counted, never raised: the alert is recorded above either way,
            # and a delivery failure is an observability datum, not an incident
            # inside the caller's transaction.
            self.delivery_failures += 1
