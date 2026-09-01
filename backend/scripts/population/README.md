# Populating a CINQFLOW data plane

Loads the client's real de-identified extracts into a real Postgres plane by
**running the platform's own pipeline**. Nothing in `src/cinqflow/` is modified,
and nothing here is imported by the platform.

Design rationale: [ADR-0024](../../docs/adr/ADR-0024-populating-the-plane-is-a-pipeline-run.md).

---

## Quick start

**One plane.** This runs against `cinqflow` — the same database the platform,
`cinqflow ingest` and `pytest tests/pipeline` all address. The population was
proved on a separate `cinqflow_pop` first and then migrated here; that second
database is gone. See [the migration note](#the-migration) for what that cost.

```bash
cd cinqflow

# 1. the plane already exists. re-run install only after a schema change (additive).
PYTHONPATH=src .venv/bin/python -m cinqflow.installer.cli install --profile profiles/local.yaml

# 2. see what is available, and where each source can reach
cd scripts && PYTHONPATH=../src ../.venv/bin/python -m population.populate --list

# 3. register the feeds as governed objects (DRAFT)
PYTHONPATH=../src ../.venv/bin/python -m population.populate --all --metadata

# 4. load. one source first, to measure.
PYTHONPATH=../src ../.venv/bin/python -m population.populate --source optum-ga

# 5. everything
PYTHONPATH=../src ../.venv/bin/python -m population.populate --all

# 6. the receipt: every table, its count, and the medallion census
PYTHONPATH=../src ../.venv/bin/python -m population.populate --report
```

`--profile` defaults to `profiles/local.yaml`. Point it elsewhere to target
another plane; that is the only change needed — see *Reusing this* below.

## The three modules

| File | Job |
|---|---|
| `plane.py` | **The reusable connection.** `open_plane(profile)` returns every seat a populator needs — control tables, compute, metadata, storage, connector, and the W3-01 layer reader — all fitted from one profile and sharing one connection. |
| `sources.py` | **The ten payer sources, as data.** Every number measured from the files. Grain, key column, contract mappings, `is_phi` per column. |
| `populate.py` | **The driver.** Delivery, the two load paths, metadata registration, reporting. |

### `plane.py` is the part to reuse

```python
from population.plane import open_plane, table_counts

# reads — autocommit, no write lock held
with open_plane("profiles/local.yaml") as plane:
    print(table_counts(plane))
    print(plane.reader.census(spec_of(Layer.BRONZE)).row_count)

# writes — ONE transaction, committed on clean exit, rolled back on any exception
with open_plane("profiles/local.yaml", atomic=True) as plane:
    plane.control.open_batch(...)
    plane.compute.land_bronze(...)
```

It holds no module-level connection, reads no environment variable of its own,
and defaults to no database. The profile decides which plane — so populating a
second database, or rung 3, is an argument and never an edit.

## Two load paths, and why

**Member-grain sources** go through `PipelineRunner` — the platform's own
runner, the same one `cinqflow ingest` uses. Landing controls, fingerprint
check, drift classification, cast, DQ rules, quarantine attribution,
reconciliation and the balance equation all run for real.

**Segment-grain sources** stop at Bronze, via `PostgresCompute.land_bronze` —
again the platform's own adapter call. `silver_raw.members` is unique on
`(batch_id, feed_id, source_member_id)` and these sources send 2.4–11.9 rows per
member, so at full grain they collide by construction. Running them anyway would
roll back the whole transaction and lose Bronze too.

> `compile_feed` accepts `terminal_layer`, but `PipelineRunner.run()` does not
> read it — it advances to Silver Raw unconditionally. The driver does the
> stopping instead. Honouring the field in the runner is a small platform story.

## The ten sources

| Key | Rows | Grain | Reaches | Note |
|---|---|---|---|---|
| `fidelis-upstate` | 28,333 | member | Silver Raw | |
| `fidelis-downstate` | 38,489 | member | Silver Raw | xlsx; filename starts with `_` |
| `molina-ny` | 332,679 | segment | Bronze | 4 files, one feed; SCD-2 spans Feb 24 – Jan 26 |
| `centene-ga-medicaid` | 7,817 | member | Silver Raw | one `MEMBER_NAME` field, no first/last split |
| `centene-ga-medicare` | 7,731 | member | Silver Raw | |
| `optum-ga` | 3,814 | member | Silver Raw | incident #12's source |
| `optum-ny` | 37,104 | segment | Bronze | carries `CURR_MBI` — nothing can hold it yet |
| `centene-il` | 26,489 | member | Silver Raw | |
| `aco-reach` | 75,611 | segment | Bronze | **CSV named `.xlsx`** — format is declared, never sniffed |
| `cmp-1598` | 10,800 | member | Silver Raw | 576 duplicate `Account` values |

**ADT is deliberately excluded.** 437 rows, 142 columns, one row per encounter
*event*. The only Bronze table is `bronze.members_raw`, and a table name is part
of the contract — filing encounter events under members would make every
downstream count of "members" silently wrong.

## The migration

Built and proved on a separate `cinqflow_pop` database, then migrated onto
`cinqflow` so the platform's own port serves it. The migration was a **re-run,
not a dump/restore** — every row arrived through the pipeline again, with fresh
batch ids, fingerprints, drift classification and reconciliation. That is only
cheap because `open_plane` takes a profile: the whole migration was
`--profile profiles/local.yaml`.

Three things it cost, all recorded in
[ADR-0024](../../docs/adr/ADR-0024-populating-the-plane-is-a-pipeline-run.md):

1. **Two tests had to be fixed first.** Both asserted against an empty-ish
   plane. Fixing them before loading was mandatory — afterwards they would have
   been permanently red with no way to tell a real regression from the
   migration.
2. **The synthetic fixtures were truncated.** 300 Bronze / 294 Silver rows and
   one batch. Also a **fingerprint collision**: `control.input_registry` already
   held the Fidelis upstate file, so the replay guard would have refused the
   migration until that row went.
3. **`catalog` had to be fitted in `profiles/local.yaml`.** It was
   `adapter: none`; W3-01 fitted a real one. Without it the layer census has no
   seat on the platform plane.

## Measured result

```
Bronze        568,867     100% of available enrolment rows, all source columns
Silver Raw    122,892     7 member-grain sources
Landing             0     no writer exists (see below)
Identity / Silver ODS / Gold  —   no tables
14 of 30 tables populated
```

Timing on a local container: ~1,400 rows/sec through the full spine,
~3,700 rows/sec Bronze-only. Molina's 332,679 rows in 90s.

## What the platform refused, and was right to

Three guards fired. None was worked around silently — see ADR-0024 for each.

1. **`PatternSampleMismatchError`** — a feed's pattern must match its own
   sample. One `production_filename()` feeds both, so they cannot disagree.
2. **"not a portable filename"** — six files carry spaces, which a rung-3 blob
   container will not accept. Normalised, with the three affected sources named
   in `NON_PORTABLE_FILENAMES`. **The real fix is upstream, at the payer.**
3. **"already in the landing zone"** — `deliver()` writes to the filesystem,
   which is *not* in the database transaction, so a rolled-back run leaves an
   orphan file. The driver recovers it rather than delivering a second copy.

## Two platform gaps this exposed

- **`landing_ctl.landing_event` has no writer.** `core/registry/wave0.py`
  declares the landing stage writes it; no code does. It is 0 rows after a
  569k-row load. The populator does not insert into it — a hand-written arrival
  ledger would hide the gap.
- **`PipelineRunner` ignores `plan.terminal_layer`.** See above.

## Reusing this next time

**A different plane** — `--profile path/to/other.yaml`. Nothing else.

**A new source** — one `Source(...)` entry in `sources.py`. Measure the file
first (`wc -l`, `head -1`, and a distinct-key count) and put the real numbers
in; they are asserted against at load time and a wrong one is caught there.
Set `is_phi` on every mapping that carries a member value — **that flag is the
only thing that drives masking.**

**A new business date** — `--business-date 2026-04-01`. Filenames and
fingerprints differ per date, so a second cycle is a second batch rather than a
replay refusal. This is how you build the operating history that trend-bearing
screens need.

**Starting over** — `TRUNCATE` the data and control tables as the table owner,
then re-run. Bronze is append-only, so `DELETE` fires its reject-trigger
(correctly) and `TRUNCATE` is the sanctioned reset. The migration used exactly
this, and it deliberately left `registry.governed_object` alone — those 172 rows
are the client's real seeded glossary, not fixtures:

```sql
TRUNCATE bronze.members_raw, silver_raw.members, quarantine.quarantined_rows;
TRUNCATE recon.recon_history, recon.rule_results;
TRUNCATE control.batch_control, control.batch_stage_status, control.input_registry,
         control.batch_reconciliation, control.error_log, control.quarantine_records,
         control.schema_drift_log, control.feed_sla_config;
```

## Guardrails

- **This IS the plane `pytest tests/pipeline` uses.** That was a deliberate
  migration, not an accident — but it means two things. Bronze is append-only,
  so a bad load is permanent short of a `TRUNCATE`. And any test that assumes an
  empty plane will fail: two did, and both were fixed to scope their assertions
  rather than assert on the whole table
  (`test_bronze_accepts_inserts`, `test_batches_list_newest_first_for_a_feed`).
  **A new test that counts a whole data table is a bug in the test.**
- **De-identified is not non-PHI.** These files carry names, DOBs and addresses.
  Masking is driven by `is_phi` in `sources.py`. After any load, open
  `/data/layers/silver_raw/members` and confirm name, DOB and member id render
  as bullets.
- **Delivery is not transactional with the database.** Files land on disk
  outside the transaction. A failed run leaves an orphan; the driver recovers
  it, but a `--dry-run` first is cheaper.
