# The Analyst Forward Flow — Landing → Bronze → Silver Raw

> UI/UX specification for the guided, gate-by-gate path a Business Analyst walks when
> onboarding one file. Written to be implemented directly against the API in
> `backend/src/cinqflow/api/routers/` and the design language in `frontend/app/globals.css`.
> Every endpoint, field name, status value and CSS class cited here was checked against the
> source and exists in this repo today unless marked **GAP**. One claim in the existing
> frontend did *not* survive that check — see the documentation defect flagged in **S3**.
>
> Persona scope: **Analyst only.** No role switcher, no per-role panels. One job, done well.

---

## 0. The person

The analyst is Monica Ram from the Digitalurth training call
(`docs/06_incumbent_workflow_ground_truth/`). Onboards feeds for a living. Knows ADT,
enrollment and claims cold; does not know Spark. Works from convention — *"the flow
template is constant"* — and leaves the product to get answers (opens the Azure portal to
copy a blob prefix). Her files arrive **every fifteen minutes**. There are ~300 ingestion
groups in dev.

Three facts about her that decide the whole design:

| Fact | Design consequence |
|---|---|
| She is accountable for the data, not for the pipeline | Every screen ends in a decision she can defend, never a "Next" that just advances |
| She repeats this dozens of times | The flow must be resumable, scannable and boring in the good way. No screen may require re-reading |
| She context-switches out of the product to verify things | The evidence must come to her. Anything she'd otherwise go looking for is a defect |

**Her question is never "is it done?" It is "would I sign my name to this?"**

---

### 0.1 The six questions (the persona's mental model)

From `Analyst_worflow_and_DAGs/01_data_analyst_workflow.md`: the order an analyst thinks in, and
where each question lands on this flow (plan `04_…` §8, Track C).

| Question | Where it is answered here |
|---|---|
| **Question** — what business problem are we answering? | Not on this flow. The feed's domain and the contract it serves live in knowledge (`domains/*.yaml`, `what_it_answers`) |
| **Understand** — what data exists, what does it represent, which entities, measures, dimensions, time periods? | S1 profile facts; S2's recommended fields and role groups (profiler hints, prompt v3 column roles); `time_coverage` |
| **Trust** — complete, consistent, accurate, timely, fit? | S2 signals and G1; S5 preview and G2; the balance equation; quarantine; the feed view's Quality panel |
| **Analyze** — trends, relationships, segments, anomalies? | Only the deterministic anomalies (empty, constant, sentinel-heavy, duplicates). The rest waits for Silver ODS (W3) |
| **Explain** — why, with what evidence? | Every claim carries evidence; every signal its basis, check and consequence. The model explains; it does not detect |
| **Recommend** — what should the business investigate, change, prioritise, decide? | Recommendation claims and `recommended_action` at G1; the gates are where the analyst decides |

Understand and Trust label S1/S2; Recommend and Decide label the gates. Nothing on this flow is
hypothesis testing, metric calculation, segmentation or an insight narrative over Bronze or Silver
Raw - those need Silver ODS and a question.

## 1. The spine

Every screen in the flow answers three questions, always in this order, always in this
visual order:

1. **What is true?** — deterministic facts, computed by code. `ProfileFacts`,
   `BronzeProfile`, `RunCounts`. Never a model.
2. **What does the system believe, and why?** — `Interpretation.content.claims`,
   `Proposal.content.fields`. Always with confidence and evidence.
3. **What am I authorising, and what happens if I say yes?** — the gate.

### 1.1 The trust ladder

`ClaimKind` in `workflow/models.py` is already a provenance ladder. Make it the visual
spine of every review screen — it is the single most useful thing the analyst can be
handed.

| Kind | Means | Analyst posture | Token | Existing class |
|---|---|---|---|---|
| `observed_fact` | Computed from the bytes | Read, don't verify | `--ink2` | `.claim-kind.observed_fact` |
| `governed_knowledge` | From the curated knowledge base | Check the citation | `--cite` | `.claim-kind.governed_knowledge` |
| `inference` | The model reasoned | **Read the evidence** | `--acc` | `.claim-kind.inference` |
| `recommendation` | The model proposes an action | **Decide** | `--proc` | `.claim-kind.recommendation` |

Rule: **facts and inferences never share a container.** A `.card` holds one kind. The
analyst must be able to tell, at a glance and without reading, how much of what she is
looking at came from a model.

### 1.2 The reasoning contract

"Persona-based reasoning text" is a data shape, not a tone of voice. Every model-produced
statement anywhere in the flow renders through four slots:

```ts
interface ReasoningLine {
  claim: string;       // what the system asserts, in the analyst's vocabulary
  basis: string;       // why — cites a column, a row count, a glossary term, a mapping doc
  check: string;       // how she confirms it herself, in one action, without leaving the page
  consequence: string; // what changes downstream if she accepts it
}
```

Rendered:

> **`member_id` is the natural key for this roster.**
> 9,842 distinct values across 9,842 rows, zero nulls, and it matches the
> `enrollment.member_id` pattern in the canonical model.
> *Check —* open the candidate-key panel; the profiler found no other single column with
> full cardinality.
> *If accepted —* this becomes the Bronze `record_hash` input and the Silver Raw join key.

Never ship `claim` alone. A confidence number without a `check` is decoration —
it tells the analyst how sure the model is, which is not information she can act on.

`check` must be satisfiable **on the current screen**. If it isn't, the screen is missing a
panel.

---

## 2. Route map

The flow is one run with seven stops, not seven pages. Add a route group; leave the
existing `/uploads/[uploadId]`, `/batches/[batchId]` and `/mapping/[feed]` pages exactly
where they are as the deep-inspection surfaces the flow links out to.

```
frontend/app/runs/[uploadId]/
  layout.tsx          // RunShell: header + RunRail + step guard. Server component.
  page.tsx            // resolves canonical step, redirect()s to it
  processing/page.tsx // S1
  review/page.tsx     // S2  — G1
  landing/page.tsx    // S3
  bronze/page.tsx     // S4
  mapping/page.tsx    // S5  — G2
  promoting/page.tsx  // S6
  silver/page.tsx     // S7
```

### 2.1 Step resolver

The URL must never disagree with the control plane. `layout.tsx` fetches
`GET /api/uploads/{uploadId}` once and derives the canonical step from
`upload.status` + `runs[]`:

```ts
// frontend/lib/runStep.ts
export type RunStep =
  | "processing" | "review" | "landing" | "bronze"
  | "mapping" | "promoting" | "silver";

export const RUN_STEPS: RunStep[] = [
  "processing", "review", "landing", "bronze", "mapping", "promoting", "silver",
];

/** The furthest step the control plane says this run has reached. */
export function canonicalStep(detail: UploadDetail): RunStep {
  const { upload, runs } = detail;
  const promotion = runs.find((r) => r.kind === "promote_silver");
  if (promotion?.state === "completed") return "silver";
  if (promotion) return "promoting";                      // received | in_progress | failed

  switch (upload.status) {
    case "received": case "profiling": case "profiled":
    case "interpreting": case "profile_failed": case "interpret_failed":
      return "processing";
    case "interpreted": case "rejected":
      return "review";
    case "approved": case "landing": case "land_failed":
      return "landing";
    case "landed":
      return "bronze";                                    // S5 is reached by CTA, not by status
  }
}
```

**Navigation law.** A step at or behind `canonicalStep` is viewable — the analyst can look
back at what she approved. A step ahead of it redirects to `canonicalStep`. The primary CTA
always points forward; looking back is secondary and visibly marked *Completed*.

`S5 (mapping)` is the one step the status field cannot express, because promotion is
invisible on the upload row. It is entered from S4's CTA and is viewable whenever
`upload.status === "landed"`.

---

## 3. The Run Rail

The persistent component that makes this a flow rather than seven pages. Rendered by
`layout.tsx`, visible on all seven steps, above the content.

```
CINQCARE_DOWNSTATE_Member_Roster_03_05_2026.csv   ·   fidelis_ny / enrollment   ·   2026-03-05

  ①─────②─────③─────④─────⑤─────⑥─────⑦
  Profile  G1   Land  Bronze  Map   Promote  Silver
   ✓       ✓     ✓     ●       ○      ○       ○
  LANDING ───────────────► BRONZE ──────────► SILVER RAW
```

- Three medallion bands sit **under** the seven step dots, spanning the steps they contain:
  `Landing` spans ①–③, `Bronze` spans ④–⑤, `Silver Raw` spans ⑥–⑦. This is the one place
  the analyst sees the medallion architecture as a physical journey.
- Each dot carries a `StatusWord` from `lib/statusWords.ts`. **The seven words are law**
  (`Expected · Received · Processing · Completed · Needs Review · Needs Attention · Missing`).
  `StatusWord` renders anything else as `.status.unbound` — visibly wrong. Do not extend the set.
- Completed dots are links. The current dot is not. Future dots are `aria-disabled`.
- The file identity line above never changes for the life of the run. It is the analyst's
  anchor across a 40-second LLM wait and a coffee break.

Props: `RunRail({ detail, current }: { detail: UploadDetail; current: RunStep })`.
Derives everything from `detail` — no extra fetch.

---

## 4. Screens

Each screen below gives: the analyst's question, the route, the exact data in, the layout,
the reading controls, every state, the copy, and what "done" means.

---

### S0 · Add File

**Analyst question —** *"What am I telling the platform about this file?"*

**Route** `/data/intake/new` (exists). **Component** `components/ingestion/AddIngestionForm.tsx` (exists).
**Submits** `submitUpload` server action → `POST /api/uploads` (multipart) → `redirect('/runs/{upload_id}/processing')`.

> Change one line in `app/actions.ts`: `redirect(`/uploads/${uploadId}`)` → `redirect(`/runs/${uploadId}/processing`)`.

**What the backend actually consumes** — `file`, `source_system`, `feed`, `domain`,
`business_date`, `uploader`. Everything else on the form (`project`, `environment`,
`compute_config`, `workflow`, `pipeline_template`, `flow_template`, `source_connection`,
`target_connection`, `medallion_tiers`, `description`) is posted and ignored by this build.

**The design problem.** The form has 15 fields, 6 of which do something. The analyst cannot
tell which. Today `platformCatalog.ts` is honest in a code comment; the form is not honest
on screen.

**Fix — three fieldsets, each with one honest margin note:**

| Fieldset | Fields | Margin note |
|---|---|---|
| **This file** | file · source system · business date · uploader | *Everything here is read by the profiler. Choose carefully.* |
| **Where it belongs** | data domain · group name (feed) | *Names the Bronze and Silver Raw tables. Cannot be changed after landing.* |
| **How it will run** | compute · workflow · templates · connections · medallion tiers | *Recorded now, applied when scheduling ships. Nothing here affects this upload.* — use `.tag` styling, collapsed by default via `CollapsibleSection` |

Collapsing the third fieldset removes nine fields from the analyst's first pass and tells
the truth about why. This is the highest-value change on the screen.

**Client-side, on file select** — filename, size, detected type from
`ALLOWED_TYPES` (`.csv` · `.xlsx` · `.xlsm`). Nothing else; the profiler owns the rest.

**Duplicate handling.** `POST /api/uploads` returns `409` with
`{ message, fingerprint, upload_id }` when the content hash already exists. Render it as a
route, not an error:

> **This file has already been uploaded.** Same checksum as `up_01J8…`, received 2026-03-05.
> [Open that run] [Choose a different file]

**Primary CTA copy** — `Profile this file`. Names the machine action that follows.
Not "Submit", not "Proceed" — the analyst should know what the button starts.

**Acceptance**
- [ ] Third fieldset is collapsed on load and labelled as not affecting this upload
- [ ] File chosen → name, size, type shown before submit
- [ ] 409 renders as a two-option choice with a working link to the existing run
- [ ] 415 / 400 (empty file) render as inline field errors, not a page-level alert
- [ ] Successful submit lands on `/runs/{id}/processing`, never on a spinner-only page

---

### S1 · Processing

**Analyst question —** *"Is it working, and is it stuck?"*

**Route** `/runs/[uploadId]/processing`
**Poll** `GET /api/uploads/{id}/progress` every **1500 ms** (`POLL_MS` in `UploadProgress.tsx`)
**Stop when** `isUploadInFlight(status) === false`, then `router.refresh()`

Returns `UploadProgress { upload_id, status, error, stages[] }` where each `Stage` is
`{ key: "profile" | "interpret" | "gate", label, state, steps? }` and `state` is
`pending | running | done | failed`. The `interpret` stage carries `steps[]` — the three
LangGraph nodes with human labels already written in `models.py`:

| node | label |
|---|---|
| `ground` | Gathering knowledge and context |
| `infer` | Interpreting with the LLM |
| `assemble` | Assembling structured claims |

**Layout — a timeline, not a spinner.**

```
┌ Processing ──────────────────────────────────────────────────┐
│ ✓  Parsing and profiling the file                      1.2s  │
│ ◐  AI interpretation                                    :14  │
│      ✓ Gathering knowledge and context                       │
│      ◐ Interpreting with the LLM                             │
│      ○ Assembling structured claims                          │
│ ○  Analyst decision (G1)                                     │
└──────────────────────────────────────────────────────────────┘
```

**The rule that makes this screen good: never show dead time.**

The `profile` stage completes in ~1s; the `interpret` stage is an LLM call and can run 30–60s.
The moment `stages.find(s => s.key === "profile").state === "done"`, the deterministic
profile is available from `GET /api/uploads/{id}` — so **render it below the timeline while
the LLM is still running.** The analyst reads row count, columns, PHI candidates and
candidate keys during the wait instead of watching a spinner. By the time the interpretation
arrives she already knows the file.

This turns the longest wait in the flow into the most useful part of it.

**Elapsed time.** Show seconds elapsed on the running stage. A 40-second LLM call with no
timer reads as broken; the same call with `:14` climbing reads as working. Client-side
`Date.now()` delta from when the stage first reported `running` — no backend change.

**States**

| status | Rail dot ① | Screen |
|---|---|---|
| `received` | `Received` | Timeline, all pending |
| `profiling` | `Processing` | Profile stage running, elapsed timer |
| `profiled` | `Processing` | Profile done + **profile facts rendered**, interpret pending |
| `interpreting` | `Processing` | Node-level steps, elapsed timer |
| `interpreted` | `Needs Review` | Poll stops → `router.refresh()` → redirect to `/review` |
| `profile_failed` | `Needs Attention` | `.alert.error` with `upload.error` verbatim + Retry |
| `interpret_failed` | `Needs Attention` | Same, and the profile stays on screen — it is still valid |

**Failure copy** — no apology, name the fix:

> **The profiler could not read this file.** `{upload.error}`
> The original is preserved at `{upload.landing_key}` and nothing was written to the data plane.
> [Retry profiling] [Back to intake]

`LEGAL_TRANSITIONS` permits `PROFILE_FAILED → PROFILING` and `INTERPRET_FAILED → INTERPRETING`,
so Retry is legal. **GAP: no retry endpoint exists.** See §6.

**Acceptance**
- [ ] Poll stops on terminal status; no runaway `setTimeout` after unmount
- [ ] Profile facts appear the instant the profile stage reports `done`
- [ ] Elapsed timer on the running stage only
- [ ] A transient API failure does not clear the last good progress payload
- [ ] Auto-advance to `/review` on `interpreted` — the analyst never clicks to leave here

---

### S2 · Review — Gate G1

**Analyst question —** *"Is this file what it claims to be, and will I authorise it into Bronze?"*

**Route** `/runs/[uploadId]/review`
**Data** `GET /api/uploads/{id}` → `UploadDetail { upload, profile, interpretation, approvals, runs }`
**Decision** `POST /api/uploads/{id}/approve` | `/reject`, body `{ approver, note? }` → `202`

This is the most important screen in the platform. Everything else is transport.

**Layout — two columns, left column sticky at ≥1100px, stacked below.**

```
┌─ VERDICT (sticky, 380px) ─┐  ┌─ EVIDENCE ────────────────────────┐
│ 9,842 rows · 31 columns   │  │ [Verdict] [Evidence] [Forensic]   │
│ 4 PHI · key: member_id    │  │                                   │
│ 0 duplicate rows          │  │ ⚠ Unknowns (2)          expand ▾  │
│                           │  │ ⚠ Risks (1)             expand ▾  │
│ ─────────────────────     │  │ ───────────────────────────────   │
│ Blockers                  │  │ Observed facts (3)                │
│ • 2 unknowns unresolved   │  │ Governed knowledge (2)            │
│                           │  │ Inferences (4)                    │
│ ─────────────────────     │  │ Recommendations (1)               │
│ ☐ Column names match      │  │                                   │
│ ☐ Row count plausible     │  │ ▸ Deterministic profile (31 cols) │
│ ☐ PHI correctly flagged   │  │ ▸ Sample rows (PHI masked)        │
│ ☐ Unknowns are acceptable │  │ ▸ Provenance                      │
│                           │  └───────────────────────────────────┘
│ [ Reject ] [ Approve—G1 ] │
└───────────────────────────┘
```

#### The verdict sentence

Composed **from `ProfileFacts`, never from the model.** This is the one line the analyst
reads first and the one she will quote in standup.

```ts
`${row_count.toLocaleString()} rows · ${columns.length} columns · ` +
`${phi_candidates.length} PHI candidates · ` +
`key ${candidate_keys[0]?.join(" + ") ?? "none found"} · ` +
`${duplicate_rows} duplicate rows`
```

Then the interpretation shape, also computed not generated:
`3 observed facts · 2 governed · 4 inferences · 1 recommendation · 2 unknowns`.

#### Reading modes — the toggle that matters

A `.chip-row` of three `.chip` buttons. This is the "toggle" the flow needs — it filters by
**trust level**, which is the analyst's real question, rather than expanding everything.

| Mode | Shows | For |
|---|---|---|
| **Verdict** | Verdict card + risks + unknowns + recommendations only | The 15-second pass on file #40 today |
| **Evidence** *(default)* | + all claims with evidence collapsed, profile summary | The normal 2-minute review |
| **Forensic** | + full column table, masked sample rows, `provenance.prompt`, `provenance.model`, `provenance.knowledge[]`, raw `interpretation_id` / `profile_id` | Something looks wrong |

Persist the choice in `localStorage` per analyst. She has one mode she lives in.

Separately, every claim's `EvidenceList` expands and collapses independently
(`CollapsibleSection` exists). Modes set the *floor* of what is visible; the analyst opens
individual items above it.

#### Risks and unknowns go ABOVE the claims

`InterpretationContent.risks[]` and `.unknowns[]` are the only things on this page that
require a decision. Claims are read; risks are decided. Put them first, in `.alert.warn`.

An unknown is not a failure — it is the system declining to guess, which is the behaviour
the analyst should reward. Copy accordingly:

> **2 unknowns — the model declined to guess.**
> `plan_code` has no match in the canonical model. `term_date` is 100% null in this delivery.
> These land in Bronze as-is. Nothing is lost; they are simply not interpreted yet.

#### Claims

`ClaimCard` exists and is correct — kind pill, field, confidence bar, value, evidence list.
Group by kind, ordered up the trust ladder: observed → governed → inference → recommendation.
Header per group carries the count and one line of posture:

- *Observed facts — computed from the file. No model involved.*
- *Governed knowledge — from the curated knowledge base. Check the citation.*
- *Inferences — the model reasoned. Read the evidence before accepting.*
- *Recommendations — the model proposes an action. This one is yours to decide.*

Add the `check` slot (§1.2) beneath each inference and recommendation. It is the difference
between a confidence score and a review.

#### PHI

`mask_facts` and `mask_row` mask server-side; PHI columns return `sample_values: []` and
row values as `•••`. Say so on screen rather than showing empty cells:

> `4 columns flagged as PHI candidates. Values are masked in every view — the mask is applied
> in the API, not the browser.`

`.tag.phi` on the column, `.unc` on the masked value.

#### The gate

`GateActions.tsx` exists with the right consequence statement. Two changes:

**1. Turn the free-text note into a composed checklist.** The `note` field currently reads
`placeholder="what you checked"`. Four checkboxes compose the note string that posts to the
same endpoint — same API, far better audit trail:

```
☐ Column names match the expected roster layout
☐ Row count is plausible for this delivery
☐ PHI flags look right
☐ The unknowns are acceptable for Bronze
[ optional free text ]
```

→ `note: "checked: columns, row count, PHI | 1 unresolved: unknowns | <free text>"`

Nothing is enforced. The point is deliberateness, and a note that means something six months
later.

**2. State the consequence of both buttons, not one.** Current gate-note covers approve
well. Add reject:

> **Approve** mints a batch and writes Bronze — append-only, enforced by a reject-mutation
> trigger. **Reject** moves the file to `rejected/` and writes nothing to the data plane.
> `interpreted → rejected` is terminal: `LEGAL_TRANSITIONS[REJECTED]` is empty. There is no
> un-reject; a rejected file is re-uploaded, not revived.

That last sentence is the most important copy on this screen. Reject is irreversible and the
UI must say so before the click, not after.

**States**

| Condition | Screen |
|---|---|
| `status === "interpreted"`, no approval | Full review, gate live, rail ② `Needs Review` |
| `status === "approved"` / later | Read-only. Gate replaced by the decision record: approver, `decided_ts`, note. Primary CTA becomes *Continue to landing* |
| `status === "rejected"` | Read-only, `.alert.error`. Rail ② `Needs Attention`. CTA *Upload a corrected file* → `/data/intake/new?feed={feed}` |
| `409 already {decision}` | Reconcile, don't error: `router.refresh()` and show the existing decision |
| `409 G1 requires an interpreted upload` | The analyst is on a stale tab. Refresh and re-resolve the step |

**Acceptance**
- [ ] Verdict line is computed from `ProfileFacts`, never from `interpretation`
- [ ] Facts and inferences never appear in the same `.card`
- [ ] Risks and unknowns render above claims
- [ ] Every inference and recommendation carries a `check` line
- [ ] Reject's irreversibility stated before the click
- [ ] Reading mode persists across runs
- [ ] Read-only after decision; the decision record replaces the gate
- [ ] `409` reconciles rather than alerting

---

### S3 · Landing

**Analyst question —** *"What is actually being written, and did it balance?"*

**Route** `/runs/[uploadId]/landing`
**Entered on** `202` from `/approve` → `status: approved`, `queued: "batch.land_bronze"`

> **This step does not work today.** `isUploadInFlight()` excludes `approved` and `landing`,
> and `UploadProgress.stages` stops at `gate`. The `approve` response carries no `batch_id` —
> the batch is minted inside the `land_bronze` worker. See **§6.1** for the exact patch.
> It is ~25 lines and it unlocks the entire second half of this flow.

**Poll** `GET /api/uploads/{id}/progress` (with the §6.1 `land` stage) every 1500 ms.
Once `batch_id` appears in the payload, also `GET /api/batches/{batch_id}` for live `RunCounts`.

**Landing is verbatim preservation, not a DQ pass.** Read `PipelineRunner.land_bronze`:
it parses the file and appends **every row unchanged** as a `BronzeRow` — no typing, no
rules, no rejection. `RunCounts(records_in=parsed.row_count, records_out=written)`; both
other counters stay zero and balance is the strict `in == out`.

> ⚠ **Documentation defect.** The comment in `lib/lifecycleStage.ts` claims *"the landing run
> **is** the DQ pass — it types every column, rejects what fails, and writes the quarantine."*
> The runner does none of those things, and `get_batch_quarantine` reads
> `quarantine_table(feed, schema=silver_schema)` — the **Silver** quarantine, written at
> promotion. The stage label `Dq Applied` therefore overstates what happened. Fix the comment,
> and consider renaming the stage `Landed`. Do not let the UI repeat the claim.

What the screen should say instead:

> **Landing to Bronze.** Every row is preserved exactly as it arrived — no casting, no rules,
> no rows dropped. Bronze is append-only, and typing happens later, at the mapping.
> Once the rows are safely in Bronze the original moves from `incoming/` to `processed/`.

That last clause is worth showing, because `upload.landing_key` is **rewritten** at that
moment: the path the analyst saw at G1 is not the path she will see afterwards. Show both.

**The balance panel.**

`RunCounts { records_in, records_out, quarantined, attributed_drops }`, and here only the
first two are ever non-zero.

```
┌─ Balance ─────────────────────────────────┐
│  9,842            9,842                   │
│  records in       records out             │
│                                           │
│  ✓ Balanced — every row reached Bronze    │
└───────────────────────────────────────────┘
```

**A landed batch is always balanced.** If it doesn't balance the runner rolls the transaction
back, raises `LandingFailure`, and the upload goes to `land_failed` — so `landed && !balanced`
is unreachable. The failure surface is the error state, not a red banner on a landed run:

> **Landing failed and nothing was kept.** `balance failed: in=9842 out=9740`
> The write was rolled back — Bronze has no rows from this batch. The original is still at
> `{landing_key}`. Retry the landing.

**States**

| status | Rail ③ | Screen |
|---|---|---|
| `approved` | `Processing` | "Queued for landing" + the DQ explanation. Counts not yet available |
| `landing` | `Processing` | Live counts as the run reports them |
| `landed` | `Completed` | Balance panel green, both landing keys shown, auto-advance to `/bronze` |
| `land_failed` | `Needs Attention` | `upload.error` verbatim, "nothing was kept", Retry (`LAND_FAILED → LANDING` is legal) |

There is no unbalanced-but-landed state to design for. That is a property of the runner, not
an omission — and it is worth saying on screen, because "did I lose rows?" is the question
this step exists to answer and the answer is structurally *no*.

**Acceptance**
- [ ] Poll survives `approved` and `landing` (§6.1)
- [ ] `batch_id` discovered from the progress payload, not guessed
- [ ] Balance stated as `in == out`, with quarantine/drops absent rather than shown as zeros
- [ ] The screen does **not** claim landing types or rejects anything
- [ ] Both landing keys shown once the original moves to `processed/`

---

### S4 · Bronze Review

**Analyst question —** *"Did what landed match what I approved, and is the proposed mapping defensible?"*

**Route** `/runs/[uploadId]/bronze`
**Data**
- `GET /api/batches/{batch_id}` → `BatchDetail { run, lineage, approvals, upload, bronze_profile, proposal }`
- `GET /api/batches/{batch_id}/bronze-profile` → adds `is_sample`
- `GET /api/batches/{batch_id}/proposal` → adds `counts`, `authoritative: false`
- `GET /api/batches/{batch_id}/rows?limit=50` → PHI-masked Bronze rows
- `GET /api/batches/{batch_id}/quarantine` → `by_outcome`, `by_rule`, rows with `reasons[]`

Written by the `bronze.analyze` worker: a deterministic `BronzeProfile` **and** an AI
`Proposal`, in that order. Present them in that order.

#### Panel 1 — Reconciliation

The analyst approved a file at G1. This panel proves the same file is what landed. Nothing
else on this screen matters if this one is wrong.

| | Upload profile (pre-land) | Bronze profile (post-land) |
|---|---|---|
| Rows | `profile.facts.row_count` | `bronze_profile.rows_in_batch` — **always equal** |
| Profiled | all | `rows_profiled` — `is_sample` when fewer |
| Columns | `profile.facts.columns.length` | `bronze_profile.facts.columns.length` |
| PHI candidates | `phi_candidates.length` | same |

Because landing preserves every row, the row counts match by construction. That makes this
panel about **columns, PHI and sampling** rather than about row loss — and it makes any
mismatch a genuine alarm rather than expected attrition. Any differing row gets `.tag.danger`.

When `is_sample` is true, say so plainly — *"Profiled 5,000 of 9,842 landed rows"* — because
every figure in this panel is then a sample statistic and the analyst must read it as one.

#### Panel 2 — The proposal

`FieldStatus` is a four-way vocabulary the analyst can act on. Make it the filter.

| Status | Means | Colour | Analyst action |
|---|---|---|---|
| `candidate` | A defensible target exists | `--ok` | Skim |
| `ambiguous` | More than one plausible target, none decisive | `--proc` | **Decide** |
| `unknown` | No defensible target; deliberately not guessed | `--ink3` | Decide or leave |
| `invalid` | Named a target the canonical model does not have | `--danger` | **Fix** |

**Toggle = filter by status,** as a `.chip-row` with live counts from `proposal.counts`:

```
[ All 31 ] [ Candidate 22 ] [ Ambiguous 5 ] [ Unknown 3 ] [ Invalid 1 ]
```

Default the filter to `ambiguous + invalid` when either is non-zero. Her first move is
always "show me the ones that need me" — start her there. Say so:

> `Showing the 6 fields that need a decision. 25 candidates are hidden.` [Show all]

Each field row: `source` → `target`, `transform.op`, confidence bar, `status` pill,
`evidence[]` collapsed. `invalid` rows additionally carry `rejected_target` and `reason` —
render both, because "the model named a table that doesn't exist" is the single most useful
error the system can report and it should be legible without opening anything.

#### The honesty banner

`GET /api/batches/{batch_id}/proposal` returns `"authoritative": false`. It must be visible,
not implied:

> **Advisory.** This proposal is not applied to anything. It becomes real only when you
> create a mapping version from it and approve that version at G2 — and the version is yours,
> not the model's.

`.alert` with `--gate` border. This is the platform's core promise; give it weight.

#### Panel 3 — Bronze rows

Collapsed by default. `GET /api/batches/{batch_id}/rows?limit=50` returns rows PHI-masked,
with `phi_masked[]` naming the columns that were masked. Each row carries its `row_number`
and `record_hash`.

**There is no quarantine panel on this screen.** `GET /api/batches/{batch_id}/quarantine`
reads `quarantine_table(feed, schema=silver_schema)` — the *Silver* quarantine, which
`promote_silver` writes. Before promotion the table does not exist and the endpoint returns
`total: 0` with empty `by_outcome` / `by_rule`. Rendering an empty quarantine panel here
would tell the analyst her file was clean when nothing has been checked yet. Quarantine
belongs at S7, after the mapping has run.

When it does appear there, note that `by_rule` is **page-scoped** (computed over the returned
rows only) while `by_outcome` and `total` cover the whole batch. Label it: *"rule counts cover
the 50 rows shown."* Getting this wrong is how an analyst under-reports a data problem.

**CTA** — `Build the mapping` → `/runs/{uploadId}/mapping`.

**Acceptance**
- [ ] Reconciliation is the first panel, above the proposal
- [ ] `is_sample` stated in words wherever a sampled figure is shown
- [ ] Status filter defaults to `ambiguous + invalid` when non-zero, and says what it hid
- [ ] `invalid` rows show `rejected_target` and `reason` without expanding
- [ ] "Advisory / not authoritative" is a visible banner
- [ ] **No quarantine panel** — it is empty until promotion and would read as a clean bill of health

---

### S5 · Mapping and Preview — Gate G2

**Analyst question —** *"Have I seen this mapping run, and do I own it?"*

**Route** `/runs/[uploadId]/mapping`
**Data / actions**
- `POST /api/feeds/{feed}/mapping-versions` — create from proposal or derive from a version
- `GET /api/feeds/{feed}/mapping-versions/{v}` — the draft
- `GET /api/feeds/{feed}/mapping-versions/{v}/diff` — vs `derived_from`
- `POST /api/feeds/{feed}/mapping-versions/{v}/preview` `{ batch_id?, rows?, strategy? }` → `202`
- `GET  /api/feeds/{feed}/mapping-versions/{v}/preview` → `is_current`, `approvable`, `stale_reason`, `sample_is_partial`
- `POST /api/feeds/{feed}/mapping-versions/{v}/approve` `{ approver, note? }` → `202` → queues `mapping.promote`

`MappingStudio.tsx` and `ApproveMapping.tsx` exist. The forward-flow contribution is the
gate discipline around them.

#### Ownership, stated once, at the top

```
Version 3 · draft · origin: proposal · derived from v2 · 31 fields · 4 edited by you
```

`MappingVersion.origin` computes this string already. `MappingField.edited` flips true the
moment the analyst touches an AI-proposed field — `.tag.edited` on those rows. The count of
edited fields is the analyst's answer to *"how much of this is mine?"*, and it belongs in
the header.

#### The preview is the gate's evidence

`approve` is refused without a preview of **this exact spec**. `spec_fingerprint` makes a
preview visibly stale rather than silently wrong. Surface all three fields the API gives:

| API | UI |
|---|---|
| `approvable: false` + `stale_reason` | G2 button disabled, reason rendered beside it verbatim |
| `is_current: false` | `.alert.warn`: *"This preview describes an earlier draft. Run it again."* |
| `sample_is_partial: true` | *"Previewed 200 of 9,842 rows (head). Rules that only fire on later rows will not appear here."* |

**Never disable a control without printing why next to it.** The API already writes the
sentence; render it.

#### Impact summary

From `PreviewAggregates`:

```
┌─ Preview · 200 of 9,842 rows (head) ────────────────────────┐
│ ████████████████████░░░  186 ok  ·  9 failures  ·  5 quar.  │
│                                                             │
│ failures by rule       cast:date_parse            7         │
│                        value_map:unmapped         2         │
│ null or invalid        term_date                 200        │
│ affected sources       svc_date                    7        │
└─────────────────────────────────────────────────────────────┘
```

Each rule and each source is a click that filters `row_results[]` below. `PreviewRowResult`
carries per-field `outcome` (`ok | defaulted | null | failure | quarantined | rejected`) and
`reason` — so a filtered view shows exactly the cells that misbehaved. This is the analyst's
debug loop and it should take one click, not a scroll.

`null_or_invalid.term_date = 200` across a 200-row sample is the shape of a real finding:
a column that is entirely null. Make full-column nulls visually distinct from scattered ones.

#### G2

> **Approving freezes this version.** An approved mapping version is never edited —
> `MappingStatus` goes `draft → previewed → approved`, and a change means a new version
> derived from this one. Approving also queues the promotion: this batch is mapped into
> Silver Raw immediately.

Same composed-checklist pattern as G1.

**The `202` response is what S6 needs, so keep it:**
`{ approval, status, batch_id, preview_id, sample_was_partial, queued }`. Unlike G1, this one
carries `batch_id` directly — the promotion runs against the batch the *preview* sampled
(`preview.sample.batch_id`), which is why the handler also refuses when that batch has no
landing run. So S6 never has to discover its batch: hand it through from here.

Two more `409`s worth rendering verbatim rather than paraphrasing:
`"this version has no preview of its current spec"` with hint *"run a preview and approve
what you saw"*, and `"{feed} v{version} was superseded by a later version"`.

**States**

| Condition | Screen |
|---|---|
| No mapping version yet | Empty state + *Create version from the proposal* / *Start from scratch* |
| `draft`, no preview | Studio editable. G2 disabled: *"Run a preview before approving."* |
| `draft`, preview stale | Studio editable. `.alert.warn` + `stale_reason`. G2 disabled |
| `previewed`, `approvable` | G2 live. Rail ⑤ `Needs Review` |
| `approved` | Studio read-only. `editable === false`. Advance to `/promoting` |
| `superseded` | Read-only + link to the version that replaced it |
| `409 no completed Bronze batch` | Render the API's own `hint`: *"approve an upload at G1 so a batch exists"* |

**Acceptance**
- [ ] Every disabled G2 prints `stale_reason` beside it
- [ ] `sample_is_partial` stated in rows, not as a boolean
- [ ] Rule and source chips filter `row_results` in one click
- [ ] Edited-field count in the header
- [ ] Freeze semantics stated before the click

---

### S6 · Promoting

**Analyst question —** *"Is the promotion running, and did it balance?"*

**Route** `/runs/[uploadId]/promoting`
**Poll** `GET /api/batches/{batch_id}` every 1500 ms; read the `promote_silver` run's `state` and `counts`

Structurally identical to S3 — deliberately. The analyst has already learned this screen;
do not teach her a second one. Same balance panel, same `RunCounts`, same `.kpi`.

Two differences in content:

1. **Name the version.** `Run.mapping_version` is set on `promote_silver` runs only.
   *"Promoting batch `bt_01J8…` through mapping v3."* Which mapping ran is the first thing
   she will be asked in an incident.
2. **Quarantine here is different from Bronze quarantine.** Bronze quarantine is rows the
   *type system* refused. Silver quarantine is rows the *approved mapping* refused, and each
   carries the rule and the `mapping_version` that refused it — which means it is a statement
   about her decision, not about the file.

**States** follow `RunState`: `received → in_progress → completed | failed`.

`runStatusWord()` returns `Needs Attention` for `completed && balanced === false` — keep that
branch as a guard, but design for the state that actually occurs: like landing, an unbalanced
promotion is rolled back by `_fail_promotion` and the run finishes `failed` with its `error`
set. A **completed** promotion has always balanced. So the screen's adverse path is
`state === "failed"` carrying `run.error`, not a red banner on a completed run:

> **The promotion failed and nothing was written.** `{run.error}`
> Bronze is untouched — the batch is still there and still balanced. The mapping version stays
> approved. Fix the cause and re-queue the promotion.

`promote_silver` is a **replay**: it rebuilds this batch's Silver rows and quarantine and
leaves other batches alone, so re-running it is safe and does not duplicate.

**Acceptance**
- [ ] Same balance component as S3, not a second implementation
- [ ] Mapping version named on screen
- [ ] `batch_id` taken from the G2 response, not rediscovered
- [ ] `failed` shows `run.error`, says Bronze is untouched, and does not advance
- [ ] Replay-safety stated where re-running is offered

---

### S7 · Silver Raw

**Analyst question —** *"What exists now that did not exist before, and can I prove where it came from?"*

**Route** `/runs/[uploadId]/silver`
**Data** `GET /api/batches/{batch_id}` + `GET /api/lineage/{batch_id}` + `GET /api/batches/{batch_id}/quarantine`

The arrival screen. It closes the run and it is what the analyst screenshots.

#### Panel 1 — What landed

`Lineage.silver_tables: dict[str, int]` — *"every entity this batch actually wrote, and how
many rows each received."* One feed legitimately populates several canonical entities, so
this is a list, not a number:

```
silver_raw.enrollment_member       9,796
silver_raw.enrollment_coverage     9,796
silver_raw.enrollment_plan           412
```

`Lineage.silver_table` is the primary entity as the mapping spec declares it — mark it.

#### Panel 2 — Lineage

`LineageChain.tsx` exists. The full chain, each link addressable:

```
upload up_01J8…  →  file {landing_key}  →  batch bt_01J8…
     →  bronze.enrollment_fidelis_ny  →  mapping v3  →  silver_raw.enrollment_member
```

Two approvals sit on this chain — G1 on the interpretation, G2 on the mapping version. Show
both with approver and timestamp from `approvals[]`. **This is the audit story in one
screen**, and it is the single strongest thing CINQFLOW has over the incumbent, where lineage
is reconstructed by hand.

#### Panel 3 — What didn't make it

Quarantine `by_outcome` and `by_rule`, and the honest framing:

> These rows are in `{quarantine_table}`, not lost. Read-only — a fix is a new mapping
> version, and re-promoting this batch re-drives these rows through it.

That sentence is lifted from the API docstring because it is exactly right and the analyst
needs to know that quarantine is recoverable, not a hole.

#### Panel 4 — What's next

Be honest about the build:

> **Silver Raw → Silver ODS is not on this build.** `medallionTiers.ts` marks the tier
> `planned`: the tier list posts with the form but the control plane does not consume it yet.

Then the two things she can actually do:
- **Onboard the next delivery of this feed** → `/data/intake/new?feed={feed}` (the register already supports this)
- **Re-land this upload** — `LANDED → LANDING` is legal, Bronze is append-only, so a replay
  adds a new batch and leaves this one intact. Say that, because "will this duplicate my
  data?" is the question that stops people replaying.

**Acceptance**
- [ ] Every entity written is listed with its row count; primary entity marked
- [ ] Both approvals shown on the lineage chain with approver and timestamp
- [ ] Quarantine framed as recoverable, with the recovery path named
- [ ] Silver ODS honestly marked as not on this build
- [ ] Replay semantics (append-only, new batch) stated

---

## 5. Cross-cutting

### 5.1 Status words

Seven, no more. `StatusWord` renders anything else as `.status.unbound`. Map for the rail:

| Step | Source | Word |
|---|---|---|
| ① Profile | `uploadStatusWord(status)` | `Received` → `Processing` → `Completed` |
| ② G1 | `uploadStatusWord("interpreted")` | `Needs Review` |
| ③ Land | `runStatusWord(landRun)` | `Processing` → `Completed` / `Needs Attention` |
| ④ Bronze | proposal present? | `Needs Review` / `Expected` |
| ⑤ Map / G2 | `mappingStatusWord(status)` + `previewStatusWord()` | `Processing` → `Needs Review` → `Completed` |
| ⑥ Promote | `runStatusWord(promoteRun)` | `Processing` → `Completed` / `Needs Attention` |
| ⑦ Silver | promotion completed | `Completed` |

`Needs Review` is violet (`--gate`). Violet means *"the machine is finished and is waiting
for you."* It appears at exactly two places in the flow — G1 and G2 — and nowhere else.
Protect that: if violet starts appearing on non-gate surfaces it stops meaning anything.

### 5.2 Polling

One hook, one contract, four call sites (S1, S3, S6, and the preview in S5).

```ts
// frontend/lib/usePoll.ts
function usePoll<T>(fetcher: () => Promise<T>, opts: {
  intervalMs?: number;       // default 1500 — matches UploadProgress.POLL_MS
  until: (value: T) => boolean;
  onSettled?: (value: T) => void;
}): { value: T | null; error: null }
```

Rules, all of which `UploadProgress.tsx` already gets right and which must survive the refactor:
- **A transient failure keeps the last good value.** Never blank the screen because one tick 502'd.
- **Stop on settle**, then `router.refresh()` once. Never poll a terminal state.
- **Cancel on unmount.** The `cancelled` + `settled` flag pair, not just one.
- **Never poll faster than 1500 ms.** These are database reads behind a work queue.

### 5.3 Resumability

She has ~300 groups and files arriving every 15 minutes. Every step must be safely
abandonable at any moment, because it will be.

- No client-only state that isn't in the URL or the control plane. The single exception is
  the reading-mode chip (`localStorage`) and an in-progress mapping draft (which the API
  persists anyway).
- `/data/intake` is the queue. `groupStage()` in `lifecycleStage.ts` already computes the
  furthest stage per group. Add one count to the register header:
  **`3 runs waiting for you at a gate`** — uploads at `interpreted`, plus previewed mapping
  versions that are `approvable`. That is her worklist, and it is derivable today.

### 5.4 Errors

Every error names what happened, what it means for the data, and what to do:

```
{what failed}. {what state the data is in}. {the one action}.
```

> **The promotion failed and nothing was written.** `balance failed: in=9796 out=9740
> quarantined=54 drops=0`
> The write was rolled back. Bronze is untouched — the batch is still there and still
> balanced. The mapping version stays approved; re-queue the promotion once the cause is fixed.

Never "Something went wrong." Never an apology. The `409` payloads in `uploads.py` and
`mapping_versions.py` already carry `message` + `hint` — render them, do not paraphrase them.

### 5.5 Accessibility

- Rail dots: `<ol>` with `aria-current="step"`; future steps `aria-disabled="true"`
- Poll updates go in `aria-live="polite"` — the analyst on a screen reader hears the stage
  change without losing her place
- Reading-mode chips are a `role="radiogroup"`
- Focus visible everywhere — `:focus-visible { outline: 2px solid var(--gate) }` is already
  set globally in `globals.css`
- Status is never colour alone: `StatusWord` pairs an icon with the word already. Keep it.

---

## 6. Backend gaps

Four gaps. The first blocks the flow; the rest degrade it.

### 6.1 Progress must survive past G1 — **BLOCKING**

Today `UploadProgress.stages` stops at `gate` and `isUploadInFlight()` excludes `approved`
and `landing`, so S3 has nothing to poll and no way to learn `batch_id`.

**`backend/src/cinqflow/workflow/models.py`**

```python
class Stage(BaseModel):
    key: Literal["profile", "interpret", "gate", "land"]   # + "land"
    ...

class UploadProgress(BaseModel):
    upload_id: str
    status: UploadStatus
    error: str | None = None
    batch_id: str | None = None        # NEW — set once the land run exists
    stages: list[Stage]

_LAND_STAGE_STATE: dict[UploadStatus, StageState] = {
    UploadStatus.RECEIVED: "pending",   UploadStatus.PROFILING: "pending",
    UploadStatus.PROFILE_FAILED: "pending", UploadStatus.PROFILED: "pending",
    UploadStatus.INTERPRETING: "pending", UploadStatus.INTERPRET_FAILED: "pending",
    UploadStatus.INTERPRETED: "pending", UploadStatus.REJECTED: "pending",
    UploadStatus.APPROVED: "running",  UploadStatus.LANDING: "running",
    UploadStatus.LANDED: "done",       UploadStatus.LAND_FAILED: "failed",
}

def build_upload_progress(upload, run, land_run: Run | None = None) -> UploadProgress:
    return UploadProgress(
        upload_id=upload.upload_id,
        status=upload.status,
        error=upload.error,
        batch_id=land_run.batch_id if land_run else None,
        stages=[
            ...,                                            # profile, interpret, gate as today
            Stage(key="land", label="Landing to Bronze",
                  state=_LAND_STAGE_STATE.get(upload.status, "pending")),
        ],
    )
```

**`api/routers/uploads.py`** — `get_upload_progress` passes the land run:

```python
land_run = next((r for r in store.list_runs(upload_id=upload_id)
                 if r.kind == "land_bronze"), None)
return build_upload_progress(upload, run, land_run).model_dump(mode="json")
```

**`frontend/lib/statusWords.ts`**

```ts
export function isUploadInFlight(status: UploadStatus): boolean {
  return status === "received" || status === "profiling" || status === "profiled"
      || status === "interpreting" || status === "approved" || status === "landing";
}
```

~25 lines. Everything from S3 onward depends on it.

### 6.2 No retry endpoint

`LEGAL_TRANSITIONS` permits `PROFILE_FAILED → PROFILING`, `INTERPRET_FAILED → INTERPRETING`
and `LAND_FAILED → LANDING`, but nothing exposes them. Proposed:

```
POST /api/uploads/{upload_id}/retry  →  202
  409 when the current status has no retry transition
  body: { }        the transition is implied by the failed status
  returns: { status, queued }
```

Until it exists, S1 and S3 show the failure with the preserved `landing_key` and route the
analyst back to intake. Which is honest, but it is a re-upload for a transient failure.

### 6.3 Promotion has no progress endpoint

`GET /api/batches/{batch_id}` returns the run and its `state`, which is enough for S6 —
but it returns `BatchDetail` in full (lineage, approvals, upload, bronze profile, proposal)
on every 1500 ms tick. A lightweight `GET /api/batches/{batch_id}/progress` mirroring the
upload one would cut the poll payload by an order of magnitude.

### 6.4 No cross-run worklist

`/data/intake` computes group stage client-side from `listUploads()`. A
`GET /api/worklist` returning uploads at `interpreted` plus mapping versions where
`approvable === true` would make the queue count in §5.3 a single cheap call instead of a
fan-out. Not blocking — derivable today.

---

## 7. Components

### New

| Component | Location | Notes |
|---|---|---|
| `RunShell` | `app/runs/[uploadId]/layout.tsx` | Server. Fetches `UploadDetail`, resolves step, guards |
| `RunRail` | `components/run/RunRail.tsx` | Seven dots + three medallion bands. Pure, derives from `UploadDetail` |
| `VerdictCard` | `components/run/VerdictCard.tsx` | Computed from `ProfileFacts`. Sticky on desktop |
| `ReadingMode` | `components/run/ReadingMode.tsx` | `role="radiogroup"` of `.chip`, `localStorage`-backed |
| `ReasoningLine` | `components/run/ReasoningLine.tsx` | The four-slot contract from §1.2 |
| `BalancePanel` | `components/run/BalancePanel.tsx` | `RunCounts` + `balanced`. Shared by S3 and S6 |
| `GateChecklist` | `components/run/GateChecklist.tsx` | Composes the `note` string for both gates |
| `usePoll` | `lib/usePoll.ts` | Generalised from `UploadProgress.tsx` |
| `runStep` | `lib/runStep.ts` | `canonicalStep()` + `RUN_STEPS` |

### Reused unchanged

`AppShell` · `Sidebar` · `TopBar` · `StatusWord` · `Kpi` · `ClaimCard` · `EvidenceList` ·
`LineageChain` · `PreviewPanel` · `MappingStudio` · `ApproveMapping` · `GateActions` ·
`DataTable` · `CollapsibleSection` · `Modal` · `FormField` · `Combobox` · `FileDropzone` ·
`Pagination` · `TableToolbar`

### Design language — do not invent

Take everything from `frontend/app/globals.css`:
`--paper --surf --mat --line --line2 --ink --ink2 --ink3` ·
`--acc` (blue, links/received) · `--cite` (teal, governed knowledge) · `--gate` (violet,
gates and focus) · `--ok` (green) · `--danger` (red) · `--proc` (amber, processing/PHI) ·
`--expected` (slate) · `--missing` (magenta).
Type: **Instrument Sans** + **IBM Plex Mono**, both already loaded in `layout.tsx`.
Classes: `.card` `.kpi` `.claim` `.tag` `.chip` `.status[data-w]` `.gate-box` `.panel-label`
`.alert` `.mono` `.meta` `.num` `.unc` `.empty` `.scroll`.
Light is default; dark is opt-in via `data-theme="dark"`, never inferred from the OS.

---

## 8. Ship checklist

**Blocking**
- [ ] §6.1 progress patch merged — `land` stage + `batch_id` + `isUploadInFlight`
- [ ] `app/actions.ts` redirects to `/runs/{id}/processing`
- [ ] `canonicalStep()` guard: no URL can show a step ahead of the control plane

**The seven screens**
- [ ] S0 third fieldset collapsed and labelled as not affecting this upload
- [ ] S1 profile facts render while the LLM is still running
- [ ] S2 verdict computed from facts; risks and unknowns above claims; reject irreversibility stated
- [ ] S3 balance is `in == out`; the screen does not claim landing types or rejects anything
- [ ] S4 reconciliation first; status filter defaults to what needs a decision; advisory banner; no quarantine panel
- [ ] S5 every disabled G2 prints its reason; `sample_is_partial` in rows
- [ ] S6 reuses S3's balance component; names the mapping version; `failed` is the adverse path
- [ ] S7 every entity written listed; both approvals on the lineage chain

**Cross-cutting**
- [ ] Only the seven status words render; nothing hits `.status.unbound`
- [ ] Violet appears at G1 and G2 and nowhere else
- [ ] Every poll cancels on unmount and keeps its last good value on a failed tick
- [ ] Every model-produced statement carries `basis` and `check`
- [ ] Every `409` renders its `message` and `hint` verbatim
- [ ] PHI masking explained on screen wherever masked values appear
- [ ] `aria-current="step"` on the rail; `aria-live="polite"` on poll regions

---

*Generated for the CINQFLOW rebuild. Cites `backend/src/cinqflow/`, `frontend/app/`,
`frontend/lib/` and `docs/06_incumbent_workflow_ground_truth/` as of 2026-09-02.*
