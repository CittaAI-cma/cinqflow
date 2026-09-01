# ADR-0024 — Populating the plane is a pipeline run, not an INSERT

**Status** Accepted · **Date** 2026-08-31 · **Supersedes** nothing ·
**Relates to** ADR-0002 (Postgres is the real dev data plane), ADR-0011
(universal landing contract), ADR-0013 (additive only to the incumbent estate)

## Context

The plane provisioned 30 tables and held roughly 300 synthetic rows. The
client's real de-identified extracts — **568,867 enrolment rows across 10 payer
sources, plus 437 ADT rows** — had never been loaded, so no screen, no agent and
no reconciliation figure had ever been exercised against real data. Twenty of
the thirty tables were empty.

The obvious way to fix that is a `COPY` into `bronze.members_raw` and
`silver_raw.members`. It takes a minute.

## Decision

**Population happens by running the platform's own pipeline, and the populator
lives outside `src/cinqflow/`.**

A `COPY` would produce rows with no lineage: no fingerprint in
`control.input_registry`, no `control.batch_control` row, no drift
classification, no quarantine attributed to a named rule, no reconciliation. It
would test Postgres, not CINQFLOW. Running the real pipeline **fills fourteen
tables as a consequence of two**, and those twelve others are where every
observable property of the platform actually lives.

The populator is `scripts/population/` — three modules, no import from the
platform into it, and no file in `src/cinqflow/` changed. This is possible
because `PipelineRunner.run()` already takes `feed`, `feed_version`,
`contract`, `rules` and `plan` as **parameters**. The reason `cinqflow ingest`
only ever loaded Fidelis is that the CLI hard-codes one import
(`core/registry/golden_fidelis`) and passes `source_system="fidelis"`; the
runner itself is fully general. Ten payers therefore arrive as data.

### The three consequences worth recording

**1. Grain decides the terminal layer.** `silver_raw.members` is unique on
`(batch_id, feed_id, source_member_id)`. Six of the ten sources send 2.4 to
11.9 rows per member — coverage segments and monthly alignment — so at full
grain they collide by construction. **445,394 rows have no legal home in Silver
Raw.** They stop at Bronze, where `raw_row` is schemaless `jsonb` and keeps all
25–111 source columns intact. Collapsing them to latest-segment would fit the
constraint and discard 389,402 rows of coverage history, which is exactly the
data that makes readmission windows computable. Bronze-only loses nothing; it
declines to pretend.

**2. A separate database to prove it, then one plane to run it.** Bronze is
append-only at the database layer — a bad load cannot be deleted, only rolled
back inside its own transaction. So the population was built and proved against
`cinqflow_pop` (`profiles/pop.yaml`, `secret://pg-pop-dsn`), leaving the plane
`pytest tests/pipeline` runs against untouched and the whole thing reversible
with `DROP DATABASE`.

**It was then migrated onto `cinqflow` and the second database dropped** — see
*The migration*, below. Two planes serving the same product is a standing
invitation to read a number off the wrong one, and the populator takes a profile
precisely so that consolidating costs one argument rather than a rewrite.

**3. Registered metadata is a separate phase from the load, deliberately.** The
runner never reads the registry — a load succeeds whether or not the feed was
ever stored. That is convenient and it is also how a plane ends up holding
569,000 rows attributed to feeds that officially do not exist. `--metadata`
writes 10 feeds, 10 contracts and 8 rule sets into `registry.governed_object`
as **DRAFT**. The populator does not approve its own metadata; forging that
signature would defeat the one thing the registry exists to record.

## What the platform refused, and was right to

Three guards fired on the first runs. None was worked around silently.

| Refusal | Why it was right | What the populator does |
|---|---|---|
| `PatternSampleMismatchError` on every feed | A registry entry whose pattern cannot match its own sample would certify a feed that then fails at delivery | One `production_filename()` function feeds both the `sample_filename` and the connector, so the two cannot disagree |
| *"not a portable filename"* — 6 files carry spaces | Letters, digits, dot, dash, underscore is the intersection of what localfs at rung 0.5 and a blob container at rung 3 both accept | Normalises spaces to underscores, records the three affected sources in `NON_PORTABLE_FILENAMES`, and states that the real fix is upstream |
| *"already in the landing zone"* | `deliver()` writes to the filesystem, which is **not** inside the database transaction — a rolled-back run leaves an orphan file with no registry row | Recovers the orphan by reading it, rather than delivering a second copy under another name |

The middle one is a genuine onboarding finding, not a nuisance: **a payer that
sends spaces in filenames cannot deliver into a rung-3 blob container.**

## Measured behaviour that contradicted the plan

The population plan predicted that CMP_1598's **576 duplicate `Account` values**
would fail the batch on the uniqueness constraint. **They do not.** The compiler
dedups on the key and attributes all 576 as `attributed_drops`, storing them in
`quarantine.quarantined_rows` under `DUPLICATE-source_member_id`, and the
balance equation holds with `unattributed = 0`. Fidelis downstate contributed 5
more by the same path. "Every drop attributed" is a working property, not an
aspiration — and the prediction was wrong in the platform's favour.

## Two gaps this exposed, both real

**`landing_ctl.landing_event` has no writer.** `core/registry/wave0.py`
declares it as a table the landing stage *writes* (`writes=frozenset({...,
"landing_ctl.landing_event"})`), and no code writes it. It is 0 rows after a
569k-row population. The populator does **not** insert into it — a hand-written
arrival ledger would hide the gap rather than report it.

**`PipelineRunner` ignores `plan.terminal_layer`.** `compile_feed` accepts it and
the IR checks it, but `run()` advances Bronze → Silver Raw unconditionally. The
driver therefore does the stopping itself, using `PostgresCompute.land_bronze`
— the same adapter call the runner's own Bronze stage makes. Honouring the
field in the runner is a small, well-scoped platform story.

## Result

| Layer | Rows | Note |
|---|---|---|
| Landing | 0 | no writer exists — see above |
| Bronze | **568,867** | 100% of available enrolment rows, all source columns |
| Silver Raw | **122,892** | 7 member-grain sources; ceiling was 122,897 |
| Identity · Silver ODS · Gold | — | no tables; Wave 3/4 |

14 of 30 tables populated. 229 source columns classified `added` — *"in the file
but not under contract — ignored, not dropped"* — which is the mapping
suggestion agent's work queue, quantified by the platform itself.

## The migration

The population plane was a scaffold, and keeping it would have meant the
platform's own port (8000/3000) showing 594 synthetic rows while the real
569,000 sat on a second port nobody would remember. So the data was moved onto
`cinqflow` — **by re-running, not by dump/restore**. Every row arrived through
the pipeline a second time, with fresh batch ids, fingerprints, drift
classification and reconciliation. A `pg_dump` would have carried the rows and
their lineage, but it would also have been the one step in this whole exercise
that was not a pipeline run.

Four things had to happen first, in this order, and the order matters:

**1. Fix the two isolation-fragile tests — before the load, not after.**
`test_bronze_accepts_inserts` asserted `count(*) == 1` over the whole of Bronze,
and `test_batches_list_newest_first_for_a_feed` asserted an exact two-element
batch list. Both are true of an empty plane and false of a real one. Loaded
first, they would have been permanently red with no way to distinguish a real
regression from the migration. Both now scope their assertions: the Bronze
helper inserts under a per-call batch id and counts only that, and the batch
list asserts *relative order* plus descending `started_ts` rather than an exact
membership. **A test that counts a whole data table is a bug in the test.**

**2. Truncate the synthetic fixtures.** 300 Bronze / 294 Silver / 1 batch.
`TRUNCATE` as the table owner, not `DELETE` — Bronze's reject-trigger fires on
`DELETE`, correctly. `registry.governed_object` was left alone: its 172 rows are
the client's real seeded glossary. This also cleared a **fingerprint collision**
— `control.input_registry` already held the Fidelis upstate file from an earlier
delivery, so the replay guard would have refused the migration, correctly, until
that row went.

**3. Fit the `catalog` pin in `profiles/local.yaml`.** It was `adapter: none`
because nothing was fitted; W3-01 fitted `pg-information-schema`. Without it the
medallion census has no seat on the platform plane.

**4. Correct the Identity layer's spine declaration.** The identity schema
landed in `schema_spec` while this work was in flight, so the plane grew five
`identity.*` tables — and `core/layers.SPINE` still said *"no schema on the
plane"*. A screen that reports a provisioned schema as absent is lying about the
one thing it exists to show, so Identity moved from `NOT_BUILT` to
`PROVISIONED_EMPTY`. Two tests encoded the old assumption that an unbuilt layer
has no tables; both were rewritten to assert **per status** rather than per
layer name, so the next layer to move statuses needs no test edit.

Result: `cinqflow` holds 568,867 Bronze and 122,892 Silver rows, served on the
platform's own port through the existing **Medallion Layers** sidebar item.
`cinqflow_pop`, `profiles/pop.yaml`, `secret://pg-pop-dsn`, `.cinqflow/pop-landing`
and ports 8010/3010 are all gone.

## Alternatives rejected

- **`COPY` into the two tables.** Fast, and it proves nothing. Rejected above.
- **Deploy `silver_ods` tables so the spine reaches ODS.** That is a
  schema-contract change; the conformance kit would flag it as drift and the
  layer browser would mask every column as unclassified. It belongs in a Wave-3
  story with the canonical model behind it, not in a population run.
- **Load ADT into `bronze.members_raw`.** A table name is part of the contract.
  Filing 437 encounter events under members would make every downstream count
  of "members" silently wrong. ADT waits for `bronze.adt_events`.
- **Collapse the segment sources to fit the constraint.** Discards 389,402 rows
  of coverage history to make a number look complete.
