# Stage 2 report — 2026-09-02

Scope implemented: **G1 approval → Landing (processed) → Bronze**, with the data-plane
contract, run/balance accounting and end-to-end lineage. Mapping, preview and Silver Raw
are **not** implemented.

> **Instruction conflict, resolved and flagged.** The task header said "Implement Stage 2
> from checklist.md only", while the goal paragraph beneath it restated Stage 1 (already
> delivered on 2026-09-02) and said "Do not implement Bronze". Stage 2 in `checklist.md`
> *is* G1 → Landing + Bronze, so the two cannot both hold. I implemented Stage 2 per the
> header and the checklist, because the goal paragraph is verbatim-identical to the previous
> turn's and Stage 1 is already verified green. Everything added is additive — new tables
> under new names — so it is reversible if Stage 1-only was meant.

---

## 0. Pre-flight traces

| Trace | Finding (read, not assumed) |
|---|---|
| Upload flow | `api/app.py::create_upload` → `workflow.upload` → enqueue `upload.profile`. Reused unchanged. |
| Queue/orchestration | `queue/queue.py`, Postgres `FOR UPDATE SKIP LOCKED`, dedupe unique, claim committed before handler, `reclaim_stale()`. Reused; two topics added. |
| Worker/consumer | `queue/worker.py::handlers()` topic→handler map. Extended with the two new topics. |
| AgentRuntime/LangGraph | `intelligence/runtime.py` + `graphs/interpret_file.py`. **Untouched** — Stage 2 adds no AI. |
| PipelineRunner/data-plane | Did not exist. `dataplane/filestore.py` was the only plane code. Created `contract.py`, `port.py`, `pg.py`, `engine/runner.py`. |
| Persistence/models/tests | `workflow/{states,models,ddl,store}.py` + 56 tests. Extended, nothing rewritten. |

Database findings that shaped the design (all verified with `psql`):

1. `bronze.members_raw` (trigger `trg_members_raw_append_only`) and
   `public.cinqflow_reject_mutation` belong to the **previous implementation**. Neither is
   touched: this build writes `bronze.member_roster_raw` and creates its own guard function
   `bronze.cinqflow_append_only_guard`. Verified after install — the old table and function
   are unchanged, and a test asserts the old trigger set is intact.
2. **The dev role is superuser, and superusers bypass `REVOKE`.** So the append-only
   `REVOKE` is defence-in-depth only; the **trigger** is the real enforcement. Verified by
   attempting UPDATE / DELETE / TRUNCATE as superuser — all three refused (§3).

---

## 1. Files changed

**New — data plane (the logical contract and its PG rendering)**
```
src/cinqflow/dataplane/contract.py   Layer, TypeName, Column/Table, AUDIT_COLUMNS,
                                     bronze_table(feed), record_hash, new_batch_id,
                                     StageCounts (balance equation), identifier guard
src/cinqflow/dataplane/port.py       DataPlanePort Protocol (install/ensure/append/count/read)
src/cinqflow/dataplane/pg.py         PostgresDataPlane + render_table/render_guard —
                                     the only data-plane SQL in the codebase
src/cinqflow/engine/runner.py        PipelineRunner.land_bronze / .reject
src/cinqflow/workers/land_bronze.py  topic batch.land_bronze
src/cinqflow/workers/reject_upload.py topic upload.reject
```

**Extended**
```
workflow/states.py   +APPROVED/REJECTED/LANDING/LANDED/LAND_FAILED, +RunState,
                     transitions incl. LANDED→LANDING (replay)
workflow/models.py   +Approval, Run, RunCounts, Lineage, BatchDetail; UploadDetail gains
                     approvals + runs
workflow/ddl.py      +workflow.approval (unique per gate/artifact/version), +workflow.run,
                     +workflow.lineage
workflow/store.py    +put_approval/list_approvals, open_run/set_run_state/finish_run/
                     get_run/list_runs, put_lineage/get_lineage, set_landing_key
api/app.py           +POST /uploads/{id}/approve, /reject; GET /batches,
                     /batches/{id}, /batches/{id}/rows, /lineage/{id}; queue depth extended
queue/worker.py      registers the two new topics
cli.py               `cinqflow install` also provisions the Bronze layer namespace + guard
```

**Frontend**: `components/GateActions.tsx` (approve/reject), `app/actions.ts`
(`submitDecision`), `lib/api.ts` (+Approval/Run/BronzeRows/LineageChain types,
`decideUpload`, `getBatchRows`, `getLineage`, extended `UploadStatus`),
`app/uploads/[uploadId]/page.tsx` (G1 gate, Decisions, Batches sections),
`app/batches/[batchId]/page.tsx` (run counts, lineage chain, masked Bronze rows),
`app/globals.css` (secondary button).

**Tests**: `tests/unit/test_contract.py` (12), `tests/integration/test_dataplane_and_landing.py`
(13), `tests/integration/test_approvals.py` (6), `tests/e2e/test_stage2_flow.py` (6).

---

## 2. Actual runtime flow

```
POST /api/uploads/{id}/approve
  → refuse if a decision already exists for this interpretation version   → 409
  → refuse unless status = interpreted                                    → 409
  → INSERT workflow.approval (G1, interpretation@v, approver, note)  [append-only]
  → status → approved
  → enqueue batch.land_bronze (dedupe_key includes the approval id)
  → COMMIT, return 202                                     [measured: 19 ms]

worker: batch.land_bronze → PipelineRunner.land_bronze
  → refuse unless status ∈ {approved, landing, land_failed}   ← approval is the authorisation
  → mint batch_id; open run (received); write lineage(upload,fingerprint,incoming key)
  → run → in_progress; upload → landing; COMMIT   ← the batch is durable before data moves
  → read preserved original → parse → rows of strings
  → ensure bronze.<feed>_raw from the contract (idempotent; trigger + revoke applied)
  → append rows in 2000-row chunks: raw_row JSONB + row_number + audit columns
  → counts: records_in = parsed rows, records_out = written
  → if not balanced: ROLLBACK, run → failed, upload → land_failed, raise
  → move original incoming/ → processed/; update upload.landing_key
  → lineage += bronze_table; run → completed; upload → landed

POST /api/uploads/{id}/reject → approval(rejected) + status rejected + enqueue upload.reject
worker: upload.reject → move original incoming/ → rejected/     [no plane writes]
```

**Measured on the real de-identified Fidelis upstate roster + one probe row (28,334 rows,
45 columns):**

- G1 approve returned in **19 ms**; landing queued, `runs: 0` until the worker ran.
- Landing wrote **28,334 rows to `bronze.member_roster_raw` in 2.8 s**, run `completed`,
  `balanced: true`, counts `{in: 28334, out: 28334, quarantined: 0, attributed_drops: 0}`,
  matching the profile's row count exactly.
- Original moved to
  `enrollments/fidelis_ny_upstate/member_roster/processed/2026-07-01/stage2_roster.csv`.
- Source alignment verified by querying the probe row directly:
  `28334 | STAGE2-PROBE-001 | PROBE | TANF Adult | TESTVILLE | fidelis_ny_upstate | 0275b6d1ea67 | 83d926fe64ce`
  — values verbatim, **45 JSON keys preserved**, no mapping applied.
- Lineage: `upload_id → sha256-7ff3bed9… → processed key → batch 0275b6d1ea67 →
  bronze.member_roster_raw`, `mapping_version: null`, `silver_table: null`, approvals `[G1 approved]`.
- Reject path: file moved to `rejected/2026-08-01/`, status `rejected`, **0 runs**, Bronze
  count unchanged at 28,334.
- Second approve of the same upload → **409 "already approved"**.

---

## 3. Tests executed and results

```
$ cd backend && poetry run pytest -q
93 passed, 1 warning in 25.03s          (56 from Stage 1 + 37 new)

$ poetry run ruff check src tests
All checks passed!

$ cd frontend && pnpm build
✓ Compiled successfully   (routes: /, /uploads/[uploadId], /batches/[batchId])
```

Append-only proven against the live table **as superuser**:

```
UPDATE bronze.member_roster_raw …  → ERROR: cinqflow: bronze.member_roster_raw is append-only; UPDATE refused
DELETE FROM bronze.member_roster_raw → ERROR: … DELETE refused
TRUNCATE bronze.member_roster_raw    → ERROR: … TRUNCATE refused
SELECT count(*) → 28334              (intact after all three)
```

Other notable tests: landing refused for an un-approved upload (no table created, no run);
rejected upload never lands; a second decision on the same artifact version raises
`AlreadyDecided`; unparseable original leaves `run=failed` + `upload=land_failed` with the
original retained and **no Bronze table created**; row hashes identical across two batches
from the same file (replay); feed names that are not safe identifiers
(`roster"; DROP TABLE x;--`) are refused before reaching SQL.

**Two real defects were found by these tests and fixed:**

1. `PipelineRunner._fail()` rolled back the transaction that had created the run row, so the
   failure had nothing to record itself on. Fixed by committing the run + lineage + `landing`
   status **before** any data work — the same lesson as the Stage 1 queue bug.
2. `LANDED` was a terminal state, which made replay impossible even though replay is a stated
   global requirement. Fixed to allow `LANDED → LANDING`; because Bronze is append-only this
   creates a **new** batch and leaves the earlier one intact (asserted by test).

One behaviour was improved rather than accommodated: a duplicate approval was already
refused with 409, but the message said "G1 requires an interpreted upload" because the status
check fired first. The existing-decision check now runs first, so the analyst is told
"already approved" with the prior decision's id and timestamp.

---

## 4. Stage 2 checklist status

| Item | Status |
|---|---|
| Pre-flight traces re-run against Stage 1 code | done (§0) |
| Approve/reject endpoints; approval append-only; 409 unless interpreted | done |
| `land_bronze`: batch id, move to processed, table from contract, 1:1 rows + audit columns + row_number, run + counts, balance checked | done |
| Bronze append-only verified by a test attempting UPDATE/DELETE | done — all three refused, superuser included |
| Reject path: file → rejected/, no plane writes | done |
| Lineage rows: upload → fingerprint → batch → bronze table | done |
| e2e: approve → Bronze row count == profile row count, balanced run | done (28,334 = 28,334) |
| DoD: G1 click to queryable Bronze with lineage, no manual steps | met — `/batches/{id}/rows` and the batch page render masked Bronze rows and the full chain |

Non-negotiables specific to this stage: no semantic mapping at Bronze (the source row is
preserved whole in `raw_row`; no target names appear anywhere in Stage 2 code); FastAPI does
not ingest inline (19 ms approve); approval is the sole authorisation for a plane write
(enforced in the worker, tested); AI is untouched by this stage.

---

## 5. Remaining gaps

- **`quarantined` and `attributed_drops` are structurally present but always 0.** Bronze
  accepts every parsed row by design; they become meaningful at Silver (Stage 6). The balance
  equation is therefore real but not yet exercised with non-zero terms.
- **No `clear_batch_derived`.** Stage 6 needs it to rebuild a batch's Silver rows; not written
  because nothing derives from Bronze yet.
- **Replay creates a second Bronze batch and no UI drives it.** `LANDED → LANDING` is legal
  and tested, but re-landing must be triggered manually (no endpoint). Deliberate: the
  reprocessing UX belongs with Stage 6's rebuild semantics.
- **Whole file held in memory.** Parse + row construction for 28k rows is fine (2.8 s), but a
  GB-scale file would need streaming; `INSERT_CHUNK` bounds the statement size, not memory.
- **`executemany`, not `COPY`.** Fast enough here; `COPY` is the obvious optimisation when
  volume demands it.
- **No feed registry.** `feed` is a free-text form field validated only as a safe identifier,
  so a typo silently creates a new Bronze table. A registry (E3) would close this.
- Still no authentication: `approver` is a request field, so the audit trail records a claim,
  not a verified identity. Must not survive into a shared environment.
- `templates.md` still shows the pre-verification canonical names (`source_member_id`,
  `gender`, `members_programs`) flagged in the Stage 1 report; unchanged again to keep this
  diff to Stage 2.
- `docs/blueprints/structure.md` still says schema `queue` (this build uses `jobq`) and does
  not list `dataplane/contract.py`/`port.py`/`pg.py` or `engine/runner.py` as built.

## 6. Assumptions

- **One Bronze table per feed**, named `bronze.<feed>_raw`. The prior build used
  `bronze.members_raw` for its roster feed; this build's names are feed-derived, so the two
  coexist without collision.
- The whole source row is stored as JSONB rather than as typed per-source columns. This keeps
  Bronze genuinely source-aligned and schema-drift-tolerant, and defers typing to the mapping
  stages — consistent with "no semantic mapping at Bronze", but it does mean Bronze is not
  column-queryable without JSON operators.
- `row_number` is 1-indexed over **non-blank parsed rows** (the parser skips wholly empty
  lines), so it is a position within the parsed file, not a physical line number.
- One decision per `(gate, artifact_type, artifact_id, artifact_version)`. Re-deciding
  requires a new interpretation version — approvals are facts, not toggles.
- A rejected upload is terminal. Re-submitting the same bytes is refused by the fingerprint
  unique index, so a corrected file must differ in content.
- The original moves to `processed/` only after Bronze rows are committed; if the process
  died between the commit and the move, the file stays in `incoming/` while the run reads
  `completed`. The file's folder is treated as recoverable metadata, not the source of truth.

## 7. UNKNOWN FROM REPOSITORY

- Whether CINQCARE expects Bronze to be **one table per feed** (this build) or one wide table
  per domain with a `feed_id` discriminator (the prior build's `members_raw` shape suggests
  the latter). Nothing in `docs/` states the convention; the contract makes either renderable.
- Whether Bronze should store the source row as JSON or as typed source columns. The prior
  implementation also used `raw_row JSONB`, which is corroborating practice but not a
  documented requirement.
- The authoritative `batch_id` format. `enrollment_silver_raw_model.sql` declares
  `batch_id INT`; this build uses a 12-hex-character string. Reconciling that is a real
  decision for Silver in Stage 6, since the canonical DDL and this build currently disagree
  on type.
- Who may approve at G1 (role model), and whether G1 approval should be recorded against an
  authenticated identity for audit purposes.
