# CINQFLOW

A value-based-care data platform: analysts upload raw source files and, through
a deterministic engine paired with a governed AI layer, get a reviewed,
lineage-tracked path into a canonical data model — with an analyst decision at
every gate that matters.

```
Analyst → CSV/XLSX Upload → Deterministic Profiling → AI Understanding → G1 Approval
→ Landing → Bronze → Bronze Intelligence → AI Mapping Proposal → Editable Mapping
→ Deterministic Preview → G2 Approval → Silver Raw
```

This repository is a clean-room rebuild of the platform's working foundation —
a working vertical slice, not a speculative platform. Stages ship one at a
time; see [`docs/blueprints/`](docs/blueprints/) for the full specification.

## Principles

- **Deterministic before AI.** Code computes facts — profiling, statistics,
  parsing, execution. The LLM only reasons over persisted facts and a bounded
  sample, and writes structured proposals.
- **AI is never the record.** No AI output becomes authoritative without an
  analyst approval (gates **G1** and **G2**). No free-form model text as
  source of truth — every claim is structured, evidenced, and versioned.
- **First-class artifacts.** Every noun in the flow — upload, profile,
  interpretation, proposal, mapping version, preview, approval, run, lineage —
  is a persisted row with an id, a version, and an explicit state.
- **Replay.** Original files are immutable in landing; any batch can be
  reprocessed from its landed original. Bronze is append-only at the database
  level (trigger + `REVOKE`).

## Architecture

| Layer | What it does |
|---|---|
| **Frontend** (`frontend/`) | Next.js (App Router) analyst workspace — upload, review, mapping studio, preview, lineage. Server components/actions only talk to the API; no client-side calls. |
| **API** (`backend/.../api/`) | FastAPI control plane. Every handler: validate → persist → enqueue → return. Never runs long work inline. |
| **Worker** (`backend/.../queue/worker.py`) | Drains a Postgres-backed durable queue (`FOR UPDATE SKIP LOCKED`, dedupe keys, crash-safe claim) — independently runnable from the API. No Celery, no separate broker. |
| **Engine** (`backend/.../engine/`) | The only mover of rows — deterministic profiling, mapping execution, and the medallion pipeline (landing → Bronze → Silver). Never imports the AI layer. |
| **Intelligence** (`backend/.../intelligence/`) | One LangGraph runtime; capabilities are graphs/nodes, not separate agents. Reasons over deterministic facts plus governed knowledge; never executes or writes bulk data. Anthropic and OpenAI providers supported (Structured Outputs on the OpenAI path), plus a deterministic offline stub for tests. |
| **Data plane** (`backend/.../dataplane/`) | One PostgreSQL instance, separated logically by schema — `workflow`/`jobq` (control plane) vs `bronze`/`silver` (medallion layers) — rendered from a single engine-neutral contract. |

Every AI artifact records its provenance: prompt version, model id, and the
exact governed-knowledge documents it reasoned over.

## Repository layout

```
backend/    FastAPI + LangGraph + Postgres (Poetry-managed)
frontend/   Next.js analyst workspace (pnpm-managed)
knowledge/  Governed knowledge (YAML): source defs, canonical model, glossary,
            approved mapping decisions — read only through a provider, never
            touched by the AI layer directly
compose/    Docker Compose orchestration for local dev
docs/       Blueprints, source samples, architecture decisions, stage reports
```

## Getting started

**Prerequisites:** Docker Desktop (recommended path), or Python 3.12 + Poetry
+ Node 24 + pnpm + PostgreSQL 16 for native dev.

### Docker (recommended)

```bash
cp compose/.env.example compose/.env   # fill in an LLM key if you have one
make up
```

This brings up Postgres, runs the idempotent schema install, then starts the
API (`localhost:8000`), worker, and frontend (`localhost:3000`) in dependency
order. `make down` / `make logs` / `make docker-test` round out the workflow —
see the [`Makefile`](Makefile) for the full target list.

### Native

```bash
make install      # poetry install
make install-db   # create schemas against your local Postgres
make api          # uvicorn, --reload
make worker       # separate terminal — the queue consumer
cd frontend && pnpm install && pnpm dev
```

Every environment difference lives in `backend/src/cinqflow/settings.py`,
sourced from `.env` — no credentials or paths hardcoded elsewhere. Set
`CINQFLOW_LLM_PROVIDER=stub` for a fully offline, deterministic run, or
`anthropic`/`openai` with `CINQFLOW_LLM_API_KEY` for the real thing.

## Testing

```bash
make test          # native: pytest, real Postgres, throwaway schemas
make docker-test    # same suite, run inside the api container
```

Unit, integration, and end-to-end suites cover every stage; e2e tests drive
the real flow — API call → worker drain → assert persisted artifacts — against
a real database, never mocked.

## Deployment

- **Docker Compose** (`compose/docker-compose.yml`) is the containerized-parity
  target for local development.
- **Railway** is supported via `backend/railway.json` / `backend/railway/` /
  `backend/Dockerfile.railway`, with one documented, scoped exception —
  API and worker run combined in a single service there, to work within
  Railway's one-volume-per-service limit. See
  [`docs/blueprints/checklist.md` §0.2](docs/blueprints/checklist.md) for the
  full rationale; every other environment keeps them as two independent
  processes.

## Documentation

The full specification lives in [`docs/blueprints/`](docs/blueprints/):

- [`features.md`](docs/blueprints/features.md) — required behavior, stage by stage
- [`structure.md`](docs/blueprints/structure.md) — where every piece lives and the boundaries between them
- [`templates.md`](docs/blueprints/templates.md) — canonical shapes for every artifact, knowledge document, and API contract
- [`checklist.md`](docs/blueprints/checklist.md) — execution discipline and definition-of-done per stage

Per-stage completion reports are recorded under `docs/reports/`.

## License

Proprietary — Citta AI. All rights reserved.
