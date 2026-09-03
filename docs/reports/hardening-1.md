# Hardening pass 1 — closing the gaps the six stage reports listed

**Status:** complete. **Date:** 2026-09-03. **Suite:** 280 passed (was 257 · 23 new).

Not a stage. `checklist.md` defines six stages and Stage 6 was the last, so this pass
closes concrete gaps named in `docs/reports/stage-{1..6}.md` without adding surface area.

---

## 0. Audit first: is the checklist actually complete?

All 73 checkboxes in `checklist.md` were audited against the repository and runtime.
Findings, before any code was written:

| Claim | Verdict |
| --- | --- |
| No Celery/Procrastinate/new queue | Clean — `pyproject.toml` has no such dependency. |
| No LLM-generated code executed | Clean — no `eval`/`exec`/`__import__` anywhere in `src/`; the only `compile(` hits are `re.compile` and LangGraph's own graph compilation. |
| FastAPI handlers do nothing long-running inline | Clean — every POST returns 202 (201 for creating a draft, which only persists). |
| Six stage reports written | Clean — `stage-1.md` … `stage-6.md`. |
| `make api`, `make worker`, `make test` all work | **Clean — the failure was environmental, not the repository** (below). |
| "grep confirms no YAML import in `intelligence/`" | **Diverges from the wording, satisfied in substance** (below). |
| `python -m cinqflow.dataplane install` | Command does not exist; the implemented equivalent is `cinqflow install` (`make install-db`). Documentation wording, not a missing capability. |

**`make` was never broken.** `poetry run python` could not import `cinqflow` because this
shell exports `VIRTUAL_ENV=/Users/apple/.pyenv/versions/3.12.8`; Poetry honours an
activated environment, so it used the pyenv interpreter instead of the project venv
(`cinqflow-Ats_6qDf-py3.12`, where the package is correctly installed). With that variable
removed, `make test` → **257 passed**, `make lint` → clean, `make install-db` → idempotent.
This retires the workaround used since Stage 1 — the fix is `env -u VIRTUAL_ENV`, or
unsetting it in the shell, and no repository change.

**The YAML boundary was a deliberate Stage 3 decision, not an oversight.** The checklist
says "no YAML import in `intelligence/`"; what exists is stricter in the way that matters
and looser in one place that does not. Two Stage 3 tests already enforce it: no module in
`intelligence/` parses YAML or opens a file, and no graph or context builder names a
concrete provider — only `runtime.py`, the composition root, does, and injection is
available (`AgentRuntime(knowledge=…)`). Left alone: changing tested, deliberate wiring
would be unrequested refactoring.

## 1. Per-entity balance for the Silver fan-out

*Gap (stage-6 §6): 28,334 source rows produce 120,143 Silver rows, and the balance
equation is at source-row grain, so a fan-out defect that dropped every address row would
still balance.*

`expected_entity_rows` (`engine/mapping_exec.py`) counts, per entity, the rows that entity
should receive — derived from **field outcomes**, deliberately not from `group_by_entity`
or `is_empty`, which is what makes it a second opinion rather than a restatement. A test
asserts the independence over the compiled function's referenced names (`co_names`), not
its text, since its docstring names both functions precisely to say it avoids them.
`_write_page` compares the two per page and raises `WriteShortfall` on disagreement, so
the run fails instead of writing less than it read.

**One real bug this surfaced.** `is_empty(values, ignoring={MEMBER_KEY})` was applied to
*every* entity, including the primary one — so a member row whose only value was its own
identifier was skipped, and the source row became an `attributed_drop`. For a child entity
that rule is right (a row carrying nothing but the propagated member key is not a record);
for the primary entity the identifier *is* the record. Now nothing is ignored for the
primary. Covered by `test_a_member_with_only_an_identifier_is_written` (20 rows, 15 with no
city → 20 members, 5 addresses).

## 2. Quarantine visibility

*Gap (stage-6 §6): quarantined rows are stored with reasons but nothing surfaces them
beyond a count.*

`GET /api/batches/{batch_id}/quarantine` — read-only, paged, and **PHI-masked exactly as
the Bronze rows endpoint already masks**, using the upload's own profile. Understanding
why a rule fired does not require the value it fired on. It returns `total` and
`by_outcome` for the whole batch, `by_rule` for the returned page (labelled as such), and
each row's `reasons` naming source, target, rule and message. The batch page renders the
count in the Silver panel and a per-row table below it.

Deliberately **no re-drive endpoint**: there is nothing to add. A fix is a new mapping
version, and promoting the batch again re-reads Bronze, so previously quarantined rows are
re-driven through the new rules. `test_re_promoting_re_drives_quarantined_rows_through_the_new_version`
proves it — 15 quarantined under v1, then 20 promoted and 0 quarantined under v2, with v1
marked superseded.

## 3. The `value_map` transform is retired

*Gap (stage-5/6): `Transform(op="value_map")` validated cleanly and did nothing.*

Removed from `ALLOWED_OPS` and from `recommend_mapping.ALLOWED_OPS`, so the model cannot
propose a transform the analyst would then be unable to save. `RETIRED_OPS` gives the
refusal a pointer — *"the 'value_map' transform is no longer supported — set the field's
own value_map table (and on_unmapped_value) instead"* — rather than an unhelpful list of
what is allowed. A rule that silently does nothing is worse than one that is refused. No
stored spec or knowledge document used it (checked before removing).

## 4. Timestamps state their offset

*Gap (stage-6 §6): a date-only value in a `TIMESTAMPTZ` column lands at midnight in
whatever timezone the session has; correctness rested on `db.py` pinning `TimeZone=UTC`.*

The `timestamp` cast now emits `1997-11-04T00:00:00+00:00` instead of
`1997-11-04T00:00:00`. The stored instant no longer depends on the connection. The `date`
cast is unchanged (`DATE` columns have no such ambiguity).

**Note for anyone comparing hashes across this change:** `record_hash` is derived from
mapped values, so a feed that casts to `timestamp` will hash differently after this change
than before it — once. Replay determinism holds within a version of the code, not across a
deliberate change to a canonical form. The live batch is unaffected (its `member_dob` field
casts to `date`), and its hashes verified identical after re-promotion.

## 5. Sampling spread across the batch

*Gap (stage-5 §6): sampling is `first_N` only, so a clean 200-row window over 28,334 rows
says as much about the window as about the mapping.*

Two named, deterministic strategies — `first` (the first N rows) and `spread` (every k-th
row, k = `rows_in_batch // N`) — with **`spread` as the default**. Random sampling was not
added: a preview that could not be reproduced would not be evidence of anything, and the
determinism requirement is explicit in Stage 5's acceptance criteria. `stride` is applied
in SQL over `row_number` (via `mod()`, because psycopg reads `%` as a placeholder), so the
same stride always returns the same rows. The API takes `strategy` and refuses anything
else with 422 and the allowed list; the selector is recorded on the artifact and included
in the dedupe key, so `first_200` and `spread_200` are separate facts and neither
overwrites the other.

**A second bug this surfaced.** Preview row numbers came from the executor's own
enumeration (1..N over the *sample*). Under `first_N` that coincides with the batch's row
numbers; under `spread` it does not, so "row 2" would have sent the analyst to the wrong
row of the file. The worker now supplies the batch `row_number` from the Bronze row it
came from. (Promotion was already correct — quarantine always used the Bronze row number.)

## 6. Tests

`280 passed` (23 new) · `ruff check src tests` clean · `pnpm build` compiles ·
`env -u VIRTUAL_ENV make test` green.

- `tests/unit/test_hardening.py` (14): timestamp offsets, the retired op and its mirror in
  the graph's vocabulary, selector parsing and refusal, stride arithmetic
  (`28334 → 141`), per-entity expectations including the primary-vs-child key rule, and
  the independence assertion.
- `tests/integration/test_hardening_runtime.py` (9): a 20-row batch where only the last
  five rows carry a city, so `first_5` reports no addresses and `spread_5` finds one —
  the change earning its keep; both previews kept side by side; the API's 422; the
  identifier-only member; a monkeypatched fan-out that loses an entity failing the run
  with *"members_addresses prepared 0 rows where the mapped values account for 5"*;
  quarantine with masking, `by_outcome`, `by_rule` and pagination; and the re-drive.

Three existing assertions were updated to the new intended behaviour (two naive-timestamp
strings, one `selector.startswith("first_")`).

## 7. Live verification

Real batch `15486198c898`, 28,334 rows.

- **Sampling:** v3 previewed both ways. `first_200` covers rows 1→200; `spread_200` covers
  rows 1→28,060 at stride 141, second row 142 — real batch row numbers, both artifacts
  stored, `strategy: "random"` refused with 422.
- **Per-entity reconciliation at scale:** re-promoted v2 under the hardened engine —
  `in=28334 out=28334 quarantined=0 drops=0`, all five entities agreeing with their
  independently counted expectations (`members`/`addresses`/`phones`/`enrollment_segments`
  28,334, `members_emails` 6,807), and the aggregate hash of all 28,334 `record_hash`es
  unchanged: `4ca14510f2be55698100b74c6f6f0130`.
- **Quarantine:** the endpoint answers correctly on this clean batch — 0 rows,
  `by_outcome: {}`, correct table name, 404 for an unknown batch. The populated path is
  covered by tests rather than live, deliberately: producing 21,527 live quarantined rows
  would have meant approving a version that superseded v2 and leaving the live Silver in
  that state.
- **UI:** the preview panel explains the spread sample; the batch page renders the
  per-entity Silver counts and (when non-empty) the quarantine table.

## 8. Gaps that remain

- **Authentication.** Still none, on any surface. PHI now sits in landing, Bronze,
  preview, Silver and quarantine. The quarantine endpoint masks PHI candidates, which
  narrows the exposure but does not remove it. This is the largest open risk and was
  offered as the alternative to this pass.
- **`by_rule` on the quarantine endpoint is page-scoped**, not batch-scoped. Batch-wide
  rule tallies would need a JSONB aggregation over `reasons`; `by_outcome` and `total` are
  batch-wide, and the response labels the difference.
- **No `attributed_drops` path is exercised by a test.** After the primary-entity fix, a
  drop requires a row where *every* mapped target is empty — reachable only for a spec
  whose identifier field permits nulls. The counter is asserted as 0 throughout.
- **Preview retention.** Now that two selectors coexist per version, previews accumulate
  faster; nothing prunes them.
- **`spread` assumes dense row numbers.** Bronze numbers rows 1..N per batch, so this
  holds; a batch with gaps would sample fewer rows than requested without saying so.
- **The `install` CLI message** still lists only `workflow, jobq, bronze`; the silver
  namespace is created lazily on first promotion.

## 9. Assumptions

1. Sampling must stay reproducible, so `random` was refused rather than added with a seed.
2. `spread` is the better default; `first` remains available and is what a file's own head
   shows.
3. Quarantine is read-only. A fix is a new mapping version, not an edit to a refused row.
4. Masking quarantine values matches the Bronze-rows precedent and is the right default
   while there is no authentication.
5. Writing the UTC offset into the `timestamp` cast is correct because the contract's type
   is `TIMESTAMP_UTC` and every connection already pins UTC — this makes the existing
   intent explicit rather than changing it.
6. A one-off `record_hash` change for `timestamp`-cast feeds is acceptable; nothing in the
   repository compares hashes across code versions.

## 10. Unknowns — **UNKNOWN FROM REPOSITORY**

1. Whether a preview must cover the whole batch before G2 may open (carried).
2. How `address_type` / `phone_type` / `email_type` are supplied (carried).
3. The enrollment-segment grain: `member_plan`, `member_payor`, `insurance_id` unmapped
   (carried).
4. `batch_id INT` in the canonical DDL versus 12-hex text here (carried).
5. Who may view Silver, quarantine or a preview containing PHI (carried).
6. Retention for previews, quarantine rows and superseded Silver batches (carried).
7. Whether analysts should be able to choose a sampling strategy at all, or whether the
   platform should pick one — the UI currently always uses the default.
