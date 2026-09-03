# Stage 1 report — 2026-09-02

Scope implemented: **CSV/XLSX upload → immutable original → queued deterministic profile
→ persisted profile → queued LangGraph interpretation → persisted structured interpretation
→ API/UI visibility.** Bronze, mapping, preview, Silver and G1/G2 approvals are **not**
implemented.

---

## 0. Pre-flight traces (required before coding)

The blueprint says the repository is authoritative. What it actually contained:

| Trace | Finding |
|---|---|
| Current upload flow | **None.** Repo held only `docs/`. Stage 1 therefore includes the bootstrap. |
| Queue/orchestration flow | **None.** Built per `structure.md`: Postgres queue, `FOR UPDATE SKIP LOCKED`. |
| Worker/consumer flow | **None.** Built: topic→handler registry, `drain`, `serve`. |
| AgentRuntime/LangGraph wiring | **None.** Built: one runtime, one graph (`interpret_file`). |
| PipelineRunner/data-plane path | **Not built** — correctly out of Stage 1 scope. Only the landing filestore exists. |
| Existing persistence/models/tests | **None.** All created here. |

Two environment findings that changed decisions:

1. The local `cinqflow` database **already contains the previous implementation's 16 schemas**
   (`bronze`, `silver_raw`, `control`, `queue`, `profiling`, …) with data (568k Bronze rows per
   the prior build's notes). Nothing pre-existing was altered, dropped or written to. The new
   build installs only `workflow` and `jobq`.
2. `structure.md` specified a `queue` schema. **That name is taken** by the prior
   implementation, so the setting defaults to `jobq`. This is a deliberate deviation from the
   blueprint, recorded here and in `settings.py`.

---

## 1. Files changed

Created (nothing pre-existing was modified except adding `docs/reports/`):

**Backend — `backend/`**
```
pyproject.toml                          Poetry project, ruff + pytest config
.env.example                            all env difference, documented
src/cinqflow/settings.py                pydantic-settings; DSN, landing root, LLM provider
src/cinqflow/db.py                      thin psycopg3 helpers (no ORM)
src/cinqflow/cli.py                     cinqflow install | work [--once] | status
src/cinqflow/workflow/states.py         UploadStatus + legal transition map
src/cinqflow/workflow/models.py         Upload, ProfileFacts, Interpretation, Claim, Provenance
src/cinqflow/workflow/ddl.py            idempotent DDL for workflow + jobq schemas
src/cinqflow/workflow/store.py          the only workflow SQL
src/cinqflow/queue/queue.py             durable queue: dedupe, claim, stale reclaim
src/cinqflow/queue/worker.py            consumer: registry, run_once, drain, serve
src/cinqflow/workers/profile_upload.py  topic upload.profile   (deterministic)
src/cinqflow/workers/interpret_upload.py topic upload.interpret (AI)
src/cinqflow/engine/parsers.py          CSV (sniffed dialect) + XLSX → rows of strings
src/cinqflow/engine/profiler.py         deterministic facts; profile_id = hash(facts)
src/cinqflow/dataplane/filestore.py     landing zone: place once, move between folders
src/cinqflow/knowledge/provider.py      KnowledgeProvider Protocol + KnowledgeDoc
src/cinqflow/knowledge/yaml_provider.py the ONLY module that reads knowledge YAML
src/cinqflow/intelligence/context.py    ContextBuilder — selective knowledge per job
src/cinqflow/intelligence/llm.py        LlmClient: AnthropicClient | StubClient
src/cinqflow/intelligence/prompts/      versioned prompts + registry (interpret_file@1)
src/cinqflow/intelligence/graphs/interpret_file.py  ground → infer → assemble
src/cinqflow/intelligence/runtime.py    one AgentRuntime; graphs are capabilities
src/cinqflow/api/app.py                 control plane: 5 endpoints, thin handlers
tests/…                                 56 tests across unit / integration / e2e
```

**Frontend — `frontend/`**: `package.json`, `tsconfig.json`, `next.config.ts`,
`pnpm-workspace.yaml`, `lib/api.ts` (only module talking to the API), `app/layout.tsx`,
`app/globals.css`, `app/page.tsx` (upload form + list), `app/actions.ts` (server action),
`components/UploadForm.tsx`, `app/uploads/[uploadId]/page.tsx` (profile + interpretation).

**Knowledge — `knowledge/`** (grounded in `docs/`, see §5): `glossary.yaml`,
`sources/fidelis_ny_upstate__member_roster.yaml`,
`sources/fidelis_ny_downstate__member_roster.yaml`.

**Root**: `Makefile` (`install`, `install-db`, `api`, `worker`, `work-once`, `status`,
`test`, `lint`, `fmt`).

---

## 2. Actual runtime flow (verified, not described)

```
POST /api/uploads (multipart)
  → validate extension (.csv/.xlsx/.xlsm) + non-empty        → 415 / 400
  → compose landing key {domain}/{source}/{feed}/incoming/{business_date}/{file}
  → INSERT workflow.upload (status=received)                 → 409 on duplicate fingerprint
  → write original to landing (place-once, never overwrite)
  → INSERT jobq.message topic=upload.profile (dedupe_key=upload.profile/<id>)
  → COMMIT, return 202 {upload_id, status, fingerprint, landing_key}

cinqflow work
  → claim (FOR UPDATE SKIP LOCKED), commit the claim, then handle
  → upload.profile:  read original → parse → profile (code only)
                     → INSERT workflow.profile (profile_id = sha256(facts)[:32])
                     → status=profiled → enqueue upload.interpret
  → upload.interpret: ContextBuilder selects source + matching glossary terms
                     → LangGraph ground(no model) → infer(model) → assemble(no model)
                     → INSERT workflow.interpretation v1 (draft) with provenance
                     → status=interpreted

GET /api/uploads/{id} → upload + profile (PHI sample values masked) + interpretation
GET /api/uploads, GET /api/queue/depth, GET /api/health
UI: / (form + list with status) · /uploads/{id} (profile table, claims, risks, unknowns)
```

**Measured on the real de-identified Fidelis upstate roster (11 MB, 28,333 rows, 45 columns):**

- `POST /api/uploads` returned in **0.045 s** — the API does not ingest inline.
- Worker `--once` processed both jobs in **2.3 s** total.
- Result: `status=interpreted`, `profile_id=2afd6c67…`, 28,333 rows / 45 columns / 0 duplicates,
  candidate keys `[member_id]` and `[member_phone_number]`, 17 PHI candidate columns.
- Interpretation: 5 claims — `likely_domain=enrollments` (**governed_knowledge**, 0.99,
  evidence `source:sources/fidelis_ny_upstate__member_roster.yaml@1`),
  `likely_dataset=member_roster` (inference 0.85),
  `likely_grain=one row per member_id per monthly delivery` (inference 0.80),
  `row_count=28333` (observed_fact 1.0), `phi_handling` (recommendation 0.90).
  Risks included real null rates (`member_address_line_2` null in 88.6% of rows).
  Provenance: `interpret_file@1` / `stub-reasoner-1` / knowledge
  `[sources/fidelis_ny_upstate__member_roster.yaml@1, glossary.yaml@3]`.
- The XLSX downstate roster (38,489 rows, 45 columns, `Sheet1`) also reached `interpreted`.
- Duplicate upload of the same bytes under a new filename → **409** citing the original
  `upload_id`. Unsupported `.txt` → **415** with nothing persisted.

---

## 3. Tests executed and results

```
$ cd backend && poetry run pytest -q
56 passed, 1 warning in 20.78s

$ poetry run ruff check src tests
All checks passed!

$ cd frontend && pnpm build
✓ Compiled successfully   (routes: /, /uploads/[uploadId])
```

Breakdown: 31 unit (profiler determinism/type inference/keys/duplicates/PHI, filestore
place-once + move + unsafe filenames, state machine, context selectivity, graph validation),
15 integration (queue dedupe / skip-locked isolation / retry / dead-lettering; store duplicate
refusal, lifecycle enforcement, both workers, failure paths, supersede, full drain),
10 e2e (API→worker→API for real CSV and XLSX, duplicate 409, 415, 400, PHI masking, failed
parse visibility, list, 404).

Notable adversarial tests, all passing: a model returning a claim with **no evidence** has that
claim discarded and recorded as an unknown; a model returning malformed claims yields **zero**
stored claims plus two unknowns; the stub reasoner is asserted byte-identical across runs;
re-profiling identical bytes produces one profile row, not two.

**Two real bugs were found by these tests and fixed:**

1. `queue.claim()` rolled back the handler's transaction on failure, which also **rewound the
   attempt counter** — a poison message would have retried forever and never reached `dead`.
   Fixed by committing the claim before the handler runs, plus `reclaim_stale()` (default
   300 s) so a hard-crashed worker's message returns to `pending` without losing attempts.
2. Recording a failure after that rollback attempted an illegal transition
   (`profiled → interpret_failed`), because the rollback discarded the in-progress marker.
   Fixed in the transition map, with the reason stated in `states.py`.

---

## 4. Non-negotiables — how each is satisfied

| Rule | Where |
|---|---|
| Handlers validate/persist/enqueue/return | `api/app.py`; 0.045 s response on an 11 MB file |
| Long work in workers | `workers/*.py`, driven by `cinqflow work` |
| No Celery/Procrastinate | `queue/queue.py` on Postgres only |
| Deterministic profiling precedes AI | `upload.profile` completes and persists before `upload.interpret` is enqueued |
| LangGraph reasons, never moves data | `graphs/interpret_file.py`; only `_infer` calls a model; no data-plane import |
| AI never the system of record | interpretation persists as `draft`; no approval, no Bronze exists |
| No free-form LLM text as truth | `assemble` validates into `Claim`/`InterpretationContent`; evidence-less claims dropped |
| Structured artifacts with provenance | `provenance = {prompt@version, model, knowledge@version[]}` |
| Originals preserved / replay | `filestore.place()` refuses overwrite; `UNIQUE(fingerprint)`; profile idempotent |
| LangGraph not coupled to YAML | graphs use `KnowledgeProvider`; only `yaml_provider.py` reads files |
| PROMPT / KNOWLEDGE / CONTEXT / LLM separated | `prompts/`, `knowledge/`, `context.py`, `llm.py` |
| Selective context | source doc + only glossary terms matching observed columns; full sample rows excluded from the prompt |
| Claim kinds distinguished | `observed_fact` / `governed_knowledge` / `inference` / `recommendation`, rendered in the UI |

---

## 5. Knowledge grounded in `docs/` (per instruction)

No column names or canonical targets were invented. Sources read:

- `docs/03_data_source_schemas/enrollment/enrollment_silver_raw_model.sql` — the real canonical
  tables (`members`, `members_addresses`, `members_emails`, `members_phones`,
  `members_enrollment_segments`) and their fields/PKs.
- `docs/04_source_data_samples_and_profiles/1-Enrollment/1.Fedelis_NY/` — the real 45 roster
  columns and value formats, read from both the upstate CSV and the downstate XLSX (verified
  identical column sets).

This corrected three assumptions that would have propagated into Stage 3/4:

1. Canonical member identity is **`members.source_system_id`** with PK
   `(source_system_id, source_system)` — not `source_member_id`; and sex is **`sex`**, not
   `gender`.
2. `members_programs` / `members_providers` (used in my earlier `templates.md` examples)
   **do not exist**. Plan data lives in `members_enrollment_segments` (`lob`, `member_plan`,
   `member_payor`, `pcp_npi`, `tin`, `member_group`).
3. `members_enrollment_segments` carries **no effective/end/recertification/status fields**.
   So `enrollment_date`, `coverage_end_date`, `recertification_*`, `harp_eligible`,
   `member_status`, `last_awv_date`, `member_age`, `guardian_*` and `primary_coverage_payor`
   have **no canonical target**. The glossary marks these `canonical_target: none` with the
   reason, instead of inventing a destination — these are exactly the mappings Stage 3 must
   surface as `unknown`.

---

## 6. Remaining gaps

- **Live LLM is unverified.** No API key exists in this environment, so
  `CINQFLOW_LLM_PROVIDER=stub` is the default and every run above used the deterministic
  offline reasoner. `AnthropicClient` is implemented and JSON-strict but has **never executed
  against the real provider** — that is untested code. The prompt (`interpret_file@1`) is
  written for a real model and has not been evaluated against one.
- **`templates.md` needs correcting** to the real canonical field names found in §5 (it
  currently shows `source_member_id` / `gender` / `members_programs`). Not changed in this
  stage to keep the diff to Stage 1 code.
- No G1 approval, Bronze, mapping, preview or Silver — Stage 2+ by design.
- No `docs/blueprints/structure.md` update for the `queue`→`jobq` rename (recorded here).
- Worker is CLI/cron-shaped; no compose service, no supervisor. `compose/` not created.
- Single-worker retry only; no exponential backoff between attempts.
- No authentication: `uploader` is a form field, not an identity. Acceptable for Stage 1,
  must not survive into a shared environment.
- Candidate-key search is capped (singles, then up to 3 pairs among the first 8 complete
  columns) — a deliberate bound, not exhaustive.
- Profiler holds the parsed file in memory. Fine at 11 MB / 38k rows; untested at GB scale.
- Frontend has no auto-refresh; the analyst reloads to see the worker's result.

## 7. Assumptions

- One Postgres instance carries both workflow state and (later) the medallion; separable by
  DSN when needed.
- Non-empty string ⇒ non-null; whitespace is stripped at parse. Blank ≠ zero, blank ≠ false.
- PHI candidacy is decided from **column names only**, never from values, so no PHI is read to
  classify PHI. Consequence: a PHI-bearing column with an opaque name is missed — the analyst
  is the backstop at G1.
- `profile_id` covers all observed facts including sample rows; a different sample ⇒ a
  different id.
- The first XLSX sheet is the data sheet; other sheets are listed with `rows: 0`, not parsed.
- Type inference thresholds (95% for date/int/bool/timestamp, `code_like` at 95%) are
  judgement calls, not derived from the corpus.
- Interpretation versions are per `(upload_id, profile_id)`; a new profile restarts at v1.

## 8. UNKNOWN FROM REPOSITORY

- Whether the canonical enrollment model in `enrollment_silver_raw_model.sql` is the version
  Stage 3 should target, or whether a newer model supersedes it (several spreadsheets in
  `docs/03_data_source_schemas/enrollment/` carry later dates, e.g. `DataModel_02APR2026.xlsx`,
  which were not opened).
- Where coverage periods and recertification are meant to live canonically (§5 item 3).
- The correct `source_system` naming convention for feeds — `fidelis_ny_upstate` was chosen
  from the `vbp_id` values (`CINQUpstate`/`CINQDownstate`); no registry in the corpus defines it.
- Which LLM provider/model CINQCARE intends to use, and the PHI egress rules governing it.
  Nothing in `docs/` states this, so no PHI leaves the process today: prompts carry column
  facts and bounded example values but not sample rows.
