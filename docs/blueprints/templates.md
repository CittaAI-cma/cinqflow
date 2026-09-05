# CINQFLOW Working Foundation — Templates

Canonical shapes for artifacts, knowledge, jobs and reports. These are contracts:
code that produces or consumes these shapes validates against them (pydantic on the
backend). Companion to `features.md`, `structure.md`, `checklist.md`.

Conventions used throughout:
- ids: `upload_id`/`proposal_id`/… are UUIDs minted in code; `batch_id` is short hex;
  file fingerprints are `sha256-<first 32 hex>` and unique.
- every artifact carries `created_ts` (UTC) and an explicit `status`.
- timestamps are `timestamptz` UTC everywhere.

---

## 1. Workflow artifacts

### 1.1 Upload record
```yaml
upload_id: "6f0c…"                # uuid
fingerprint: "sha256-9b1c04…"     # UNIQUE — exactly-once
filename: "_CINQDOWNSTATE_Member_Roster_202606.csv"
file_type: csv                    # csv | xlsx
size_bytes: 4183321
uploader: "info@cittaai.com"
source_system: "fidelis_downstate"
feed: "roster"
domain: "enrollments"
business_date: "2026-06-01"
landing_key: "enrollments/fidelis_downstate/roster/incoming/2026-06-01/_CINQDOWNSTATE_Member_Roster_202606.csv"
status: received                  # received | profiling | profiled | interpreting |
                                  # interpreted | approved | rejected |
                                  # profile_failed | interpret_failed
created_ts: "2026-09-02T12:00:00Z"
```

### 1.2 Deterministic profile (immutable; `profile_id = sha256(facts)[:32]`)
```yaml
profile_id: "c41a…"
upload_id: "6f0c…"
profiler_version: "1"
facts:
  sheets: [{ name: "Sheet1", rows: 51230 }]      # xlsx only
  row_count: 51230
  columns:
    - name: "MemberID"
      inferred_type: string       # closed vocab: string|int|decimal|date|timestamp|bool|code
      null_count: 0
      distinct_count: 51230
      sample_values: ["100234", "100235"]         # bounded, PHI-masked in UI
      patterns: { numeric_ratio: 1.0, date_ratio: 0.0, code_like: false }
      phi_candidate: false
  candidate_keys: [["MemberID"]]
  duplicate_rows: 0
  phi_candidates: ["First_Name", "Last_Name", "DOB"]
profiled_ts: "…"
```

### 1.3 AI interpretation (versioned against one profile)
Every claim is labelled by kind and carries evidence. Never free text alone.
```yaml
interpretation_id: "…"
upload_id: "…"
profile_id: "c41a…"               # what it reasoned over
version: 1
status: draft                     # draft | superseded
provenance:
  prompt: "interpret_file@3"      # prompt name @ version
  model: "<LLM_MODEL env>"        # exact model id used
  knowledge: ["sources/fidelis_downstate.yaml@2", "glossary.yaml@5"]
claims:
  - kind: inference               # observed_fact | governed_knowledge | inference | recommendation
    field: likely_domain
    value: "enrollment"
    confidence: 0.93
    evidence: ["column:MemberID", "column:LOB", "pattern:DOB is date 99.8%"]
  - kind: inference
    field: likely_grain
    value: "one row per member per month"
    confidence: 0.71
    evidence: ["candidate_key:[MemberID]", "filename:_…_202606"]
risks:
  - "DOB null in 0.2% of rows — quarantine or default?"
unknowns:
  - "Column 'LOB' code set not confirmed against glossary"
```

### 1.4 Approval decision (append-only; used at G1 and G2)
```yaml
approval_id: "…"
gate: G1                          # G1 | G2
artifact_type: interpretation     # interpretation | mapping_version
artifact_id: "…"
artifact_version: 1
decision: approved                # approved | rejected
approver: "info@cittaai.com"
note: "Grain confirmed with payer."
decided_ts: "…"
```

### 1.5 Mapping proposal (Stage 3, AI-produced)
```yaml
proposal_id: "…"
feed: "roster"
batch_id: "a3f9c12d4e01"
status: proposed                  # proposed | folded_into_draft | dismissed
provenance: { prompt: "recommend_mapping@2", model: "…", knowledge: [ "canonical/enrollment.yaml@4", "mappings/approved/molina_ny_roster.yaml@1" ] }
fields:
  - source: "DOB"
    target: "member.date_of_birth"     # MUST exist in canonical model — validated by code
    transform: { op: parse_date, format: "MM/DD/YYYY" }
    confidence: 0.95
    evidence: ["glossary:DOB", "approved:molina_ny_roster.DOB→date_of_birth"]
    status: candidate                  # candidate | ambiguous | unknown
  - source: "Plan_Cd"
    target: null
    status: unknown                    # no defensible candidate — never guessed
    evidence: ["no canonical match", "not in glossary"]
```

### 1.6 Mapping version (Stage 4 — the analyst-owned spec)
```yaml
feed: "roster"
version: 2
status: draft                     # draft | previewed | approved | superseded
derived_from: 1                   # v(N+1) provenance; null for v1
origin: { proposal_id: "…" }      # or analyst_created
spec:                             # THE CONSTRAINED REPRESENTATION — data, never code
  target_table: "silver_raw.members"
  fields:
    - source: "MemberID"
      target: "source_member_id"
      cast: string
      on_null: reject             # reject | default | pass
    - source: "DOB"
      target: "date_of_birth"
      cast: date
      transform: { op: parse_date, format: "MM/DD/YYYY" }
      on_null: { default: null }
    - source: "Gender"
      target: "gender"
      cast: string
      value_map: { "M": "male", "F": "female", "U": "unknown" }
      on_unmapped_value: quarantine
  # allowed transform ops (Stage 4 scope): parse_date, trim, upper, lower, concat,
  # substring, value_map, cast. Anything else fails validation.
```

### 1.7 Preview result (Stage 5, immutable per (version, sample))
```yaml
preview_id: "…"
mapping_version: { feed: "roster", version: 2 }
sample: { batch_id: "a3f9…", rows: 500, selector: "first_n" }
row_results:                       # bounded; full detail persisted, paged in UI
  - row_number: 17
    fields:
      - { source: "DOB", source_value: "13/45/1990", mapped_value: null,
          outcome: failure, reason: "parse_date MM/DD/YYYY failed" }
aggregates:
  rows_ok: 493
  rows_with_failures: 7
  failures_by_rule: { "DOB:parse_date": 5, "Gender:on_unmapped_value": 2 }
  null_or_invalid: { "date_of_birth": 6 }
```

### 1.8 Run / batch record (Stages 2 & 6)
```yaml
batch_id: "a3f9c12d4e01"
upload_id: "…"
feed: "roster"
kind: land_bronze                  # land_bronze | promote_silver
mapping_version: 2                 # promote_silver only
state: completed                   # received | in_progress | completed | failed
counts: { records_in: 51230, records_out: 51218, quarantined: 12, attributed_drops: 0 }
balanced: true                     # in == out + quarantined + drops; false ⇒ state failed
started_ts: "…"
finished_ts: "…"
```

### 1.9 Lineage record (append-only)
```yaml
batch_id: "a3f9c12d4e01"
chain:
  upload_id: "…"
  fingerprint: "sha256-9b1c04…"
  landing_key: "enrollments/.../processed/2026-06-01/_CINQ….csv"
  bronze_table: "bronze.roster_raw"
  mapping: { feed: "roster", version: 2 }
  silver_table: "silver_raw.members"
approvals: [ { gate: G1, approval_id: "…" }, { gate: G2, approval_id: "…" } ]
```

### 1.10 Step run (the explicit ledger; one row per generation of one step of one scope)
```yaml
step_run_id: "…"
scope_kind: upload                 # upload | batch | feed_version   (workflow/dag.py)
scope_id: "…"                      # upload_id | batch_id | "<feed>:v<version>"
step_key: land                     # profile | interpret | gate_g1 | land | analyze | preview | gate_g2 | promote
generation: 1                      # +1 when a finished step runs again (replay, re-run)
state: done                        # pending | running | done | failed | skipped
attempts: 1                        # the queue's retries of this generation
message_id: "…"                    # queue.message that carries it (null for a gate)
artifact_type: batch               # what it made: profile | interpretation | approval | batch | proposal | preview
artifact_id: "a3f9c12d4e01"
error: null                        # failure text · refusal reason · "rejected by <who>: <note>" at a gate
queued_ts: "…"                     # a `pending` row exists from the moment the message does
started_ts: "…"
finished_ts: "…"
```
Gates are steps: `running` while a person is deciding, `done` on approval, `failed` (with the
decision as `error`) on rejection - the run stopped there; `StepDef.gate` says it is a decision,
not something to re-run. `skipped` is a step that looked and declined (the scope was not in a
runnable state) or will now never run (landing after a G1 rejection). `not_reached` is not a
state: it is the absence of a row, and the `/progress` payloads say so explicitly.

---

## 2. Knowledge documents (YAML now; provider-mediated always)

Each document carries `version:` and `updated:`; provenance in AI artifacts cites
`path@version`. Graphs never read these files — only `KnowledgeProvider` does.

### 2.1 Source / feed definition — `knowledge/sources/<feed>.yaml`
```yaml
version: 2
updated: "2026-09-02"
feed: "roster"
source_system: "fidelis_downstate"
domain: "enrollments"
file_pattern: "_CINQDOWNSTATE_Member_Roster_\\d{6}\\.(csv|xlsx)"
expected_columns: ["MemberID", "First_Name", "Last_Name", "DOB", "LOB"]
cadence: monthly
notes: "Downstate NY roster; LOB codes are payer-specific."
```

### 2.2 Canonical entity — `knowledge/canonical/<domain>.yaml`
```yaml
version: 4
updated: "2026-09-02"
domain: "enrollment"
entity: "member"
target_table: "silver_raw.members"
fields:
  - { name: source_member_id, type: string, required: true,  phi: true }
  - { name: first_name,       type: string, required: false, phi: true }
  - { name: date_of_birth,    type: date,   required: false, phi: true }
  - { name: line_of_business, type: string, required: false, phi: false }
# The ONLY legal mapping targets. AI may not propose fields not listed here.
```

### 2.3 Glossary term — `knowledge/glossary.yaml`
```yaml
version: 5
terms:
  - term: "LOB"
    means: "Line of business — the payer product (Medicaid, Medicare, Marketplace…)"
    maps_toward: "member.line_of_business"
    aliases: ["line_of_business", "product"]
```

### 2.4 Approved mapping decision — `knowledge/mappings/approved/<feed>.yaml`
Written after each G2 approval (analyst decisions become future knowledge):
```yaml
version: 1
feed: "molina_ny_roster"
approved: "2026-08-14"
decisions:
  - { source: "DOB", target: "member.date_of_birth",
      transform: { op: parse_date, format: "MM/DD/YYYY" }, decided_by: analyst }
```

### 2.5 Domain rule — `knowledge/rules/<domain>.yaml`
```yaml
version: 1
domain: enrollment
rules:
  - id: enr-001
    rule: "end_date, when present, must be >= effective_date"
    on_violation: quarantine
```

---

## 3. Context assembly (per job — never the whole knowledge base)

```yaml
job: recommend_mapping
inputs:
  observations: <bronze profile facts>          # observed fact (deterministic)
  sample: <bounded, PHI-scrubbed rows>
context:                                        # selected by ContextBuilder
  source: sources/fidelis_downstate.yaml@2      # governed
  canonical: canonical/enrollment.yaml@4        # governed — the legal targets
  glossary: [LOB, DOB]                          # only terms matching observed columns
  history: mappings/approved/*.yaml where domain=enrollment
prompt: recommend_mapping@2                     # instructions only; no facts inside
output_schema: MappingProposal                  # §1.5 — structured, validated
```

## 4. Queue job payload
```json
{ "topic": "upload.interpret",
  "dedupe_key": "upload.interpret/6f0c…/profile:c41a…",
  "payload": { "upload_id": "6f0c…", "profile_id": "c41a…" },
  "enqueued_by": "api", "attempts": 0 }
```
Topics: `upload.profile`, `upload.interpret`, `batch.land_bronze`, `bronze.analyze`,
`mapping.preview`, `mapping.promote`.

## 5. API shapes (representative)

```
POST /api/uploads                     multipart: file, source_system, feed, business_date
  → 202 { "upload_id": "…", "status": "received" }
GET  /api/uploads/{id}                → upload + status + links to profile/interpretation
POST /api/uploads/{id}/approve        body: { note } → 200 approval (G1)  [409 if not interpreted]
POST /api/feeds/{feed}/mapping-versions            body: { from_proposal_id } → 201 draft v1
PUT  /api/feeds/{feed}/mapping-versions/{n}        body: spec  [409 if status != draft]
POST /api/feeds/{feed}/mapping-versions/{n}/preview → 202 { preview_id }
POST /api/feeds/{feed}/mapping-versions/{n}/approve → 200 (G2) [409 without current preview]
GET  /api/lineage/{batch_id}          → §1.9 shape
```

## 6. Test template (pytest)

```python
def test_<stage>_<behavior>():
    # arrange: real Postgres (test schema), tmp landing root, seeded knowledge fixture
    # act:     call the API / drain the worker once
    # assert:  artifact rows + states + data-plane rows; never assert on log text
```
Stage e2e tests drive the real flow: API call → worker drain → assert persisted artifacts.
LLM calls in tests: recorded/replayed fixtures by default; a marked lane hits the live model.

## 7. Stage completion report (required at the end of every stage)

```markdown
## Stage N report — <date>
FILES CHANGED: <actual paths>
RUNTIME FLOW: <what actually executes, end to end>
TESTS: <command run> — <pass/fail counts, verbatim>
REMAINING GAPS: <known, listed>
ASSUMPTIONS: <each one stated>
UNKNOWNS: anything not establishable from repository evidence is listed as
          "UNKNOWN FROM REPOSITORY" — never fabricated.
```
