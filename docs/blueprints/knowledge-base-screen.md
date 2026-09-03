# Governed Knowledge — a read-only screen over `knowledge/`

> UI/UX specification for the Knowledge Base surface. Read-only by design.
> Every file path, field name, rule id and value cited here was read from `knowledge/` in this
> repo. Counts were computed from the files, not estimated. Nothing is marked GAP except §5,
> which is entirely new backend surface.

---

## 0. The problem this screen solves

`knowledge/` holds **116 YAML files across 11 directory groups**, plus `manifest.yaml` and `glossary.yaml` at the root. A file browser over them would be a failure:
the analyst does not want a path, she wants to know **what governs the decision she is about to
make**. So the screen is organised by governance role, not by directory.

| The ask | Where it lives | Screen section |
|---|---|---|
| Documents that matter to CINQCARE | `sources/organizations.yaml`, `sources/source_systems.yaml` | §1 The organisation |
| Decision directives | `decisions/analyst_decisions.yaml`, `decisions/exceptions.yaml`, `intelligence/escalation.yaml` | §2 Decision directives |
| Rules | `dq/*.yaml`, `intelligence/reasoning_rules.yaml`, `transformations/*.yaml` | §3 Rules |
| Required data of the domain | `canonical/*.yaml`, `glossary.yaml` | §4 Required domain data |

### 0.1 The organising insight

`manifest.yaml` already declares `confidence_semantics` — three levels that say **how far a fact
may be pushed**:

| Level | Manifest wording | UI treatment |
|---|---|---|
| `authoritative` | "Traceable to a regulator, standards body or the payer agreement (CMS, NCQA, HL7, X12, NYS DOH). May be asserted with its citation." | `--ok` green pill |
| `conventional` | "Industry-standard analyst practice with no single authority. Present as 'standard practice is X'." | `--acc` blue pill |
| `shop_specific` | "True only for CINQCARE. **Highest operational value, highest staleness risk.** Never generalise to another tenant." | `--gate` violet pill |

This is the same shape as `ClaimKind` in the forward flow — a provenance ladder — but for
governance rather than inference. Make it the spine: **every knowledge object on this screen
carries its confidence level in a consistent place.**

### 0.2 The thesis

**The knowledge base is not a library. It is a set of commitments with expiry dates.**

`escalation.yaml` ES-09 makes this load-bearing:

> *"Volatile regulatory fact past `review_by` — surface staleness; **do not use the fact**."*

Eight files carry `review_by`. The ACO REACH model ends `2026-12-31`, taking its benchmark,
attribution and four feeds with it. Six of the seven open exceptions expire the same day; EX-005
expires `2026-10-31`. **The staleness ticker belongs in the masthead**, not buried in a table —
it is the one number on this page that changes every day and that nobody would otherwise look for.

---

## 1. Route and navigation

```
frontend/app/knowledge/page.tsx      // one page, five sections, anchor-linked
```

`lib/navigation.ts` already reserves three entries for this surface, all `NOT_BUILT`. Point them
here rather than adding a fourth:

```ts
// Pipeline section
{ label: "Knowledge Base", href: "/knowledge" },
// Data Gov section
{ label: "Glossary", href: "/knowledge#glossary" },
{ label: "Domain",   href: "/knowledge#model" },
```

One page rather than five routes, because the sections are read together — an analyst checking
whether a rule applies to her feed needs the exception register and the DQ table on the same
screen. Deep links are anchors, and the left rail is an index, not a router.

---

## 2. Visual treatment

**White ground, black text — single theme, deliberately.** The repo's light palette *is* white on
black (`--paper: #ffffff`, `--ink: #000000`), so this screen commits to it: no
`prefers-color-scheme` block, no `data-theme` stamps. Paint `background` and every colour
explicitly from tokens so the page holds on any host ground.

Colour earns its place only as semantics, never as decoration:

| Token | Means, on this screen |
|---|---|
| `--gate` violet | shop-specific knowledge · escalation · "a human decides" |
| `--danger` red | `block` action · unreconciled fact · contested field |
| `--proc` amber | `quarantine` / `warn` · PHI · staleness |
| `--ok` green | `authoritative` · live contract |
| `--cite` teal | a citation or `grounded_in` reference |
| `--ink3` grey | `observe` · system-populated · metadata |

Type: **Instrument Sans** + **IBM Plex Mono**, as `app/layout.tsx` already loads. Mono carries
every identifier, path, field name and rule id — on this screen mono means *"this is a literal
from the corpus,"* and prose means *"this is us explaining it."* Hold that line.

### 2.1 The provenance strip — the screen's signature object

Every knowledge object renders in a card whose **footer is a fixed provenance strip**:

```
sources/organizations.yaml@1   AUTHORITATIVE   owner business_analyst   updated 2026-09-03
grounded in CINQCARE_VBC_Contract_MindMap.html · NPPES · cinq.care
```

Four slots, always the same order: `path@version` · confidence · owner · updated, then
`grounded_in` beneath. This is what makes the page read as a governed corpus rather than a wiki,
and it is a direct rendering of what every file already declares. The manifest's own convention
says it plainly: **"Every file names what it is `grounded_in`. A fact without a source does not
belong here."**

```tsx
<KnowledgeCard
  path="sources/organizations.yaml"
  version={1}
  confidence="authoritative"
  owner="business_analyst"
  updated="2026-09-03"
  groundedIn={["CINQCARE_VBC_Contract_MindMap.html", "NPPES", "cinq.care"]}
>{children}</KnowledgeCard>
```

### 2.2 What "view only" means here

No create, edit, delete or approve control anywhere. But **filters are view state and belong** —
26 DQ rules and 21 directives need triage. Two filter groups, both `role="group"` with
`aria-pressed` chips, both purely client-side:

- §2 — All · Decision records · Exceptions · Escalations
- §3 — All · Block · Quarantine · Warn · Observe

Say why the screen is read-only rather than letting it look unfinished:

> **Read-only by design.** This layer is edited in the repository and reviewed in pull requests.
> The platform reads it through `KnowledgeProvider` and never writes to it — with one exception:
> `knowledge/export.py` appends to `mappings/approved/` at G2. That write is a consequence of an
> analyst decision, never a UI action.

---

## 3. Section by section

### §0 · Masthead

Six figures, all computed from the corpus:

| Figure | Source |
|---|---|
| 116 governed files | `find knowledge -name '*.yaml' \| wc -l` |
| 11 knowledge groups | top-level directories |
| 12 contracted populations | `organizations.yaml` — 10 contract rows, 12 populations |
| ~151,400 attributed lives | `tenant.total_attributed_lives` |
| 28 open directives | 9 decision records + 7 exceptions + 12 escalation rules |
| 26 enrollment DQ rules | `dq/enrollment.yaml` `rules[]` |
| **days to ACO REACH end** | computed client-side against `2026-12-31` |

The last one is computed in the browser, not stored, so a cached response can never claim
something is current when it is not. Static fallback in the HTML for the pre-JS frame.

### §1 · The organisation

**Tenant card** — CINQCARE, "Care, Where You Live", Tony Welters, Washington DC, four service
lines, ten states, eight legal entities. One copy note worth honouring platform-wide: **patients
are called Family Members.** Use the tenant's own word wherever the UI names a person.

**Contracts table** — 10 rows: contract id, payer, risk arrangement, lives, wave, analyst.
Ordered by lives descending, which puts Fidelis (64,201) first and matches the priority tracker.

**Source systems table** — 10 systems, their organisation, population, feed count, registered
count and cadence. A feed is *registered* when a loader contract exists at
`sources/<source_system>__<feed>.yaml`; the rest are declared from the client's data model because
no de-identified sample exists. Show the ratio — 10 systems, **54 declared feed entries, 11 with a loader contract** — because
that is the honest state of the corpus.

**The three disagreements panel.** This is the most valuable thing in §1 and it must not be
buried. The corpus records conflict rather than resolving it:

| | What the corpus says |
|---|---|
| ACO REACH lives | Client tracker 6,973 · CMS PY2024 PUF `BENE_CNT` 10,607. `lives_note: "Different vintages and definitions - do not reconcile silently."` |
| MSSP ACO id | No CINQ-named ACO in the public PY2025 MSSP file. Recorded as unknown, not guessed. |
| Fidelis agreement | `partnership_public: false` — the largest contract by lives has the least external corroboration. |

A knowledge base that hides its own uncertainty is worse than none. Render these as first-class
content with a `.tag.danger` marker, not as footnotes.

### §2 · Decision directives

Opens with the principle, verbatim from `escalation.yaml`:

> *"The model proposes; the analyst decides. Anything that changes a number someone has seen or
> will file, or that writes to a published table, requires a human. A plausible inference that is
> wrong costs more than a delay."*

**Decision records (9).** Each renders as a slotted record: `trigger · options considered ·
decision · rationale · downstream impact · decided by · reversibility · generalisable`. The
`decision` slot is the only one in `--ink` at weight 500 — everything else is `--ink2`. That one
typographic move makes a record scannable in a second.

Worth surfacing on the card face, not inside: `reversibility` (`reversible` /
`reversible_with_restatement` / irreversible) and `generalisable` (only DR-20260902-003, the sex
value map, is `true`). Those two fields answer "how much does this bind us?"

**Exception register (7).** Table: id, status, rule, feed, reason + proposed action, owner,
`review_by`. **All seven are `proposed`; none is approved** — say that in the section note, because a
reader will otherwise assume they are in force. Highlight EX-005 (`2026-10-31`, 58 days): it is
the nearest expiry *and* the only one whose underlying rule is a `block`.

**Escalation rules (12).** Table: id, trigger, action, owner. Close the section with
`never_autonomous` as a standalone statement, not a row:

> **never_autonomous —** "Writing to a Silver Raw, Silver ODS or published table."

One line, and it is the reason G1 and G2 exist.

### §3 · Rules

**Severity model.** Four actions with the legacy Critical/High/Medium/Low crosswalk beside them,
so a legacy rule can be traced without re-arguing its severity.

| Action | What it does | Legacy |
|---|---|---|
| `block` | Fails the cycle for this table. No promotion. Human notified. | Critical |
| `quarantine` | Rows held, counted, visible, reprocessable. Cycle continues. | High |
| `warn` | Promote with a quality flag that propagates downstream. | Medium |
| `observe` | Record and trend. The signal is in the trajectory. | Low |

**Layer defaults** and the two policies that decide how a rule is written — both from
`dq/severity.yaml`, both worth quoting rather than paraphrasing:

- `threshold_policy` — a threshold is derived from at least three months of observed behaviour per
  feed, then held as a governed constant with an owner. **Until a threshold exists the rule runs in
  observe mode and says so.**
- `failure_meaning_required` — *"'DQ-S-04 failed, discharge disposition null 22%' is engineering
  output; the analyst output names the metric affected and the likely cause."*

**The client's own rules, verbatim.** `dq/enrollment.yaml` carries `client_rules_verbatim` with
four cleanse rules and two DQ rules in the client's exact words. Give them their own treatment —
a bordered block, numbered, quoted not paraphrased — because a disagreement about intent can then
be settled against the client's actual sentence.

Then the point worth making beside them: **client DQ rule 2 ("Member Must have PCP Assigned to
him") becomes `DQ-E-07` at `warn`, not `block`** — because three feeds legitimately fail it and
two exceptions are open against it. The client's rule is preserved; its enforcement is negotiated
in the open. That is the whole governance model in one example.

**DQ rules table (26).** Columns: id, legacy id, rule, action, what a failure means. Filter chips
by action with live counts, read from the file — **block 5 · quarantine 4 · warn 11 · observe 6**.

**Model operating rules.** The always-on `RR-*` set. Thirteen of the twenty-five are worth
surfacing here; the rest are per-screen and belong in the prompt registry view. The ones that must
appear:

- RR-01 Observations are true — never contradict a deterministic profile fact; explain it.
- RR-05 Cite the governing node or `file@version` for every rule you assert. **An assertion you cannot attribute is one you do not make.**
- RR-06 A truthful unknown is worth more than a plausible guess; **a wrong mapping corrupts silently.**
- RR-08 No PHI in reasoning output.
- RR-20 Bronze never deduplicates.
- RR-23 Sentinel dates are nulls in disguise, never outliers.
- RR-24 **A plausible file with a 40% drop is the most dangerous case, because every other check passes.**

### §4 · Required domain data

Opens with the constraint, which is the point of the whole section: `canonical/*.yaml` is **the
only source of legal mapping targets**; `mappings/`, `dq/` and `glossary.yaml` reference it and
never extend it. A proposal naming a field that is not here is a validation failure, and the AI
marks it `invalid`.

Then the reasoning, from the file's own header — worth quoting because it explains why the
spreadsheets lose to the DDL:

> **Why the DDL and not the spreadsheets.** Every field was read from
> `enrollment_silver_raw_model.sql` — the executable DDL — including its declared type, its comment
> and its primary key. **Nothing here is inferred.** A target is only legal if a Silver Raw write
> could actually land in it, and the DDL is what the write must satisfy.

**Entity blocks (5).** Header carries entity name, table, grain, field count and PHI count. Body
is a wrap of field chips styled by role:

| Chip | Style |
|---|---|
| primary key | bordered, `--ink`, weight 600 |
| PHI | `--warn-weak` ground, `--proc` text |
| ordinary | `--mat2` ground, `--ink2` text |
| system-populated | strikethrough, `--ink3` |

A field can be both PK and PHI — `source_system_id` is — and the key styling wins. Say so in the
legend rather than letting the reader wonder.

Counts, read from the file (do not re-estimate these):

| Entity | Table | Fields | PHI | PK parts |
|---|---|---:|---:|---:|
| member | `members` | 17 | 12 | 2 |
| member_address | `members_addresses` | 14 | 7 | 3 |
| member_phone | `members_phones` | 4 | 2 | 3 |
| member_email | `members_emails` | 4 | 2 | 3 |
| member_enrollment_segment | `members_enrollment_segments` | 15 | 2 | 5 |

Field names that are easy to guess wrong and are not what you would guess:
`members_phones.phone_number` (not `phone`), `members_emails.email_address` (not `email`), and
`members_enrollment_segments` carries `member_group · lob · tin · tin_market · tin_submarket ·
pcp_name · pcp_npi · pay_to_zip · add_reason · control_number · control_number_pbp` — **and no
coverage dates at all.** See the contested-fields panel.

**System-populated (10).** `lsdeleted · record_hash · source_system · created_by · created_at ·
updated_by · updated_at · batch_id · secure_id · record_creation_date`. A proposal targeting one
is a validation failure, not a suggestion. Note that `source_system` appears in both lists
deliberately: it is part of every primary key *and* platform-populated, so it is supplied as an
upload constant rather than mapped from a column.

**Contested fields (6) — the panel the analyst most needs.**
`members_enrollment_segments` **declares no effective or end dates.** Every roster feed supplies
`enrollment_date` and `coverage_end_date` and there is nowhere for either to land in this
canonical version. The corpus records this under `contested_fields` rather than offering the
fields as targets, so a proposal aiming at `effective_date` is marked `invalid` rather than
quietly accepted. The other four are `guardian_first_name`, `guardian_last_name`,
`guardian_phone_number`, `guardian_email` — all mapped in
`Enrollment_DB_to_Datalake_SilverRaw_mapping.xlsx` from a `Members_Guardian` table the DDL has no
equivalent for — plus `feed_name`, where the DDL carries `source_system` only.

**A field named in a client spreadsheet is not a legal target until the DDL declares it.** Render
that sentence; it is the section's whole argument.

**Equivalences.** `gender` → `members.sex`, evidenced from the client's own mapping workbook.
Approved-mapping knowledge is recorded against the DDL name so proposals stay landable.

**Glossary.** Term → `maps_toward` → aliases → PHI. The rule that makes it honest, from the file
header: `maps_toward` is present **only** when the field actually exists in the DDL; where a roster
carries something the canonical model has no home for, the term says so via
`canonical_target: none` with a reason. Those are exactly the columns the AI must surface as
unknown rather than guess at. Note the matching rule too: case-insensitive, space treated as
underscore — so a column named `MEMBER DOB` already matches the term `member_dob`.

---

## 4. Components

| Component | Location | Notes |
|---|---|---|
| `KnowledgeCard` | `components/knowledge/KnowledgeCard.tsx` | Body + fixed provenance strip. The screen's signature object |
| `ConfidencePill` | `components/knowledge/ConfidencePill.tsx` | `authoritative` / `conventional` / `shop_specific` |
| `DecisionRecord` | `components/knowledge/DecisionRecord.tsx` | Slotted record; `decision` slot is the only emphasised one |
| `RuleTable` | `components/knowledge/RuleTable.tsx` | Sticky header, action pills, filterable |
| `EntityBlock` | `components/knowledge/EntityBlock.tsx` | Header + field chips by role |
| `FieldChip` | `components/knowledge/FieldChip.tsx` | `pk` / `phi` / `plain` / `system` |
| `StalenessTicker` | `components/knowledge/StalenessTicker.tsx` | Client-side day count against `review_by` |
| `FilterChips` | reuse the `.chip` idiom in `globals.css` | `role="group"`, `aria-pressed` |

Reused unchanged: `AppShell`, `Sidebar`, `TopBar`, `DataTable`, `CollapsibleSection`,
`TableToolbar`, `Pagination`.

---

## 5. Backend — all new surface **GAP**

`KnowledgeProvider` (`backend/src/cinqflow/knowledge/provider.py`) is a five-method Protocol keyed
by domain and feed: `get_source`, `get_glossary`, `get_canonical`, `get_approved_mappings`,
`get_domain_knowledge`. **None of them lists, and no router exposes them.** This screen needs a new
router and three new provider methods.

```
GET /api/knowledge                      manifest, counts, every review_by with days remaining
GET /api/knowledge/organisation         tenant, contracts, source systems, feeds
GET /api/knowledge/directives           decision records, exceptions, escalation rules
GET /api/knowledge/rules?domain=        severity model, layer defaults, DQ rules, RR rules
GET /api/knowledge/canonical/{domain}   entities, grain, PK, fields+types+PHI, system_populated,
                                        contested_fields, equivalences
GET /api/knowledge/glossary             terms, maps_toward, aliases, PHI
```

Three provider additions:

```python
class KnowledgeProvider(Protocol):
    ...
    def get_manifest(self) -> KnowledgeDoc | None: ...
    def list_docs(self, group: str | None = None) -> list[KnowledgeDoc]: ...
    def get_doc(self, ref: str) -> KnowledgeDoc | None: ...
```

Three properties of this surface worth asserting rather than assuming:

1. **All GET, by construction.** The layer is edited in git and reviewed in pull requests. There is
   no POST/PUT/DELETE — the screen is read-only *because the layer is*, not because a feature was
   cut. The single write path is `knowledge/export.py` appending to `mappings/approved/` at G2.
2. **No PHI masking needed.** The manifest's `conventions` guarantee it: `no_phi` — "No
   member-level values anywhere in this tree. Column names, shapes, counts and code sets only";
   `no_data` — uploaded file contents and Bronze rows never enter these files. **Unlike every other
   screen in the platform, this one needs no `mask_row`.** State it in the response.
3. **Staleness is computed, not stored.** Return `review_by` and let the client compute days
   remaining against today. ES-09 makes this load-bearing: a fact past its `review_by` must not be
   used — the API should mark it and the AI context builder should drop it.

---

## 6. Ship checklist

- [ ] Single theme: white ground, black text, painted explicitly from tokens. No `prefers-color-scheme` block
- [ ] Every knowledge object carries a provenance strip: `path@version · confidence · owner · updated`, then `grounded_in`
- [ ] Mono means "literal from the corpus"; prose means "our explanation". No mixing
- [ ] Staleness ticker computed client-side, with a static pre-JS fallback
- [ ] The three disagreements in §1 render as first-class content, not footnotes
- [ ] §2 says all seven exceptions are `proposed`, none approved
- [ ] Counts match the files: 116 · 11 · 9 DR · 7 EX · 12 ES · 26 DQ (5/4/11/6)
- [ ] `never_autonomous` closes §2 as a statement, not a table row
- [ ] Client rules quoted verbatim, with the DQ-E-07 negotiation shown beside them
- [ ] Field counts and PHI counts read from `canonical/enrollment.yaml`, never re-estimated
- [ ] `phone_number` and `email_address` spelled correctly; no invented coverage-date fields
- [ ] Contested fields panel present, with the missing coverage dates named
- [ ] No create/edit/delete/approve control anywhere; filters are the only interaction
- [ ] The read-only reason stated on screen, including the `export.py` exception
- [ ] Nav's three reserved entries point here; no fourth added
- [ ] Every table scrolls in its own `overflow-x: auto`; the page body never scrolls sideways

---

*Cites `knowledge/` (116 files), `backend/src/cinqflow/knowledge/`, `frontend/lib/navigation.ts`
and `frontend/app/globals.css` as of 2026-09-03.*
