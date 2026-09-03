# Stage 3 report — 2026-09-03

Scope implemented: **Bronze intelligence → AI mapping proposal.** A landed batch is
profiled deterministically, then the AI proposes source→canonical mappings grounded in
governed knowledge, validated by code, and persisted as an advisory artifact. Editable
mapping versions (Stage 4), preview (5) and Silver Raw (6) are **not** implemented.

> **Instruction conflict, again resolved and flagged.** The header said "Implement Stage 3
> from checklist.md only"; the goal paragraph beneath it restated Stage 1 and said "do not
> implement Bronze… or future-stage functionality". Stage 3 in `checklist.md` *is* Bronze
> intelligence. I followed the header, as in Stage 2. Everything added is additive.

---

## 0. Pre-flight traces

| Trace | Finding |
|---|---|
| Upload → profile → interpret | Stages 1–2, unchanged. Reused as-is. |
| Queue/orchestration | `queue/queue.py`; one topic added (`bronze.analyze`). Landing now chains into it. |
| Worker/consumer | `queue/worker.py::handlers()`; extended. |
| AgentRuntime/LangGraph | `intelligence/runtime.py` held one graph. Extended to a builder map; `interpret_file` untouched. |
| PipelineRunner/data-plane | `dataplane/{contract,port,pg}.py` + `engine/runner.py` from Stage 2. Read back through the same port; **no new plane writes**. |
| Persistence/models/tests | Extended `workflow/{models,ddl,store}.py`; 93 tests were green before this stage. |

---

## 1. Files changed

**New**
```
knowledge/canonical/enrollment.yaml               the governed target model (see §5)
knowledge/mappings/approved/enrollment_historical.yaml   prior decisions, from docs/
src/cinqflow/engine/bronze_profiler.py            deterministic profile of a landed batch
src/cinqflow/intelligence/graphs/recommend_mapping.py    ground -> recommend -> validate
src/cinqflow/intelligence/prompts/recommend_mapping_v1.md
src/cinqflow/workers/analyze_bronze.py            topic bronze.analyze
tests/unit/test_mapping_intelligence.py           (17)
tests/integration/test_bronze_intelligence.py     (9)
tests/e2e/test_stage3_flow.py                     (5)
```

**Extended**
```
knowledge/provider.py       +get_canonical, +get_approved_mappings
knowledge/yaml_provider.py  implements both; merges approved sets per domain
intelligence/context.py     +for_mapping(), +legal_targets()
intelligence/llm.py         StubClient gains a rule-based mapping mode
intelligence/runtime.py     graph builder map
workflow/models.py          +BronzeProfile, FieldCandidate, Transform, ProposalContent,
                            Proposal, FieldStatus; BatchDetail gains both
workflow/ddl.py             +workflow.bronze_profile, +workflow.proposal
workflow/store.py           +put/get_bronze_profile, +put/get_proposal(_by_id)
workers/land_bronze.py      queues bronze.analyze after a clean landing
api/app.py                  +GET /batches/{id}/bronze-profile, +/batches/{id}/proposal;
                            batch detail carries both; +_mask_facts(); queue depth extended
queue/worker.py             registers bronze.analyze
frontend                    lib/api.ts (+BronzeProfile, MappingProposal, getters),
                            app/batches/[batchId]/page.tsx (profile + proposal views)
tests/e2e/test_stage2_flow.py  drain assertion 1 -> 2 (landing now chains into analysis)
```

---

## 2. Actual runtime flow

```
worker: batch.land_bronze  (Stage 2)
  → on success, enqueue bronze.analyze {batch_id}          ← no manual step

worker: bronze.analyze
  → refuse unless the run state is completed  ← never describe a partial batch
  → DETERMINISTIC: read the batch back through the data-plane port (bounded window),
    rebuild the ParsedFile shape, run the SAME profiler as Stage 1
    → persist workflow.bronze_profile (profile_id = hash(facts), rows_in/rows_profiled)
    → COMMIT                                  ← the facts survive an AI failure
  → AI: ContextBuilder assembles observations + canonical model + source def +
    matching glossary terms + approved decision sets  (no sample rows travel)
    → recommend_mapping graph: ground(no model) → recommend(model) → validate(no model)
    → persist workflow.proposal with provenance and per-field status
```

The `validate` node is where the AI becomes safe to listen to. Deterministically, it:
rejects any target absent from the canonical model (kept visible as `rejected_target`,
proposal marked `invalid`); rejects platform-populated columns as targets; discards fields
naming a column that is not in Bronze; drops transform ops outside the allowed vocabulary
while keeping the mapping; zeroes confidence on evidence-free candidates; adds every
unaddressed column as an explicit `unknown`; and marks two columns claiming one target as
`ambiguous`.

**Measured on the real de-identified Fidelis upstate roster (28,334 rows, 45 columns):**

```
landed 28334 rows to bronze.roster_stage3_raw for batch 15486198c898
analysed batch 15486198c898: 45 columns profiled, proposal proposed
counts: {candidate: 21, ambiguous: 2, unknown: 22}      (45 columns, all accounted for)
provenance: recommend_mapping@1 | stub-reasoner-1
knowledge: canonical/enrollment.yaml@1, glossary.yaml@3, mappings/approved@1
```

23 columns received landable targets, e.g.
`member_dob → members.date_of_birth` (with `parse_date`, because the DDL column is a
TIMESTAMP and the observed values are ISO dates), `member_sex → members.sex` (the DDL
name, **not** the spreadsheets' "gender"), `member_city → members_addresses.city`,
`product → members_enrollment_segments.lob`, `provider_npi → …pcp_npi`,
`vbp_id → …tin_submarket`.

22 columns were returned as **unknown rather than guessed**: `enrollment_date`,
`coverage_end_date`, `recertification_*`, `harp_eligible`, `health_home_*`,
`member_status`, `member_age`, `guardian_*`, `last_awv_date`, `primary_coverage_*`,
`provider_id`, `ep_assignment_flag`, `high_need_status`, `in_care_management_with_fidelis`,
`pcp_assignment_type`, `recertification_source`. The proposal's own notes explain that the
canonical model records several of these as contested/absent.

2 columns were marked **ambiguous**: `member_id` and `medicaid_id` both proposed
`members.source_system_id` — "2 columns propose members.source_system_id: medicaid_id,
member_id. One must win."

---

## 3. Tests executed and results

```
$ cd backend && poetry run pytest -q
123 passed, 1 warning in 27.79s      (93 from Stages 1-2 + 30 new)

$ poetry run ruff check src tests
All checks passed!

$ cd frontend && pnpm build
✓ Compiled successfully
```

The adversarial suite the checklist asks for, all passing:

| Attack | Behaviour |
|---|---|
| Model invents `members.member_uuid` | proposal `invalid`; field `status=invalid`, `target=None`, `rejected_target` retained, reason recorded. **Not silently corrected.** A legitimate candidate in the same response survives. |
| Model targets `members.record_hash` (platform-populated) | refused the same way |
| Model names a source column never in Bronze (`patient_ssn`) | discarded with a note; all real columns still accounted for |
| Model returns `transform: exec_python(code=…)` | transform dropped, note recorded, mapping kept. No LLM-authored code is ever retained, let alone run |
| Candidate with empty `evidence` | confidence forced to 0.0, downgraded to `ambiguous` |
| Two columns claiming one target | both `ambiguous` with the conflict named |

Architecture invariants are asserted, not assumed: no module in `intelligence/` imports
`yaml`, calls `yaml.safe_load`, or calls `open(`; and no graph or `context.py` mentions
`YamlKnowledgeProvider` (only `runtime.py`, the composition root, may).

**One real defect found and fixed:** Postgres **JSONB does not preserve key order**, so
source column order was lost through Bronze and the Bronze profile disagreed with the upload
profile. This is a genuine consequence of Stage 2's `raw_row JSONB` decision. Fixed by having
the worker pass the upload profile's column order into `profile_batch()`; columns not in that
list (drift) are appended. A test now documents the underlying property directly, and another
asserts the two profiles agree on row count, column order, null counts, distinct counts,
inferred types and candidate keys.

**One improvement made because the live run exposed it:** the real roster proposed
`members.source_system_id` from two different columns. Nothing caught it, and it would have
surfaced as a failed write much later. The validator now detects contested targets and marks
them `ambiguous` — verified on the real batch.

---

## 4. Stage 3 checklist status

| Item | Status |
|---|---|
| Deterministic Bronze profile over batch + bounded sample; persisted | done — `workflow.bronze_profile`, window recorded, `is_sample` exposed |
| KnowledgeProvider serves canonical model, source def, glossary, approved mappings (versioned); no YAML import in `intelligence/` | done — asserted by test, not by grep alone |
| `recommend_mapping` graph → proposal; `validate_proposal` (no model) rejects non-canonical targets; unknowns explicit | done — validation is a node in the graph, model-free |
| Provenance persisted (knowledge versions, prompt version, model id) | done |
| UI: proposal view with per-field confidence/evidence/status | done — batch page renders both profile and proposal, PHI-masked |
| Adversarial test: fake target fails validation, persisted as such | done (§3) |
| DoD: bronzed batch → persisted, validated proposal | met, on the real 45-column roster, with no manual step |

Non-negotiables: AI never wrote to the data plane in this stage (it only reads a bounded
window through the port); deterministic profiling completed and committed **before** the model
ran; no free-form text is stored as record; the proposal API returns `"authoritative": false`
and the UI says a mapping does not exist yet.

---

## 5. Knowledge grounded in `docs/` — and a conflict found there

`knowledge/canonical/enrollment.yaml` was built by reading
`docs/03_data_source_schemas/enrollment/enrollment_silver_raw_model.sql` in full: five
entities (`members`, `members_addresses`, `members_phones`, `members_emails`,
`members_enrollment_segments`), every field with its declared type, comment and primary key.

`knowledge/mappings/approved/enrollment_historical.yaml` was extracted from
`Enrollment_DB_to_Datalake_SilverRaw_mapping.xlsx` — the client's own three decision sets
(Non-ACO internal DB, ACO REACH D0284, MSSP).

**The two documents disagree, and the disagreement matters:**

| Discrepancy | Resolution taken |
|---|---|
| Spreadsheet maps `Members.Sex → "gender"`; the DDL column is `members.sex` | Recorded as a documented `equivalence`. Legal target is `members.sex`; approved examples cite the DDL name with `source_document_target: gender` preserved. |
| Spreadsheet targets `guardian_first_name`, `guardian_last_name`, `guardian_phone_number`, `guardian_email`, `feed_name` — none exist in the DDL | Recorded under `contested_fields`, **not** offered as targets. A mapping to a field that does not exist would fail at Silver. The roster's `guardian_*` columns therefore come back as `unknown`, which is the honest answer. |
| Roster feeds carry `enrollment_date` / `coverage_end_date`; `members_enrollment_segments` declares no effective/end dates | Recorded as contested; surfaced as `unknown`. Confirms the Stage 1 finding. |

The rule applied: **a target is legal only if a Silver Raw write could land in it**, so the
executable DDL governs. This also corrects my own earlier `templates.md` examples.

---

## 6. Remaining gaps

- **Live LLM still unverified.** Everything above ran on `CINQFLOW_LLM_PROVIDER=stub`
  (no API key in this environment). `recommend_mapping@1` is written for a real model and has
  **never been executed against one**; `AnthropicClient` remains untested code. The stub is a
  documented rule-based reasoner, and it is honest about being one (`model: stub-reasoner-1`
  in every provenance record), but a real model will propose differently — in particular it may
  attempt targets the validator then rejects, which is precisely what the adversarial tests
  simulate.
- **The stub cannot judge semantics.** It matches on glossary `maps_toward`, prior decisions
  and exact name equality. It found 23 of 45 columns; a real model should do better on the
  remaining 22 (some genuinely have no home, but `provider_id` and `member_status` plausibly do).
  So the 23/45 figure measures the stub, not the ceiling.
- **`health_home_status → members.care_management_program`** is a glossary-driven mapping the
  canonical model may not intend: `care_management_program` is a single string and the roster
  has four health-home columns. The knowledge file says so; the proposal does not yet warn.
- **Bronze profiling reads a 5,000-row window by default.** For the 28k-row roster that is a
  sample, and while `is_sample` is exposed, null/distinct counts from a window can mislead.
  A full-batch count pass would be more truthful for column statistics.
- **No proposal endpoint by id**, no proposal history view: only the latest per batch is
  surfaced (`get_proposal_by_id` exists in the store, unused by the API).
- **Re-analysis creates a second proposal** (verified) but nothing supersedes the first, and
  no UI triggers re-analysis.
- No G1-style gate on the proposal — deliberately, since Stage 4 is where the analyst takes
  ownership.
- `docs/blueprints/templates.md` and `structure.md` remain out of date (canonical field names,
  `queue`→`jobq`, and now the Stage 3 modules).

## 7. Assumptions

- **The landing domain is plural, the canonical domain singular** (`enrollments` →
  `enrollment`), so the worker strips a trailing "s". Crude, and it will mis-handle a domain
  whose name legitimately ends in "s".
- Legal targets are `table.field` strings derived from the canonical YAML minus
  `system_populated`. Cross-entity concerns (that `members_addresses` needs an
  `address_type` the roster never supplies, so a row could not satisfy its PK) are **not**
  validated here — that is Stage 4/5 territory.
- The Bronze profile is comparable to the upload profile on facts but has a different
  `profile_id`, because each records which sheet/table its rows came from. That provenance is
  deliberately part of the hashed facts.
- Transform vocabulary for Stage 3 is `parse_date, trim, upper, lower, cast, value_map`.
  Stage 4 will narrow or extend it; anything else is dropped, never stored.
- One proposal per analysis run, immutable once written. Proposals are not versioned against
  each other — versioning belongs to the mapping artifact in Stage 4.

## 8. UNKNOWN FROM REPOSITORY

- **Which canonical version is authoritative**: `enrollment_silver_raw_model.sql` (used here)
  or the newer-looking spreadsheets that reference guardian/coverage fields. Several files in
  `docs/03_data_source_schemas/enrollment/` carry later dates
  (`DataModel_02APR2026.xlsx`, `Enrollment_Lake_Models.xlsx`) and were **not** opened. If one
  of those supersedes the DDL, `canonical/enrollment.yaml` must be regenerated and the
  contested-field list will shrink.
- Whether `member_id` or `medicaid_id` is the intended `source_system_id` for the Fidelis
  roster, and what `source_system_id_type` should then carry. The corpus does not say; the
  proposal correctly refuses to choose.
- Where coverage periods and recertification dates are meant to live canonically.
- Whether `vbp_id` ("CINQUpstate"/"CINQDownstate") belongs in
  `members_enrollment_segments.tin_submarket` (commented "PCP Upstate/Downstate") or in
  `members.location`. Recorded as a candidate with a note, not a settled decision.
