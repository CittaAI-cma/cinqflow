# CINQFLOW Working Foundation — Structure

Where every piece of the vertical slice lives, and the boundaries between them.
Companion to `features.md`, `templates.md`, `checklist.md` and `CINQFLOW_Rebuild_Blueprint.html`.

This is a fresh build. The only inherited element is the **data-plane contract**
(layers, audit columns, batch semantics — a logical contract Postgres renders today).
The chip/plug-play port registry from the previous implementation is **not** carried forward.

---

## Repository layout

```
cinqflow/
├── docs/                          # this document set + the blueprint HTML
├── backend/
│   ├── pyproject.toml             # Poetry; ruff configured here
│   ├── src/cinqflow/
│   │   ├── settings.py            # pydantic-settings; ALL env difference lives here
│   │   ├── api/                   # CONTROL PLANE (thin)
│   │   │   ├── app.py             # create_app() — composition root
│   │   │   ├── deps.py            # request-scoped wiring (db, stores, queue)
│   │   │   └── routers/
│   │   │       ├── uploads.py     # POST/GET uploads, profile, interpretation, G1
│   │   │       ├── mappings.py    # proposals, mapping versions, preview, G2
│   │   │       ├── batches.py     # batch/run status
│   │   │       └── lineage.py     # lineage chain queries
│   │   ├── workflow/              # FIRST-CLASS ARTIFACTS
│   │   │   ├── models.py          # pydantic models: Upload, Profile, Interpretation,
│   │   │   │                      #   Proposal, MappingVersion, Preview, Approval, Run, Lineage
│   │   │   ├── states.py          # allowed states + legal transitions (enforced here)
│   │   │   └── store.py           # SQL persistence for the artifacts (schema: workflow)
│   │   ├── migrations/            # CONTROL-PLANE SCHEMA CHANGES: NNN_name.sql, applied in
│   │   │                          #   order by `cinqflow install` / `cinqflow migrate`;
│   │   │                          #   recorded in workflow.schema_version (see module docstring)
│   │   ├── queue/                 # DURABLE QUEUE (Postgres, no Celery)
│   │   │   ├── queue.py           # enqueue/claim; FOR UPDATE SKIP LOCKED; dedupe_key UNIQUE
│   │   │   └── worker.py          # consumer loop; topic → handler registry; CLI entrypoint
│   │   ├── workers/               # JOB HANDLERS (one file per topic)
│   │   │   ├── profile_upload.py  # topic upload.profile      (Stage 1, deterministic)
│   │   │   ├── interpret_upload.py# topic upload.interpret    (Stage 1, AI)
│   │   │   ├── land_bronze.py     # topic batch.land_bronze   (Stage 2, deterministic)
│   │   │   ├── analyze_bronze.py  # topic bronze.analyze      (Stage 3, det profile + AI)
│   │   │   ├── run_preview.py     # topic mapping.preview     (Stage 5, deterministic)
│   │   │   └── promote_silver.py  # topic mapping.promote     (Stage 6, deterministic)
│   │   ├── engine/                # DETERMINISTIC ENGINE — the only mover of rows
│   │   │   ├── parsers.py         # CSV/XLSX bytes → typed table (all values strings first)
│   │   │   ├── profiler.py        # deterministic profiling; profile_id = hash(facts)
│   │   │   ├── mapping_spec.py    # the constrained mapping representation + validation
│   │   │   ├── mapping_exec.py    # spec executor: preview mode + full mode (same code)
│   │   │   └── runner.py          # PipelineRunner: sequences land → bronze → silver,
│   │   │                          #   opens/closes runs, enforces the balance equation
│   │   ├── intelligence/          # AI RUNTIME (LangGraph) — proposes, never executes
│   │   │   ├── runtime.py         # one runtime; run_graph(name, inputs) → structured output
│   │   │   ├── graphs/
│   │   │   │   ├── interpret_file.py     # nodes: ground(no model) → infer → assemble(no model)
│   │   │   │   └── recommend_mapping.py  # nodes incl. validate_proposal (no model)
│   │   │   ├── context.py         # ContextBuilder — selects knowledge per job
│   │   │   ├── prompts/           # versioned prompt files; prompt_version recorded per output
│   │   │   └── schemas.py         # pydantic output schemas (interpretation, proposal)
│   │   ├── knowledge/             # KNOWLEDGE ACCESS (not the knowledge itself)
│   │   │   ├── provider.py        # KnowledgeProvider Protocol:
│   │   │   │                      #   get_source(feed) / get_canonical(domain)
│   │   │   │                      #   get_glossary(terms) / get_approved_mappings(domain)
│   │   │   │                      #   get_rules(domain) — each returns versioned documents
│   │   │   └── yaml_provider.py   # reads /knowledge/**.yaml; swappable for db/vector later
│   │   └── dataplane/             # THE LOGICAL CONTRACT + ITS PG RENDERING
│   │       ├── contract.py        # engine-neutral declaration: layers, tables, columns,
│   │       │                      #   audit columns, append-only rules, uniqueness
│   │       ├── port.py            # read/write Protocol: land_bronze, load_silver_raw,
│   │       │                      #   read_sample, read_batch, clear_batch_derived, counts
│   │       ├── pg.py              # Postgres adapter: renders DDL from contract.py,
│   │       │                      #   implements port.py (psycopg, parameterised SQL)
│   │       └── filestore.py       # landing zone: place/move/read/fingerprint (local FS)
│   └── tests/
│       ├── unit/                  # engine, workflow states, mapping spec, contract
│       ├── integration/           # queue, workers, dataplane against real Postgres
│       └── e2e/                   # one test per stage proving the slice end-to-end
├── frontend/                      # Next.js (App Router), server components + actions
│   └── app/
│       ├── uploads/               # list, upload form, upload detail (profile + interpretation)
│       ├── review/[uploadId]/     # G1 screen: interpretation vs profile, approve/reject
│       ├── mapping/[feedId]/      # studio: proposal → draft vN editing, diff
│       ├── preview/[versionId]/   # preview results, failures, counts; G2 approve
│       └── lineage/[batchId]/     # the full chain
├── knowledge/                     # GOVERNED KNOWLEDGE (YAML now; see templates.md)
│   ├── sources/<feed>.yaml        # source/feed definitions
│   ├── canonical/<domain>.yaml    # canonical entities + fields (the only legal targets)
│   ├── glossary.yaml              # healthcare glossary
│   ├── mappings/approved/*.yaml   # approved mapping decisions (grows from G2 outcomes)
│   └── rules/<domain>.yaml        # basic domain rules
├── compose/                       # docker compose for containerised parity
│   └── docker-compose.yml         # backend, frontend, postgres (parity with prod shape)
└── Makefile                       # dev entrypoints: install, api, worker, test, psql
```

---

## Data stores

One PostgreSQL instance in dev (local Homebrew `postgresql@16` service), two logical groups
of schemas — kept separable so the medallion can move to a warehouse without touching
workflow state:

| Group | Schema | Contents |
|---|---|---|
| Workflow / platform | `workflow` | uploads, profiles, interpretations, proposals, mapping_versions, previews, approvals, runs, lineage |
| Workflow / platform | `queue` | messages (dedupe_key UNIQUE, attempts, claimed_at) |
| Data plane | `bronze` | one source-aligned table per feed, append-only (trigger + REVOKE) |
| Data plane | `silver_raw` | one canonical table per domain |

Landing is **file storage, not tables**: local filesystem in dev at the configured
`LANDING_ROOT`, laid out `{domain}/{source_system}/{feed}/{folder}/{business_date}/{filename}`
with folders `incoming | processed | rejected | archive | parked`. The arrival registry
(fingerprint `sha256-…`, UNIQUE) lives in `workflow`, not in the warehouse.

All data-plane DDL is rendered from `dataplane/contract.py` by `dataplane/pg.py` and applied
by an idempotent installer command (`python -m cinqflow.dataplane install`). No hand-written
migrations for the plane; the contract file is the source of truth. Audit columns on every
data row: `source_system, ingestion_ts, batch_id, record_hash, created_ts, updated_ts`.

The **control-plane** schemas (`workflow`, `queue`, `auth`) are different: their baseline
DDL (`workflow/ddl.py`, `auth/ddl.py`) is idempotent `CREATE … IF NOT EXISTS` and is now
**frozen**. Every change since — a new table, a widened column — is a numbered SQL file in
`src/cinqflow/migrations/` (`NNN_snake_case_name.sql`, contiguous from `001`, schema names
written as `{{workflow}}` / `{{queue}}` / `{{auth}}`), applied one transaction each and
recorded in `workflow.schema_version (version, name, applied_ts)`. `cinqflow install` applies
pending migrations after the baseline, so compose's `migrate` service and Railway's
`bootstrap.sh` need nothing new; `cinqflow migrate --status` shows one database's position.
Applied migrations are never edited, renamed or deleted — the runner refuses to start if one
is missing. No Alembic: one table and a directory listing, in visible SQL, is the whole
mechanism (`migrations/__init__.py`).

---

## Boundaries (enforced in review; optionally by import-linter later)

1. `api/` never runs long work; it may import `workflow`, `queue`, and read-side `dataplane`.
2. `engine/` never imports `intelligence/` — determinism cannot depend on a model.
3. `intelligence/` never imports `dataplane/pg.py` write paths — the AI reads profiles,
   samples and knowledge; it writes only workflow artifacts (proposals, interpretations).
4. `intelligence/` never reads YAML directly — only through `knowledge/provider.py`.
5. `workers/` are thin: claim job → call engine or intelligence → persist artifact →
   transition state. Business logic lives in `engine/`, `intelligence/`, `workflow/`.
6. Only `dataplane/pg.py` contains data-plane SQL. Only `workflow/store.py` contains
   workflow SQL. Parameterised statements everywhere; no ORM on the data plane.
7. Nothing executes LLM-generated code, ever. Mappings are data (`engine/mapping_spec.py`),
   validated then executed by `engine/mapping_exec.py`.

## Runtime processes (dev)

| Process | Command | Role |
|---|---|---|
| API | `make api` → uvicorn `cinqflow.api.app:create_app` | control plane |
| Worker | `make worker` → `python -m cinqflow.queue.worker` | drains all topics |
| Frontend | `pnpm dev` in `frontend/` | analyst workspace |
| Postgres | Homebrew service (dev) / compose (parity) | both store groups |

Environment via `.env` + `settings.py`: `DATABASE_URL`, `LANDING_ROOT`, `ANTHROPIC_API_KEY`
(or configured provider), `LLM_MODEL`, `KNOWLEDGE_ROOT`. No credentials or paths hardcoded
anywhere else.
