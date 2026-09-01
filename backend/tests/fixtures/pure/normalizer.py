"""A pure module, shaped exactly like real core/ code. It must PASS.

Without this, a lint that rejected everything would look like a working lint.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from pydantic import BaseModel

_COMPACT = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


class Money(BaseModel):
    amount: Decimal
    currency: str = "USD"


def normalize_date(raw: str) -> date | None:
    """19900101 and 1990-01-01 are the same date, identically on both planes."""
    if m := _COMPACT.match(raw.strip()):
        return date(int(m[1]), int(m[2]), int(m[3]))
    return None
