"""`db.connect` refuses an unresolved Railway reference variable with a clear
error, instead of letting psycopg's own confusing parse error surface.

Regression coverage for a real incident: `CINQFLOW_DATABASE_URL` was set to
`${{Postgres.DATABASE_URL}}` with the Postgres service misnamed (and, in a
second occurrence, with the leading `$` dropped entirely by whatever pasted
the value in) - Railway never substituted it, and psycopg received the
literal template string. Its error ("missing '=' after '{{Postgres....'")
gives no hint that the real problem is a templating mismatch, not a
malformed connection string - this had to be diagnosed from a stack trace
with no other signal.
"""

from __future__ import annotations

import pytest

from cinqflow.db import UnresolvedDatabaseUrl, _check_database_url
from cinqflow.settings import Settings


@pytest.mark.parametrize(
    "url",
    [
        "${{Postgres.DATABASE_URL}}",
        "postgresql://user:${{Postgres.PASSWORD}}@host:5432/db",
        "{{Postgres.DATABASE_URL}}",  # the leading `$` dropped, seen in practice
    ],
)
def test_unresolved_reference_variable_is_rejected(url):
    with pytest.raises(UnresolvedDatabaseUrl):
        _check_database_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://cinqflow:cinqflow@postgres:5432/cinqflow",
        "postgresql://user:pass@containers-us-west-1.railway.app:5432/railway",
    ],
)
def test_resolved_urls_pass(url):
    _check_database_url(url)  # must not raise


def test_settings_default_is_not_flagged_as_unresolved():
    # The localhost default is a *different* problem (see bootstrap.sh's own
    # check for it) - it must not also trip the reference-variable check.
    _check_database_url(Settings().database_url)
