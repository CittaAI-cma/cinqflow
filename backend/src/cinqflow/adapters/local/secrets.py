"""dotenv — secrets from the process environment. Rungs 0.5 and 1.

    verb: fetch_by_name   mock: mem   dev: env_files   target: key_vault
    — docs/architecture/plates/04-pin-out-map.md

The naming convention is the whole adapter:

    secret://llm-key   ->   CINQFLOW_SECRET_LLM_KEY

Mechanical, so a profile reference and an environment variable can never drift
apart by a typo nobody notices until a credential silently resolves to "". Key
Vault at rung 3 keeps the same reference form and changes only the lookup —
which is what makes that swap roughly forty lines instead of a migration.

A missing secret RAISES. Returning "" would push the failure to an adapter far
away, where it surfaces as an authentication error nobody can trace back to a
profile line.
"""

from __future__ import annotations

import os

from cinqflow.ports import port
from cinqflow.ports.secrets import SecretNotFoundError, is_reference, reference_name

PREFIX = "CINQFLOW_SECRET_"


def env_var_for(name: str) -> str:
    """`llm-key` -> `CINQFLOW_SECRET_LLM_KEY`. One rule, no exceptions table."""
    return PREFIX + name.replace("-", "_").replace(".", "_").upper()


@port("secrets", "dotenv")
class DotenvSecrets:
    """Reads the environment. The `.env` file is loaded by the shell or the
    installer, never by this class — a library that reads files from the
    working directory behaves differently depending on where it was started."""

    def __init__(self, environ: dict[str, str] | None = None) -> None:
        self._environ = dict(environ) if environ is not None else dict(os.environ)

    def fetch(self, name: str) -> str:
        variable = env_var_for(name)
        value = self._environ.get(variable, "")
        if not value:
            raise SecretNotFoundError(
                f"no secret named {name!r}: {variable} is unset or empty. Profiles carry "
                f"`secret://{name}` references; this environment must carry {variable}."
            )
        return value

    def resolve(self, value: str) -> str:
        return self.fetch(reference_name(value)) if is_reference(value) else value
