"""mem — secrets from a dict. Holds nothing real, on purpose."""

from __future__ import annotations

from cinqflow.ports import port
from cinqflow.ports.secrets import SecretNotFoundError, is_reference, reference_name


@port("secrets", "mock")
class MemSecrets:
    """A dict. Lanes 1 and 2 hold no live credentials, so this is what they get.

    A missing secret raises rather than returning "" — an empty credential
    reaching an adapter produces a confusing auth failure at a distance,
    instead of a clear error at the point of misconfiguration.
    """

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = dict(values or {})

    def fetch(self, name: str) -> str:
        try:
            return self._values[name]
        except KeyError:
            known = ", ".join(sorted(self._values)) or "(none)"
            raise SecretNotFoundError(
                f"no secret named {name!r} in this environment. Known: {known}"
            ) from None

    def resolve(self, value: str) -> str:
        return self.fetch(reference_name(value)) if is_reference(value) else value
