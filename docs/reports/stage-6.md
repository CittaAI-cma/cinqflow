# Stage 6 — G2 Approval → Silver Raw + lineage

**Status:** complete. **Date:** 2026-09-03. **Suite:** 257 passed (was 222 · 35 new).

The task named Stage `<N>` unsubstituted; Stages 1–5 were complete, so this is Stage 6
per `checklist.md`. With it the target flow runs end to end:

    Analyst → upload → AI understanding → G1 → Bronze → AI Bronze understanding
    → mapping recommendation → editable mapping → preview → G2 → Silver Raw

---

## 1. What was built

| Concern | Where |
| --- | --- |
| G2 endpoint | `api/app.py` — `POST /api/feeds/{feed}/mapping-versions/{n}/approve` |
| Approval + freeze + supersede | `workflow/store.py` — `approve_mapping_version`, `approval_for_mapping`, `mapping_artifact_id` |
| Promotion | `engine/runner.py` — `promote_silver`, `_write_page`, `_write`, `PromotionOutcome`, `WriteShortfall` |
| Worker | `workers/promote_silver.py` (`mapping.promote`), registered in `queue/worker.py` |
| Silver + quarantine declarations | `dataplane/contract.py` — `silver_table`, `quarantine_table`, `CANONICAL_TYPES`, `MEMBER_KEY`, `Table.physical_schema` |
| Writes and batch rebuild | `dataplane/pg.py` — `write_rows`, `delete_batch`, `install_layer(physical=…)`; `dataplane/port.py` |
| Fan-out helpers | `engine/mapping_exec.py` — `group_by_entity`, `is_empty`, `row_reasons` |
| Entity view of governed model | `knowledge/canonical.py` — `fields_of`, `phi_of`, `phi` |
| Decisions → knowledge | `knowledge/export.py` — `export_approved_mapping`, `decisions_of` |
| Run/lineage shape | `workflow/models.py`, `workflow/ddl.py` (composite run key, `mapping_version`, silver links) |
| Full-chain lineage | `api/app.py` — `GET /api/lineage/{batch_id}` |
| UI | `components/ApproveMapping.tsx`, `app/mapping/[feed]/page.tsx`, `app/batches/[batchId]/page.tsx`, `lib/api.ts`, `app/mapping/actions.ts` |

## 2. Two collisions the runtime forced into the design

**`silver_raw.members` already exists — and belongs to the previous implementation.**
Its columns are that build's (`member_row_id`, `source_member_id`, `is_active`, all
`NOT NULL`, no defaults); this build populates none of them, so a write there is
impossible and `CREATE TABLE IF NOT EXISTS` would silently give us the wrong table.
Resolved the way this repository already resolved the identical problem for the queue
(`queue` → `jobq`): the *logical* layer stays `SILVER_RAW`, and the *physical* namespace
is `settings.silver_schema` (default `silver`). `Table.physical_schema` carries the
distinction, `install_layer(layer, physical=…)` validates the layer and creates the
namespace. Verified after the live run: `silver_raw.members` still has 0 rows and its own
indexes.

**A batch now has two runs.** `run.batch_id` was the primary key, but templates §1.8
shows one `batch_id` with `kind: land_bronze | promote_silver`. The key is now
`(batch_id, kind)`, with `mapping_version` alongside it. New databases get that from the
`CREATE TABLE`; this one was migrated by a guarded `DO` block that converts a
single-column key once and is a no-op afterwards. `get_run(batch_id)` still answers with
the landing (the run that defines the batch); `list_batch_runs` returns the history.

## 3. Decisions worth naming

- **Fan-out is the unit of work.** One roster row populates five canonical entities, so
  promotion groups mapped targets by table and writes one row per (source row × entity).
  Each Silver table carries the *whole* canonical entity, not today's mapped subset, so a
  later mapping version adds rows rather than columns.
- **Balance is measured against the database, not the engine.** `_write` compares rows
  handed over with rows accepted and raises `WriteShortfall` on any difference. This was
  added because a test proved the first version wrong: counting what the executor
  *intended* let a lost row still "balance". The equation now stands on what was written.
- **Row destinations.** `ok`/`defaulted`/`null` → Silver; `failure`/`quarantined`/
  `rejected` → quarantine with per-field reasons and the source row; a row that mapped to
  nothing anywhere → `attributed_drops`. Nothing is dropped silently, so
  `records_in = records_out + quarantined + attributed_drops` holds by construction.
- **Empty child rows are skipped.** A member with no email produces no
  `members_emails` row: the absence of a child record is not a record of an absence.
  Live evidence — 28,334 members, 6,807 emails.
- **The member key travels with the row.** Every child entity declares
  `source_system_id` in its canonical key ("one row per member per address type"), and a
  roster does not map it per child, so it is propagated from `members.source_system_id`
  of the same source row. Without this the child tables cannot be joined to a member.
- **Silver declares no PRIMARY KEY.** The canonical key of every child begins with a
  discriminator (`address_type`, `phone_type`, `email_type`) a roster does not supply;
  declaring the key would refuse rows the mapping legitimately produces. Replay relies on
  `batch_id` + content-derived `record_hash` instead.
- **Replay rebuilds, so Silver is not append-only.** `delete_batch` refuses append-only
  tables, which is what makes rebuilding safe: Bronze is the record, and it refuses
  deletion at the database.
- **Knowledge is exported after a balanced promotion**, not at the moment of approval —
  the document asserts a mapping that demonstrably ran. A failed export never undoes a
  completed promotion.
- **G2 artifact identity.** An approval records a UUID `artifact_id`, but a mapping
  version is `(feed, version)`. `mapping_artifact_id(feed) = uuid5(NAMESPACE_URL, …)` is
  derived and stable, so the one-decision-per-artifact-version index keeps working. The
  approval's `upload_id` is the previewed batch's upload, which is what makes both gates
  appear in one lineage query.

## 4. Tests

`257 passed` · `ruff check src tests` clean · `pnpm build` compiles.

**New — 35:** `tests/unit/test_silver_contract.py` (12) ·
`tests/integration/test_promotion.py` (15) · `tests/e2e/test_stage6_flow.py` (8).

Acceptance criteria (features.md §Stage 6):

| Criterion | Test |
| --- | --- |
| Silver rows exist only for approved versions; version on every row's lineage | `test_nothing_reaches_silver_without_g2`, `test_promotion_refuses_a_version_that_is_not_approved`, `test_lineage_proves_the_chain_from_either_end` |
| `records_in = records_out + quarantined + attributed_drops` | `test_promotion_writes_every_entity_the_mapping_targets`, `test_a_run_that_does_not_balance_is_failed_and_writes_nothing` |
| Re-promoting yields the same `record_hash`es | `test_replaying_a_promotion_rebuilds_only_this_batch_and_hashes_the_same`, `test_replaying_the_promotion_leaves_the_same_silver` |
| `GET /api/lineage/{batch_id}` returns the full chain | `test_lineage_proves_the_chain_from_either_end` |
| No model on the write path | `test_promotion_needs_no_model_at_all` (poisons `AgentRuntime.__init__`, `.run`, `StubClient.complete_json`), `test_the_promotion_path_imports_no_intelligence` (over parsed imports, so prose cannot pass or fail it) |

Two Stage 4/5 tests asserted Stage 6 *did not exist*. They now assert what their own
stages guarantee: `test_g2_cannot_approve_a_version_nobody_has_previewed` (Stage 4) and
`test_preview_alone_writes_no_silver` (Stage 5 — the preview queues no promotion and
leaves the version `previewed`, not `approved`).

Test isolation was tightened: `settings` now copies `knowledge/` into `tmp_path`, so
tests read the governed documents the platform ships and a G2 export cannot write into
the repository; the per-test `silver_schema` is dropped with the others.

## 5. Live run — the DoD

Real batch `15486198c898`, feed `roster_stage3`, **28,334 rows** from
`deidentified_CINQUPSTATE_Member_Roster_03_05_2026_1.csv`.

1. The analyst reconsidered the rule that had rejected 140 of 200 previewed rows
   (`member_email: on_null = reject → pass`, noted "an absent email is not a reason to
   drop the member").
2. **G2 refused immediately** — `409 this version has no preview of its current spec`.
3. Re-previewed: `ok=200 rejected=0`, `is_current: true`, `approvable: true`.
4. **G2 accepted in 37 ms**, returning `202`, `status: approved`,
   `queued: mapping.promote`, `sample_was_partial: true` (200 of 28,334 previewed).
5. Worker promoted in ~4 s:
   `in=28334 out=28334 quarantined=0 drops=0`, balanced, across five entities —
   `members 28334 · members_addresses 28334 · members_phones 28334 ·
   members_enrollment_segments 28334 · members_emails 6807`.
6. Spot check: `KAREN GLASS · sex=female` (the value map ran) ·
   `care_management_program='none recorded'` (the default applied) ·
   `date_of_birth=1990-04-06 00:00:00+00` · `source_system=fidelis_ny_upstate` ·
   `batch_id=15486198c898` · `record_hash` present. Addresses carry city/state/zip/county
   keyed by `source_system_id`.
7. **Lineage** returned the whole chain: upload → `sha256-cd87cb00…` →
   `…/processed/2026-09-01/s3_roster.csv` → batch → `bronze.roster_stage3_raw` →
   mapping v2 → `silver.members` + per-entity counts, both runs balanced, and both gates
   (`G1 by info@cittaai.com`, `G2 by lead@cinqcare.com`).
8. **Replay:** re-promoted the same batch — `rebuilt: true`, identical counts, and the
   aggregate hash of all 28,334 `record_hash`es unchanged
   (`4ca14510f2be55698100b74c6f6f0130`). `silver.members` still holds 28,334 rows, not
   double; Bronze still 28,334.
9. **Knowledge:** `knowledge/mappings/approved/roster_stage3.yaml` written with 22
   decisions, `approved_by: lead@cinqcare.com`, `batch_id`, and each field marked
   `analyst` or `analyst_accepted_ai`. No source values in the file. The provider now
   merges it: `['enrollment_historical.yaml@1', 'roster_stage3.yaml@1']` — so the next
   feed's proposal has this feed's decisions as an exemplar. Replay did not duplicate the
   decision set.
10. **UI:** the studio shows the frozen version and "v2 is approved and frozen" with a
    link to what was written; the batch page shows a Silver Raw panel with rows read,
    promoted, quarantined, balanced and the per-entity row counts; v1 renders as
    superseded and cannot be approved again.

## 6. Gaps

- **PHI in quarantine, unauthenticated.** The quarantine table stores the whole source
  row (necessarily — a quarantined row exists to be re-examined), which makes it the
  second PHI store outside landing/Bronze after `preview`. Silver itself holds PHI by
  design. There is still no authentication anywhere. This needs a decision before real
  PHI, and it is the same open question Stage 5 raised.
- **`records_out` counts source rows, not Silver rows.** The balance equation is at
  source-row grain (in = out + quarantined + drops), while 28,334 source rows produced
  120,143 Silver rows. Per-entity counts are recorded on the lineage, but there is no
  balance equation *per entity*, so a fan-out defect that dropped every address row would
  still balance. `WriteShortfall` catches losses inside a page; it does not assert an
  expected per-entity total.
- **No date-only canonical type.** Canonical `timestamp` renders as `TIMESTAMPTZ`, so a
  date-only value is stored as midnight in the session timezone. Every connection pins
  `TimeZone=UTC` (`db.py`), and the live rows verify as midnight UTC — but the
  correctness rests on that setting rather than on the type.
- **Quarantined rows are terminal.** They are stored with reasons and can be replayed by
  re-promoting, but nothing re-drives them after a mapping fix, and nothing surfaces them
  in the UI beyond a count.
- **`Transform(op="value_map")` remains a no-op** that validation accepts (carried from
  Stage 5), and `cast` still accepts only a narrow set of date formats.
- **Promotion is one transaction.** 120,143 inserts committed at once was fine at this
  size; a much larger feed would want per-page commits, which would mean a resumable run
  rather than an all-or-nothing one.
- **Superseding is not retrospective.** Approving v3 marks v2 `superseded`, but Silver
  rows already written by v2 stay as they are (correctly — they are what happened); there
  is no "re-promote everything under the new version" operation.
- **The `install` CLI message still lists only `workflow, jobq, bronze`.** The silver
  namespace is created lazily by `ensure_table` on first promotion, so the message is
  incomplete rather than wrong.

## 7. Assumptions

1. The physical namespace of a layer is a rendering choice, not part of the logical
   contract — so rendering SILVER_RAW into `silver` preserves compatibility. The Databricks
   renderer would map the same declarations to its own namespace.
2. `(batch_id, kind)` is the run's identity, read from templates §1.8.
3. A Silver table carries the whole canonical entity, so it is stable across mapping
   versions.
4. `source_system_id` propagates from the primary entity to child entities of the same
   source row when the mapping does not map it per child.
5. All non-`ok` rows are quarantined rather than dropped, so `attributed_drops` is only
   ever "this row mapped to nothing anywhere".
6. A `record_hash` over an entity row's mapped values is the right content identity for
   replay (it does not include the entity name, so identical values in different tables
   hash identically — they are in different tables).
7. G2 promotes the batch the current preview sampled. Promoting a *different* batch under
   the same approved version is possible through the worker but is not exposed.
8. Exporting knowledge after a balanced promotion (not at approval) is the right moment.
9. `PROMOTE_PAGE = 1000` balances quarantine attribution against memory; not from any
   documented requirement.

## 8. Unknowns — **UNKNOWN FROM REPOSITORY**

1. **How `address_type`, `phone_type`, `email_type` are supplied.** Each is the first
   column of its entity's canonical key, and a roster has no such column. They land NULL.
   (An analyst *can* supply a constant today by mapping a source column that does not
   exist in the file with `on_null: default` — that works, but nothing in `docs/`
   says it is the intended mechanism.) Carried unresolved from Stage 5.
2. **The enrollment-segment key.** `members_enrollment_segments` is keyed on
   `(member_plan, member_payor, insurance_id, source_system_id, source_system)`; this
   roster maps `lob`, `member_group`, `pcp_*`, `tin*` but none of the first three, so the
   grain "one row per member per plan/payor/insurance id" is not achieved.
3. **`batch_id INT`.** The canonical DDL declares it as an integer; this build mints
   12 hex characters. It does not matter while this build renders its own tables, and it
   would matter immediately when writing CINQCARE's physical Silver.
4. **Whether a promotion may run against a partial preview.** G2 currently allows it and
   says so (`sample_was_partial: true`, and the UI states the promotion covers all rows);
   nothing in `docs/` says whether full-batch preview should be required.
5. **Whether quarantined rows must reach a quarantine table in Silver or be dropped with
   attribution** — this build stores them; the documents do not say.
6. **Who may view Silver, quarantine or a preview containing PHI.** No authentication or
   authorisation model exists anywhere in the repository.
7. **Retention.** Nothing says how long previews, quarantine rows or superseded Silver
   batches should be kept.
