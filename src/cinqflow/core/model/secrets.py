"""The `secret://name` reference form — a convention, not a capability.

    "Everything secret is a `secret://name` REFERENCE. Naming a secret is not
     holding one."

Which is exactly why this belongs in core: recognising the form is domain
knowledge that the connection profile depends on, while RESOLVING it is the
secrets pin's job. Core knows what a reference looks like and can therefore
refuse a literal credential; it has no way to turn one into a value.

`ports/secrets.py` re-exports these.
"""

from __future__ import annotations

import re

SECRET_REFERENCE = re.compile(r"^secret://(?P<name>[A-Za-z0-9._-]+)$")


def is_reference(value: str) -> bool:
    """True for `secret://name`. The core-purity lint exempts exactly this form."""
    return bool(SECRET_REFERENCE.match(value.strip()))


def reference_name(value: str) -> str:
    match = SECRET_REFERENCE.match(value.strip())
    if not match:
        raise ValueError(f"not a secret reference: {value!r} (expected `secret://name`)")
    return match["name"]
