# CINQFLOW Working Foundation — Features

Companion to `CINQFLOW_Rebuild_Blueprint.html` (the architecture), `structure.md` (where code lives),
`templates.md` (artifact/knowledge/config shapes) and `checklist.md` (per-stage execution).

**Mission:** a working vertical slice, not a speculative platform.

```
Analyst → CSV/XLSX Upload → Deterministic Profiling → AI Understanding → G1 Approval
→ Landing → Bronze → Bronze Intelligence → AI Mapping Proposal → Editable Mapping
→ Deterministic Preview → G2 Approval → Silver Raw
```

Stages ship **one at a time**, in order. Stage 1 is the first delivery; nothing from Stage 2+
is started until Stage 1 is working and tested.

---

## Global features (present from Stage 1)

| Feature | Behaviour |
|---|---|
| Thin control plane | Every FastAPI handler only validates, persists workflow state, enqueues, and returns. No long-running work inline. |
| Durable queue | Postgres-backed (`SELECT … FOR UPDATE SKIP LOCKED`), dedupe keys, attempt counts, crash-safe claim. No Celery/Procrastinate. |
| First-class workflow artifacts | Every noun in the flow (upload, profile, interpretation, proposal, mapping version, preview, approval, run, lineage) is a persisted row with id, version and explicit state. Nothing lives only in a response body or an LLM transcript. |
| Deterministic before AI | Code computes facts (profiling, statistics, parsing, execution). The LLM only reasons over persisted facts + small samples and writes structured proposals. |
| AI is never the record | No AI output becomes authoritative without an analyst approval. No free-form LLM text as source of truth — structured outputs only, with confidence + evidence. |
| Replay | Original files are immutable in landing; any batch can be reprocessed from its landed original. `UNIQUE(fingerprint)` refuses duplicate arrivals. |
| Status visibility | Every artifact's state is queryable via API and visible in the UI. |

**Non-goals for the foundation** (deliberately out of scope): chip/plug-play connector
architecture, SFTP/API-pull ingestion, identity resolution, Silver ODS, gold layer, DQ rule
authoring, reconciliation packs, multi-tenant RBAC (a simple uploader identity is enough),
Databricks adapter (the *logical contract* keeps the door open; only the PG rendering is built).

---

## Stage 1 — Upload + AI Understanding (FIRST DELIVERY)

**Goal:** an analyst uploads a CSV/XLSX and, without any human help, gets back a persisted
deterministic profile and a persisted structured AI interpretation of what the file is.

### Features
- `POST /api/uploads` — multipart CSV/XLSX upload with `source_system`, `feed`, `business_date`.
- Upload record persisted: `upload_id`, filename, type, size, `sha256-…` checksum, uploader, timestamp, status.
- Original file preserved byte-for-byte (landing zone `incoming/` folder).
- Deterministic profiler (worker, code only — no model): sheets, columns, inferred types,
  row count, null counts, distinct counts, sample rows, candidate keys, duplicates,
  date/number/code patterns, potential PHI/PII columns.
- Profile persisted immutably; `profile_id` = hash of the computed facts.
- AI interpretation (worker → LangGraph runtime): likely domain, likely dataset, likely grain,
  interpretation, insights, risks/unknowns — each with confidence and evidence keyed to
  profile facts. Persisted as a structured artifact versioned against the profile.
- API + UI: upload list, upload detail with profile and interpretation, per-column drill-down.

### Acceptance criteria
1. Uploading `_CINQDOWNSTATE_Member_Roster_202606.csv` (in `04_source_data_samples_and_profiles/`
   class of files) yields status `received → profiled → interpreted` with no manual step.
2. The profile is reproducible: re-profiling the same bytes produces the same `profile_id`.
3. The interpretation JSON validates against its schema; every claim carries `evidence`
   referencing profile facts; nothing is stored as free text only.
4. A second upload of identical bytes is refused (fingerprint conflict) with a clear message.
5. XLSX and CSV both work; a malformed file lands in state `profile_failed` with the error persisted.
6. FastAPI request completes in < 1s regardless of file size (work is queued).

### Out of scope in Stage 1
Bronze, mapping, Silver, approvals (the G1 button may render disabled).

---

## Stage 2 — G1 Approval → Landing + Bronze

**Goal:** an analyst decision — not an AI output — releases data into the plane.

### Features
- `POST /api/uploads/{id}/approve` / `/reject` (G1). Approval persisted append-only:
  who, when, artifact version approved.
- On approval (worker): batch identity created (`batch_id`), original moved
  `incoming/ → processed/` in the landing layout
  `{domain}/{source_system}/{feed}/{folder}/{business_date}/{filename}`,
  Bronze written source-aligned — one table per feed, values as received, typed but
  semantically untouched, plus the audit columns
  (`source_system, ingestion_ts, batch_id, record_hash, created_ts, updated_ts`) and `row_number`.
- Bronze is append-only (DB-enforced: reject-mutation trigger + `REVOKE UPDATE, DELETE, TRUNCATE`).
- Per-stage counts recorded; the balance equation holds:
  `records_in = records_out + quarantined + attributed_drops`.
- **No semantic mapping at Bronze.**

### Acceptance criteria
1. Rejecting an upload never touches the data plane.
2. Approving writes exactly `row_count` Bronze rows for the batch; balance recorded.
3. Re-running a batch replaces only its own rows downstream — Bronze itself is never mutated.
4. `UPDATE`/`DELETE` against Bronze fails at the database level.
5. Lineage query answers: which upload/file produced this batch's Bronze rows.

---

## Stage 3 — Bronze Intelligence → AI Mapping Proposal

**Goal:** the AI proposes source→canonical mappings, grounded in governed knowledge —
it never invents canonical targets.

### Features
- Deterministic Bronze profile (code): column stats over the batch + a bounded sample.
- Context assembly (ContextBuilder over KnowledgeProvider — see `templates.md`):
  Bronze observations + source/feed definition + canonical entity/fields + glossary terms
  + approved historical mapping decisions. **Selective** — never the whole knowledge base.
- LangGraph `recommend_mapping` graph produces a persisted proposal: per source field →
  candidate target field (must exist in the canonical model), transformation if required,
  confidence, evidence, `unknown`/`ambiguous` status where no defensible candidate exists.
- Proposal is a first-class artifact (`proposal_id`), state `proposed`.

### Acceptance criteria
1. Every proposed target field exists in the governed canonical model — validated by code
   before persisting; violations fail the proposal, not the validator.
2. Fields with no candidate are explicitly `unknown`, never guessed.
3. Provenance persisted: knowledge document versions + prompt version + model id used.
4. The proposal renders in the UI with confidence and evidence per field.

---

## Stage 4 — Editable Mapping (versioned)

**Goal:** the analyst owns the mapping; the proposal is only a starting point.

### Features
- `POST /api/feeds/{feed}/mapping-versions` — create draft v1 from a proposal (or empty).
- Draft editing: source→target field, datatype conversion, simple transformations
  (trim, upper/lower, date parse with format, concat), null/default handling, basic value
  mapping (e.g. `M→male`). Constrained representation only — see `templates.md`; never code.
- Drafts are mutable. **Approved versions are immutable** — editing an approved vN creates
  draft v(N+1). Enforced in the store, not by convention.
- Diff view: draft vs latest approved; proposal-origin vs analyst-edited per field.

### Acceptance criteria
1. `PUT` against an approved version returns 409 and creates nothing implicitly.
2. Creating v(N+1) from vN copies the spec and records `derived_from: vN`.
3. A mapping spec that names a non-canonical target or an unsupported transform is rejected
   on save with a field-level error.

---

## Stage 5 — Deterministic Preview

**Goal:** the analyst sees exactly what the mapping will do before it touches Silver.

### Features
- `POST …/mapping-versions/{n}/preview` → worker executes the mapping spec **in code**
  against a bounded Bronze sample of the batch.
- Persisted preview result: per row and per field — source value, mapped value,
  transformation result, failure reason; aggregates — failures, null/invalid counts,
  affected record counts.
- Preview marks the version `previewed`; editing the draft invalidates the preview.

### Acceptance criteria
1. Identical spec + identical sample ⇒ identical preview (deterministic, LLM not invoked).
2. Every transformation failure is attributed to a row and a rule, not swallowed.
3. G2 approval is impossible for a version without a current preview.

---

## Stage 6 — G2 Approval → Silver Raw

**Goal:** the approved mapping runs deterministically, and the whole chain is auditable.

### Features
- `POST …/mapping-versions/{n}/approve` (G2): approval persisted; version frozen.
- Worker executes the approved spec over the full Bronze batch → `silver_raw` table for the
  domain, with audit columns; run recorded (`received → in_progress → completed|failed`);
  failed rows quarantined with reasons; balance equation enforced — an unbalanced run is
  `failed`, not "mostly fine".
- Re-runs rebuild only their own batch in Silver + quarantine; Bronze untouched.
- Lineage persisted end to end: `upload → file → batch → Bronze → mapping vN → Silver Raw`,
  queryable from either end.

### Acceptance criteria
1. Silver rows exist only for approved mapping versions; the version id is on every row's lineage.
2. `records_in = records_out + quarantined + attributed_drops` for the run.
3. Re-promoting the batch (replay) yields the same `record_hash`es.
4. `GET /api/lineage/{batch_id}` returns the full chain with artifact ids and versions.

---

## AI & knowledge architecture (applies from Stage 1, grows with stages)

- **One** intelligence runtime; capabilities are graphs/nodes/tools
  (`interpret_profile`, `retrieve_source_knowledge`, `retrieve_canonical_model`,
  `retrieve_historical_mapping`, `analyze_bronze`, `recommend_mapping`, `validate_proposal`)
  — not one agent per capability.
- Knowledge starts as YAML under `knowledge/` but LangGraph is **never** coupled to YAML:
  graphs consume a `KnowledgeProvider` / `ContextBuilder` abstraction, so YAML can later
  become database/catalog/vector/document retrieval without rewriting graphs.
- Strict separation: **PROMPT** (reasoning instructions, versioned) ≠ **KNOWLEDGE**
  (governed facts, versioned) ≠ **CONTEXT** (knowledge selected for this job) ≠ **LLM**
  (reasoning over observations + context).
- Every AI artifact distinguishes: observed fact / governed knowledge / inference /
  recommendation / analyst decision — and records provenance (knowledge versions, prompt
  version, model id).
- Approved analyst decisions feed back as knowledge (`knowledge/mappings/approved/`);
  AI inference never silently replaces governed knowledge.
- Long-running AI work: FastAPI → queue → worker → runtime. Bounded interactive calls may
  go API → runtime directly if they stay within request-time budgets.
