"""The `secrets` pin — fetch a secret by name.

    verb: fetch_by_name   mock: mem   dev: env_files   target: key_vault
    — docs/architecture/plates/04-pin-out-map.md

    "no model credentials exist outside the LLM gateway"
    "Store model credentials anywhere except the secrets port." (a documented don't)

Everything in a connection profile is a `secret://name` REFERENCE. Resolution
is the adapter's job — dotenv at rungs 0.5-1, Key Vault at rung 3 — and the
reference format NEVER changes, which is what makes that swap roughly forty
lines instead of a migration.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from cinqflow.core.model.secrets import SECRET_REFERENCE, is_reference, reference_name

__all__ = [
    "SECRET_REFERENCE",
    "SecretNotFoundError",
    "SecretsPort",
    "is_reference",
    "reference_name",
]


class SecretNotFoundError(KeyError):
    """A named secret the environment does not carry.

    Refused loudly and by name. The alternative — falling back to a default or
    an empty string — turns a misconfigured environment into a runtime mystery,
    and for a credential it turns it into a silent security change.
    """


@runtime_checkable
class SecretsPort(Protocol):
    def fetch(self, name: str) -> str:
        """The secret's value, or SecretNotFoundError. Never a default."""
        ...

    def resolve(self, value: str) -> str:
        """Resolve `secret://name` to its value; pass any other string through.

        This is what lets a profile be read without knowing which of its fields
        are secrets — the reference form carries that, in the profile.
        """
        ...
