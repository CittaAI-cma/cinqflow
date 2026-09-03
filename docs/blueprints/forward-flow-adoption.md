# Adopting the Analyst Forward Flow in the Digitalurth frontend

> **What this is.** The implementation plan for turning the shipped Digitalurth console
> into the seven-screen run flow. It is the companion to `analyst-forward-flow.md` — that
> document is the *screen specification*; this one is the *adoption plan*: what changes in
> the frontend we have already built, what the backend can and cannot serve today, and how
> the single-persona flow extends to a persona-based platform without forking the product.
>
> **Scope note from the brief.** Every screen in the artifact is adopted **except S0 Add
> File**, which ships as built. Only its *exit* changes.
>
> **Verification.** Every endpoint, status value, model field and file path cited below was
> read from this repo on 2026-09-03. Claims that contradict the source blueprint are marked
> **CORRECTION** and carry the source line. Claims about work not yet done are marked
> **GAP**.

---

## 1. The problem this plan solves: two information architectures

We have shipped an **object-oriented** console. The artifact specifies a **task-oriented**
flow. Both are correct; they answer different questions.

| | Shipped today | The forward flow |
|---|---|---|
| Organising idea | The **feed** — a durable thing you configure | The **run** — one file, walked once |
| Entry | `/data/intake` register (~300 groups in dev) | `/runs/[uploadId]/…` |
| Question | "What do I own, and what needs me?" | "Would I sign my name to *this file*?" |
| Lifetime | Permanent | Minutes, then closed |
| Existing routes | `/data/intake`, `/data/intake/[group]`, `/mapping/[feed]` | none yet |

**The decision: keep both, and make the seam explicit.** The register is the *queue*; the
run is the *job*. Neither subsumes the other, and merging them would break one of the two
questions. Concretely:

- **Feed-level configuration** — group name, medallion tiers, bronze extensions, supporting
  document — stays on `/data/intake/[group]`. It outlives every run.
- **File-level decisions** — profile, G1, landing, mapping, G2, promotion — move to
  `/runs/[uploadId]/[step]`. They are per-run and irreversible.
- **The group's stage badge** (`Stage : …` on the group view) is a *rollup of its runs*. It
  is a summary, never a control. Clicking an object opens that object's run.
- `/uploads/[uploadId]` — today's single long page — becomes a **redirect** to the run's
  canonical step. The URL stays alive; the page stops being a place.

---

## 2. The layers, and what each one actually guarantees

Four planes, three medallion bands. The analyst never sees the plane names, but every
screen's honesty depends on getting them right.

### 2.1 The planes

| Plane | What it is | Where it lives | What the UI may claim |
|---|---|---|---|
| **Control plane** | Uploads, profiles, interpretations, approvals, runs, mapping versions, previews | Postgres `workflow` tables, FastAPI in `api/routers/` | Everything the UI states as fact |
| **Data plane** | Bronze and Silver Raw tables, quarantine tables | Postgres, via `dataplane/pg.py` behind `dataplane/port.py` | Only via `RunCounts` and profiles — never queried directly |
| **Intelligence plane** | `interpret_file`, `recommend_mapping` LangGraph graphs, the LLM | `intelligence/` | Always labelled as belief, never as fact |
| **Knowledge plane** | Glossary, canonical model, domain rules | `knowledge/*.yaml` via `knowledge/provider.py` | Citations — **GAP: no HTTP surface, see §6.5** |

The trust ladder in `ClaimKind` is exactly the boundary between plane 1 and plane 3. That
is why it is the visual spine of every review screen: it tells the analyst *which plane a
sentence came from* without her reading the sentence.

### 2.2 The bands

**Landing band — preservation.**
`POST /api/uploads` writes the original to `incoming/{domain}/{source}/{feed}/{date}/{file}`
*before* the record commits, then enqueues `profile_upload`. Nothing reaches the data plane.
The analyst's decision here is G1, and until she makes it the file is inert.

**Bronze band — verbatim append.**
`PipelineRunner.land_bronze` (`engine/runner.py:113`) opens the run, writes lineage, sets
`LANDING`, **commits** — so a crash always leaves a run that explains itself — then parses
and appends every row unchanged as a `BronzeRow`. Balance is `records_in == records_out`,
strictly. If it doesn't balance the runner rolls back and raises `LandingFailure`. There is
no unbalanced-but-landed state, and Bronze is append-only, so re-landing adds a batch and
leaves the earlier one intact (`LANDED → LANDING` is legal — `workflow/states.py:49`).

> **CORRECTION — mine, shipped earlier this session.** I labelled a landed group
> `Dq Applied`, on the reasoning that landing types and screens rows. It does not.
> `land_bronze` performs **no typing, no rules and no rejection**; quarantine is written
> only by `promote_silver` (`engine/runner.py:215–273`). The lifecycle vocabulary in
> `frontend/lib/lifecycleStage.ts` has been corrected: `landed → "Landed"`, and
> `Dq Applied` now means *a completed `promote_silver` run* — the first moment the approved
> mapping's rules have actually run against every row. `Promoting` was added for the run
> in flight. This keeps the reference screen's word and gives it the true meaning.

**Silver Raw band — mapping applied.**
`promote_silver` replays the batch through the approved mapping version: rows that satisfy
the spec are written to one or more canonical entities, rows that don't go to
`{feed}_quarantine` with their rule and mapping version. Balance is
`in == out + quarantined + drops`. One feed legitimately populates several entities, so
"what landed" is a **list**, not a number (`Lineage.silver_tables`).

**Silver ODS — not on this build.** `medallionTiers.ts` marks the tier `planned`; the tier
list posts with the intake form and the control plane ignores it. Say so on screen.

### 2.3 The two gates

| | G1 | G2 |
|---|---|---|
| Screen | S2 Review | S5 Mapping & Preview |
| Endpoint | `POST /api/uploads/{id}/approve` \| `/reject` → 202 | `POST /api/feeds/{feed}/mapping-versions/{v}/approve` → 202 |
| Authorises | Minting a batch and writing Bronze | Freezing a mapping version and queueing promotion |
| Irreversible? | **Reject is terminal** — `REJECTED: frozenset()`, no legal transition out. A rejected file must be re-uploaded | Approval freezes the version; changes mean a new version derived from it |
| Refuses when | Already decided (409), not `interpreted` (409) | Already approved (409), superseded (409), no preview of the *current* spec (409 + hint) |

Violet (`--gate`) appears at these two places and nowhere else in the product. That is the
whole meaning of the colour; spending it anywhere else spends it everywhere.

---

## 3. The seven screens: adopt, reuse, build

`S0` ships as-is. `S1–S7` are new routes under `app/runs/[uploadId]/`.

| Step | Route | Reuses today | New | Data source |
|---|---|---|---|---|
| **S0 Add File** | `/data/intake/new` | **unchanged** — `AddIngestionForm`, `FileDropzone`, `Combobox`, `MedallionTierEditor`, `Modal` | — | `POST /api/uploads` |
| S1 Processing | `/runs/[id]/processing` | `UploadProgress`, `StatusWord`, `Kpi` | `usePoll`, facts-during-wait panel | `GET /api/uploads/{id}/progress` + `GET /api/uploads/{id}` |
| S2 Review — G1 | `/runs/[id]/review` | `ClaimCard`, `EvidenceList`, `GateActions` | `VerdictCard`, `ReadingMode`, `ReasoningLine`, `GateChecklist` | `UploadDetail.profile` + `.interpretation` |
| S3 Landing | `/runs/[id]/landing` | `Kpi`, `StatusWord` | `BalancePanel` | `UploadDetail.runs[kind=land_bronze]` |
| S4 Bronze Review | `/runs/[id]/bronze` | `DataTable`, `TableToolbar`, `Pagination` | reconciliation panel, `FieldStatus` filter | `/batches/{b}/bronze-profile`, `/proposal`, `/rows` |
| S5 Mapping — G2 | `/runs/[id]/mapping` | `MappingStudio`, `PreviewPanel`, `ApproveMapping`, `StartDraft` | `GateChecklist`, disabled-reason renderer | mapping-versions endpoints |
| S6 Promoting | `/runs/[id]/promoting` | `BalancePanel` **(same component as S3)** | version attribution line | `GET /api/batches/{batch_id}` |
| S7 Silver Raw | `/runs/[id]/silver` | `LineageChain`, `Kpi` | entity list, quarantine rollup | `GET /api/lineage/{batch_id}`, `/quarantine` |

**Only S0's exit changes**: `app/actions.ts` currently redirects to `/uploads/{id}`; it
redirects to `/runs/{id}/processing`. And the `409` from `POST /api/uploads` — which
already carries `{message, fingerprint, upload_id}` (`api/routers/uploads.py:88`) — stops
being an error string and becomes a choice: *"Open that run"* (we have the id) or *"Choose
a different file"*.

**Structural rules carried from the artifact, worth repeating because they are what make
the screens honest:**

1. Facts render while the LLM is still running. Profiling takes ~1s; interpretation takes
   30–60s. Never show dead time.
2. Facts and inferences never share a container. One card, one `ClaimKind`.
3. A confidence number without a **check** is decoration. Every model statement renders
   through the four slots: `claim` / `basis` / `check` / `consequence`.
4. The check must be satisfiable **on the current screen**. If it isn't, the screen is
   missing a panel.
5. Never disable a control without printing why beside it. Every 409's `message` and `hint`
   render verbatim.
6. S6 reuses S3's balance component exactly. She has learned that screen; don't teach a
   second one.

---

## 4. Design system: the dark→light conversion is already done

**Finding: there is nothing to convert.** The artifact's tokens are lifted from
`frontend/app/globals.css`, and its `:root` light palette is identical to ours —
`--paper:#ffffff`, `--ink:#000000`, `--acc:#1d4ed8`, `--cite:#0f766e`, `--gate:#6d28d9`,
`--ok:#15803d`, `--proc:#a45200`, `--danger:#b91c1c`. The artifact merely *defaults to
dark* (`:root:not([data-theme="light"])`). Our app defaults to light and opts into dark.

So the brief — white backgrounds, black text — is satisfied by building with the tokens we
already have. No re-palette, no per-screen overrides. Concretely:

| Semantic role | Token | Where it may appear |
|---|---|---|
| Gate awaiting you | `--gate`, `--gate-weak` | G1 and G2 **only** |
| Governed knowledge | `--cite`, `--cite-weak` | `.tag.cite`, citations |
| Model inference | `--acc` | confidence bars, inference cards |
| Recommendation | `--proc`, `--warn-weak` | recommendation cards, `Rediscover layout` |
| Balanced / completed | `--ok`, `--ok-weak` | balance panels, `Completed` |
| Failure / rejection | `--danger`, `--danger-weak` | `.alert.error`, adverse stages |

### New CSS blocks required

`.run-rail` · `.run-step` (seven dots, three bands, `aria-current="step"`) ·
`.verdict-card` (sticky ≥1100px) · `.reading-mode` (`role="radiogroup"`) ·
`.reasoning-line` (four-slot grid) · `.balance-panel` · `.gate-checklist` ·
`.recon-table`. Everything else — cards, tags, KPIs, alerts, tables, the shell — exists.

### What stays out

No new visual primitive. The artifact's own closing note is right: *"Nothing in this design
invents a visual primitive the repo doesn't already have."* If a screen seems to need one,
the screen is wrong.

---

## 5. Navigation and IA changes

```
app/runs/[uploadId]/
  layout.tsx        # RunShell — server. Fetches UploadDetail once, resolves the
                    # canonical step, guards the URL, renders the rail.
  processing/page.tsx
  review/page.tsx
  landing/page.tsx
  bronze/page.tsx
  mapping/page.tsx
  promoting/page.tsx
  silver/page.tsx
lib/runStep.ts      # RUN_STEPS + canonicalStep(upload, runs)
lib/usePoll.ts      # generalised from UploadProgress.tsx
```

- **`canonicalStep()` is a guard, not a suggestion.** No URL may show a step ahead of the
  control plane. Ask for `/runs/x/silver` on an `interpreted` upload and you land on
  `/runs/x/review`. This is the single rule that keeps a bookmarked or shared URL honest.
- **Breadcrumbs**: `Pipeline › Ingestion › {feed} › {filename} › {step}`. `breadcrumbsFor()`
  in `lib/navigation.ts` gains a `/runs/` branch; the feed crumb links back to the group.
- **Two steppers, two meanings.** `GroupStageTabs` (Configuration / Map to domain /
  Schedule / Publish) describes *the feed's configuration surfaces*. `RunRail` (seven dots)
  describes *one file's journey*. They must not look alike — the rail is dots-and-bands, the
  tabs are underlined labels — or the analyst will read one as the other.
- **The register gains a "waiting for you" filter** — see §6.4; derivable client-side today.

---

## 6. Backend compatibility

### 6.0 What exists and who consumes it

| Endpoint | Method | Feeds |
|---|---|---|
| `/api/uploads` | POST 202 | S0 |
| `/api/uploads` | GET | register, group view, worklist rollup |
| `/api/uploads/{id}` | GET | S1–S3 (status, profile, interpretation, approvals, **runs incl. `batch_id`**) |
| `/api/uploads/{id}/progress` | GET | S1 poll |
| `/api/uploads/{id}/approve` · `/reject` | POST 202 | **G1** |
| `/api/batches/{id}` | GET | S6 poll, S4 |
| `/api/batches/{id}/bronze-profile` | GET | S4 reconciliation |
| `/api/batches/{id}/proposal` | GET | S4 proposed mapping |
| `/api/batches/{id}/rows` | GET | S4 bronze rows (PHI-masked) |
| `/api/batches/{id}/quarantine` | GET | S7 |
| `/api/lineage/{batch_id}` | GET | S7 — includes `gates.G1` / `gates.G2` |
| `/api/feeds/{feed}/mapping-versions` (+`/{v}`, `/diff`, `/preview`, `/approve`) | | S5 / **G2** |
| `/api/queue/depth` | GET | ops |

PHI masking is applied **in the API** (`mask_facts`, `mask_row` in `workflow/models.py`),
not in the browser. The UI states that as a fact because it is one.

### 6.1 Progress stops at the gate — real, but **not blocking**

`Stage.key` is `Literal["profile", "interpret", "gate"]` and `UploadProgress` has no
`batch_id` (`workflow/models.py:430,436`). `isUploadInFlight` excludes `approved` and
`landing` (`frontend/lib/statusWords.ts:40`).

> **CORRECTION to the source blueprint.** The blueprint marks this **blocking** for S3–S7.
> It isn't. `PipelineRunner.land_bronze` opens the run and **commits before any data moves**
> (`engine/runner.py:122–136`), so `GET /api/uploads/{id}` returns
> `runs[kind="land_bronze"].batch_id` from the instant landing starts — and
> `isUploadInFlight` is a constant in *our* code, not the API's. S3 through S7 can be built
> today against the detail endpoint.

What the ~25-line patch actually buys: a cheap poll target (the detail endpoint returns
profile + interpretation + approvals on every tick) and a timeline that doesn't lie by
ending at the gate. Worth doing — just not first.

```python
# workflow/models.py
key: Literal["profile", "interpret", "gate", "land"]
batch_id: str | None = None          # set once the land run exists
```

### 6.2 No retry endpoint — **GAP**

`LEGAL_TRANSITIONS` permits all three retries — `profile_failed → profiling`,
`interpret_failed → interpreting`, `land_failed → landing` (`states.py:51–53`) — and
nothing exposes them. Until `POST /api/uploads/{id}/retry → 202` exists, a transient
failure means re-uploading the file, and every "Retry" button in S1/S3 is a lie. **Ship the
endpoint or don't draw the button.**

### 6.3 Promotion has no progress endpoint — **GAP (degrading)**

S6 polls `GET /api/batches/{id}`, which builds a full `BatchDetail` — run + lineage +
approvals + upload + bronze profile + proposal (`api/routers/batches.py:27`) — every
1500 ms. A `/api/batches/{id}/progress` mirror would cut the payload by an order of
magnitude. Not blocking; wasteful.

### 6.4 No cross-run worklist — **GAP (derivable)**

The register computes group stage client-side from `GET /api/uploads`. That works and is
what ships. `GET /api/worklist` — uploads at `interpreted` plus approvable mapping versions
— would make *"3 runs are waiting for you at a gate"* one cheap call instead of N.

### 6.5 A failed promotion cannot be re-queued — **GAP (new, not in the blueprint)**

S7's copy says *"the mapping version stays approved; re-queue the promotion once the cause
is fixed."* There is no way to do that. `approve_mapping_version` is the only thing that
enqueues `promote_silver`, and calling it again returns
`409 "{feed} v{version} is already approved"` (`mapping_versions.py:349`). The dedupe key
even includes the `approval_id`, so a manual re-enqueue would be swallowed. Either add
`POST /api/feeds/{feed}/mapping-versions/{v}/promote` (idempotent per batch), or the S6
failure copy must say the truth: *a new version is required*.

### 6.6 The knowledge plane has no HTTP surface — **GAP (new)**

`governed_knowledge` claims are supposed to render `.tag.cite` **with a link into the
glossary term**. There is no knowledge router — `app.py` mounts health, uploads, batches,
mapping_versions, queue and nothing else. Today the citation can name its source but cannot
link to it. Either add `GET /api/knowledge/terms/{term}` (the provider and canonical model
already exist under `knowledge/`), or render citations as text and don't imply a link.

### 6.7 Summary

| Gap | Severity | Cost | Order |
|---|---|---|---|
| 6.1 land stage + batch_id | improves S1/S3 | ~25 lines | after S2 |
| 6.2 retry endpoint | **blocks a button we'd otherwise draw** | ~40 lines | with S3 |
| 6.5 re-queue promotion | **blocks copy we'd otherwise write** | ~50 lines | with S6 |
| 6.6 knowledge surface | blocks a link | ~80 lines | with S2 or defer |
| 6.3 batch progress | payload only | ~30 lines | any time |
| 6.4 worklist | latency only | ~40 lines | with personas |

---

## 7. From one persona to a persona-based platform

The blueprint is deliberately analyst-only: *"One job, done well."* That is right for the
first release and wrong as an end state. The extension rule:

> **One flow, four vantage points. Not four products.**
> Personas do not get different screens. They get different **authority** and different
> **evidence density** on the same screens. The trust ladder is shared, because "how much of
> this came from a model?" is everyone's question.

### 7.1 The personas

| Persona | Owns | Decides | Evidence they need | Surfaces |
|---|---|---|---|---|
| **Analyst** (Monica) | One file, end to end | **G1, G2** | Facts, claims, balance, preview | S0–S7 — *ships first* |
| **Data steward** | The knowledge plane | Glossary terms, canonical model, PHI policy | Which claims cited which term; where a term is used | Glossary pages, canonical browser, citation back-references |
| **Platform engineer** | The queue and the workers | Retry, re-queue, replay | Queue depth, failed runs, worker errors, run history | OpsHub — run list, failure triage |
| **Consumer** (analytics lead) | What Silver contains | Nothing — read-only | Freshness, lineage, entity row counts, quarantine rates | Catalog / Explore |

The top nav already names four of these areas (Catalog, Design, OpsHub, Explore) and every
one is currently inert with a stated reason. Those reasons become the persona roadmap.

### 7.2 The mechanism

`lib/persona.ts` — capability predicates, not routes:

```ts
type Persona = "analyst" | "steward" | "engineer" | "consumer";
interface Capabilities {
  canDecide: (gate: "G1" | "G2") => boolean;
  canRetry: boolean;          // needs §6.2
  canEditKnowledge: boolean;  // needs §6.6
  evidenceDensity: "verdict" | "evidence" | "forensic";  // seeds ReadingMode
}
```

Three properties this design must keep:

1. **A persona never hides a fact.** It changes what is *actionable*, never what is *true*.
   A consumer sees the same balance panel; they simply cannot retry it.
2. **Anything a persona cannot do renders inert with a stated reason** — the pattern already
   used throughout this console for Schedule & Monitoring, Publish, Bronze extensions and
   Delete object. Consistency here is the whole point: "greyed out" already means "we know,
   and here's why" in this product.
3. **Persona is not authentication.** There is no auth on this build. Until there is, the
   persona selector is a *local preference that changes emphasis*, and the UI must say so.
   It must never be presented as an access control, because it isn't one.

### 7.3 Where AI shows up per persona

| Persona | Live today | Not on this build |
|---|---|---|
| Analyst | `interpret_file` (claims + confidence), `recommend_mapping` (field proposals) | — |
| Steward | grounding: claims cite `knowledge/*.yaml` | term suggestion, drift detection on the canonical model |
| Engineer | — | failure narration ("this parse failed the same way 4 times this week") |
| Consumer | — | semantic search over the canonical model |

Everything in the right column is marked *not on this build* on screen, not omitted. An
empty panel that could be mistaken for "clean" is the failure mode this platform is
designed against — the same reasoning that keeps a quarantine panel off S4.

---

## 8. Build plan

Each phase is independently shippable and independently useless to skip.

**Phase 0 — foundations.** `lib/runStep.ts` (`RUN_STEPS`, `canonicalStep()`), `lib/usePoll.ts`,
`app/runs/[uploadId]/layout.tsx` (RunShell), `components/run/RunRail.tsx`. `/uploads/[id]`
becomes a redirect. `actions.ts` redirects into `/runs/{id}/processing`.
*Accept:* every existing link still resolves; no URL shows a step ahead of the control plane.

**Phase 1 — S1 + S2.** The two screens that carry the product.
*Accept:* facts render while the LLM is still running; verdict is computed from
`ProfileFacts`; reject's irreversibility is stated **before** the button; every claim
carries `basis` and `check`.

**Phase 2 — backend §6.1 + §6.2, then S3.**
*Accept:* balance is `in == out`; the screen never claims landing types or rejects anything;
Retry exists because the endpoint does.

**Phase 3 — S4.**
*Accept:* reconciliation first; sample statistics labelled as such (`is_sample`); the
`FieldStatus` filter defaults to what needs a decision; **no quarantine panel**.

**Phase 4 — S5.**
*Accept:* every disabled G2 prints its reason verbatim; a stale preview blocks approval and
says why.

**Phase 5 — backend §6.5, then S6 + S7.**
*Accept:* S6 renders the *same* `BalancePanel` component as S3; both approvals appear on the
lineage chain with approver and timestamp; replay semantics are stated before the button.

**Phase 6 — §6.3, §6.4, and the register's "waiting for you" filter.**

**Phase 7 — persona scaffolding** (§7.2), then one persona's surface at a time, steward first
(it needs §6.6 and unblocks citations for the analyst too).

### Cross-cutting acceptance, checked every phase

- Only the seven status words render — nothing reaches `.status.unbound`.
- Violet appears at G1 and G2 and nowhere else.
- Every poll cancels on unmount and keeps its last good value through a 502.
- Every 409 renders its `message` and `hint` verbatim.
- `aria-current="step"` on the rail; `aria-live="polite"` on every polling region.
- Light is the default and the only theme the screens are designed in; dark stays a token
  swap with no per-screen rules.

---

## 9. Open decisions — these are yours, not mine

1. **Register granularity.** It lists one row per upload; the reference screens list one row
   per group. I bridged it by linking GROUP NAME to the group view. Refactor to true group
   rows, or keep upload rows and add a group filter?
2. **`Dq Applied` on the group view.** Now means *promotion completed*, per §2.2. If the
   reference intended it to mean *landed*, that intent conflicts with what `land_bronze`
   does and I would rather change the word than the meaning.
3. **Retry and re-queue (§6.2, §6.5).** Ship the endpoints, or cut the buttons and say the
   truth in the failure copy? I recommend shipping them — a data platform where a transient
   failure costs a re-upload will not survive 300 feeds at fifteen-minute intervals.
4. **Persona timing.** Scaffold `lib/persona.ts` now so the capability seams exist, or ship
   the analyst flow whole and retrofit? I recommend scaffolding now and exposing nothing —
   retrofitting authority checks across seven screens later is the expensive path.
5. **Auth.** Personas are cosmetic until there is one. Is auth in scope for this build?
