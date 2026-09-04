# Validation and Implementation Plan — Analyst Workflow, Persona View, DAG Architecture

> Reads `01_data_analyst_workflow.md`, `02_persona_based_data_view.md` and
> `03_dag_background_worker_architecture.md` against the repository as it stands on 2026-09-05
> (Stages 1–6 green, auth Phase 1, S0–S5 run flow, `Signal`/headline work, Railway production).
> Every "already built" claim below cites the file that proves it; every gap is marked **GAP**.
> Corpus figures were measured with the platform's own parser, not estimated.

---

## 0. Verdict

| Document | Verdict | One-line reason |
|---|---|---|
| 03 DAG + background workers | **Adopt as the written architecture; build the one missing piece (an explicit step ledger). Do not adopt an orchestrator.** | The platform already *is* this architecture — the DAG is just implicit. |
| 02 Persona-based data view | **Adopt, in three scoped increments (column roles → persona defaults → statistical profile). Defer the question-aware view.** | The corpus proves the premise; the progressive-disclosure UX already exists; the semantic layer and persona defaults do not. |
| 01 Data analyst workflow | **Adopt as vocabulary and north star. Do not build the analytics half now.** | Its second half is a product over Silver ODS, which does not exist yet (wave W3). |

Together they make the platform more powerful in exactly one way that matters for the MVP: they
turn "here is everything the profiler and the model produced" into "here is what *you*, in *your*
role, need to decide" — on top of a workflow substrate that can finally be observed and re-run
generically instead of screen by screen.

---

## 1. Validation method

1. Read the three documents in full.
2. Traced the current architecture end to end: `queue/queue.py`, `queue/worker.py`, every
   `workers/*.py`, `workflow/states.py`, `workflow/models.py`, `engine/profiler.py`,
   `intelligence/context.py`, both LangGraph graphs, every router, `frontend/lib/runStep.ts`,
   the run-flow pages, and where `CurrentUser.roles` is consumed.
3. Measured the de-identified corpus (`docs/04_source_data_samples_and_profiles/`) with
   `cinqflow.engine.parsers.parse` to test doc 02's "30,000 × 100" premise.
4. Checked each proposal against the repo's own guardrails: `docs/blueprints/checklist.md §0`
   (no new queue, no invented APIs beyond the blueprint, tests for every behavioural change),
   `features.md` (deterministic before AI; AI is never the record), and the derived wave order
   (W0 hardening → W1 auth/PHI/feed registry → W2 landing zone → W3 ODS model → …).

### 1.1 The corpus, measured

| Feed | Columns | Rows |
|---|---:|---:|
| Fidelis NY upstate roster (CSV) | 45 | 28,333 |
| Fidelis NY downstate roster (XLSX) | 45 | 38,489 |
| Molina NY MEDICAID up (TXT) | 60 | 174,363 |
| Molina NY MEDICAID down (TXT) | 60 | 142,184 |
| Molina NY HARP up / down (TXT) | 60 | 12,387 / 3,745 |
| Centene GA Medicaid / Medicare (TXT) | 34 | 7,817 / 7,731 |
| Centene IL roster (CSV) | 45 | 26,489 |
| Optum NY elig (CSV) | 25 | 37,104 |
| Optum GA HouseCalls (XLSX) | 30 | 3,814 |
| CMP 1598 (CSV) | 29 | 10,800 |
| **ADT data for payors (CSV)** | **142** | 437 |
| ACO REACH alignment roster (XLSX) | — | **parse error: "File is not a zip file"** |

Doc 02's premise holds: Molina alone is ~10.5M cells, and ADT is 142 columns wide. Column
compression is not a nice-to-have for this corpus. (Aside, unrelated to these docs: the ACO REACH
`.xlsx` is not a real xlsx — likely a mislabelled `.xls` or HTML export. Belongs in W2's format
widening.)

---

## 2. Document 03 — DAG + background workers

### 2.1 What is already built (and where)

| Doc 03 principle | Repo reality | Where |
|---|---|---|
| Workers execute deterministic/bounded tasks | Seven topics: `upload.profile`, `upload.interpret`, `batch.land_bronze`, `upload.reject`, `bronze.analyze`, `mapping.preview`, `mapping.promote` | `queue/worker.py` `handlers()`; `workers/*.py` |
| Durable, retrying, crash-safe execution | Postgres queue: `SELECT … FOR UPDATE SKIP LOCKED`, `dedupe_key UNIQUE`, claim committed *before* the handler runs, `reclaim_stale()`, `MAX_ATTEMPTS = 3`, `dead` state with `last_error` | `queue/queue.py` |
| AI is a reasoning step inside a node, not the orchestrator | `interpret_file`: ground (no model) → infer → assemble (no model). `recommend_mapping`: ground → recommend → validate (no model). Docstrings say it verbatim: "Only the middle node calls a model" | `intelligence/graphs/*.py` |
| "Avoid agent-thinks → tool-call loops" | No agent loop anywhere; structured outputs only (`schemas.py`, OpenAI strict mode) | `intelligence/schemas.py`, `llm.py` |
| Human decisions are first-class states | `UploadStatus` state machine with `LEGAL_TRANSITIONS`; `Approval` rows append-only; `MappingStatus` draft → previewed → approved → superseded | `workflow/states.py`, `workflow/models.py` |
| Multiple workflows connected by durable state, not one giant DAG | Each topic is a workflow boundary; `profile_upload` enqueues `interpret`, `land_bronze` enqueues `analyze`; the connecting state is the upload/batch row | `workers/profile_upload.py:59`, `workers/land_bronze.py:38` |
| State-first; evidence persisted | Every noun is a row with id/version/state; claims carry `evidence[]`, signals carry basis/check/consequence, provenance records prompt@version + model + knowledge citations | `workflow/models.py` |
| Resumable / selective re-run | Retry for `profile_failed`, `interpret_failed`, `land_failed`; `LANDED → LANDING` replay; `promote_silver` is a replay | `states.py:38-53`, `routers/uploads.py:187` (`/retry`) |

Verdict: doc 03 is a faithful description of `features.md`'s "Global features" table. Adopt it as
the canonical architecture statement (an ADR), because it is already true.

### 2.2 What is missing — **GAP**

The DAG is **implicit**. Nothing declares "these are the steps, in this order, with these
dependencies." Consequences, all observed this session:

1. **No generic progress.** `build_upload_progress` hand-builds four `Stage`s; `RunRail` hardcodes
   `RUN_STEPS` in `frontend/lib/runStep.ts` as a second, hand-copied list. S5 (mapping) cannot be
   derived from status at all (`runStep.ts` comment: "the control plane has no status field for 'a
   mapping version exists'").
2. **Screen-by-screen polling.** Three bespoke pollers were written *today* to close "run `make
   worker` and reload" dead-ends: `LandingWait`, `BronzeAnalysisWait`, and `PreviewPanel`'s
   preview poll — each re-deriving "is step X done yet" from a different endpoint.
3. **Invisible failures.** A `bronze.analyze` or `mapping.preview` failure exists only as
   `queue.message.last_error`/`state = dead`. No artifact carries it; `BronzeAnalysisWait` would
   poll forever.
4. **Re-run is partial.** `/retry` covers three upload states. There is no re-run for
   `bronze.analyze`, `mapping.preview`, or `mapping.promote` — the last is the documented §6.5 gap
   in `forward-flow-adoption.md` ("the dedupe key even includes the `approval_id`, so a manual
   re-enqueue would be swallowed"). Confirmed: the route inventory has no `/promote`.

### 2.3 What NOT to do

Do not introduce Airflow, Prefect, Temporal, Celery, or any orchestrator. `checklist.md §0`:
*"No Celery/Procrastinate/new queue — the Postgres queue carries all async work."* Railway's
topology (api + worker fused in one service because of the volume) makes a second control plane
expensive for no gain, and doc 03 itself says the goal is small workflows connected by durable
state — which is what the topic boundaries already are.

---

## 3. Document 02 — persona-based data view

### 3.1 What is already built

| Doc 02 element | Repo reality | Where |
|---|---|---|
| Progressive disclosure L1 Summary → L2 Insight → L3 Evidence → L4 Records | `ReadingMode` Verdict / Evidence / Forensic; `VerdictCard` (one glance, composed from facts); `SignalCard`/`ClaimCard` (evidence); Forensic table + `/batches/{id}` (records). "Summary first, evidence always available, raw data remains accessible" is `analyst-forward-flow.md §1` almost verbatim | `components/run/*`, `app/runs/[uploadId]/*` |
| "Insight before records" | S4 defaults the proposal to the fields that need a decision and hides the rest behind "show all"; Bronze rows are a Forensic link | `app/runs/[uploadId]/bronze/page.tsx` |
| AI as a compression layer: Profile → Classify → Summarize → Prioritize → Detect anomalies → Explain | Profile ✓ (deterministic). Summarize ✓ (`headline`, composed). Detect anomalies ✓ *structural only* (null rate, duplicates, absent columns → `Signal(kind="risk")`). **Classify ✗. Prioritize ✗. Explain — partial** (claims carry evidence; no narrative) | `engine/profiler.py`, `intelligence/llm.py` stub, `graphs/interpret_file.py` |
| Identity foundation for personas | Seven roles seeded: `business_analyst`, `data_steward`, `data_engineer`, `operations`, `approver`, `administrator`, `read_only`. `CurrentUser.roles` is on every page | `auth/ddl.py`, `frontend/lib/auth.ts:23` |

### 3.2 What is missing — **GAP**

1. **Column compression / semantic classification.** The model sees, per column: `name`,
   `inferred_type`, `null_count`, `distinct_count`, bounded `sample_values`,
   `patterns{numeric_ratio, date_ratio, code_like}`, `phi_candidate` (`context.py:39-42`). Nothing
   anywhere says *identifier / measure / dimension / date / business attribute / technical /
   derived*, and nothing ranks columns. `FieldCandidate.concept` ("what the column means") is the
   nearest thing and exists only at mapping time. On a 142-column ADT file the Forensic table is a
   142-row table.
2. **Row compression / statistical profile.** `profiler.py` (PROFILER_VERSION "1") is structural:
   types, nulls, distincts, sample values, candidate keys, duplicate rows, PHI-by-name. No min/max,
   no value frequencies, no cardinality buckets, no time coverage, no sentinel detection
   (`RR-23`: sentinel dates are nulls in disguise). Doc 02's "distributions, outliers, representative
   samples, segments, trends" is almost entirely new work — and half of it (segments, trends,
   correlations) belongs to analysis over Silver, not onboarding.
3. **Persona-based prioritisation.** Roles change exactly one thing on screen: whether the Admin nav
   section renders (`AppShell.tsx:68`, `requireRole("administrator")` on admin routes). Six of the
   seven roles are inert. `forward-flow-adoption.md §7.2` already specifies the mechanism
   (`lib/persona.ts` capability predicates, `evidenceDensity` seeding `ReadingMode`) — it was
   scheduled as Phase 7 and never started.
4. **Question-aware view.** Absent, and out of place at onboarding: a business question drives
   analysis over Silver, not landing a roster. The nearest existing hook is the domain's
   `what_it_answers` (`knowledge/domains/*.yaml`, already loaded by `context.py:132-141` for
   mapping) — a *purpose*, not a question.

### 3.3 Where doc 02 would mislead if applied literally

- "Question → view" at onboarding time would ask an analyst landing a roster to state a business
  question she does not have yet. Use feed purpose instead, and defer real question-driven views to
  the Consumer/Explore surface (`forward-flow-adoption.md §7.1`), after Silver ODS (W3).
- "Segments, trends, correlations" over Bronze would violate the layer model: Bronze is verbatim,
  untyped, unmapped. Analysis belongs to Silver. Doing it earlier produces insights about the wrong
  thing.
- Anything that hides a fact by persona breaks adoption §7.2's first property: *"A persona never
  hides a fact. It changes what is actionable, never what is true."* Defaults and ordering only.

---

## 4. Document 01 — the analyst workflow

The six questions — Question → Understand → Trust → Analyze → Explain → Recommend → Decide — map
onto the platform like this:

| Question | Platform surface | Status |
|---|---|---|
| Understand ("what data exists, what does it represent") | S1 profile facts, S2 claims (`likely_domain`, `likely_dataset`, `likely_grain`), S4 reconciliation | Built |
| Trust ("complete, consistent, accurate, fit") | S2 signals + G1; S5 preview + G2; balance equation; quarantine | Built |
| Analyze / Explain / Recommend / Decide (over a business question) | Explore / Consumer surface over Silver ODS | **Not on this build** — requires W3 (ODS model) first |

Adopt doc 01 as the vocabulary for copy and documentation (the S2 headline already speaks in
"recommend/decide" terms). Do **not** start building analytics on Bronze or Silver Raw: the wave
order puts the ODS model (E10) before anything that consumes it, and `escalation.yaml`'s
`never_autonomous` line is exactly about writing to published tables.

---

## 5. End-to-end approach

Three tracks, ordered by dependency and by value per unit of change. Each is independently
shippable; none requires a new external system.

```
P0  Migrations mechanism (W0 prerequisite for anything that ALTERs)
P1  Track A — explicit step ledger + generic progress + selective re-run   (doc 03)
P2  Track B1 — column roles (semantic classification)                       (doc 02)
    Track B3 — persona defaults from roles                                  (doc 02)
P3  Track B2 — statistical profile v2 + deterministic anomaly signals       (doc 02)
P4  Track C  — question-aware / Explore                                     (doc 01/02) — after W3
```

Why this order: P1 is pure plumbing that every later screen sits on and that removes three
hand-rolled pollers already in the tree. P2 is the largest UX gain for the least risk (it reuses the
`Signal`/`Claim` assembly, prompt versioning and reading-mode machinery built this week). P3 changes
`profile_id` for every future upload and touches PHI masking, so it goes after the persona surface
exists to display it. P4 waits for the layer it needs.

---

## 6. Track A — explicit step ledger (doc 03)

### 6.1 Backend

**Declare the workflow once.** `backend/src/cinqflow/workflow/dag.py`:

```python
@dataclass(frozen=True)
class StepDef:
    key: str            # profile | interpret | gate_g1 | land | analyze | preview | gate_g2 | promote
    label: str
    scope: Literal["upload", "batch", "feed_version"]
    topic: str | None   # None for gates (human steps)
    depends_on: tuple[str, ...]
    gate: bool = False

WORKFLOW: tuple[StepDef, ...] = (...)
```

This replaces two hand-maintained lists: the `Stage` construction in `build_upload_progress`
(`workflow/models.py:673`) and `RUN_STEPS` in `frontend/lib/runStep.ts`. One source, exported.

**Persist step runs.** New table, installed by `cinqflow install` (a `CREATE TABLE IF NOT EXISTS`
— it needs no migration framework; see §9):

```sql
CREATE TABLE IF NOT EXISTS workflow.step_run (
  step_run_id   UUID PRIMARY KEY,
  scope_kind    TEXT NOT NULL,          -- upload | batch | feed_version
  scope_id      TEXT NOT NULL,
  step_key      TEXT NOT NULL,
  generation    INT  NOT NULL DEFAULT 1, -- increments on re-run
  state         TEXT NOT NULL,          -- pending | running | done | failed | skipped
  attempts      INT  NOT NULL DEFAULT 0,
  message_id    UUID,                   -- queue.message link
  artifact_type TEXT, artifact_id TEXT, -- profile_id / interpretation_id / batch_id / …
  error         TEXT,
  started_ts    TIMESTAMPTZ, finished_ts TIMESTAMPTZ,
  UNIQUE (scope_kind, scope_id, step_key, generation)
);
```

`StepLedger` in `workflow/store.py` (`start(scope, step)`, `finish(step_run_id, artifact)`,
`fail(step_run_id, error)`). Workers stay thin (`structure.md` boundary 5): each handler already
opens/commits its own transaction; the ledger write joins it. This generalises what already exists
piecemeal — `InterpretationRun.completed_steps`, `Run` for land/promote, `Stage` for progress — into
one artifact, which is `features.md`'s own "first-class workflow artifacts" rule, not a new idea.

**Progress from the ledger.** `GET /api/uploads/{id}/progress` and `GET /api/batches/{id}/progress`
keep their current shape and add `steps[]` from `step_run` — so the frontend can migrate without a
flag day. Gate steps read their state from `approvals`.

**Selective re-run.** `POST /api/uploads/{id}/steps/{step_key}/rerun` (and the batch/feed-version
analogues), generalising `/retry` (`routers/uploads.py:187`). Guarded by a per-step legality table in
`states.py`'s style (a failed or done step may re-run; a pending/running one may not; a gate never
may). The enqueue uses `dedupe_key = f"{scope}:{step}:{generation}"` so the queue's `UNIQUE` no longer
swallows a legitimate re-run — this is precisely what closes `forward-flow-adoption.md §6.5`
(re-queue promotion) and adds re-run for `analyze` and `preview`, which have none today.

**Failure visibility.** A handler exception now marks the step `failed` with the error *before*
`queue.claim` re-raises, so `bronze.analyze`/`mapping.preview` failures become `Needs Attention` on
screen instead of a silent `dead` message.

**Tests (required by `checklist.md §0`).** Unit: `WORKFLOW` is a DAG (no cycles, deps resolve), each
worker writes start/finish/fail. Integration: `/progress` returns ledger steps; rerun increments
generation and enqueues; rerun of a running step is 409. E2E: the existing stage tests assert the
ledger reaches `done` for every step they already exercise.

### 6.2 Frontend

- `lib/runStep.ts`: `RUN_STEPS` becomes derived from the `steps[]` payload (or a shared JSON export
  of `WORKFLOW`), `canonicalStep(detail)` becomes "the furthest `done` step, else the first
  `running`/`failed`" — which finally makes S5 derivable (`mapping` is `done` when a mapping version
  for the feed is approved, `running` when a draft exists).
- One `WorkflowProgress` client component (`usePoll` over `/progress`) replaces `LandingWait`,
  `BronzeAnalysisWait` and `PreviewPanel`'s bespoke poll, and renders `RunRail`'s dots from the same
  data. A `failed` step renders its `error` with a **Re-run** button that calls the new endpoint —
  the `RetryButton` pattern, generalised.
- Engineer persona (Track B3) gets a dead-letter/failed-steps list on `/data/intake` from the same
  endpoint — adoption §6.4's worklist, for free.

### 6.3 Infra

- No new services. The ledger makes the existing single worker observable; splitting api/worker
  into separate Railway services waits on the W0 item that dissolves their fusion (landing zone off
  local disk onto object storage).
- Queue table growth: `step_run` is one row per step per generation — bounded by uploads × 8.

---

## 7. Track B — persona-based data view (doc 02)

### 7.1 B1 — column roles (semantic classification)

**Deterministic hints first** (`RR-01`: observations are true). `engine/profiler.py` adds
`ColumnFacts.hint: ColumnRoleHint` computed by code:

| Hint | Rule |
|---|---|
| `date` | `inferred_type in {date, timestamp}` |
| `identifier` | member of a candidate key, or `code_like` with `distinct_count == row_count` |
| `measure` | numeric type and not an identifier |
| `dimension` | string/code with `distinct_count ≤ 50` or `distinct_count / row_count < 0.05` |
| `technical` | name matches audit/system tokens (`created`, `updated`, `batch`, `record_hash`, `row_number`, `source_system`, `lsdeleted`, the canonical model's `system_populated` list) |
| `phi` | `phi_candidate` (kept as a flag, not a role) |
| `unclassified` | otherwise |

This is JSONB inside `facts`; **no migration**. It bumps `PROFILER_VERSION` (profile identity is the
hash of facts — that is the contract, and it is fine: re-profiling is idempotent).

**Then the model classifies against the hints.** `intelligence/schemas.py`:

```python
ColumnRole = Literal["identifier", "measure", "dimension", "date",
                     "business_attribute", "technical", "derived", "unclassified"]

class LlmColumnRole(BaseModel):
    name: str
    role: ColumnRole
    importance: Literal["high", "medium", "low"]
    reason: str            # the basis, in the analyst's vocabulary

class InterpretationResponse(BaseModel):
    claims: list[LlmClaim]
    signals: list[LlmSignal]
    column_roles: list[LlmColumnRole] = Field(default_factory=list)
```

`prompts/interpret_file_v3.md` + `REGISTRY["interpret_file"] = 3` (the versioning convention:
bumping a prompt means adding a file). `_assemble` validates: only observed columns survive,
unknown roles → `unclassified`, a column the model skipped falls back to its deterministic hint,
malformed entries become `info` signals — the same discipline as claims and signals today.
`StubClient` emits roles straight from hints. Persisted as
`InterpretationContent.column_roles: list[ColumnRoleOut]` (JSONB; no migration).

**Importance is bounded by knowledge, not enthusiasm.** The prompt is told: a column the glossary
maps toward a canonical field, or that the domain's `what_it_answers` names, is `high`; a
`technical` column is never above `low`. That is doc 02's "prioritize by entities, relevant metrics,
relationships, quality, analytical relevance" made checkable.

**UI.**
- S2 Evidence mode: a **Recommended fields** panel (`importance = high`, grouped by role) above the
  claims — the L1/L2 answer to "what is this file made of" without reading 60 rows.
- S2 Forensic table: grouped by role, `technical` collapsed by default (`CollapsibleSection`), the
  role as a `.claim-kind`-style pill. A 142-column ADT file becomes seven groups.
- S4 `ProposalTable`: role column and default ordering identifiers → measures → dimensions → dates →
  business → technical; the status filter stays.

**Tests.** Unit: hint rules on synthetic columns; `_assemble` fallback/validation; determinism of
the stub. Golden: the Fidelis and Molina profiles classify to an expected set (this is the
`features.md` E16 "golden sets" obligation, applied).

### 7.2 B3 — persona defaults from roles

`frontend/lib/persona.ts`, as `forward-flow-adoption.md §7.2` specified:

```ts
export type Persona = "analyst" | "steward" | "engineer" | "consumer";
export function personaFor(roles: string[]): Persona   // business_analyst|approver → analyst,
                                                       // data_steward → steward,
                                                       // data_engineer|operations → engineer,
                                                       // read_only → consumer; administrator → analyst
export interface PersonaDefaults {
  readingMode: ReadingModeKey;        // analyst: evidence · steward: evidence · engineer: forensic · consumer: verdict
  proposalFilter: "decisions" | "all"; // analyst: decisions · engineer: all
  collapseTechnical: boolean;          // everyone but engineer
  canDecide: boolean;                  // analyst only (approver role) — UI affordance; the API is the boundary
}
```

Wired server-side from `getCurrentUser().roles` (already on every page) into `ReviewEvidence`'s
initial mode, S4's default filter, and the Forensic grouping. The analyst's own `localStorage`
override (spec §S2) still wins. Three properties are enforced in review: never hide a fact; anything
a persona cannot do renders inert *with a stated reason* (the console's existing pattern); persona
is not authorisation — until W1's permission model lands, `canDecide` only hides a button the API
would still accept, and the screen says so.

### 7.3 B2 — statistical profile v2 + deterministic anomalies

`PROFILER_VERSION = "2"`, per column: `null_ratio`, `min`/`max` (parsed for numeric/date),
`top_values` (value → count, cap 10, **omitted for `phi_candidate` columns**), `constant` flag,
`sentinel_count` (RR-23: `1900-01-01`, `9999-12-31`, `0000-00-00`, all-zeros); at facts level:
`time_coverage` (min/max across date columns). `mask_facts` (`workflow/models.py:479`) must strip
`top_values` for PHI columns — PHI never leaves the API unmasked, and value frequencies are values.

Anomalies become deterministic `Signal(kind="risk")`s in `_assemble`/the stub: 100%-null column,
constant column, sentinel-heavy date, cardinality collapse against a prior profile of the same feed
(needs the feed registry, W1 — mark **depends on E3**). The real model *explains*; it does not detect.

Not here: distributions as charts, segment comparisons, time trends, correlations. Those need Silver
and a question. They are P4.

### 7.4 B4 — question-aware view (deferred)

Interim proxy: weight `importance` by the domain's `what_it_answers` (already in context for
mapping; extend `for_interpretation` to load it). Full question → view lives on the Consumer/Explore
surface over Silver ODS: after W3, not before.

---

## 8. Track C — doc 01 as vocabulary

- Use Understand / Trust in S1/S2 section labels and the headline copy; use Recommend / Decide at
  the gates. No new screens.
- Record the six questions in `analyst-forward-flow.md §0` as the persona's mental model.
- Explicitly *not* now: hypothesis testing, metric calculation, segmentation, insight narratives
  over Bronze or Silver Raw. Trigger to revisit: Silver ODS exists (W3) and a Consumer surface is
  scheduled (W6).

---

## 9. Infra plan

| Item | Why | When |
|---|---|---|
| **Versioned migrations** (`workflow.schema_version` + numbered SQL under `backend/migrations/`, applied by `cinqflow install`; no Alembic — consistent with "no ORM, visible SQL") | W0's top item. Nothing in P1–P3 *needs* it (P1 is a new table; P2/P3 are JSONB) — but every later epic ALTERs, and it should exist before the first one does | P0 |
| No orchestrator, no broker | `checklist.md §0`; Railway topology; doc 03's own guidance | — |
| Split api / worker into two Railway services | Only after W0 moves landing to object storage (the volume is why they are fused). The ledger makes the split observable | W0 |
| Object storage decision: Azure Blob vs S3 | Coupled to the host decision (client docs target Azure + Entra ID); must precede real PHI. Persona work does not change it | before W2 |
| LLM cost | B1 adds ~one short object per column (≤ 142 in corpus) to one call per profile; interpretation is versioned against `profile_id`, so re-profiling identical bytes costs nothing new | P2 |
| PHI | B2's `top_values` is the one new place values could leak; `mask_facts` covers it; `RR-08` (no PHI in reasoning output) already governs the prompt | P3 |
| Observability | `step_run` + `/progress` give a dead-letter view and a worklist without a metrics stack | P1 |

---

## 10. Sizing (estimates, not commitments)

| Phase | Backend | Frontend | Notes |
|---|---|---|---|
| P0 migrations | S (1–2 d) | — | Mechanism + one no-op migration |
| P1 ledger + progress + re-run | M (3–5 d) | S–M (2–3 d) | Deletes three pollers |
| P2 column roles + persona defaults | M (3–4 d) | M (3 d) | Prompt v3 + golden set is the long pole |
| P3 profile v2 + anomaly signals | M (3–4 d) | S (2 d) | `mask_facts` change is the review-critical line |
| P4 Explore / question-aware | — | — | Not scheduled; depends on W3 |

---

## 11. Risks

- **Prompt regression when adding `column_roles`.** Mitigation: golden set on the corpus (E16),
  deterministic fallback to hints, stub parity test.
- **`profile_id` churn.** Every future upload gets a v2 profile; existing interpretations are
  untouched (they are versioned against their own profile). No forced re-profile.
- **Persona hides a gate.** Review rule: a persona changes defaults and affordance copy only. Tests
  assert the approve/reject form still renders for `approver`/`business_analyst`.
- **Ledger/status divergence.** Write the step row in the same transaction as the status
  transition; a test asserts they agree after every worker.
- **Dedupe generation misuse.** Re-run must be explicit (endpoint), never automatic; the generation
  suffix is only ever minted there.

---

## 12. Decisions needed

1. Ship P1 before or after P0? (P1 does not require it; W0 says do P0 first regardless.)
2. Role → persona mapping — in particular where `approver` and `operations` land.
3. Is `importance` model-assigned (proposed) or rule-only in the first cut?
4. The 142-column ADT file is a flattened HL7 export. Does column compression need segment
   awareness (`PID`, `PV1`, …) — a W2 question — or is role classification enough for now?
5. Confirm P4 (analytics over a business question) is out of scope until Silver ODS exists.

---

*Cites `backend/src/cinqflow/{queue,workers,workflow,engine,intelligence}`, `frontend/lib`,
`frontend/components/run`, `docs/blueprints/{features,structure,checklist,forward-flow-adoption,
knowledge-base-screen}.md` and the measured corpus as of 2026-09-05.*
