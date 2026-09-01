"""Populating a CINQFLOW data plane from the client's real de-identified extracts.

    "Populate by RUNNING the platform, not by INSERT."

WHY THIS LIVES IN `scripts/` AND NOT IN `src/cinqflow/`. The chip is not
modified to load data into it. Every object below is built from the platform's
OWN public surface — `FeedRecord`, `SchemaContract`, `compile_feed`,
`PipelineRunner`, the `connector` and `storage` pins — so a population run
exercises exactly the code path a real delivery takes. Nothing here is
importable by the platform, and `lint-imports` never sees it.

The consequence worth stating: a `COPY` into `bronze.members_raw` would finish
in a minute and prove nothing. There would be no fingerprint in
`control.input_registry`, no `landing_ctl.landing_event`, no reconciliation,
no quarantine attributed to a named rule, no `governance.audit_ledger` row and
no `profiling.file_profile`. Running the pipeline fills TWENTY tables as a
consequence of two, and those eighteen others are where the observability
lives.

    plane.py     the reusable connection — one function, any profile
    sources.py   the ten payer sources, as DATA
    populate.py  the driver
"""
