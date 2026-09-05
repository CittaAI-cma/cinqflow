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
| **Versioned migrations** (`workflow.schema_version` + numbered SQL in `src/cinqflow/migrations/`, applied by `cinqflow install`; no Alembic — consistent with "no ORM, visible SQL") | W0's top item. Nothing in P1–P3 *needs* it (P1 is a new table; P2/P3 are JSONB) — but every later epic ALTERs, and it should exist before the first one does. **Built in PR-0.** | P0 |
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

*Part I cites `backend/src/cinqflow/{queue,workers,workflow,engine,intelligence}`, `frontend/lib`,
`frontend/components/run`, `docs/blueprints/{features,structure,checklist,forward-flow-adoption,
knowledge-base-screen}.md` and the measured corpus as of 2026-09-05.*

---
---

# Part II — Persona implementation plan: Data Analyst and Data Platform

> Decided 2026-09-05 with the product owner. Part I is the validation; this is the build. Every
> package below names the files it touches, the tests it needs, and what "done" means, so each
> can be handed to an implementation session as one PR. Nothing here is started yet.

## 13. Decisions recorded

| # | Decision | Chosen |
|---|---|---|
| D1 | Role → persona | **Data Analyst** ← `business_analyst`, `approver`, `data_steward`, `read_only`. **Data Platform** ← `data_engineer`, `operations`, `administrator`. |
| D2 | Switcher | **None.** Persona is strictly derived from roles. Administrators land in Data Platform (they keep the Admin nav). |
| D3 | Data Platform scope | Run observability + selective re-run; schema / lineage / DQ view per feed. **Not** queue/worker health dashboards; **not** feed registry/scheduling (W2). |
| D4 | Data Analyst depth | Statistical profile (v2) + column roles + recommended fields + anomaly signals over the uploaded file. **Not** analytics over Silver. |
| D5 | Sequencing | Migrations mechanism (P0) first; the step ledger ships as migration 001. |
| D6 | Column importance | Model-assigned, bounded by deterministic rules, deterministic fallback. |
| D7 | Home | Persona-specific home pages, both keeping the greeting and `ActionLauncher`. |
| D8 | HL7 segment awareness for the 142-column ADT file | Deferred to W2. Role classification only. |
| D9 | Question-aware view / analytics | Out of scope until Silver ODS exists (W3). |

Two things the decisions settle that the plan must hold to:

- **Persona is emphasis; capability is authority.** Persona chooses defaults (reading mode,
  filters, grouping, home page). Whether a user may *decide a gate* or *re-run a step* is a role
  predicate enforced by the API, independent of persona. An administrator who also holds
  `approver` can approve; a `data_steward` sees the gate but cannot press it — and the screen says
  why. This is `forward-flow-adoption.md §7.2` properties 1–3, made concrete.
- **Multi-role precedence.** Any platform role → Data Platform. Otherwise any analyst role → Data
  Analyst. `read_only` alone → Data Analyst with no capabilities.

---

## 14. The persona model

### 14.1 Backend — the single source of truth

`backend/src/cinqflow/auth/persona.py` (new):

```python
Persona = Literal["data_analyst", "data_platform"]

PLATFORM_ROLES = frozenset({"data_engineer", "operations", "administrator"})
ANALYST_ROLES  = frozenset({"business_analyst", "approver", "data_steward", "read_only"})
GATE_ROLES     = frozenset({"business_analyst", "approver"})   # may decide G1/G2

class Capabilities(BaseModel):
    can_decide_gates: bool   # roles ∩ GATE_ROLES
    can_rerun_steps: bool    # roles ∩ PLATFORM_ROLES
    can_manage_users: bool   # "administrator" (already enforced by users.py)

def persona_for(roles: list[str]) -> Persona: ...
def capabilities_for(roles: list[str]) -> Capabilities: ...
```

`CurrentUser` (`auth/models.py`) gains `persona: Persona` and `capabilities: Capabilities`,
populated in `AuthStore.current_user` — so `/api/auth/login`, `/api/auth/refresh` and
`/api/auth/me` all carry them and the frontend never re-derives the mapping.

**Enforcement, using the `require_role` infrastructure that exists** (`api/deps.py`):

| Endpoint | Guard |
|---|---|
| `POST /api/uploads/{id}/approve` · `/reject` | `require_capability("can_decide_gates")` |
| `POST /api/feeds/{feed}/mapping-versions/{v}/approve` | same |
| `POST …/steps/{step}/rerun` (§16) · existing `POST /api/uploads/{id}/retry` | `require_capability("can_rerun_steps")` |
| Everything else | unchanged this plan (the Phase-2 "gate every router" item stays W1's) |

`require_capability` is a one-line sibling of `require_role`. This is the first time the API
refuses a decision by *who* is asking; today `submitDecision` records the approver but any signed-in
user can call it.

Tests: unit for `persona_for`/`capabilities_for` across all role combinations; integration —
`approver` approves (202), `data_steward` gets 403, `data_engineer` re-runs (202),
`business_analyst` re-run gets 403.

### 14.2 Frontend — defaults, never a second mapping

`frontend/lib/persona.ts` (new) reads `user.persona` and `user.capabilities` and exposes the
defaults table:

| Default | Data Analyst | Data Platform |
|---|---|---|
| Home | Worklist home (§17.1) | Attention home (§17.2) |
| `ReadingMode` initial | `evidence` | `forensic` |
| S4 proposal filter | needs-decision | all |
| Forensic column grouping | by role, `technical` collapsed | by role, all expanded |
| Register (`/data/intake`) columns | object, stage, status, business date, decision-needed | + fingerprint, landing key, batch id, attempts |
| Register default filter | "waiting for me" | all, adverse first |
| Group view (`/data/intake/[group]`) | Configuration · Map to domain | + Schema · Lineage · Quality (§18) |
| Gate controls (G1/G2) | rendered iff `can_decide_gates`; otherwise the decision block reads *"Your role can review this run but not decide it — an approver or business analyst signs the gate."* | same rule (an admin with `approver` can decide) |
| Re-run controls | hidden | rendered iff `can_rerun_steps` |
| `WorkflowSteps` panel (§16) | collapsed | expanded |

The analyst's own `localStorage` reading-mode override (spec §S2) still wins over the default.
`CurrentUser` (`lib/auth.ts`) gains the two fields; `AppShell` passes `persona` where it already
passes `isAdmin`.

---

## 15. Work package map

```
PR-0  Migrations mechanism ─────────────────────────────┐
PR-1  Persona + capabilities (backend + frontend) ──────┤
PR-2  Step ledger + WORKFLOW + progress steps[] ─────────┼─► PR-3 Re-run ─► PR-4 Persona homes
PR-5  Profiler v2 (hints + statistics + PHI mask) ──────┼─► PR-6 Prompt v3 column roles ─► PR-7 Analyst UI
PR-8  Platform feed view (schema · lineage · quality) ◄──┘  (needs PR-2 for step state, PR-5 for stats)
PR-9  Docs: templates.md §1.2/1.3, ADR for doc 03, analyst-forward-flow §0
```

Two independent lanes after PR-1: the **platform lane** (PR-2 → PR-3 → PR-4 → PR-8) and the
**analyst lane** (PR-5 → PR-6 → PR-7). They touch different layers and can be built in parallel;
PR-8 is the join. Recommended single-track order: 0, 1, 2, 3, 5, 6, 7, 4, 8, 9 — platform value
lands at PR-3, analyst value at PR-7, both homes at PR-4.

---

## 16. Platform lane

### PR-0 — Migrations mechanism — **built** (branch `feat/w0-versioned-migrations`)

*Backend.* `backend/src/cinqflow/migrations/` — inside the package, next to the code, the way
`intelligence/prompts/*.md` ship, so Docker, Railway and `poetry run` see the same files with
no packaging step (the plan first said `backend/migrations/`; that would have needed its own
`COPY` in two Dockerfiles). `NNN_snake_case_name.sql`, contiguous from `001`; schema names as
`{{workflow}}` / `{{queue}}` / `{{auth}}` (no data-plane token on purpose — Bronze/Silver DDL
stays contract-rendered, `structure.md` boundary 6). `workflow.schema_version (version INT PK,
name TEXT, applied_ts TIMESTAMPTZ)`. Each file runs in its own transaction under a
`pg_advisory_xact_lock`, re-checks the version row after acquiring it (two concurrent
`install`s apply each file once), and a failure rolls that file back and stops. The runner
refuses a gap, a duplicate, a misnamed `.sql`, an unknown token, and any applied version
whose file was renamed or deleted — all before the first statement runs.

`cinqflow install` applies pending migrations after the frozen baseline DDL and before the
bootstrap admin, and prints `migrations: applied …` / `migrations: none pending (N applied)`;
`cinqflow reset --yes` re-applies them so a reset lands on the same shape. New
`cinqflow migrate [--status]` for an operator looking at one database. **From this PR on,
`workflow/ddl.py` and `auth/ddl.py` are frozen; every schema change is a migration.** No
migration ships in PR-0 (no no-op baseline file — an empty, validated set is the honest state);
`001_step_run.sql` is PR-2's.

*Tests.* Unit (`tests/unit/test_migrations.py`): discovery, ordering, gap/duplicate/misnamed
refusal, token rendering. Integration (`tests/integration/test_migrations.py`): applies in
order and records `applied_ts`; idempotent re-run; a later file applies only itself; a gap
refuses everything; a failing file rolls back completely and leaves the connection usable;
renamed/deleted applied files refused; `$$` blocks and multi-statement files apply as one;
`{{queue}}`/`{{auth}}` resolve; the shipped directory applies cleanly on a fresh install. CLI
(`tests/unit/test_cli_migrate.py`): `install` applies and reports, `migrate --status` never
applies, `migrate` applies, `reset --yes` re-applies, a bad set fails install loudly.

*Frontend.* None — PR-0 has no user-visible surface. *Infra.* No change to compose or
Railway: both already run `cinqflow install`. *Docs:* `structure.md` layout tree + "Data
stores" paragraph.

### PR-1 — Persona + capabilities — **built** (branch `feat/w0-versioned-migrations`)

*Backend.* `auth/persona.py` exactly as §14.1: `PLATFORM_ROLES` / `ANALYST_ROLES` /
`GATE_ROLES`, `persona_for` (any platform role wins), `capabilities_for` (three predicates, each
a role intersection). `CurrentUser` carries `persona` and `capabilities`, computed once in
`AuthStore.current_user` — so login, refresh and `/me` all return them and nothing downstream
re-derives the mapping. `api/deps.py` gains `require_capability(name, get_current_user)`, the
one-line sibling of `require_role` (403 `missing_capability:<name>`). Guards land where the
table said: G1 approve/reject and G2 approve take `Depends(require_decide)`, `POST /retry`
takes `Depends(require_rerun)`. **The approver is now the session's user** — the `approver`
body field is gone from both gates (it was `Body("analyst@cinqcare.com")` on G2), so an
approval row can no longer name someone who didn't press the button. One addition the plan
missed: `PATCH /api/users/{id}/roles` (`AuthStore.set_roles`, validates every name before the
first write). Without it every bootstrap account stays `administrator`-only — Data Platform
persona, `can_decide_gates = false` — and *nobody* can sign a gate; the four production
accounts are exactly that shape today. `POST /api/uploads`' `uploader` form field is
unchanged (W1's "gate every router").

*Tests.* Unit (`tests/unit/test_persona.py`): every role alone, platform-wins precedence,
`read_only` → analyst with nothing, empty roles, unknown role names ignored. Integration
(`tests/integration/test_capabilities.py`): no token → 401; `data_steward` → 403 on both gates
and on retry; `approver` passes the gate check (reaches the 404 for an unknown upload) but 403
on retry; `data_engineer` the inverse; `administrator` alone cannot decide until given
`approver` via the new endpoint, after which the next request succeeds; non-admin → 403 on
the endpoint; unknown role → 400 with no partial write. `tests/conftest.py` gains
`authed_client(...)` (`approver` + `data_engineer`, so it can do everything the e2e flows
need); every e2e module uses it, and `test_stage2_flow` now asserts the recorded approver is
the session's email, not a body value.

*Frontend.* `lib/auth.ts`: `Persona`, `Capabilities`, the two `CurrentUser` fields; the API's
object-shaped 409/404 (`message — hint`) and the two `missing_capability` codes are humanised
in `authMutate`. `lib/persona.ts`: the §14.2 defaults table as data (`personaDefaults`), plus
the two locked-reason sentences. Applied this PR: reading mode (`ReviewEvidence` starts in the
persona's mode; the analyst's saved choice still wins), S4 proposal filter (`/bronze` shows
"needs a decision" for Data Analyst, everything for Data Platform, either overridable from
the URL), gate controls (`GateActions` and `ApproveMapping` render iff `can_decide_gates`,
otherwise `components/run/GateLocked.tsx` states why, verbatim from the table), re-run
controls (`RetryButton` on review and the failure surface in `RunProcessing` render iff
`can_rerun_steps`, otherwise the reason). Home, register columns, group-view panels and the
`WorkflowSteps` default wait for PR-4 / PR-8 / PR-2 as planned. `TopBar` shows the persona
beside the name. `/admin/users` gets an inline `RolesEditor` per row (`updateRoles` action).
Every capability-gated call now goes through a Server Action with `authMutate`
(`submitDecision`, new `submitRetry`, `approveVersion`) — `decideUpload`, `retryUpload` and
`approveMappingVersion` are removed from `lib/api.ts`, because only the Next.js server holds
the bearer token and those three were the unauthenticated fetches the guards would now refuse.

*Infra.* None: no schema change (roles and memberships already exist), no env. *Docs:*
`structure.md` tree (`auth/`) and boundary 8 (authority is a capability, never a persona).

### PR-2 — Step ledger — **built** (branch `feat/w0-versioned-migrations`)

*Backend.*
- `workflow/dag.py`: `StepDef` + `WORKFLOW` (§6.1). Add `scope`: `upload` for
  profile/interpret/gate_g1/land, `batch` for analyze, `feed_version` for preview/gate_g2/promote.
- `migrations/001_step_run.sql` (§6.1 DDL).
- `workflow/store.py`: `StepLedger.start/finish/fail/list(scope)`; `StepRun` model in
  `workflow/models.py` (templates.md §1 gets a §1.10).
- Each worker: `start` on entry, `finish(artifact)` on success, `fail(error)` in the exception
  path *before* re-raising (so `queue.claim`'s rollback cannot lose it — write it on a fresh
  cursor after `conn.rollback()`, the same way `interpret_upload.py` records `INTERPRET_FAILED`).
  `analyze_bronze.py` already swallows the proposal failure into its return dict; it now also
  writes `fail`.
- Gates: `gate_g1`/`gate_g2` step rows are written by the approve/reject handlers (`done`, with
  `artifact = approval_id`), so a gate is a step like any other.
- `build_upload_progress` and `GET /api/batches/{id}/progress` add `steps: list[StepRun]`; the
  existing `stages[]` shape stays until the frontend has migrated (PR-4), then goes.
- `GET /api/steps?state=failed&limit=` and `GET /api/steps?scope_kind=&scope_id=` (new
  `routers/steps.py`, read-only, mounted in `app.py`) for the platform home and the feed view.

*Frontend.*
- `lib/runStep.ts`: `RUN_STEPS` derived from `WORKFLOW` (exported once as JSON via
  `GET /api/workflow` or embedded in `/progress`); `canonicalStep(steps)` = furthest `done`, else
  first `running`/`failed`. This makes S5 derivable and retires the hand-copied list.
- `components/run/WorkflowSteps.tsx`: one `usePoll` over `/progress`; renders every step's state,
  attempts, duration, error. Replaces `LandingWait`, `BronzeAnalysisWait` and `PreviewPanel`'s
  poll (all three deleted). `RunRail` reads the same payload.
- Persona: expanded for Data Platform, collapsed for Data Analyst (§14.2).

*Tests.* Unit: `WORKFLOW` is acyclic, every `topic` has a handler in `worker.handlers()`.
Integration: after each existing stage e2e, `step_run` shows the expected `done` rows; a forced
handler exception leaves a `failed` row with the error. *Acceptance:* no screen in
`app/runs/**` polls anything but `/progress`.

**As built** (what differs from the package above, and why).

- *Promotion is `batch`-scoped, not `feed_version`.* A promotion writes one batch's Silver
  rows and PR-3's re-run of it "rebuilds this batch only"; a second batch promoted under the
  same approved version is a different step run, not generation 2 of the first. `preview` and
  `gate_g2` stay `feed_version` (`scope_id = "<feed>:v<n>"`). PR-3's promote re-run route
  therefore lives under `/api/batches/{id}/steps/promote/rerun`.
- *No handler carries ledger code.* `queue/worker.py: run_once` opens the step (`running`,
  committed before the handler runs) and closes it from the handler's return dict: `error` /
  `*_failed` → `failed`; `<verb>: False` + `reason` → `skipped` (a refusal - the scope was not
  runnable; new state used for that and for landing after a G1 rejection); otherwise `done`
  with the artifact id the handler returned. A handler that raises leaves `failed` with the
  exception text before `Queue.claim` records the message failure. Workers stay thin
  (`structure.md` boundary 5, now stated there).
- *`pending` rows are real.* Every enqueue site calls `StepLedger.queued` (upload → profile,
  profile worker → interpret, G1 approve → land, land worker → analyze, preview request,
  G2 approve → promote, `/retry`), so "queued, no worker has taken it" is a ledger fact, not a
  UI inference. `queued_ts` was added to the table for it.
- *Gates.* `interpret`/`preview` finishing opens `gate_g1`/`gate_g2` (`running`, awaiting a
  person; opened once). A decision closes it: approval → `done`; rejection → `failed` with
  `error = "rejected by <who>: <note>"`, and `land` is written `skipped`. `StepDef.gate` is
  how PR-4 keeps rejections out of "needs attention".
- *Generations vs attempts.* `start` on a `pending`/`running`/`failed` row re-enters it
  (`attempts + 1`, the queue's own retry); on a `done`/`skipped` row it mints `generation + 1`
  (a replay or a PR-3 re-run, which will pre-create the `pending` row).
- *Endpoints.* `steps[]` on `GET /api/uploads/{id}/progress` (upload scope + its latest
  batch's + its feed's latest version's, the last two only once landed - a fresh upload of a
  feed with history must not be shown at G2), on `GET /api/batches/{id}/progress`, and new
  `GET /api/feeds/{feed}/mapping-versions/{v}/progress` (the studio's poll). `routers/steps.py`:
  `GET /api/workflow` (the declaration) and `GET /api/steps?state=|scope_kind=&scope_id=`.
  `stages[]` is untouched; `RunProcessing` still reads it (PR-4 retires both).
- *Frontend.* `components/run/WorkflowSteps.tsx` (one `usePoll` over `/progress`; polls only
  while a worker step is queued/running; `router.refresh()` on settle; persona `expanded`)
  replaced `LandingWait`, `BronzeAnalysisWait` and `PreviewPanel`'s poll - all three deleted.
  `lib/runStep.ts`: `RUN_STEPS` keeps the seven *screens* but each names its ledger steps;
  `canonicalStepFromSteps` (furthest active upload/batch-scoped step; feed-version steps are
  not consulted, so an earlier delivery's approved mapping cannot send a new file past its
  own Bronze review) with `canonicalStep(status)` as the fallback for pre-ledger runs;
  `railStates` drives `RunRail`'s dots (adverse if any step failed, done if all are). S5 is
  now derivable: a preview or G2 for the feed's version resolves `/runs/{id}/mapping`. The
  pages read the ledger once per render (`lib/runProgress.ts`, `cache()`-wrapped, shared
  with the layout). Bronze review offers "Build the mapping without a proposal" when the
  analysis step failed, instead of a wait that never ends.
- *Deferred, as planned.* The `/data/intake` failed-steps list and `lifecycleStage.ts`'s
  `stageOf` move to the ledger with PR-4/PR-8; `BatchProcessing` (`/batches/{id}`, outside
  `app/runs/**`) keeps its polls until then.
- *Verification.* 478 backend tests (30 new: `test_dag.py`, `test_worker_ledger_outcome.py`,
  `test_step_ledger.py`; ledger assertions added to the stage 2/5/6 and gaps e2e). The test
  harness now installs baseline + shipped migrations (`conn`), with `bare_conn` for the
  migration runner's own tests. `tsc`, `next build`, compose rebuild (`migrate` applies
  `001_step_run`).

### PR-3 — Selective re-run — **built** (branch `feat/w0-versioned-migrations`)

*Backend.* `POST /api/uploads/{id}/steps/{step}/rerun`, `POST /api/batches/{id}/steps/{step}/rerun`,
`POST /api/feeds/{feed}/mapping-versions/{v}/steps/{step}/rerun`, all `require_capability
("can_rerun_steps")`. Legality table `RERUNNABLE: dict[step_key, frozenset[StepState]]` in
`workflow/dag.py` (`failed`, `done` → yes; `pending`, `running` → 409; gates → never). Re-run
mints `generation + 1` and enqueues with `dedupe_key = f"{scope_id}:{step}:{generation}"` — the
change that closes `forward-flow-adoption.md §6.5` (promote could never be re-queued because the
dedupe key swallowed it). `promote` re-run reuses `promote_silver`'s replay semantics (rebuild this
batch only); `analyze` re-run supersedes the prior proposal (new `proposal_id`, prior stays for
lineage). The existing `/retry` becomes a thin alias.

*Frontend.* **Re-run** button per `failed`/`done` step in `WorkflowSteps`, shown iff
`can_rerun_steps`, with the consequence stated before the click (`ConfirmDialog`, as G1 does):
*"Re-running promotion rebuilds this batch's Silver rows and quarantine; Bronze is untouched."*

*Tests.* Integration: rerun of `failed` → 202 + new generation + message enqueued; rerun of
`running` → 409; rerun of a gate → 409; `data_steward` → 403; promote rerun yields identical
`record_hash`es (features.md Stage 6 acceptance 3, now reachable on demand).

**As built.** The three routes as planned (`routers/steps.py`), all `require_capability
("can_rerun_steps")`, one function underneath (`workflow/rerun.py: rerun_step`). A re-run
re-queues **the last generation's own message payload** (so a preview re-run samples the same
batch with the same selector; a promotion re-run rebuilds the same batch under the same
version), with dedupe key `{topic}/rerun/{scope_id}/g{generation}` and a new ledger generation
(`StepLedger.rerun`; the worker's `start` re-enters that pending row). `RERUNNABLE` allows
`failed`, `done` **and `skipped`**. Refusals beyond the plan's table, each a 409 with `message`
(+ `status`/`hint`): a step whose last message the queue will still retry on its own (`pending`/
`claimed`, attempts below the maximum - re-queueing would run one failure twice; re-run opens once
the message is `dead`, or `done` because the handler recorded the failure and returned); an
upload step its status makes impossible (re-profiling past G1; re-landing before approval),
refused at the request instead of as a worker exception; a step under the wrong scope's route
(the 409 names the right route). `LANDED` is now in `land_bronze._RUNNABLE`, so the replay
`LEGAL_TRANSITIONS` always allowed is reachable: a second batch, the first untouched. `/retry`
is the alias (same refusals; response gains `generation`). *Frontend:* Re-run per
`failed`/`done`/`skipped` worker step in `WorkflowSteps`, shown iff `can_rerun_steps` (otherwise
the locked reason is on screen), through `ConfirmDialog` with a per-step consequence sentence and
the ledger row as the audit line; the row's own `scope_kind`/`scope_id` picks the route, so a
batch step shown under an upload re-runs against its batch. `submitRerun` Server Action. *Tests:*
`tests/e2e/test_rerun.py` (failed → 202 + generation 2 + actually runs; already queued → 409;
gate / unknown / wrong scope / unknown object; past-G1 re-profile → 409; steward → 403; analyze
re-run → new proposal, old one still served; re-landing → second batch, first intact) and the
stage-6 promote re-run from the API with identical `record_hash`es.

### PR-4 — Persona homes (`app/page.tsx`)

*Backend.* `GET /api/worklist` exists (`routers/worklist.py`). Extend its payload with counts
(`waiting_at_g1`, `approvable_at_g2`) and, for the platform home, reuse `GET /api/steps?state=failed`
plus `GET /api/queue/depth`.

*Frontend.*
- **Data Analyst home** — greeting · *"3 runs are waiting for you at a gate"* · worklist table
  (file, feed, gate, waiting since → links to `/runs/{id}/review` or `/runs/{id}/mapping`) ·
  recent runs · `ActionLauncher`. `components/home/AnalystWorklist.tsx`.
- **Data Platform home** — greeting · **Needs attention**: failed steps (feed, step, error,
  attempts, **Re-run**) and dead-letter messages · in-flight steps · feeds by health (last step
  state per feed, adverse first) · `ActionLauncher`. `components/home/PlatformAttention.tsx`.
- `app/page.tsx` branches on `user.persona`; nothing else about the page changes.

*Acceptance.* An `approver` sees exactly the runs the register would flag "Needs Review"; a
`data_engineer` sees every `failed` step in the ledger and can re-run it from home.

### PR-8 — Platform feed view (`/data/intake/[group]`)

`GroupPanels`/`GroupStageTabs` gain three persona-conditional panels (tabs, not a new route — the
group *is* the feed, and `structure.md` keeps feed-level surfaces here):

| Panel | Source | Content |
|---|---|---|
| **Schema** | latest `Profile` for the group (+ v2 statistics from PR-5) | columns · type · null ratio · distinct · candidate keys as constraints · PHI · role · drift vs previous upload of the same feed (columns added/removed/type changed — computed client-side from the two most recent profiles) |
| **Lineage** | `GET /api/lineage/{batch_id}` per batch (`LineageChain` reused) | upload → file → batch → Bronze table → mapping vN → Silver tables, with both approvals and approvers |
| **Quality** | `GET /api/batches/{id}/quarantine`, `Run.counts`, `Run.balanced` | balance equation per run · quarantine `by_outcome` / `by_rule` · rows refused, with the `dq/severity.yaml` action vocabulary (block · quarantine · warn · observe) as the legend |

No new backend endpoints: every figure is already served. *Acceptance:* a `data_engineer` can
answer "what changed in this feed's schema since last month, and what did the last promotion
refuse" without opening `/batches/{id}`.

---

## 17. Analyst lane

### PR-5 — Profiler v2 (deterministic) — **built** (branch `feat/w0-versioned-migrations`)

`engine/profiler.py`, `PROFILER_VERSION = "2"`. Per column, in addition to today's facts:

| Fact | Rule | PHI handling |
|---|---|---|
| `hint: ColumnRoleHint` | §7.1 table (`date` · `identifier` · `measure` · `dimension` · `technical` · `unclassified`; `phi` stays a flag) | n/a |
| `null_ratio` | `null_count / row_count` | n/a |
| `min`, `max` | parsed numeric / date only | omitted for PHI columns |
| `top_values` | value → count, cap 10 | **omitted for PHI columns; `mask_facts` strips it defensively** |
| `constant` | `distinct_count == 1` | n/a |
| `sentinel_count` | `1900-01-01`, `9999-12-31`, `0000-00-00`, all-zero / all-nine strings (RR-23) | n/a |

At facts level: `time_coverage: {column, min, max}` over date columns. `bronze_profiler.py`
consumes the same function, so S4 reconciliation gets the same facts for free.

`profile_id` changes for every future upload (identity is the hash of facts — by contract).
Existing rows are untouched. `templates.md §1.2` updated (PR-9 folds it in if preferred).

*Tests.* Unit on synthetic columns for every rule; golden: the Fidelis upstate CSV and the Molina
MEDICAID TXT produce an expected hint per column and expected `time_coverage`. PHI: `mask_facts`
output for a `phi_candidate` column has no `top_values`, `min`, `max`, `sample_values`.

**As built.** `engine/profiler.py`, `PROFILER_VERSION = "2"`; every new fact is defaulted on
`ColumnFacts`/`ProfileFacts` so a v1 profile row still loads (JSONB, no migration), and
`bronze_profiler.py` gets the same facts unchanged. Rule refinements from running the corpus,
each deterministic and named in the module: name tokens are matched whole (`tin` does not fire on
`destination`); a *label* column (`zip`, `phone`, `mobile`, `number`, `code`, `flag`, `status`,
`type`, `indicator`) is never an identifier by uniqueness alone nor a measure by being numeric -
the Fidelis phone column and the Molina mobile number were coming out as identifier and measure; a
period stored as a number (`Member_Month` = 202402) is a `date` hint but stays out of
`time_coverage`, which uses real date/timestamp columns only; an all-null column is `unclassified`
(no evidence for any role - PR-6 raises the anomaly); `technical` takes precedence over `date`
(`created_at`); timestamps with a one-digit hour (`2025-09-01 0:00:00`, the Fidelis
`enrollment_date`) are recognised; `death`/`deceased` join the PHI name tokens, so a date of death
is neither exposed nor part of the data's period. `min`/`max` exclude sentinels (`9999-12-31` is a
placeholder, not a maximum) and are `None` for PHI; `top_values` is empty for PHI; `mask_facts`
strips all three defensively. *Golden:* the Fidelis roster (45 columns: ids, NPI, TIN identifiers;
DOB a PHI date; age a measure 0–97; product a dimension; coverage 2021 → 2027-10-31 excluding DOB)
and the first 5,000 rows of the Molina MEDICAID TXT (60 pipe-delimited columns; identifiers by name
on a history grain with no key; coverage exactly the file's period, 2024-02-01 → 2026-01-31,
excluding birth and death dates). `templates.md §1.2` is updated in PR-9.

### PR-6 — Prompt v3: column roles and importance — **built** (branch `feat/w0-versioned-migrations`)

`intelligence/schemas.py`: `LlmColumnRole { name, role, importance, reason }`;
`InterpretationResponse.column_roles`. `prompts/interpret_file_v3.md` + `REGISTRY["interpret_file"] = 3`.
The prompt receives hints as observations and is told the bounds (D6): glossary match or domain
`what_it_answers` → `high`; `technical` never above `low`; a role that contradicts a hint must say
why in `reason`. `ContextBuilder.for_interpretation` adds the domain's `what_it_answers`
(already loaded for mapping).

`_assemble`: only observed columns survive; invalid role → `unclassified`; missing column → hint
with `importance = "medium"`, `reason = "from profile hint"`; malformed → `info` signal (the
claims/signals discipline). Persist `InterpretationContent.column_roles: list[ColumnRole]`
(JSONB, no migration). `StubClient` emits from hints. Deterministic anomaly signals from PR-5's
facts (100%-null, constant, sentinel-heavy, duplicate rate) are added to the stub and to
`_assemble` as `Signal(kind="risk")` so they exist regardless of provider.

**Token budget.** `settings.llm_max_tokens` defaults to 2048; a 142-column ADT interpretation with
roles will not fit. Raise the default to 8000 (production already runs 16000) and cap
`column_roles` to observed columns. Interpretation is versioned against `profile_id`, so
re-profiling identical bytes costs nothing new.

*Tests.* `test_intelligence*.py` extended: roles validated, fallback applied, importance bounds
enforced by `_assemble` (a `technical` column marked `high` is demoted with an `info` signal),
stub determinism, OpenAI-strict schema compatibility (`test_llm_schema_openai_compat.py` already
guards this). Golden set on the two corpus files.

**As built.** `prompts/interpret_file_v3.md`, `REGISTRY["interpret_file"] = 3`; `LlmColumnRole`
on `InterpretationResponse` (role/importance typed as `str` in the *contract* so an
out-of-vocabulary value reaches `_assemble` and is corrected there instead of failing schema
validation and taking every other role with it - the persisted `ColumnRoleOut` is strictly typed);
`InterpretationContent.column_roles` (JSONB, defaulted, no migration), each entry carrying the
profiler `hint` it was judged against and `source: model | hint`. `ContextBuilder.for_interpretation`
takes the upload's landing domain (the worker passes it) and adds the domain's `what_it_answers`;
the prompt states the bounds (glossary `maps_toward` or `what_it_answers` → `high`; `technical`
never above `low`; a role that contradicts its hint must argue it; a `reason` never quotes a
value). `_assemble` enforces: unobserved column → dropped; unknown role → `unclassified`; skipped
column → hint, `importance` high if glossary-mapped else medium (low for technical/unclassified),
`reason = "from profile hint"`; technical above low → demoted; contradiction without a reason →
hint kept; each correction an `info` signal. **Anomaly signals moved out of the stub into
`_assemble`**, deterministic from the v2 facts for every provider: empty column, null rate ≥ 1%,
constant column (non-technical, >1 row), sentinel-heavy date (≥ 5% of populated values),
duplicate rows - the model explains, it does not detect. `StubClient` emits one role per column
from the hints with the same bounds. `llm_max_tokens` default 2048 → 8000 (`.env.example`
updated). *Tests:* `tests/unit/test_column_roles.py` (v3 prompt + domain knowledge in context; one
role per observed column, bounded; every `_assemble` rule; anomalies raised whatever the model
says; null-rate risk once, not twice; golden Fidelis roles with no sample or top value in any
reason); the four `interpret_file@2` assertions moved to `@3`. `templates.md §1.3` is updated in
PR-9.

### PR-7 — Analyst UI — **built** (branch `feat/w0-versioned-migrations`)

- **`components/run/RecommendedFields.tsx`** — S2, Evidence and Verdict modes: `importance = high`
  columns grouped by role, each with its `reason` and the glossary/canonical citation as text (the
  knowledge layer has no HTTP surface — `knowledge-base-screen.md §5` — so no link yet).
- **`VerdictCard`** adds one line from v2 facts: *"time coverage 2024-02-01 → 2026-01-31 · 3
  constant columns · 2 sentinel-heavy dates"*. Composed, never generated.
- **Forensic table** (`ReviewEvidence`) grouped by role via `CollapsibleSection`, `technical`
  collapsed by default for Data Analyst; columns gain null ratio, min/max, top values (non-PHI).
- **`ProposalTable`** gains a role column and default ordering identifiers → measures →
  dimensions → dates → business → technical (the status filter is unchanged).
- **`SignalCard`** already renders the new anomaly signals — no change.

*Acceptance.* On the 60-column Molina file in Evidence mode, the analyst sees ≤ 12 recommended
fields and seven role groups before any 60-row table. Facts are never hidden: every column is
reachable in Forensic.

**As built.** `lib/columnRoles.ts` holds the vocabulary, the group order (identifiers → measures
→ dimensions → dates → business attributes → derived → unclassified → technical) and the one rule
for a column's role: the interpretation's judged role where the model saw it, the profiler's
`hint` otherwise (a pre-v3 interpretation, a v1 profile) - the frontend never classifies.
`components/run/RecommendedFields.tsx`: the `importance = high` columns by role with their
`reason` (the citation is in the text), capped at 12 with "N more in Forensic". `ReviewEvidence`:
Evidence and Verdict show it between the signals (which stay above, in every mode) and the claims;
Forensic groups every column by role in `CollapsibleSection`s, `technical` closed by default for
the Data Analyst (`personaDefaults.technicalCollapsed`, passed from the review page), each row
with null ratio, range, top values (PHI rows read `•••• masked` for all three), constant and
sentinel tags, and importance + reason. `VerdictCard` gains one composed line from the v2 facts
(time coverage · constant columns · dates with placeholder values), absent for a v1 profile.
`ProposalTable` takes an optional `roles` map (source column → role, built on the Bronze review
page from the upload's interpretation): a role pill column and stable ordering by role; without
the map it renders exactly as before, so the batch page and the studio's seed view are unchanged.
`SignalCard` unchanged. `tsc`, `next build`.

---

## 18. Cross-cutting

### 18.1 Register and group view (both personas)

`IngestionTable` (`/data/intake`) reads `persona` for its default column set and filter (§14.2).
`lifecycleStage.ts`'s `stageOf` moves to the ledger once PR-2 lands (a group's stage becomes the
furthest `done` step across its objects — same semantics, one source).

### 18.2 What a persona may never do

- Hide a fact. Personas change defaults, ordering, grouping and home. Forensic is always reachable.
- Present a disabled control without its reason on screen (the console's existing pattern).
- Stand in for authorisation. Every capability is enforced by the API (§14.1); the UI only
  mirrors it.

### 18.3 Infra

| Item | Plan |
|---|---|
| Schema changes | `001_step_run.sql` via PR-0's mechanism. Everything else is JSONB. |
| LLM | `llm_max_tokens` default → 8000; document in `compose/.env.example`. |
| PHI | `mask_facts` covers v2 facts (PR-5). `RR-08` governs the prompt; `column_roles.reason` must not quote values — say so in the prompt and assert it in the golden tests (no sample value string appears in any `reason`). |
| Railway | No topology change. Ledger + `/api/steps` give failed-step visibility without a metrics stack. |
| Hosting/PHI gate | Unchanged from memory: no real PHI until the Azure/Entra + BAA decision. All of this plan runs on de-identified data. |

### 18.4 Testing and review discipline

- Backend: pytest for every PR (`checklist.md §0`: "Tests exist for every behavioral change and
  were actually run"); the full suite must stay green (384 today).
- Frontend: `tsc --noEmit` + `next build`, then the manual smoke script below (the owner's
  decision: no frontend test infra yet).
- **Review checklist item, added after today:** no function-typed prop may cross from a Server
  Component into a `"use client"` component (`tsc` and `next build` cannot catch it; the
  `PreviewPanel.limitHref` incident is the precedent).
- Each PR ends with a stage completion report (`templates.md §7`).

### 18.5 Manual smoke script (per PR, per persona)

Sign in as an `approver`, then as a `data_engineer` (create both via `/admin/users`), and for
each: home renders the right variant → upload the Fidelis CSV → S1 → S2 (recommended fields,
reading mode default, gate visible only for the approver) → approve → Bronze review (grouped
proposal) → mapping → preview → G2 → `/data/intake/[group]` (Schema/Lineage/Quality only for the
engineer) → force a failure (`CINQFLOW_LLM_API_KEY` bad) → the step shows `failed` with its error
→ engineer re-runs it from home; approver cannot.

---

## 19. Hand-off to implementation

Each PR above is one implementation session. When starting one, the prompt should name:

1. **The PR number** (e.g. "implement PR-2") — the package, its files, tests and acceptance are
   defined here; the session should re-read only that section and the files it names.
2. **Branch discipline** — one branch per PR off latest `main`, merged via PR (the convention used
   throughout this work).
3. **Verification expected** — `poetry run pytest` green, `tsc --noEmit`, `next build`, and the
   §18.5 smoke steps relevant to that PR; docker stack rebuilt (`docker compose up --build -d`) so
   the owner can click through.
4. **Anything decided differently** since this document — D1–D9 are the assumptions; changing one
   changes §14 and the affected PR.

Recommended first session: **PR-0 + PR-1 together** (both small, both unblock everything, and
PR-1 is the first time the API refuses a decision by role — worth seeing on its own).

---

*Part II decided and written 2026-09-05. Cites, additionally, `backend/src/cinqflow/api/app.py`,
`routers/worklist.py`, `workers/analyze_bronze.py`, `frontend/app/layout.tsx`,
`components/shell/AppShell.tsx`, `components/home/ActionLauncher.tsx`, `lib/lifecycleStage.ts`
and `docs/blueprints/templates.md`.*
