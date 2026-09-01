# CINQFLOW — Domain & Decisions Guide

This is the knowledge-transfer document for a developer joining the build. It
is not the architecture — that is generated from `atlas.html` into
[`docs/architecture/`](architecture/) and is authoritative there — and it is
not the ADR log — that is [`docs/adr/`](adr/) plus the programme's decision
record. This document is the middle layer: **what business the platform is
in, what the healthcare data actually looks like, and why the non-obvious
parts of the code are shaped the way they are.** Read this before your first
PR; read the ADRs when you need the forces behind a specific decision.

Status: written 2026-09-01 against `main`. The programme's full context store
— why decisions were made, what the client's estate contains, incident
history, open questions — lives one level up at `../../memory/`; start with
its `MEMORY.md` for anything this file doesn't answer.

---

## 1. What CINQFLOW is, and the business it serves

CINQCARE is a **value-based-care (VBC) organization** covering roughly
**150,000 attributed lives** across ten payer populations (Fidelis NY, Centene
IL/GA, UHC MD, ACO REACH, Optum NY/GA, Molina NY, MSSP, Wellpoint DC). About
**118K of those lives sit under full-risk contracts** — meaning CINQCARE
carries the financial risk for the cost and quality of that population's
care. **The data platform's purpose is the contracts**: if identity
resolution is wrong, the same member gets counted twice or not at all; if
claims lineage is wrong, the financial reconciliation that the contracts are
paid against is wrong. This is why identity resolution and financial
reconciliation are treated as core platform guarantees, not features.

CINQFLOW replaces an incumbent vendor product (**Digitalurth**, at
`ui.dataplatform.cinq.care`) that CINQCARE depends on today for onboarding,
running and troubleshooting its healthcare data feeds. The outcome the whole
programme is judged against:

> Analysts can onboard a standard healthcare feed through a guided five-step
> process, define data-quality rules in plain English, validate mappings and
> rules using sample data, and publish an approved configuration. CINQFlow
> then processes the data from Landing Zone through Silver ODS, while the
> internal team can monitor, reconcile, troubleshoot, and reprocess it with
> minimal dependency on data engineers or external product teams.

Two words carry the weight: **ownership** (CINQCARE runs it, not a vendor)
and **self-service** (a trained analyst, not an engineering ticket).

### What CINQFLOW automates, concretely

The product removes two recurring jobs:

- **Data Analyst.** Today: asks an engineer what a number means, re-derives
  "coverage loss" by hand, cannot tell whether a dashboard is answering from
  stale data. With CINQFLOW: a certified definition is a governed object with
  a steward and a version; a per-domain reliability score answers "can I
  trust it today?"; a copilot answers from the control plane with citations.
- **Data Engineer.** Today: reads dozens of overnight batch runs by
  timestamp, hunts logs across systems, does control-row surgery to allow a
  replay, re-learns the same failure every quarter. With CINQFLOW: failures
  are fingerprinted against a recovery library with prior-occurrence
  statistics; recovery is a governed button with an audit trail; a drop
  ledger makes silent row loss structurally impossible.

### Scope boundaries — what CINQFLOW is *not*

- **Not a replacement for the client's lakehouse.** The Medallion layers, the
  11 control tables, the batch lifecycle, the Verato identity integration —
  these stay as designed. CINQFLOW builds *beside* the existing estate, never
  inside it or in place of it.
- **Does not host production.** The client owns the tenant; the build team
  never hosts. It ships as an installer with an uninstall path.
- **Never writes production state from an agent.** Agents propose; humans
  approve, always, for anything identity- or PHI-consequential.
- **Holds no PHI in development.** Every byte of development data is
  synthetic, generated from real layouts, never real members.
- **Gold is not designed yet.** The MVP spine ends at Silver ODS.

---

## 2. The data domain — what actually arrives

### Source systems, by clinical domain

| Domain | Source systems |
|---|---|
| Enrolments | Fidelis NY (upstate & downstate) · Molina NY · Optum NY · Centene GA/IL · Athena · Medent · ACO/CMS (D0284, MSSP) |
| Daily Census / ADT | Fidelis · 7 HL7 HIE feeds via Mirth/MuleSoft (BronxRHIO, HiBridge, HealtheLink, Health Connection, PCC National, PCC IL) · Particle Health (API) · Garage (daily) |
| Claims | Centene (Fidelis NY, Centene GA/IL) · Molina · OptumECG · CMS (ACO REACH, via BCDA) |
| Enhanced Roster | Clinigence, for Fidelis NY / Molina NY / Optum NY / Centene IL |

Delivery methods the platform must normalize: **SFTP, API, FHIR, database,
Azure storage, file upload, streaming** — all through one universal landing
contract (a file appears in `incoming/` matching a registered pattern).
**Fidelis alone has 26 production file patterns** (header + line pairs across
IP/OP/Dental/Professional/Vision, plus Pharmacy, Membership, Member-PCP), all
full-snapshot every month. The largest known feed is **~22M rows/month**
(Fidelis downstate enrollment); claim volumes reach 24–34M+ lines.

### The onboarding complexity ladder

Feed formats step up in this order, and the test/simulator infrastructure
climbs it alongside the real story work:

1. Delimited txt/csv (Centene GA rosters)
2. Excel with quirks (Fidelis rosters — filenames beginning with `_` once
   broke the Excel reader; see the incident library below)
3. Multi-file header/line sets (Fidelis claims, 26 patterns)
4. NDJSON FHIR (BCDA EOBs — non-trivial claim lineage semantics)
5. HL7-derived JSON (7 ADT sources, each with per-source quirks)
6. Fixed-width (CCLF)

### Volume and change rate is a real cost lever

Measured across recent deliveries: `enrollment_fidelis_downstate_newyork`
moves **22.0M rows/cycle at a 0.68% change rate** — meaning a full load
re-transfers ~150K rows' worth of actual change every time. This single
example (22M → 150K) is the platform's cost thesis: a **Processing Strategy
Engine** watches change rate against volume and proposes snapshot-diff or
watermark strategies per feed. It proposes; it never switches a production
feed without a steward's approval — a one-flag, reversible change.

### Known structural gaps in the source data — carry these into any design

- **`pcp_npi` is not extracted by any Mirth HL7 channel.** Only the Optum UM
  and Fidelis UM flat-file feeds provide it. This is a "CRITICAL GAP" in the
  client's own documentation.
- **Six ADT feeds carry no stable member id** (Bronx, Particle, HiBridge,
  Optum UM, Fidelis UM, Garage). Each resolves identity today via a *live SQL
  lookup into the legacy Cinq DB* during Mirth processing — a runtime
  coupling to the system CINQFLOW is meant to decommission, invisible in the
  lake's own lineage.
- **A02 transfer events are not captured**, so encounter location history is
  permanently empty for HL7 sources.
- **Per-source ADT quirks are real and numerous** (BronxRHIO's misspelled
  `Discharge_Diposition` field, Garage's non-standard date format,
  HiBridge's quote-stripping, field-name variants like `cinqID`/`CINQID`).
  These must be **reviewable configuration**, never buried code.
- Historical HL7 archive location is unknown; BCDA raw claims exist only
  from January 2025. Longitudinal analytics are truncated unless this is
  resolved before the legacy system is decommissioned.

These are documented, out-of-programme-scope gaps in the client's own Mirth/
MuleSoft layer — CINQFLOW's job is to make them *visible and measured*
(coverage telemetry, drift sentinel change requests), not to silently work
around them.

---

## 3. The pipeline — layers, gates, and the guarantees that hold it together

### The five layers (never abbreviate, never reorder)

```
Landing → Bronze → Silver Raw → Identity → Silver ODS → (Gold, undesigned)
```

Landing is the **control entry point**: structural validation only, no
semantic validation. Every arriving file is registered — including
unexpected ones, which are parked and surfaced, never discarded. Bronze is
**append-only, enforced at the database layer** (an `UPDATE`/`DELETE` is
refused and logged, not merely discouraged by convention).

### The five gates — data cannot advance a layer until its gate passes

| Gate | Between | Checks |
|---|---|---|
| **G1 structural** | Landing → Bronze | filename, size, structure, fingerprint vs. registry, arrival SLA, idempotency |
| **G2 schema + DQ** | Bronze → Silver Raw | drift classified **by meaning**, contract enforced, DQ rules by severity |
| **G3 completeness** | Silver Raw → Identity | record-level reconciliation, every drop attributed |
| **G4 resolution** | Identity → Silver ODS | `submitted == resolved + unresolved + failed`; unresolved never loads |
| **G5 certification** | Silver ODS → Gold | relationship validation, consumer compatibility, atomic publish |

### The balance equation — the platform's central invariant

```
rows_in == rows_out + quarantined + attributed_drops
```

Proven every stage, every batch. The corollary: **no drop category may ever
be named `"other"` or `"unknown"`** — this is a schema-level constraint, not
a style guide. The historical incident that motivated it: `member_provider`
silently lost rows where `pcp_npi` was null, understating rosters with no
trace. The drop ledger exists so that can never happen invisibly again.

### The 11 control tables — the client's existing framework, kept as-is

All joined on **`batch_id`**, the one key that threads arrival, execution,
failure and reconciliation end to end: `feed_sla_config`, `input_registry`,
`schema_registry`, `schema_drift_log`, `batch_control`, `batch_stage_status`,
`error_log`, `quarantine_records`, `batch_reconciliation`, `sla_instance`,
`sla_alerts`. This is **genuinely good, existing design** — CINQFLOW is built
on top of it, never a replacement for it. The deterministic error hash
(`error_log.error_id_hash = hash(batch_id, stage_name, record_key,
error_type, rule_id)`) is what makes "reprocess only the failed records" a
safe, ordinary button instead of a control-row surgery session.

### The seven words a user ever sees

`Expected · Received · Processing · Completed · Needs Review · Needs
Attention · Missing` — no synonyms, no per-screen dialects, enforced by a
lexicon test. The richer internal state machines (batch: `RECEIVED →
VALIDATED → IN_PROGRESS → COMPLETED`, plus `FAILED · RESTARTED ·
WAITING_DEPENDENCY · BLOCKED`) are never shown to users directly.

---

## 4. The canonical model — entities, keys, and the rules that are easy to get wrong

### Members — the spine

`Members` (PK `OurId`, matched via Verato `LinkId`) holds **SCD-1** history
(current state only). Its satellites (`Members_Addresses`,
`Members_Phones`, `Members_Provider`, `Members_Risk`, etc.) each carry their
own record GUID and hold **SCD-2** (effective-dated) history.

### Identity resolution — the crosswalk, and the three scenarios that must match 100%

`bridge_member_source_to_verato` links `source_member_id` (the payer's own
id) to `internal_member_id` (`Cinq_id`) via `verato_person_id`. Full
Verato request/response payloads are stored with hashes — **the audit trail
is the design's core, not an add-on.**

Three worked scenarios form the golden set for identity resolution
(`CF-V3-E9-03`):

1. **Normal update** (two payers reporting the same person) → one `CinqId`,
   no SCD-1 change; an address change closes the old row and opens a new one.
2. **Merge** (`L200 → L100`) → the losing id is marked `MERGED_TO_C1`;
   satellite rows **repoint and dedup** (two identical addresses collapse to
   one).
3. **Split** (`L100 → L100 + L300`) → a new `CinqId` is created; satellite
   rows are **reassigned**, with the reassigned row's start date set to the
   split day.

**Merge and split are R4 — a human steward, always, at any confidence.**
There is no autonomy path for this, ever. The preview shown to the steward
must list every affected record (a hidden record is a defect class), and
post-change verification must match the preview in 100% of executed
decisions. "Unresolved identity never loads" — those records wait, visibly,
in an exception queue rather than being force-loaded.

### Claims — the lineage rule, stated once so it's implemented once

CCLF claims give the adjustment type explicitly (original/cancellation/
adjustment). **BCDA FHIR v4 does not** — a claim is an **evolving event
lineage** that must be derived from `EOB.related.relationship`:

| FHIR signal | Derived type | Normalized payment |
|---|---|---|
| no related claim | `ORIGINAL` | **+**payment |
| `relationship = prior` (or `replaces`) | `ADJUSTMENT` | **+**payment |
| `relationship = replacedby` | `CANCELLATION` | **−**payment |

The worked example every implementation must match: an original, its
cancellation and its adjustment at `+500 / −500 / +450` **net to $450**. Any
financial figure computed without that netting is wrong by construction.
**Status is not lineage** — `EOB.status` (active/cancelled/entered-in-error)
is the operational state; `relationship` is the evolution chain; they are
stored separately, never conflated. Team guidance, verbatim: *do not update
prior claims in place — append immutable claim events, derive latest state
dynamically, normalize financial signs.*

### ADT / encounters — an event log, not a snapshot

One row per HL7 message, duplicates retained and flagged (`has_conflict`),
with current state computed over the full history. Two documented gaps to
design around: `has_conflict` deliberately does not auto-resolve and has no
owner today; `encounter_location_history` is permanently empty because A02
transfer events are not captured anywhere upstream.

### The five model rules that matter everywhere

1. Surrogate keys are generated; **source identifiers are always retained**
   beside them.
2. History per entity is declared explicitly: `current_only` or
   `effective_dated`.
3. Dedup precedence is **logged**, and the losing value is **retained in
   history**, never discarded.
4. **Unresolved identity never loads.**
5. Every batch records the model version it loaded into.

---

## 5. Data quality, PHI and the business glossary

### The 110 legacy DQ rules and 171-term glossary are ground truth, not invented

Both are harvested directly from the client's own data model workbook and
serve **two roles at once**: they are the golden set an AI capability is
graded against, and they are the grounding (K2 knowledge) that capability
retrieves from at runtime. Same artifact, examined once, reused forever — no
test set is ever authored for the occasion of testing something.

- **110 DQ rules**, each pairing a natural-language description with an
  **executable SQL validation query** and a glossary link — the exact shape
  the natural-language → rule agent must reproduce. Dimensions: Completeness
  30, Validity 30, Consistency 19, Timeliness 10, Accuracy 10, Integrity 7,
  Uniqueness 4.
- **171 glossary terms**, 29 of them PHI-flagged. Those 29 drive the masking
  policy, and the PHI-detection gate is **100% recall** — because *missing*
  PHI is the failure that matters, not a false positive. Changing a PHI flag
  requires steward approval with a negative test proving it.

### DQ rules — the model chooses a check, never writes SQL

The natural-language rule agent does not emit SQL or PySpark. It selects a
**check kind from a closed, harvested vocabulary** (the 110 rules' own
sub-dimensions: Mandatory Field, Code Set, Format, Intra-Record, Referential,
Range, Uniqueness) plus scalar parameters — and the platform renders the SQL,
the PySpark and the row predicate deterministically from that. This makes
injection structurally impossible (identifiers are checked against a closed
vocabulary, never escaped and interpolated) and means a rule the vocabulary
cannot express is a *type*, not a filtered-out case — it routes to a
technical-review queue rather than silently failing to generate.

A null passes every check *except* `NOT_NULL` — conflating absence with
invalidity would make one missing field fail two rules and break the balance
equation's arithmetic. Both the analyst's original sentence and the
generated explanation are stored side by side, because where they disagree,
that disagreement is the signal something is wrong — collapsing to one text
would hide it.

### PHI detection — the asymmetry is deliberate

Schema inference, faced with a column it cannot settle, refuses to guess and
asks a human. PHI detection does the opposite: faced with the same
uncertainty, it **classifies the column as PHI** and routes it to a steward.
Both are the safe answer to "we don't know" — a field typed wrong is caught
by the next load; a field left unmasked wrong is a disclosure. The detector
is **never shown an actual value** — only column names, integer statistics,
pattern hit-rates and glossary definitions — because an agent whose job is
deciding what to protect should not need to read the protected data to
decide it. Accepting a PHI classification and *clearing* one are two
different, separately-permissioned actions (author vs. steward) — an early
draft folded them into one route and made the steward path unreachable.

### Masking — driven by one flag, nothing else

A cell is masked if and only if its contract's `is_phi` flag says so — never
by column name pattern-matching, never by a regex over values. That flag is
a governed contract term requiring steward approval. The masked value object
carries no original and no shape (no `J•••`-style partial reveal, which was
tried and reverted because it let a reader re-identify a member from a
roster they already held) — just `•••`. Masking is not a permission tier:
read-only and steward roles get byte-identical masked output.

---

## 6. Governance — the lifecycle every governed object obeys

Everything with meaning in the registry — source, feed, schema contract,
mapping, DQ rule, glossary term, runbook, release — moves through **one
state machine**, no exceptions:

```
Draft → In Review → Approved → Published → Retired   (+ Paused, for feeds)
```

Two rules are structural, not conventions:

- **A different person must approve than authored.** An author cannot
  approve their own change — refused and logged, always.
- **A named approver is required before Published.** No anonymous or
  implicit approval path exists.

**Save is permissive; activation is not.** A half-gathered feed configuration
must be saveable — an analyst waiting days on a payer's response needs
somewhere to keep partial work. Validation only bites at *submission*
(`core.lifecycle.submit` refuses an incomplete readiness checklist). This
was learned the hard way: validating at save-time is how a registry fills up
with placeholder values like `owner@example.com`.

**Pause is a second axis, not a lifecycle state.** A naive reading of the
feed lifecycle (Draft → Active → Paused → Retired) suggests adding `PAUSED`
to the state machine. This is wrong: resuming would then need a named
approver (finding a steward at 3am to un-pause something), and "which
version was live in March" would incorrectly answer "none" for any week the
feed was paused. `PUBLISHED` *is* Active; pause lives as a separate,
append-only suspension record.

### Roles — eight today, seven named as the eventual requirement

The client's MVP objective names **seven** platform roles (Business Analyst,
Data Steward, Data Engineer, Operations, Approver, Administrator,
Read-Only). The codebase's `Role` enum has grown to these plus a split of
"Approver" into technical and business variants, added exactly when a
routing table or a failing test named the gap — never speculatively:

| Wave added | Roles | Why |
|---|---|---|
| Wave 0 | `ENGINEER`, `READ_ONLY`, `ADMINISTRATOR` | the smallest set that makes the Read-Only server-side denial testable |
| Wave 1 | `BUSINESS_ANALYST`, `DATA_STEWARD`, `PLATFORM_ENGINEER`, `BUSINESS_APPROVER` | approval routing "with one approver role routes nothing" — a first draft tried overloading `ENGINEER` as the approver and a test correctly failed: the engineer builds and runs, the platform engineer signs off |
| Wave 2 | `OPERATIONS` | eight of Wave 2's eleven stories are written "As an Operations [person]", deliberately not the feed's author |

**The standing rule survives every count change: never add a role to make a
screen look complete.** Each addition traces to a routing table or a failing
test, not an org chart. Scopes (source/feed/domain/environment-level RBAC)
are a separate, still-open axis.

### Releases and drift

Configuration promotes DEV → PROD **byte-identical**, substituting only
declared connection-profile parameters. Anything in PROD not traceable to a
release opens a **drift incident** automatically — there is no logged,
sanctioned path for a direct PROD edit outside an emergency change process.

---

## 7. The AI / agent layer — how intelligence is allowed to touch the platform

### Risk class gates capability; confidence only routes within it

```
R0 observe · R1 safe-op · R2 config proposal · R3 code PR · R4 human-always
```

**No confidence value ever raises a risk class.** An agent scoring 99%
confident on an identity merge still cannot execute it — R4 (identity /
PHI-consequential decisions) is human-always, unconditionally, with no
autonomy path, ever. This is the single most important guardrail in the
system: it means "the model was very sure" is never an argument for
skipping a human.

### The access boundary the intelligence plane cannot cross

Agents may **read everything**. They may **write only** to
`proposals.* · knowledge.* · ops.* · forecasts.* · audit.agent_action`. They
may **never write** to `control.*`, `bronze.*`, `silver_raw.*`,
`silver_ods.*` or `gold.*` — no agent has a path to mutate pipeline data,
structurally, regardless of what any prompt says.

### The call pipeline — one order, every agent

```
context_assembly → phi_scrub → prompt_assembly → llm_gateway → schema_validation → action_gateway
```

`phi_scrub` runs **before any prompt is assembled**, with its own test on the
ordering. `action_gateway` is non-bypassable: a tool whitelist per agent,
rate limits, a global kill switch that degrades all agents to R0 and returns
the platform to today's exact non-AI behavior. Every action is logged to
`audit.agent_action`.

### Certified query tools — the only way an agent reads operational data

Agents never get a database connection and never compose SQL. They call
**named, typed, read-only tools** (`get_batch`, `get_reconciliation`,
`get_drop_ledger`, `list_errors`, etc.) whose results already carry a
resolvable citation and have RBAC scope applied *inside the tool, before
execution* — an out-of-scope query returns empty with an explicit
out-of-scope marker, never a partial result or an error that reveals the
object exists. **No tool may ever emit a member-level or data-layer row** —
`get_quarantine_summary` returns counts and reasons, never the quarantined
record itself. This matters even in development, where the data is
synthetic: a tool that's safe only because the data happens to be fake is
not a safe tool.

### Golden sets, and the rule that keeps evaluation honest

**No evaluation threshold may ever be claimed from a mock or a replay.**
Quality claims come only from Lane 3: the real model, against held-out data
that already existed in the client's own artifacts before the agent that's
graded against it was written. Few-shot examples shown to a model are drawn
from these goldens only, **never invented**, and must include at least one
example of a correct refusal — so that declining is cheaper for the model
than guessing.

### The pattern every R2 agent follows (schema inference, PHI detection,
mapping suggestions, rule generation all share this shape)

1. A pure `grounding.py` answers everything the evidence already settles and
   exposes what's still open. **If grounding is empty, no model is called at
   all.**
2. A versioned prompt template in `core/agents/<name>/prompts.py` — every
   refusal spelled out as a constraint.
3. Three graph nodes, two of them deterministic, writing exactly one
   `Proposal` object.
4. **The model picks the concept; the platform spells the name.** Where an
   answer cites a glossary term, the canonical name and PHI flag are read
   from that governed term, never from the model's free text — a model
   asked to name something with no vocabulary in front of it will invent a
   plausible-sounding wrong name.
5. **Never infer a constraint from a sample.** No nulls seen in 200 rows is
   not evidence of `NOT NULL`; a guessed constraint quarantines real records
   later. Constraints come only from an explicit human declaration.

### Two hard-won lessons about the PHI scrubber, worth internalizing

The scrubber sits between every prompt and the model and is tuned for total
recall on real values — which means, over a page of pure *metadata* (column
names, not data), it produces false positives that mangle identifiers the
model is then asked to copy back verbatim. The fix that moved one agent's
gate from an unusable 0% to 100%: **number the redacted options and have the
model answer with the number**, never the mangled text. And: **a degraded
run must never look like a careful one** — when the gateway falls back to a
manual path, every result must say so explicitly (`manual_path: true`), or a
run that answered nothing because of an infrastructure failure reads
identically to a run that carefully declined every column.

---

## 8. The platform's construction discipline (the "chip")

The architecture calls the system a **chip**: `core/` holds all business
logic and imports zero vendor SDKs, URLs, paths or credentials — that's a
CI-enforced lint rule, not a convention. Every touch of the outside world
goes through a **port** (a small interface phrased as a business verb), with
a **real adapter**, a **local stand-in**, and a **mock**, all proven by one
shared contract suite. Everything that differs between environments — which
database, which model endpoint, which storage bucket — lives in one
versioned **connection profile** and nowhere else in the code.

Why this matters for the domain, not just the engineering: it's what makes
"zero PHI in development" actually true rather than aspirational — the same
core logic that runs against a mock in CI runs against the client's real
tenant in production, with nothing but the profile and the adapter set
different. A team that hardcodes against comfortable cloud resources ships a
platform welded to one tenant; the constraint (no Azure subscription of our
own, no access to the target data plane, zero real member data in dev) is
what forces the design that makes CINQFLOW installable into *any* client
environment.

Two ideas worth carrying into any new code:

- **`citation_id` is the platform's address space, not an AI artifact.**
  `recon:8842#DQ-002` is simultaneously something an agent emits, a route
  the UI resolves, and a link a person can paste into Slack. One resolver
  serves all three.
- **The compiled execution plan does three jobs at once**: the engine runs
  it, the agent explains it, and the eval harness grades against it — so the
  golden set for "does the agent's explanation match reality" is *generated*
  at zero annotation cost and grows automatically with every feed added.

---

## 9. Hard-won lessons — incidents that are now permanent regression tests

**The platform is never allowed to re-learn an old lesson.** Every
documented historical incident from the client's own operational history
became a permanent seeded-failure test, replayed on every build:

| Incident | Lesson | Now enforced by |
|---|---|---|
| A Fidelis filename starting with `_` broke the Excel reader | Filenames must start alphanumeric | A permanent landing pre-flight check |
| `member_provider` silently dropped rows with null `pcp_npi` | Silent row loss understates rosters | The drop ledger; the balance equation |
| A duplicate Feb-2025 Fidelis roster arrived twice | The same month can arrive twice | Fingerprint-based replay refusal |
| Optum data was structurally valid but semantically bad, found only after load | Structural checks pass while semantics rot | Statistical anomaly detection against learned per-feed baselines |
| Diagnoses/procedures found with missing claim IDs, discovered by a person reading data | Referential gaps surface late | Relationship validation at the G5 gate |
| Manual control-row deletion was required to allow a replay | — | A test proving no manual deletes are ever needed for a legitimate replay |

Two structural lessons sit behind all of them:

- **Cascade separation.** One upstream fault often logs as three errors, two
  of which are downstream consequences of the first. Fingerprinting must
  isolate the first actionable error, or every incident reads as three.
- **Grouping.** Five feeds missing the same cycle boundary through the same
  upstream system is *one* incident, not five — alerting must group by root
  cause, not by symptom count.

Any newly-learned failure is added to this library in the same PR that fixes
it — a fix without a seeded regression test will recur.

---

## 10. Where the build actually stands (read this before assuming a feature exists)

This section is the fastest way to avoid designing against a story that
isn't built yet, or duplicating one that already is.

**Scope is 80 stories across 16 epics**, six waves. Story counts and wave
membership come from `platformdata/CINQFLOW_User_Stories_Final.docx` plus
two ADR-driven amendments (ADR-0019 adds 3 Wave-0 AI stories; ADR-0023 adds
the delivery/`connector` pin story) — never from any other document, several
of which are stale generations of the same backlog.

| Wave | Built | Status |
|---|---|---|
| **0 — Power-On** | 13/13 | Closed. Ports, mocks, the Postgres plane, the compiler, landing controls, reconciliation, the LLM gateway, certified query tools, the Pipeline Insight agent. |
| **1 — The Analyst's Workshop** | 23/27 | Four stories open (connector's remaining delivery methods, the three-lane test harness unlabeled, half of knowledge ingestion, persona document upload — none blocks a screen). |
| **2 — The Control Room** | 11/11 | Closed; exit demo green. |
| **3 — The Spine Completes** (Verato identity, canonical ODS, hard formats) | 0 | Not started. |
| **4 — Trust at Scale** (RBAC, PHI masking depth, client-tenant socket) | 0 | Not started. |
| **5 — Migrate & Own** | 0 | Not started. |

Things that are easy to assume and would be wrong right now:

- **Silver ODS is provisioned but empty** — its schema exists, zero tables
  are populated, gated behind G4 identity resolution which isn't built yet.
- **`ACTIVE_WAVE = 0`** in `core/navigation.py` — seven built and tested
  Wave-1/2 UI destinations exist in code but are deliberately hidden from
  navigation until their wave is declared active.
- **The onboarding wizard's backend is fully built** (`core/onboarding/` +
  the `/api/feeds/{id}/onboarding`, `/infer-schema`, `/suggest-mapping`,
  `/author-rules`, `/detect-phi`, `/evidence` routes) but **no UI page calls
  any of it yet** — there is no front door to the guided journey a user
  could click through today.
- **No scheduler consumes `pg_orchestration.due()`.** The only way a
  pipeline run starts today is the `cinqflow ingest` CLI command.
- **Lane 3 (the only lane allowed to claim quality) is not currently
  stable**, and Wave 2's two AI capabilities (failure fingerprinting, alert
  enrichment) have no Lane-3 evaluation run against them yet despite the
  wave being closed on functional tests. Do not quote an evaluation number
  for either without re-running Lane 3 first.

For the exhaustive, continuously-updated version of this table — plus what
each specific open story is missing — read `../../memory/MEMORY.md` and the
programme's `06-product/00-epics-and-stories.md`.

---

## 11. Where to go next

| You need... | Read |
|---|---|
| The architecture itself — components, invariants, plates | [`docs/architecture/INDEX.md`](architecture/INDEX.md) |
| Why a specific decision was made, and what would overturn it | [`docs/adr/`](adr/) (implementation-originated) and `../../memory/02-decisions/ADR-INDEX.md` (programme-originated) |
| The always/never list enforced by tests and lints | `../../memory/03-directives/00-non-negotiables.md` |
| Real facts about the client's data estate — control tables, model, feeds, incidents | `../../memory/05-ground-truth/` |
| The 79/80-story backlog, the screens, the agent roster | `../../memory/06-product/` |
| How to do a recurring job (start a story, add a port, ship an AI feature) | `../../memory/07-runbooks/RB-00-index.md` |
| What we still don't know and must ask the client | `../../memory/08-open/00-open-questions.md` |
| Day-to-day repo layout and how to run things | [`README.md`](../README.md) |
