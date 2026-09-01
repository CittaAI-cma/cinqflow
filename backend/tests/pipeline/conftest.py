"""The rung-0.5 test harness — every test inside a rolled-back transaction.

    "Let every pipeline test run inside a rolled-back transaction, so thousands
     of tests finish in minutes with no cleanup code."
    — CF-V0-E8-07

    "Why Postgres makes the tests BETTER, not just cheaper: transactions give
     perfect isolation with no cleanup code; assertions are SQL row-level
     diffs; the balance equation is one query per stage; and a failing test
     leaves a database you can open and query."
    — memory/03-directives/02-testing-pyramid.md

The alternative — truncating between tests — is slower, order-dependent, and
destroys the evidence at exactly the moment someone needs it.

`pg_profile`, `plane` and `_load_dotenv` now live in the root `tests/conftest.py`
— any suite needing the rung-0.5 plane uses them, not only this one.
"""

from __future__ import annotations
