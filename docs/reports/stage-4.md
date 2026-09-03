# Stage 4 report — 2026-09-03

Scope implemented: **editable, versioned mapping.** An AI proposal becomes a draft the
analyst owns, edited through a constrained representation, validated against governed
knowledge at save time, immutable once approved. Deterministic preview (Stage 5) and G2
approval (Stage 6) are **not** implemented.

> **The task named Stage `<N>`** — the placeholder was not substituted. Stages 1–3 are
> complete and green, so I continued the progression with Stage 4 as defined in
> `checklist.md`. Stated here in case a different stage was intended.

---

## 0. Traces (before coding)

| Trace | Finding |
|---|---|
| Execution path | `POST /api/uploads` → `upload.profile` → `upload.interpret` → G1 → `batch.land_bronze` → `bronze.analyze` → proposal. Stage 4 attaches **after** the proposal and adds no queue work: editing is synchronous request/response, so no worker or topic was added. |
| Reuse found | `Transform` model, `Provenance`, `WorkflowStore` patterns, `ddl.statements()`, the `_canonical_for` domain-singularisation from Stage 3, and `ContextBuilder.legal_targets` (the target list the Stage 3 validator already used). |
| Boundary problem found | `legal_targets` lived in `intelligence/context.py`, but `structure.md` boundary 2 forbids `engine/` importing `intelligence/`, and Stage 4's validator belongs in `engine/mapping_spec.py`. Resolved by lifting the derivation into `knowledge/canonical.py`; `ContextBuilder.legal_targets` now delegates to it, so both sides validate against **one** list. |
| Files requiring change | listed in §1; no unrelated file touched. |

**Smallest plan executed:** shared canonical reader → constrained spec + validator in the
engine → artifact/DDL/store with the immutability guard → four endpoints → studio UI → tests.

---

## 1. Files changed

**New**
```
backend/src/cinqflow/knowledge/canonical.py     CanonicalModel: legal targets, declared
                                                types, primary keys, contested fields
backend/src/cinqflow/engine/mapping_spec.py     ALLOWED_OPS/CASTS/ON_NULL/ON_UNMAPPED,
                                                validate_spec, assert_valid,
                                                spec_from_proposal, diff_specs
backend/tests/unit/test_mapping_spec.py         (21)
backend/tests/integration/test_mapping_versions.py (11)
backend/tests/e2e/test_stage4_flow.py           (9)
frontend/app/mapping/[feed]/page.tsx            studio page + version picker + diff
frontend/app/mapping/actions.ts                 saveSpec, startDraft server actions
frontend/components/MappingStudio.tsx           the editor
frontend/components/StartDraft.tsx              seed a draft / derive the next version
```

**Extended**
```
workflow/models.py       +MappingStatus, MappingField, MappingSpec, MappingVersion
                         (extra="forbid" on both spec models)
workflow/ddl.py          +workflow.mapping_version (PK feed+version, partial unique index
                         enforcing one open draft per feed)
workflow/store.py        +create_mapping_version, update_draft_spec, set_mapping_status,
                         get/list/latest_mapping_version; +NotEditable,
                         +UnknownMappingVersion, +DraftAlreadyOpen
api/app.py               +POST/PUT/GET/GET-diff for mapping versions
intelligence/context.py  legal_targets now delegates to knowledge/canonical.py
frontend/lib/api.ts      mapping types + listMappingVersions, getMappingVersion,
                         getMappingDiff, saveMappingSpec, createMappingVersion
frontend/app/globals.css form controls, invalid-field styling
```

---

## 2. Runtime flow (verified live on the real roster proposal)

```
POST /api/feeds/{feed}/mapping-versions {from_proposal_id}
  → spec_from_proposal(): carries ONLY status=candidate fields with a legal target,
    drops contested targets (two columns claiming one field), sets each cast from the
    canonical declared type, marks every field edited=false
  → store.create_mapping_version(): next version for the feed, always status=draft
  → 201                                        [21 fields seeded from 23 candidates]

PUT /api/feeds/{feed}/mapping-versions/{n}  body = spec
  → 409 if the version is not editable (approved | superseded)
  → assert_valid(spec, canonical) → 422 with field-level errors, nothing saved
  → store.update_draft_spec(): guard re-checked in the store; a previewed version
    returns to draft, because an edit invalidates its preview
  → 200

POST … {derive_from_version: N} → draft v(N+1), spec copied, derived_from=N, vN untouched
GET  …/{n}       → version + vocabulary (legal targets, types, ops, casts, null rules)
GET  …/{n}/diff  → vs latest approved (else v(N-1)): added/removed/changed +
                   analyst_edited vs from_proposal
```

Measured against `roster_stage3` (the real 28,334-row Fidelis roster batch from Stage 3):

- **Draft v1 seeded**: 21 fields from the proposal's 23 candidates. The two contested
  identifier candidates (`member_id`, `medicaid_id`, both proposing
  `members.source_system_id`) were **not** carried — the analyst decides.
- **Invalid spec refused (422)** with five distinct field-level errors:
  `members.member_uuid` not in the model; `members.guardian_email` "contested/absent";
  `exec_python` not a supported transform; `cast 'string'` cannot satisfy a declared
  TIMESTAMP; `on_unmapped_value` without a `value_map`. The draft was unchanged afterwards
  (21 fields, no `member_uuid`).
- **Valid analyst edit saved (200)**: added `member_id → members.source_system_id`
  with `on_null: reject` and a note ("member_id is the Fidelis identifier; medicaid_id kept
  out"), and gave `member_sex` a value map `M→male, F→female, U→unknown` with
  `on_unmapped_value: quarantine`. 22 fields, `updated_ts` set.
- **Diff**: `analyst_edited: [member_id, member_sex]`, 20 still as proposed.
- **Approved v1 refused edit (409)** — "v1 is approved and cannot be edited", hinting at
  `derive_from_version`; the version list stayed `[1]`, so nothing was implicitly created.
- **Derived v2 (201)**: `derived_from: 1`, 22 fields copied, editable; v1 still `approved`;
  `GET /diff` reports v2 against v1 (approved).
- **Second open draft refused (409).**

UI verified in the browser (`/mapping/roster_stage3?v=2`): version pills, "Compared with v1
(approved)", ownership counts (2 analyst-edited / 20 as proposed), the editor with per-field
target/cast/transform/null/value-map controls, an "add a mapping the AI left unmapped" row,
and a save button. The studio offers **54 canonical targets and 8 named ops** and nothing
else — `grep` confirms zero occurrences of `members.record_hash`, `member_uuid`,
`guardian_email` or `exec_python` in the rendered page. The approved v1 page renders
"Frozen version", has **no save control**, and offers "Start v2".

---

## 3. Tests

```
$ cd backend && pytest -q
164 passed, 1 warning in 29.88s     (unit 83 · integration 51 · e2e 30; 41 new this stage)

$ ruff check src tests
All checks passed!

$ cd frontend && pnpm build
✓ Compiled successfully   (routes: /, /uploads/[uploadId], /batches/[batchId], /mapping/[feed])
```

Acceptance criteria (features.md §Stage 4), each covered by an e2e test:

| # | Criterion | Test |
|---|---|---|
| 1 | `PUT` against an approved version returns 409 and creates nothing implicitly | `test_editing_an_approved_version_is_refused_and_creates_nothing` |
| 2 | v(N+1) copies the spec and records `derived_from: vN` | `test_editing_after_approval_derives_the_next_version` |
| 3 | Non-canonical target or unsupported transform rejected on save with a field-level error | `test_an_invalid_spec_is_refused_with_field_level_errors` |

Also asserted: the spec model **forbids extra keys**, so a smuggled `python`/`code`
attribute is refused by the parser before validation; a draft seeded from a proposal is
valid by construction; contested targets are excluded from seeding; `previewed` returns to
`draft` on edit; one draft per feed; version numbering is per feed; and
`test_no_future_stage_endpoints_exist` asserts no `/preview` or `/approve` route exists
(404, and no such path in the route table).

**Three defects found and fixed during the stage:**

1. `spec_from_proposal` picked the alphabetically-first acceptable cast, so a TIMESTAMP
   column seeded `cast: date`. Now prefers the declared type (`_default_cast`).
2. `MappingVersion.origin` reported `analyst_created` for a version derived from v1, which
   is misleading. Now returns `derived from v1`.
3. My new CSS referenced three variables that do not exist in the stylesheet (`--fail`,
   `--done`, `--bg`), which would have silently dropped the invalid-field highlighting.
   Corrected to the real tokens (`--warn`, `--ok`, `--surface`).

One test of mine was wrong rather than the code: it expected 405 for the absent preview
route; an unrouted path returns 404. Corrected, and strengthened to inspect the route table.

---

## 4. Definition of Done

**"Analyst can shape a valid spec entirely in the UI"** — met. From the studio the analyst
can change any field's target (chosen from the governed list, annotated with its declared
type), change the cast, add or remove a named transform with its argument, set null handling
and a default, enter a value map and its unmapped-value rule, mark a field as their own,
drop a field, and add a mapping for a column the AI left unmapped — then save, with
per-field errors rendered against the offending rows when validation fails. An approved
version is read-only and offers to start the next one.

Checklist items: create draft v1 from proposal ✔ · spec validation with field-level errors ✔
· draft mutable, approved immutable enforced in the store ✔ · edit-after-approve creates
v(N+1) with `derived_from`, parent stays approved ✔ · UI studio with diff (vN vs latest
approved, proposal-origin vs analyst-edited) ✔ · report ✔.

No future-stage functionality: no preview execution, no G2 endpoint, no Silver write, and
`mapping_version` is never read by the pipeline runner.

---

## 5. Gaps

- **`set_mapping_status` has no endpoint and no guard on transitions.** It exists so the
  immutability rule is enforceable and testable now; Stage 5 (`previewed`) and Stage 6
  (`approved`/`superseded`) will own the transitions. Today any status string is accepted,
  and tests set it directly. A legal-transition table like `workflow/states.py` has for
  uploads is missing.
- **No cross-entity validation.** A spec may map only `members_addresses.city` without
  `address_type`, which that entity's primary key requires. Nothing catches it until a write
  is attempted. Real, and deferred: it belongs with preview/promotion.
- **`target_table` is cosmetic.** Field targets are fully qualified, and one roster spec
  legitimately spans five entities, so the single `target_table` from templates.md §1.6
  records only the primary entity. Stage 6 will need per-entity grouping to actually write.
- **No delete/discard for a draft**, so a wrong draft blocks the one-draft-per-feed rule
  until it is approved or removed by hand.
- **Value maps are strings only** (`M=male`), entered as comma-separated pairs in the UI. No
  escaping, so a value containing `,` or `=` cannot be expressed.
- **A transform takes one argument** in the UI (the op's required arg). `concat` with several
  sources, or `substring` with both start and length, cannot be expressed yet.
- **No authentication**: `created_by` is a request field, so authorship is a claim.
- `docs/blueprints/templates.md` §1.6 shows unqualified field targets (`source_member_id`)
  and `origin: {proposal_id}`; the implementation uses qualified targets and a flat
  `origin_proposal_id`. The contract doc should be reconciled.

## 6. Assumptions

- **Field targets stay fully qualified** (`table.field`), as Stage 3 produces them. Dropping
  the qualification to match templates.md literally would make a roster mapping ambiguous
  across five entities.
- **Seeding is conservative**: only `candidate` fields with an uncontested legal target are
  carried into a draft. Ambiguous/unknown/invalid entries are left out entirely rather than
  imported as blanks, so the draft is valid the moment it exists and every remaining decision
  is visibly the analyst's.
- **Editing a `previewed` version returns it to `draft`**, because the preview no longer
  describes the spec. Stage 5 must not treat `previewed` as sticky.
- **One open draft per feed**, enforced by a partial unique index — two open drafts would
  race for the next G2.
- `edited` is set by the client (the studio ticks it when a field is added or changed). The
  server does not diff to infer authorship, so a client could lie about ownership.

## 7. UNKNOWN FROM REPOSITORY

- Whether one feed's mapping is expected to populate **several** Silver Raw entities in one
  approved version (this build assumes yes, since a roster carries member, address, phone,
  email and enrollment-segment columns), or whether each entity should carry its own mapping
  version.
- How `address_type`, `phone_type`, `email_type` and the enrollment-segment key columns are
  meant to be supplied for a roster that has no such column — a constant, a derivation, or a
  per-feed default. Nothing in `docs/` says, and the canonical primary keys require them.
- Whether an approved mapping version should become governed knowledge automatically (the
  Stage 3 report noted `knowledge/mappings/approved/` as the destination); still unwired,
  and it needs a G2 event, so it belongs to Stage 6.
- The intended `batch_id` type mismatch flagged in the Stage 2 report (canonical DDL declares
  `INT`, this build mints 12-hex text) remains unresolved and will bite at Silver.
