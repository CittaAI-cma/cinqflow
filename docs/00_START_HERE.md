# CINQFLOW — Fresh Start Document Set

Everything needed to build the platform again from zero, and nothing that describes how the
current one was built. Assembled 2026-09-02 from the original corpus in this repo.

**What is in here:** the client's own requirement, design and schema documents; the epic and
user-story set; the de-identified source files and their data profiles; the encoded domain
and business rules; and a full record of the incumbent product (Digitalurth) that the new
platform replaces.

**What is deliberately not in here:** ADRs, the programme memory store, the existing
implementation's architecture atlas and wave plans, and any source code. See
[§7 Excluded on purpose](#7-excluded-on-purpose) for where those live if you ever want one.

Every file is a **copy** — the originals are untouched in `clientdata/`, `platformdata/`,
`docs/` and `5-TestData/`.

`MANIFEST.txt` lists all 290 files with sizes. Total 413 MB, of which 262 MB is real
de-identified source data and 88 MB is the training recording.

---

## 1. Source-of-truth precedence

When two documents disagree, prefer the higher row.

| Rank | Source | Why it wins |
|---:|---|---|
| 1 | `01_requirements_and_objectives/` + `03_data_source_schemas/` + `04_..._samples_and_profiles/` | Written or supplied by CINQCARE. Client intent and real file structure. |
| 2 | `06_incumbent_workflow_ground_truth/` | Observed behaviour of the product being replaced — recorded, not inferred. |
| 3 | `02_epics_and_user_stories/` | The agreed build scope, derived from row 1. |
| 4 | `05_business_logic_and_domain_rules/vbc_domain_knowledge_pack/` | Encoded analyst judgement. Authoritative on domain reasoning, not on client specifics. |

A schema question is answered by an actual sample file in `04/` before any data-model
spreadsheet — the spreadsheets are targets, the samples are what arrives.

---

## 2. Read in this order (fresh build, first pass)

1. `01_requirements_and_objectives/MVP_objective.docx` — the objective the whole programme is judged against.
2. `01_requirements_and_objectives/CINQFlow_Final_Navigation_and_Screen_Blueprint.docx` — the screen and navigation surface the client signed off.
3. `06_incumbent_workflow_ground_truth/DIGITALURTH_WALKTHROUGH__feed_onboarding_end_to_end.md` — one feed onboarded end to end, in the words of the person who does it today. Fastest route to understanding the job.
4. `02_epics_and_user_stories/CINQFLOW_MVP_Backlog__epics_and_stories.csv` — the whole scope on one screen.
5. `02_epics_and_user_stories/CINQFLOW_User_Stories_Final.docx` — the authoritative story set with acceptance criteria.
6. `04_source_data_samples_and_profiles/` — open two profile PDFs and the matching data file. This is what the pipeline actually eats.
7. `03_data_source_schemas/` — the Silver Raw / Silver ODS targets and the source→target mappings.
8. `05_business_logic_and_domain_rules/` — the rules that make the transforms correct rather than merely running.

---

## 3. The scope, at a glance

**16 epics · 76 stories** in the authoritative set (`CINQFLOW_User_Stories_Final.docx`).
Story IDs read `CF-V<wave>-E<epic>-<seq>` — the wave says when it ships, the epic says which
requirement it satisfies.

| Epic | Name | Stories in MVP backlog |
|---|---|---:|
| E1 | Existing Product Discovery & Configuration Migration | 4 |
| E2 | User, Role & Security Management | 4 |
| E3 | Source & Feed Registry | 4 |
| E4 | BA Self-Service Feed Onboarding | 4 |
| E5 | Data Profiling & Intelligent Schema Inference | 5 |
| E6 | Canonical Mapping & Transformation Studio | 5 |
| E7 | Natural-Language Business Rules & DQ Studio | 5 |
| E8 | Metadata-Driven Engineering & Orchestration Engine | 6 |
| E9 | Identity Resolution & Crosswalk Management | 4 |
| E10 | Silver ODS Canonical Data Model | 3 |
| E11 | Workflow, Approval & Release Management | 4 |
| E12 | Operational Control, Observability & Reprocessing | 5 |
| E13 | Reconciliation & Data Certification | 4 |
| E14 | Data Catalog, Business Glossary & Knowledge Base | 4 |
| E15 | Production Migration, Parallel Run & Cutover | 4 |
| E16 | AI / Knowledge Runtime (LLM gateway, prompt registry, retrieval, citations, test lanes) | 8 |

Personas across the backlog: **BA 18 · Engineer 14 · Ops 12 · Steward 11 · Admin 7 ·
Approver 2**. Story archetypes: A registry/metadata · B engine/deterministic · C AI-assist ·
D governance · E ops-action.

**Reconciling the three story files** — they are not three versions of the same list:

| File | Contains | Use it for |
|---|---|---|
| `CINQFLOW_User_Stories_Final.docx` | **76** stories, E1–E16 | The scope. Authoritative. |
| `CINQFLOW_User_Stories_and_Acceptance_Criteria.docx` | **65** stories, E1–E15, ~2× the detail per story | Deep acceptance criteria for the 65 it covers. |
| `CINQFLOW_MVP_Backlog__epics_and_stories.csv` | the same **65**, one row each | Planning, filtering, importing to Jira/Asana. |

The 11 stories in Final but not in the CSV are the whole of **E16** (8) plus `CF-V0-E8-07`,
`CF-V0-E8-08`, `CF-V1-E8-09`. Take E16 seriously early: it is the LLM gateway, the prompt
registry and the citation contract that every AI-assist story in E5/E6/E7 depends on.

*Known gap, stated plainly:* four story additions agreed after this document set was written
(three addendum stories and one `E3-05`) exist only in the programme memory store, which you
asked to leave out. If you want the count to reach 80, that is where the extra four are.

---

## 4. Folder guide

### `01_requirements_and_objectives/`
Client intent. `MVP_objective.docx` and the navigation/screen blueprint are the two documents
the build is measured against. `Cinq_Requirement_Plan.xlsx` is the requirement register;
the two Phase 1 Plan docs (Mar 11 and Apr 10) are the client's own scoping of the lakehouse —
read the **Apr 10** one first, the March one shows what moved. `Team_by_Payors_and_functionalities.xlsx`
tells you which payer populations and functions are in scope.

### `02_epics_and_user_stories/`
Scope. Plus `CINQFLOW_Epic_Story_Task_Templates.md` — the epic drill-down / story / task-pack
templates, including the extended task lanes (prompt, AI integration, eval + golden set,
security review) that AI stories need on top of the normal ones.

### `03_data_source_schemas/`
Per domain: the source structure, the Silver Raw target, and the mapping between them.
- `enrollment/` — lake models, Silver Raw and Silver ODS models, plus `enrollment_silver_raw_model.sql` (executable DDL, the fastest way to stand up a schema) and the enrollment DB → Silver Raw mapping.
- `claims/` — Silver Claims ODS model and nine payer/programme mappings: CCLF, BCDA (with its data dictionary), ACO `D0284`, MSSP, Fidelis, UHC/Optum NY, plus `Claims_All_Fields` and both directions of the CinqDB ↔ DataLake seed mapping.
- `adt/` — ADT data model (final and draft), Bronze → Silver Raw v2, HL7 and Fidelis mappings, and volume stats.
- `reference_data/` — reference data model, design and flow.
- `identity_verato/` — Verato identity storage schema (pair with the Verato scenarios in `05/`).
- `lakehouse_and_layers/` — the cross-domain lake data model, table statistics, and a worked sample of the layered data.

### `04_source_data_samples_and_profiles/`
**De-identified** real files from ten feeds — nine enrollment sources (Fidelis NY up/downstate,
Molina NY ×4 populations, Centene GA Medicaid + Medicare, Optum GA, Optum NY, Centene IL,
ACO REACH `D0284`, CMP_1598) and ADT — each with its **Health Data Profile / Profile Report
PDF**. Formats span CSV, XLSX, pipe/tab TXT. The profile PDFs give column inventories, types,
null rates and distributions: they are the ground truth for schema inference and for the
profiling epic (E5). Nothing here contains PHI.

### `05_business_logic_and_domain_rules/`
- `vbc_domain_knowledge_pack/` — nine YAML files encoding a VBC data analyst's judgement, retrievable node by node: domain ontology, source landscape (CCLF/BCDA/roster/ADT quirks), medallion layer contracts, identity resolution, analyst reasoning playbooks, metric definitions, data-quality rules, agent specs. Designed as agent grounding, not prose. Its own rule: *encode the decision, not the description* — and every rule carries its exception and blast radius.
- `Enrollment_Domain_Level_Rules.docx`, `BCDA_Handling_Claim_Adjustments_and_Claim_Status.docx`, `ADT_Data_Model_Open_Items.docx` — the domain rules the transforms must honour.
- `Verato_Identity_Scenarios.docx` + `Verato_Data_Scenarios.xlsx` — the identity-matching cases to satisfy.
- `CINQCARE_VBC_Contract_MindMap.html` — the contract landscape the data serves.

### `06_incumbent_workflow_ground_truth/`
What the client does today, in the product being replaced. The single richest requirements
source in the set.
- `DIGITALURTH_WALKTHROUGH__feed_onboarding_end_to_end.md` — a 30-minute training call rendered step by step: eight moves from sign-in to first run, with routes, field names and invariants called out separately from narration. Frames in `walkthrough_frames/`, full transcript in `DIGITALURTH_training_call_transcript.txt`, original recording and VTT in `training_recording/`.
- `DIGITALURTH_SURVEY__all_screens.md` — every destination in the incumbent's sidebar, in its own order, with 44 exhibits; images in `survey_frames/`. Notes which screens were never opened on camera, so you know where the gaps are.
- `incumbent_ui_full_gallery/` (49 captures, `manifest.json` maps each to its original screenshot) and `incumbent_ui_from_recording/` (24 full-resolution screens) and `client_shared_screenshots/` (12 live DataOps captures shared by the client, 19–20 Aug 2026).

### `07_client_technical_and_infra_design/`
The client's own technical framing: technical architecture document v2, the data platform
architecture and control model, the Azure infrastructure architecture, the Digital Urth
technical design deck, and the pipeline migration deck. Constraints to design within — not a
description of the platform you are replacing.

### `08_optional_stack_dependency_baseline/`
Ignore unless useful: pinned Python dependency sets (core, ai, agents, azure, databricks,
formats, dev) from an earlier stack proposal. Saves an hour of version pinning. Carries no
architecture.

---

## 5. What the platform has to do (one paragraph, from the corpus)

CINQCARE is a value-based-care organisation with roughly 150,000 attributed lives across ten
payer populations; about 118K sit under full-risk contracts, so the financial and quality risk
is CINQCARE's. The platform's job is the contracts: an analyst must be able to onboard a
standard healthcare feed through a guided flow, define data-quality rules in plain English,
validate mappings and rules against sample data, and publish an approved configuration — after
which the data moves from landing through Bronze and Silver Raw to a canonical Silver ODS with
resolved identity, under approval, with lineage and reconciliation that stands up to audit.
Identity resolution and financial reconciliation are guarantees, not features: wrong identity
double-counts or loses a member, wrong claims lineage breaks the reconciliation the contracts
are paid against.

---

## 6. Before the demo — the four files worth opening

1. `01_requirements_and_objectives/MVP_objective.docx`
2. `06_incumbent_workflow_ground_truth/DIGITALURTH_WALKTHROUGH__feed_onboarding_end_to_end.md` (the "whole path, in eight moves" section)
3. `02_epics_and_user_stories/CINQFLOW_MVP_Backlog__epics_and_stories.csv`
4. any one profile PDF in `04_source_data_samples_and_profiles/` next to its data file

---

## 7. Excluded on purpose

Left out so the new build starts clean. Original paths, if a specific one is ever wanted:

| Excluded | Where it still lives |
|---|---|
| ADRs (0001–0024) | `memory/02-decisions/`, `cinqflow/docs/adr/` |
| Programme memory store (charter, ground truth, runbooks, open questions) | `memory/` |
| Generated architecture atlas, plates, figures, invariants | `platformdata/cinqflow-knowledge-pack/`, `platformdata/CINQFLOW_Architecture_Atlas.html` |
| Wave implementation blueprints and status | `platformdata/CINQFLOW_Wave_Implementation_Blueprint.md`, `platformdata/wave2.md`, `docs/wave-epic-story-status-2026-08-31.md` |
| Current platform's own design docs (chip architecture, AI knowledge runtime, adaptive control plane, intelligent lakehouse, enhancements) | `platformdata/*.docx`, `platformdata/*.md` |
| Existing implementation source | `cinqflow/` |
| Product briefings and access-control model as built | `docs/briefings/`, `platformdata/CINQFLOW-Access-Control.pdf` |
| Developer domain guide for the current code | `cinqflow/docs/DOMAIN.md` |
| Concept UI renders for a different product direction (ValueBridge) | `platformdata/platform_screens/` |
| Project-management artefacts (resource plan, daily status, scrum tracker) | `clientdata/Uploads/0-Plan/`, `docs/Daily_Scrum_CittaAI_30_Days.xlsx` |
| Draft/backup copies of data models superseded by the finals in `03/` | `clientdata/Uploads/2-Design/Drafts/`, `.../Claims/backup/` |

One judgement call worth knowing about: `platformdata/CINQFLOW-Access-Control.pdf` describes
the role and permission model as currently built. It reads like a requirement but it is a
record of the existing implementation, so it is excluded. Pull it only if you want the role
names for continuity.
