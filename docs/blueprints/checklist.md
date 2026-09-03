# CINQFLOW Working Foundation — Checklist

Execution discipline for the six stages. Companion to `features.md` (what),
`structure.md` (where), `templates.md` (shapes).

**Method for every stage:** inspect → implement → test → run → fix → verify.
One stage at a time. Never start stage N+1 until stage N's Definition of Done is met
and its completion report (templates.md §7) is written.

---

## 0. Non-negotiables (review gate for every PR/change)

- [ ] Repository inspected before coding; existing abstractions reused, not reinvented.
- [ ] No invented APIs, classes, schemas, dependencies or infrastructure beyond the blueprint.
- [ ] No unrelated rewrites.
- [ ] No Celery/Procrastinate/new queue — the Postgres queue carries all async work.
- [ ] FastAPI handlers: validate → persist → enqueue → return. Nothing long-running inline.
- [ ] LangGraph reasons and proposes only; it never moves bulk data.
- [ ] Deterministic code performs profiling, movement, transformation, preview, Bronze, Silver.
- [ ] No AI output is the system of record without analyst approval.
- [ ] Approved mappings immutable; edits create v(N+1).
- [ ] Originals preserved; replay/reprocessing supported.
- [ ] No LLM-generated code is ever executed; mappings are validated data (mapping_spec).
- [ ] No capability claimed without verification; gaps stated as **UNKNOWN FROM REPOSITORY**.
- [ ] Tests exist for every behavioral change and were actually run.

## 0.1 Pre-flight traces (before the first line of a stage's code)

For each item: read the code built in prior stages and write down (in the stage report)
what actually exists. If nothing exists yet (greenfield), record that and follow
`structure.md` + the blueprint. Never assume.

- [ ] Trace the current upload flow (route → workflow store → queue → worker).
- [ ] Trace the queue/orchestration flow (enqueue, claim, dedupe, retry, crash recovery).
- [ ] Trace the worker/consumer flow (topic registry, handler shape).
- [ ] Trace the runtime/LangGraph wiring (runtime entry, graphs, context builder, prompts).
- [ ] Trace the PipelineRunner/data-plane path (contract → pg adapter → tables).
- [ ] Identify existing persistence models and tests touching this stage.
- [ ] Name exactly where each new change belongs (file paths) before writing it.

## 0.2 Deployment-target exceptions (scoped, temporary, documented here only)

Non-negotiables above hold everywhere by default. One exception exists, and only
for one deployment target:

- **Railway only — api + worker run combined, as one process pair in one
  container, on one Railway service.** Everywhere else (native dev via `make
  api` / `make worker`, Docker Compose in `compose/docker-compose.yml`) they
  stay two independent processes exactly as designed — this is not a change to
  `structure.md`'s runtime processes table, which still describes the real,
  default shape.
  - **Why:** Railway does not support one volume attached to more than one
    service (confirmed via Railway's own docs/support forum, not assumed), and
    the landing zone (`dataplane/filestore.py`) plus `knowledge/` are local
    filesystem paths both api and worker must read/write. Combining them into
    one service is the only way to keep that shared storage on Railway without
    first moving landing/knowledge to object storage (a separate, larger
    change — not done here).
  - **What this relaxes:** "worker independently runnable from API" no longer
    holds *on Railway specifically* — a crash in either process now brings
    down the whole combined service (verified: killing the api process inside
    the combined container exits the container with the api's own exit code,
    by design — see `backend/railway/start.sh`), and they scale as one unit
    instead of independently.
  - **What does not change:** the code. `api/app.py` and `queue/worker.py`
    remain two separate, uncoupled entry points that only ever communicate
    through the Postgres queue — nothing about their logic or the boundaries
    in `structure.md` §Boundaries was touched to make this work. Reverting to
    two separate Railway services later (e.g. once landing/knowledge move to
    object storage) is a pure infrastructure change.
  - **Where it lives:** `backend/Dockerfile.railway` (build context = repo
    root, so it can bake in a `knowledge/` seed - see the file's own header
    comment for why), `backend/railway/start.sh` (the combined process
    launcher), `backend/railway/bootstrap.sh` (Railway's pre-deploy command:
    idempotent knowledge-volume seed + `cinqflow install`), `backend/
    railway.json` (Railway config-as-code wiring the above together).
    None of this is read or used by native dev or `compose/docker-compose.yml`.

---

## Bootstrap (once, before Stage 1)

- [ ] `backend/` Poetry project; ruff; pytest; `settings.py` reads `.env`.
- [ ] `workflow` + `queue` schemas created; artifact tables per templates.md §1.
- [ ] Queue: enqueue/claim with `FOR UPDATE SKIP LOCKED`, UNIQUE dedupe_key, attempts,
      crashed-claim recovery. Integration-tested against real Postgres.
- [ ] `dataplane/contract.py` declares layers/tables/audit columns;
      `python -m cinqflow.dataplane install` renders + applies idempotently.
- [ ] Landing filestore: place/read/move/fingerprint over `LANDING_ROOT`; layout per structure.md.
- [ ] `make api`, `make worker`, `make test` all work.
- [ ] Frontend scaffold runs and can call the API.

## Stage 1 — Upload + deterministic profile + AI interpretation (FIRST DELIVERY)

- [ ] `POST /api/uploads`: validates type (csv/xlsx), stores original to landing `incoming/`,
      computes `sha256-…`, persists upload record, enqueues `upload.profile`, returns 202 fast.
- [ ] Duplicate fingerprint → 409 with clear message; no orphan file left behind.
- [ ] `profile_upload` worker: parse (all values strings) → profiler facts per templates §1.2 →
      immutable profile (`profile_id = hash(facts)`) → status `profiled` → enqueue `upload.interpret`.
- [ ] Malformed file → `profile_failed` with persisted error; original retained.
- [ ] `interpret_upload` worker: ContextBuilder selects source + glossary knowledge;
      `interpret_file` graph (ground[no model] → infer → assemble[no model]) returns
      schema-valid interpretation with claims/kinds/confidence/evidence + provenance;
      persisted; status `interpreted`.
- [ ] LLM failure → `interpret_failed`, retryable; profile untouched.
- [ ] UI: upload form, list with statuses, detail page showing profile and interpretation,
      PHI-candidate values masked.
- [ ] Tests: unit (profiler determinism, checksum, state transitions),
      integration (queue + both workers, replayed LLM fixture),
      e2e (upload roster sample → `interpreted`, all artifacts present).
- [ ] Acceptance criteria in features.md §Stage 1 all pass. Report written.
- [ ] **Definition of Done:** a cold start (`make api` + `make worker`) takes a real roster
      CSV and XLSX from upload to persisted interpretation with zero manual steps.

## Stage 2 — G1 → Landing + Bronze

- [ ] Pre-flight traces re-run against Stage 1 code.
- [ ] Approve/reject endpoints; approval persisted append-only (templates §1.4);
      approve only from `interpreted` (else 409).
- [ ] `land_bronze` worker: mint `batch_id`; move original `incoming/ → processed/`;
      create/ensure Bronze table for the feed from the contract; append rows 1:1 with
      audit columns + `row_number`; record run + counts; balance equation checked.
- [ ] Bronze append-only verified by a test that attempts UPDATE/DELETE and expects failure.
- [ ] Reject path: file → `rejected/`, no plane writes.
- [ ] Lineage rows: upload → fingerprint → batch → bronze table.
- [ ] e2e: approve Stage-1 upload → Bronze row count == profile row count, balanced run.
- [ ] Report written; DoD: G1 click to queryable Bronze with lineage, no manual steps.

## Stage 3 — Bronze intelligence → mapping proposal

- [ ] Deterministic Bronze profile (code) over batch + bounded sample; persisted.
- [ ] KnowledgeProvider serves canonical model, source def, glossary, approved mappings
      (versioned); graphs consume only the provider — grep confirms no YAML import in
      `intelligence/`.
- [ ] `recommend_mapping` graph → proposal per templates §1.5; `validate_proposal` node
      (no model) rejects any target not in the canonical model; unknowns explicit.
- [ ] Provenance persisted (knowledge versions, prompt version, model id).
- [ ] UI: proposal view with per-field confidence/evidence/status.
- [ ] Tests incl.: adversarial fixture where the LLM proposes a fake target → proposal fails
      validation and is persisted as such, not silently corrected.
- [ ] Report written; DoD: bronzed batch → persisted, validated proposal.

## Stage 4 — Editable mapping versions

- [ ] Create draft v1 from proposal; spec validation (allowed ops only, canonical targets only,
      field-level errors).
- [ ] Draft mutable; approved immutable — store rejects writes to non-draft (409 test).
- [ ] Edit-after-approve creates v(N+1) with `derived_from`; previous stays `approved` until
      superseded by the next G2.
- [ ] UI: mapping studio with diff (proposal-origin vs analyst-edited; vN vs v(N-1)).
- [ ] Report written; DoD: analyst can shape a valid spec entirely in the UI.

## Stage 5 — Deterministic preview

- [ ] `mapping.preview` worker: `mapping_exec` in preview mode over bounded Bronze sample;
      results per templates §1.7; version → `previewed`; draft edit invalidates preview.
- [ ] Determinism test: same spec + sample twice ⇒ identical results; no LLM call occurs
      (asserted, e.g. via a poisoned LLM client in the test).
- [ ] UI: source vs mapped values, failures with reasons, aggregate counts; G2 disabled
      without a current preview.
- [ ] Report written; DoD: analyst sees exactly what vN does before any Silver write.

## Stage 6 — G2 → Silver Raw + lineage

- [ ] G2 approve: requires current preview; freezes vN; approval persisted; enqueues
      `mapping.promote`.
- [ ] `promote_silver` worker: same `mapping_exec` code in full mode; Silver rows with audit
      columns; failed rows quarantined with reasons; run recorded; unbalanced ⇒ `failed`.
- [ ] Replay: re-promote clears + rebuilds only this batch's Silver/quarantine; Bronze
      untouched; `record_hash`es identical run-to-run.
- [ ] Approved decisions exported to `knowledge/mappings/approved/<feed>.yaml` (versioned) —
      analyst decisions become future knowledge.
- [ ] `GET /api/lineage/{batch_id}` returns the full chain incl. both approvals.
- [ ] e2e for the whole slice: one roster file, upload → Silver Raw, asserting every artifact,
      both gates, balance, and lineage.
- [ ] Report written; DoD: the target flow runs end to end on a real sample file, and the
      lineage query proves it.

---

## Continuous validation (every stage)

- [ ] `make test` green locally before any stage is called done; failing output reported
      verbatim, never summarized as "mostly passing".
- [ ] New behavior ⇒ new test in the same change.
- [ ] Manual smoke: `make api` + `make worker` + UI walk of the new screen.
- [ ] Stage report (templates §7) committed to `docs/reports/stage-N.md`.
- [ ] Anything unverifiable stated as **UNKNOWN FROM REPOSITORY**.

**Primary principle:** working slice first. Existing architecture over new architecture.
Deterministic execution over LLM execution. Persisted structured artifacts over
conversational output. Small verified changes over broad refactoring.
The goal is not to demonstrate AI — it is to make Analyst → AI → Approval → Data Plane
actually work.
