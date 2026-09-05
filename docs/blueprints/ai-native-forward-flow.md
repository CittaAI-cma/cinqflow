# The AI-Native Forward Flow — every step done inside the platform

> Companion to `analyst-forward-flow.md` (the S0–S7 spine, trust ladder, ReasoningLine, Run Rail)
> and `forward-flow-adoption.md` (planes, bands, gates, personas). This document adds the AI
> capability that belongs on each screen of **create ingestion group → publish**, names the
> mechanism behind it, and closes every point where the incumbent's analyst left the product.
> Ground truth for "today" is the 29 Apr 2026 DigitalUrth demo
> (`docs/blueprints/datalake-demo-walkthrough/`). Clickable prototype: the
> *CINQFLOW AI-Native Forward Flow* canvas (three artboards: prototype, capability map, agent
> architecture). Nothing here changes the rules already stated in the two parent specs; it
> applies them.

---

## 0. What the demo showed the analyst doing outside the product

| Step in the demo | Where she went | Why |
|---|---|---|
| Add ingestion → Base Path | Azure portal | to copy a blob prefix into a text field |
| Map to domain → Input | Notepad++ (`cinq.txt`) | the mapping requirement was written and kept there, then pasted |
| Profile / Silver review | Excel | to look at the actual source values |
| After publish | Databricks Catalog Explorer | to see whether tables and rows landed |
| Testing | Downloads → `bronze_validation_report.html` | validation was a separate Python script |
| Open questions (ZIP 5 vs 9, name suffix, BCDA vs CCLF) | Teams | no place in the product to record or resolve a decision |
| Reader inferred "Has Header: No"; codes typed as numbers | — | the platform inferred, the analyst had no evidence to disagree with |

Each row is a defect by the standard already set in `analyst-forward-flow.md` §0:
*"Anything she'd otherwise go looking for is a defect."* The rest of this document is the fix,
screen by screen.

---

## 1. Principles that bound every capability below

1. **Deterministic before AI.** Code computes facts (fingerprint, sniff, profile, counts,
   balance, validation). The model reasons over persisted facts and small samples and writes
   *structured* proposals. (`features.md`, global.)
2. **One `ClaimKind` per container.** `observed_fact` · `governed_knowledge` · `inference` ·
   `recommendation`. The analyst can always tell how much of a screen came from a model.
3. **Every proposal ships as a `ReasoningLine`** — `claim · basis · check · consequence`. The
   `check` must be satisfiable on the current screen. A confidence with no check is decoration.
4. **AI is never the record.** A proposal becomes authoritative only at a gate, under a name.
5. **Overrides are learning.** Every correction is written to
   `knowledge/decisions/analyst_decisions.yaml` as a governed precedent (who, when, what) and
   applied deterministically next time as `precedent:` — the model is not re-asked what a
   human already decided.
6. **Gates never advance on defaults.** G1 waits for every field decision; G2 waits for every
   SME item. Auto-approval exists only as a named, off-by-default, per-group policy with
   explicit tolerances that a steward turns on.
7. **Nothing leaves the platform.** Landing browser, requirement editor, sample viewer,
   validation-as-a-step, tracked decisions.

---

## 2. Screens

Each entry: analyst question · what today's screen makes her do · AI capability · mechanism ·
trust-ladder kinds present · what she decides · backend surface (existing → needed).

### 2.1 Register — arrivals and cadence

**Question** — *"What has arrived, and what should have?"*

**Today** — a list of groups with a typed STAGE. Nothing says a new file landed; nothing says
an expected one did not.

**Capability**
- **Landing watcher.** The platform lists the connection it already owns
  (`azure_blob / my-test-bucket`). Unattached arrivals appear in an *Arrivals* panel with the
  path read from storage — never pasted.
- **Arrival triage.** For each arrival the agent proposes *attach as batch to group X* /
  *create new group* / *ignore*, with basis (header-hash match n/32, filename series, size vs
  last batch), check (register row: last batch date and cadence) and consequence (profiling
  starts; stops at G1).
- **Cadence learned per group.** `Expected` and `Missing` are computed from arrival history,
  not typed. The seven status words stay the only vocabulary.
- **Register filter accepts a question** ("groups with a failed validation this week") and
  answers only with facts — it is a query planner over the artifacts table, not a chat.

**Mechanism** — `watch_landing` worker (list, sha256 fingerprint, header hash, size) →
`Arrival` artifact → `triage_arrival` (LLM, structured output) → `recommendation` claim.
Cadence: median inter-arrival on completed batches; `Missing` after cadence + tolerance.

**Kinds on screen** — `observed_fact` (arrivals, hashes, cadence) · `recommendation` (triage).

**Decides** — Attach · New group · Ignore. Nothing is profiled until she clicks.

**Backend** — exists: `POST /api/uploads`, fingerprint `UNIQUE`. Needed: `arrivals` table +
`GET /api/arrivals`, `watch_landing` worker on the queue, `POST /api/arrivals/{id}/attach`,
cadence columns on the group.

### 2.2 New ingestion group — pick the file, get the form

**Question** — *"What am I telling the platform about this file, and why does it think so?"*

**Today** — 15 fields, 6 consumed; Base Path pasted from the portal; templates from memory.

**Capability**
- **In-platform landing browser** (or drop). Selecting a file is the only mandatory act.
- **Pre-flight facts before submit** (code): rows, columns, encoding, delimiter, header test
  with the evidence shown ("line 1 has 0 numeric cells, lines 2–509 average 11"), header
  names.
- **Pre-flight inferences** in their own container: PHI likelihood on the first 50 lines;
  schema-hash match to an existing group ("this may be a new batch — attach instead").
- **Proposed fields** with provenance styling from `globals.css` (`.tag.proposal` dashed,
  `.tag.edited` violet): source system (`governed_knowledge`, from `source_systems.yaml`),
  domain (`inference`, header→canonical match count), business date (`inference`, filename
  token M.DD.YY confirmed by the previous batch), group name (`recommendation`, convention),
  description (`recommendation`, `desc_source: llm`). Each has a *why* that opens the
  ReasoningLine; overriding turns the field violet and records a precedent.
- **"How it will run"** stays collapsed with the honest margin note from
  `analyst-forward-flow.md` S0; values proposed from precedent (n of m groups in this
  environment use the set) and marked *recorded now, applied when scheduling ships*.
- **Duplicate checksum** renders as a route to the existing run, not an error.

**Mechanism** — `preflight` (code) → `propose_intake` (LLM over facts + `source_systems.yaml`
+ glossary + `analyst_decisions.yaml`).

**Kinds** — `observed_fact` · `governed_knowledge` · `inference` · `recommendation`.

**Decides** — confirm or correct each proposed field. CTA: **Profile this file** (lands
byte-for-byte, infers the reader, profiles).

**Backend** — exists: `AddIngestionForm.tsx`, `platformCatalog.ts`, upload endpoint. Needed:
`GET /api/landing?connection=…&prefix=…` (list), `POST /api/preflight` (sniff on the first
N KB), `propose_intake` worker, `Proposal(kind="intake")` artifact.

### 2.3 Objects & readers — inferred reader, conventional writer

**Question** — *"Did the platform read the file the way I would, and where will it land?"*

**Today** — Reader/Writer drawer; "Has Header: No" inferred wrongly; Bronze table named by
hand (`_1`…`_6` suffix drift); Post-SQL typed for JSON flattening.

**Capability**
- **Reader from the bytes, with the test shown**: file type (RFC 4180 quoting on n/n lines),
  header (all-alphabetic first line matching k canonical names), delimiter (exactly 31 commas
  per line; 0 tabs), encoding (fact). Each is a proposal the analyst can flip.
- **Writer from convention**: schema `bronze_<domain>` (`governed_knowledge`,
  `canonical/naming.yaml`), table `<source_system>_<feed>` with a live collision check against
  Unity Catalog, load mode **Append — governed, not editable**, control columns listed
  (`batch_id · feed_name · ingest_ts · ingest_year/month/day · created_ts · updated_ts ·
  corrupt_record`).
- **Schema drift vs last batch** as a badge ("0 columns changed · 32/32 names and order").
- **Multi-object groups** (D0284): Coverage / ExplanationOfBenefit / Patient recognised as
  FHIR R4 resources from the first record; one Bronze table each; **flattening views drafted
  as Post-SQL** (`d0284_claim_header`, `_line`, `_adjudication`, …) and shown for review
  before being stored on the writer.
- **Profiling progress is a step ledger**, polled from the control plane; a stall reads
  `Needs Attention` with the worker's last line, never a bare spinner.

**Mechanism** — `sniff_reader` (code) · `name_target` (rule) · `schema_drift` (hash diff) ·
`draft_postsql` (LLM, FHIR-schema-aware, output is SQL text held as a proposal).

**Kinds** — `observed_fact` · `governed_knowledge` · `recommendation`.

**Decides** — accept reader / rename target / edit SQL. A reader correction re-profiles.

**Backend** — exists: `ObjectsTable.tsx`, `GroupStageTabs.tsx`, profiler worker. Needed:
reader proposals on the upload artifact, catalog name check, `draft_postsql` worker.

### 2.4 Profile — facts, then understanding

**Question** — *"What is in this file, and what does the platform think each column is?"*

**Today** — good field profiles; types inferred as numbers for codes and ZIPs (the validation
report later showed 126,369 cast losses); Excel opened to see values; a colleague asked on
Teams what a column means.

**Capability**
- **Facts table** (code): role group, null %, unique, min/max, sample values inline — the Excel
  trip disappears because the values are on the screen.
- **Understanding** (model, separate container per field): role (identifier / demographic /
  contact / eligibility / pcp / control), **type proposal with the cast-loss rule**
  (identifiers, codes, phones and ZIPs stay `string` even when every value is digits;
  4-digit-only "dates" are years), PHI classification citing `dq/phi.yaml`, candidate key,
  *expected null* from glossary (address line 2 is sparse by nature — not a defect), and
  **diff vs last batch**.
- **Ask about this file** — a question is compiled to SQL over the profiled rows; the SQL is
  shown and the answer is an `observed_fact`, never an estimate.
- Field drawer = one `observed_fact` card + one claim card (`inference` or `recommendation`)
  with Accept / Override.

**Mechanism** — `profiler` (Spark; whole file ≤ 8 MB, sample above) → `ProfileFacts` →
`interpret_file` (LLM → claims + signals in ReasoningLine shape) · `ask` (NL→SQL, executed by
the worker, result persisted).

**Kinds** — all four, never mixed in one card.

**Decides** — accept / override role and type claims. Nothing here writes data.

**Backend** — exists: profiler, `interpret_file`, `ClaimCard.tsx`, `EvidenceList.tsx`.
Needed: type-proposal rule set in the interpreter prompt (digit-ratio + length facts already in
`ProfileFacts`), `ask` endpoint with SQL echo, batch diff.

### 2.5 Data source review — Gate G1

**Question** — *"Would I sign my name to this layout?"*

**Today** — grid with PK / nullable / description / PII; glossary term *Not mapped* on every
field; Confirm failed with `DB_6001` (pgvector missing) and the raw stack trace surfaced in a
toast.

**Capability**
- **Every property is a claim with a basis** shown in the row: PK from profile (508/508
  unique), glossary term proposed and cited (`glossary.yaml`), PII level with policy basis,
  description with `desc_source`.
- **Accept all with precedent** — one action for the fields a prior file from the same source
  already settled (`precedent:` chips, green, from `analyst_decisions.yaml`).
- **Overrides are recorded** as precedent with name and date.
- **Errors explained in analyst words** with a retry; the stack trace goes to OpsHub for the
  engineer (failure narration), never into the analyst's toast.
- **Gate box states the consequence**: Approve lands the file to Bronze as-is (append, 508
  rows + 9 control columns), records the 32 field decisions as the source layout, unlocks
  mapping; writes nothing to Silver Raw. Approve stays disabled while any row is undecided.

**Mechanism** — `recommend_layout` (LLM over profile + glossary + decisions) · precedent lookup
(deterministic, keyed by source_system + column) · `narrate_failure` (LLM over worker log,
engineer persona).

**Kinds** — `governed_knowledge` · `inference` · `recommendation`.

**Decides** — Approve G1 → land to Bronze · Reject file.

**Backend** — exists: `GateActions.tsx`, approval artifact, `land_bronze`. Needed:
`decisions` writer, precedent lookup, glossary matcher, error narration in the engineer
surface (`forward-flow-adoption.md` §7.3).

### 2.6 Map to domain — Gate G2

**Question** — *"Is this the mapping I would have written, and does it balance?"*

**Today** — requirement kept in Notepad++ and pasted; LLM spec (56 mapped, 33 *skipped per
user request*); DAG generated; ZIP 5-vs-9 and suffix questions left for Teams; nothing
previewed before publish.

**Capability**
- **Requirement drafted in the platform** from the canonical model for the domain
  (`canonical/enrollment.yaml`), the profile facts, the glossary, precedents from the same
  source, and `transformations/*.yaml` (name parser, address parser). Sources shown as
  evidence chips.
- **Edit in English, line by line, with a diff.** The column spec and the Designer graph
  regenerate; nodes the analyst has edited directly are `edited` and survive regeneration.
- **"Not provided by source" with evidence** (column absent in every batch of this source)
  replaces *skipped per user request*. It is pipeline documentation and emits nothing at
  runtime — the point Steve made at 25:06 in the demo, made structural.
- **Needs SME** items are tracked decisions: options, a recommended default, an owner, a due
  date, and *Ask steward*. Resolving one (ZIP → `zip` + `zip4`) regenerates the affected
  nodes and records decision `D-nnnn` under the analyst's name. G2 waits while any is open.
- **Designer** — every node carries the requirement lines it implements, its unit tests
  (name parser cases) and the rows it expects on the sample.
- **Preview on sample** — the generated pipeline runs on the profiled rows in the worker; the
  **balance equation** (in = out + dropped + quarantined) and a slice of each target table are
  shown. This is the fact signed at G2.

**Mechanism** — `draft_requirement` (LLM) → `recommend_mapping` (LLM → structured spec,
existing) → codegen (deterministic, spec → DAG JSON) → `preview_run` (worker on sample) ·
`decisions/*.yaml`.

**Kinds** — `observed_fact` (preview, balance) · `governed_knowledge` (canonical, glossary,
precedent) · `recommendation` (spec lines, SME defaults).

**Decides** — Approve G2 → freeze this mapping version and open Publish & validate.

**Backend** — exists: `recommend_mapping`, `MappingStudio.tsx`, `ProposalTable.tsx`,
`PreviewPanel.tsx`, mapping versions. Needed: `draft_requirement` worker, English-edit →
spec regeneration, `Decision` artifact with owner/due/default, codegen to the runner's
pipeline JSON.

### 2.7 Publish & validate — the harness is a step

**Question** — *"Did the first batch land exactly as approved, and when does the next one
run?"*

**Today** — publish history with two hashes; validation a separate script whose HTML report is
opened from Downloads; defects found after the load.

**Capability**
- **Validation runs before Silver is written**, as the existing five checks: record count,
  field count, key presence, field-value comparison (including type-cast losses), PHI
  handling. Results are `PASS / WARN / FAIL` badges in the app.
- **A failure returns as a fix on the screen that caused it** ("map MEMBER_ELIG_END_DT to
  eligibility_end_year (int) — basis: 219 four-digit values, 0 dates — check: Profile min/max")
  with a link to that screen.
- **Publish history with generated change notes** (what changed in the spec since the last
  version), editable.
- **Schedule proposed from observed cadence** (`inference`, basis = the arrival dates, check =
  the register), accepted or edited; once accepted the group shows `Expected`/`Missing`
  automatically and the watcher attaches the next batch and **always stops at G1 with a diff**.
- **Auto-approve policy** is a design note on the screen: per group, named, logged, off until a
  steward enables it, with explicit tolerances (schema hash equal, null profile ±5 pts, row
  count ±20 %).

**Mechanism** — the existing Python validation harness as a queue worker · `narrate_change`
(LLM over spec diff) · cadence inference (code).

**Kinds** — `observed_fact` (checks, versions) · `inference` (cadence) · `recommendation`
(fixes).

**Decides** — accept schedule · fix or waive a WARN · publish.

**Backend** — exists: publish versions, Databricks `PipelineRunner` JSON. Needed: validation
worker + `Validation` artifact, promotion progress endpoint (`forward-flow-adoption.md` §6.3),
schedule on the group, retry/re-queue (§6.2, §6.5).

---

## 3. The agents (narrow, structured, replaceable)

| Agent | Reads | Writes | Kind it may emit | Screen |
|---|---|---|---|---|
| `triage_arrival` | Arrival facts, group layouts | attach / new / ignore + ReasoningLine | recommendation | 2.1 |
| `propose_intake` | pre-flight facts, `source_systems.yaml`, glossary, decisions | field proposals | governed_knowledge · inference · recommendation | 2.2 |
| `draft_postsql` | first record per NDJSON object, FHIR R4 schema | view SQL as proposal | recommendation | 2.3 |
| `interpret_file` (exists) | `ProfileFacts`, sample | claims + signals | inference · recommendation | 2.4 |
| `ask` | question, profiled rows | SQL + result | observed_fact (result) | 2.4 |
| `recommend_layout` | profile, glossary, decisions | PK / nullable / PII / term / description proposals | governed_knowledge · recommendation | 2.5 |
| `draft_requirement` → `recommend_mapping` (exists) | canonical model, profile, precedents, glossary, transformations | requirement text → column spec, SME items | recommendation | 2.6 |
| `narrate_change` · `narrate_failure` | spec diff · worker log | change note · explanation | (never a record) | 2.7 · OpsHub |

Every agent: structured output only, `ClaimKind` on every statement, `ReasoningLine` shape,
cites a persisted fact or a knowledge file, and is idempotent per artifact version so a retry
never produces a second proposal.

---

## 4. Data that must exist for the loop to learn

- `arrivals` — id, connection, path, sha256, size, header_hash, seen_at, attached_run_id.
- `groups.cadence` — median interval, tolerance, next_expected_at; `auto_approve_policy`
  (nullable JSON: enabled_by, enabled_at, tolerances).
- `decisions` (`knowledge/decisions/analyst_decisions.yaml`, mirrored as a table) — id
  `D-nnnn`, scope (source_system, column or target column), decision, options offered,
  chosen, owner, decided_at, due_at, cites.
- `proposals` — already first-class; add `kind ∈ {intake, reader, layout, requirement,
  mapping, schedule, fix}` and `precedent_id` when a value came from a decision rather than a
  model.
- `validations` — run_id, check, result, detail, fix_proposal_id.

---

## 5. What stays out (deliberately)

- No free-text chat as the primary surface. The home-page prompt box in the incumbent is
  replaced by action chips that open the flow at the right screen; questions inside the flow
  are compiled to facts (`ask`) or become tracked decisions.
- No agent may advance a gate. Auto-approve is a steward policy, not an agent choice.
- No new status words. `Expected · Received · Processing · Completed · Needs Review · Needs
  Attention · Missing`.
- No persona-specific screens. Personas change authority and evidence density on the same
  screens (`forward-flow-adoption.md` §7).

---

## 6. Open decisions

1. **Where the requirement lives.** Draft in the platform (this doc) but also export/import
   the English text so SMEs without access can review it — or keep it in-platform only?
2. **Precedent scope.** Per source_system (proposed) or per source_system × feed? Centene GA
   Risk and Centene GA Risk Update share a layout today but may diverge.
3. **Type rule ownership.** The cast-loss rule (codes / IDs / ZIPs stay string) is a
   `governed_knowledge` rule in `dq/types.yaml`, or a fixed heuristic in the profiler?
   Recommended: knowledge file, so a steward can add payer-specific exceptions.
4. **`ask` cost model.** NL→SQL runs on the profiled sample only (cheap, always available) or
   optionally on the Bronze table (needs compute)? Proposed: sample by default, Bronze on an
   explicit "run on full batch".
5. **Validation as gate.** Should a `FAIL` block publish outright, or publish `landing_bronze`
   and hold `bronze_silverraw`? Proposed: the latter — Bronze is append-only and harmless;
   Silver waits for green.
