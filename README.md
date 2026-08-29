# CINQFLOW

A CINQCARE-owned, metadata-driven healthcare data platform with a governed AI layer.

A Business Analyst onboards a feed through a guided journey — sample → schema → mapping →
plain-English rules → publish — and the platform then runs it
`Landing → Bronze → Silver Raw → Identity → Silver ODS (→ Gold)`, while operations monitor,
reconcile, troubleshoot and reprocess it without engineers or the outgoing vendor.

## The five laws

Every one is enforced by a test, a lint rule or a database constraint — never by review.

1. **`core/` imports no vendor SDK, URL, path or credential.** Vendor code lives only in `adapters/`.
2. **Every external touch crosses a port** — real / dev stand-in / mock, sharing **one** contract suite.
3. **All environment difference lives in the connection profile.** Nowhere else.
4. **Agents propose; humans dispose.** R4 (identity / PHI-consequential) is human-always, not configurable.
5. **Acceptance criteria are the tests, written first** — including a negative test for every "don't".

The architecture is authoritative in the knowledge pack
(`../ours/cinqflow-knowledge-pack/docs/architecture/`), generated from `atlas.html`.
**Cite plates by path; never paraphrase one into a comment.**

## Getting started

```bash
python3 -m venv .venv && source .venv/bin/activate     # Python 3.12
pip install -r requirements/dev.txt && pip install -e . --no-deps
python -m spacy download en_core_web_lg                # the phi_scrub pin

cinqflow install --profile profiles/local.yaml         # rung 0.5 — needs Postgres 16 + pgvector
pytest -m "unit or contract"                           # seconds, no services running
```

## What has to be running, per rung

| Rung | Socket | What runs | Cost |
|---|---|---|---|
| 0 | mock | nothing but Python | $0 |
| **0.5** | **Postgres plane** | **PostgreSQL 16 + pgvector. That is the entire list.** | $0 |
| 1 | local twin | + Keycloak · MinIO · Airflow OSS · OTel Collector · mock-Verato · simulator | $0 |
| 2 | Databricks Free | + a Databricks Free workspace (nightly certification) | $0 |
| 3 / 4 | client tenant | AKS · ADLS · Azure PG · Key Vault · Foundry · Databricks | client |

Climbing a rung changes **only** the profile you pass to `--profile`. A climb that forces a
core change is a ports-discipline defect, not a task.

## The gates

| Gate | Runs | Proves |
|---|---|---|
| `ruff` · `mypy` · `lint-imports` · `lint_core_purity` · `detect-secrets` | every commit | Law 1, mechanically |
| `pytest -m "unit or contract"` | every commit | the core's logic on the mock socket, in seconds |
| `pytest -m "pipeline or invariant"` | every commit | golden pipelines + the platform's laws, on the Postgres plane |
| `twin-e2e` | every PR | the demo, asserted — so it cannot silently rot |
| `agent-evals` | prompt change + nightly | Lane 3 only: no quality claim comes from a mock |

The gate set only ever grows.

## Layout

```
src/cinqflow/core/       the die — NO I/O, no vendor, no environment difference
src/cinqflow/ports/      20 pins: a protocol + ONE contract suite each
src/cinqflow/adapters/   mock · local · agent_runtime · (databricks · azure = seats)
src/cinqflow/api/        FastAPI BFF — its OpenAPI document IS the UI's contract
src/cinqflow/simulator/  the payer source simulator — a product component, not a fixture
profiles/                every environment difference, and nothing else
conformance/             one check per energized pin; the report is the handover artefact
goldensets/              pipelines · evals · cassettes · failures · reference
ui/                      Next.js App Router
```
