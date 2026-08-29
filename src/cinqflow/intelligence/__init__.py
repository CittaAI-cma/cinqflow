"""The intelligence plane's SERVICES — the gateway, the tools, the runner.

Below `api` and above `adapters`, because the API serves this and this uses
pins. `core/` still holds every decision that can be made without a pin:
the prompt registry, the call-stage order, the budget arithmetic, the schema
subset, and the agent graphs themselves.
"""
