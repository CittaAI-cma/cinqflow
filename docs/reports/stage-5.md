# Stage 5 report — 2026-09-03

Scope implemented: **deterministic preview.** A mapping version is executed in code against
a bounded Bronze sample, and the analyst sees per-row, per-field what it would do before
anything reaches Silver. G2 approval and the Silver Raw write (Stage 6) are **not**
implemented.

> The task named Stage `<N>`; the placeholder was not substituted. Stages 1–4 are complete
> and green, so I continued with Stage 5 as defined in `checklist.md`.

---

## 0. Traces (before coding)

| Trace | Finding |
|---|---|
| Execution path | upload → profile → interpret → G1 → `batch.land_bronze` → `bronze.analyze` → proposal → draft vN (Stage 4). Stage 5 attaches to a **mapping version**, reads Bronze through the existing port, and writes only a workflow artifact. |
| Reuse found | `PostgresDataPlane.read_rows/count_rows/table_exists`, `bronze_table(feed)`, `Queue`, the worker+topic pattern, `MappingSpec`/`MappingField`/`Transform` from Stage 4, `WorkflowStore` conventions. |
| Missing piece | no way to find a feed's newest landed batch → added `store.latest_batch_for_feed`. |
| Files requiring change | §1. No AI module touched; `intelligence/` is untouched by this stage. |

**Smallest plan executed:** `engine/mapping_exec.py` (the executor `structure.md` names, with
preview *and* full mode so Stage 6 reuses the same code) → preview artifacts per
templates §1.7 → `workflow.preview` keyed by a spec fingerprint so an edit invalidates it →
`mapping.preview` worker → POST/GET endpoints → studio panel.

---

## 1. Files changed

**New**
```
backend/src/cinqflow/engine/mapping_exec.py   transforms, casts, null/value rules,
                                              execute_field/execute_spec, spec_fingerprint
backend/src/cinqflow/workers/run_preview.py   topic mapping.preview
backend/tests/unit/test_mapping_exec.py       (40)
backend/tests/integration/test_preview.py     (9)
backend/tests/e2e/test_stage5_flow.py         (9)
frontend/components/PreviewPanel.tsx          run button, aggregates, row-by-row table
```

**Extended**
```
workflow/models.py   +PreviewFieldResult, PreviewRowResult, PreviewSample,
                     PreviewAggregates, Preview
workflow/ddl.py      +workflow.preview (+ unique index on
                     feed, version, spec_fingerprint, batch_id, selector)
workflow/store.py    +put_preview, get_preview, get_current_preview,
                     latest_batch_for_feed
api/app.py           +POST/GET …/mapping-versions/{n}/preview; queue depth extended
queue/worker.py      registers mapping.preview
frontend            lib/api.ts (+preview types, getPreview, requestPreview),
                    app/mapping/actions.ts (+runPreview), app/mapping/[feed]/page.tsx
tests/e2e/test_stage4_flow.py  the "no future-stage endpoints" test now asserts only that
                    G2 is absent — /preview exists by design from this stage
```

---

## 2. Runtime flow (verified live on the real 28,334-row batch)

```
POST …/mapping-versions/{n}/preview
  → 404 unknown version · 409 empty spec · 409 no completed Bronze batch · 404 unknown batch
  → resolve the batch (explicit, else the feed's newest completed landing)
  → enqueue mapping.preview, dedupe_key = topic/feed/version/spec_fingerprint/batch
  → 202 in 25 ms                                   [nothing computed in the request]

worker: mapping.preview
  → read a bounded sample back through the data-plane port
  → execute_spec(spec, rows, detail=True)          [plain Python; no model reachable]
  → persist workflow.preview: sample (batch, table, rows/rows_in_batch, selector),
    aggregates, and every row's field-level outcomes
  → status draft → previewed  (the version stays editable)

GET …/mapping-versions/{n}/preview
  → the artifact + is_current (spec fingerprints match) + approvable + stale_reason
```

Measured on `roster_stage3` v2 (22 fields, the analyst-owned mapping from Stage 4):

- **First run**: 200 of 28,334 rows, `ok=200`, and the analyst's value map visible in the
  output (`member_sex "F" → "female"`). `null_or_invalid` reported genuinely empty source
  columns — `members_addresses.address2` 179/200, `members_emails.email_address` 140/200,
  `members.care_management_program` 192/200. Sample labelled partial.
- **Analyst tightened the spec** (`member_email` → `on_null: reject`;
  `health_home_status` → `on_null: default 'none recorded'`), and the previous preview
  immediately reported `is_current: false`, `approvable: false`,
  "the draft changed after this preview; run it again" — the old preview row was **not**
  deleted, and the version returned to `draft`.
- **Re-run**: `ok=60, rejected=140`, `failures_by_rule: {member_email:on_null: 140}`,
  `affected_sources: {member_email: 140}`, and each rejected row names the field and the
  reason ("source is empty and this field rejects the row"), with the defaulted field shown
  alongside. `is_current: true` again.
- Studio renders all of it: aggregates, failures by rule, targets that would receive no
  value, and a row-by-row source→mapped table with reasons.

---

## Tests

```
$ cd backend && pytest -q
222 passed, 1 warning in 33.09s      (unit 123 · integration 60 · e2e 39; 58 new this stage)

$ ruff check src tests
All checks passed!

$ cd frontend && pnpm build
✓ Compiled successfully
```

Acceptance criteria (features.md §Stage 5):

| # | Criterion | How it is proven |
|---|---|---|
| 1 | Identical spec + sample ⇒ identical preview, LLM not invoked | `test_the_same_spec_over_the_same_rows_is_byte_identical`; `test_the_same_spec_and_sample_produce_an_identical_preview` (one row, not two — the unique index makes a repeat idempotent); `test_preview_never_calls_a_model` **poisons `AgentRuntime.__init__`, `AgentRuntime.run` and `StubClient.complete_json`** and the preview still succeeds; `test_the_executor_imports_no_model` asserts the module cannot reach `intelligence`, `langgraph`, `anthropic`, `AgentRuntime` or `LlmClient` |
| 2 | Every failure attributed to a row and a rule, never swallowed | `failures_by_rule` is keyed `source:rule`; per-field `outcome`+`reason`+`rule`; parametrised tests for every transform and cast failure; verified live (140 rejections attributed to `member_email:on_null`) |
| 3 | G2 impossible without a **current** preview | the precondition Stage 6 will call is built and tested: `store.get_current_preview` returns nothing once the spec changes (`test_editing_the_draft_makes_the_preview_stale`), and the API surfaces `approvable`. `test_g2_approval_does_not_exist_yet` asserts no approve route exists |

Coverage worth naming: all 8 named transforms (including human date masks like `MM/DD/YYYY`,
which the mapping documents in `docs/` use and which are translated rather than refused), all
6 casts and their failures, all 3 null rules, all 3 unmapped-value rules, row-outcome
precedence, `detail=False` (the mode Stage 6 will use), bounded-sample labelling, and
`test_no_silver_write_and_no_g2_in_stage_five`.

**Two defects were mine, in the tests, not the code:** a fixture appended a `member_id`
mapping that the proposal had already seeded (this roster has no competing `medicaid_id`, so
it was not contested) and the duplicate was correctly refused with 422; and an assertion
expected `silver_raw.members` not to exist, when in fact **that table belongs to the previous
implementation** — it is present and empty in this shared database, with the old build's
columns (`source_member_id`, `gender`, `line_of_business`). The assertion now checks what
matters: that Stage 5 wrote nothing (0 rows, no `promote_silver` run, and no
`PipelineRunner.promote_silver` attribute).

## DoD

**"The analyst sees exactly what vN does before any Silver write"** — met, and demonstrated
on the real roster: for each sampled row, every field's source value, mapped value, outcome
and reason; and in aggregate, clean/failed/quarantined/rejected counts, failures grouped by
`source:rule`, targets that would receive no value, and the columns needing attention. The
preview is labelled current or stale against the exact spec, and the panel states that G2
will require a current one. No Silver write path exists in the codebase
(`grep` for `INSERT INTO silver` / `load_silver` / `def promote` returns nothing).

## Gaps

- **The preview shows, and now persists, unmasked PHI-candidate values.** This is inherent
  to its purpose — masking source values would defeat "see exactly what the mapping does" —
  but it makes `workflow.preview` the first store of PHI-bearing source values outside
  landing/Bronze, and the studio the first surface to display them (verified: real first and
  last names render in the row-by-row table). Stages 1–3 mask PHI everywhere else. With no
  authentication yet, this needs a decision (role-gated preview, per-column masking with an
  opt-in reveal, or a retention window) **before** this runs on real PHI.
- **No preview retention or cleanup.** Every distinct spec fingerprint keeps its own row with
  up to 1,000 rows of field detail; nothing prunes superseded previews.
- **`value_map` as a transform op is a no-op.** The field-level `value_map` does the work;
  `Transform(op="value_map")` passes the value through. Validation accepts it, so a spec can
  express something that does nothing. It should either be rejected or made meaningful.
- **`cast` accepts a narrow set of date formats** (ISO, `YYYY/MM/DD`, `MM-DD-YYYY`). Anything
  else must go through `parse_date`. The failure message says so, but a spec author has to
  read it to find out.
- **Sample selection is `first_N` only.** No random or stratified sampling, so a preview can
  miss problems that only occur later in a batch — the 200-row window over 28,334 rows found
  zero failures on the first pass, which says as much about the window as the mapping.
- **Aggregates are per row and per rule, not per target-entity.** A spec spanning five
  canonical entities reports one set of counts; Stage 6 will need per-entity counts to write.
- **A preview is tied to one batch.** Nothing warns that vN was previewed against an older
  batch than the one Stage 6 would promote.

## Assumptions

- **Row-outcome precedence** is `rejected > failure > quarantined > ok`, and defaults or
  permitted nulls do **not** make a row a problem. A row with three different failures counts
  once, under the worst one.
- **`writable_rows` excludes failed, rejected and quarantined rows.** Stage 6 will decide
  whether quarantined rows are written to a quarantine table (the Stage 2 balance equation has
  a `quarantined` term ready for exactly that); Stage 5 only reports them.
- **Editing a previewed version returns it to `draft`** (Stage 4's rule) and makes the preview
  stale by fingerprint mismatch. Previews are never deleted or rewritten.
- **Casting returns canonical string forms** (`true`/`false`, ISO dates). Binding real
  Postgres types is the adapter's job in Stage 6, so preview shows what the value will be, not
  its final storage type.
- **Default sample is 200 rows, capped at 1,000** — chosen so a preview stays renderable and
  storable, not from any documented requirement.

## Unknowns

- **UNKNOWN FROM REPOSITORY**: whether a preview must cover the whole batch rather than a
  sample before G2 may open. Nothing in `docs/` states a coverage requirement, and the
  difference matters — a clean 200-row window is not evidence about 28,334 rows.
- **UNKNOWN FROM REPOSITORY**: whether quarantined rows should reach Silver's quarantine
  table or be dropped with attribution, and whether a rejected row fails its whole batch or
  is counted as an attributed drop.
- **UNKNOWN FROM REPOSITORY**: who may view a preview containing PHI-candidate values.
- Still open from earlier stages: the canonical DDL declares `batch_id INT` while this build
  mints 12-hex text (Stage 2); how `address_type` / `phone_type` / `email_type` and the
  enrollment-segment key columns are supplied for a roster that has no such column (Stage 4) —
  both now block a correct Silver write in Stage 6.
