# ADR 0001 — Workflow lifecycle control with a declared DAG; background workers execute; AI reasons inside steps

**Status:** accepted, 2026-09-05. Records what `Analyst_worflow_and_DAGs/03_dag_background_worker_architecture.md` describes and what the platform already did, made explicit by PR-2 and PR-3 of `04_validation_and_implementation_plan.md`.

## Context

The platform moves a healthcare feed from a preserved original through Bronze to Silver Raw under two human gates. Three things have to hold at once: the work is long-running and must survive a crash; a person decides at defined points and nothing reaches the data plane without that; the AI proposes and never executes. Document 03 states the architecture that satisfies all three - lifecycle control in a workflow/DAG layer, execution in background workers, AI as a reasoning capability inside selected steps - and every one of its principles was already true of the code before this branch (`04_…plan.md §2.1`). What was missing was the *declaration*: the DAG was implicit, progress was hand-built per screen, and a worker failure was visible only as a queue message's last error (`§2.2`).

## Decision

1. **The workflow is declared once**, in code, as data: `backend/src/cinqflow/workflow/dag.py` - eight `StepDef`s in three scopes (`upload`, `batch`, `feed_version`), two of them gates a person performs. Nothing else lists the steps. The frontend derives its run rail from `steps[]` on the progress endpoints; `GET /api/workflow` serves the declaration itself.
2. **Execution stays in background workers over the Postgres queue** (`queue/queue.py`: `SELECT … FOR UPDATE SKIP LOCKED`, `dedupe_key UNIQUE`, attempts, `dead`). Each topic is a workflow boundary; the connecting state is the upload/batch row, not an in-memory graph. No orchestrator process.
3. **Every step run is a row** - `workflow.step_run` (`migrations/001_step_run.sql`), one per generation of one step of one scope, with attempts, the artifact it made, its error, and its timestamps. The worker loop (`queue/worker.py: run_once`) is the one place a worker step is opened and closed; handlers carry no ledger code. A handler that raises leaves a `failed` step with the exception before the queue records the failure. Gates are steps: opened when the step before them finishes, closed by the decision (a rejection ends the run adversely and is recorded as such, never as something to re-run).
4. **Re-running is re-queueing**, with a new generation and a fresh dedupe key, replaying the last generation's own message payload (`workflow/rerun.py`). Legality is a table (`RERUNNABLE`), the capability is `can_rerun_steps`, and the queue's own retry is never doubled.
5. **AI is a node, not the orchestrator.** `interpret_file` and `recommend_mapping` are LangGraph graphs whose only model call is the middle node; grounding and assembly are deterministic and enforce the contract (evidence, basis, observed columns, importance bounds). Anomalies the profile already states are raised by code for every provider; the model explains, it does not detect.
6. **Human decisions are first-class states**: `UploadStatus` with `LEGAL_TRANSITIONS`, append-only approvals, and the gate steps in the ledger.

## Consequences

- One source of truth for "what are the steps" - adding Identity Resolution and Silver ODS publication (MVP Epics 9 and 10) is adding `StepDef`s and workers, not new progress code per screen.
- Operability comes from the ledger: `GET /api/steps?state=failed`, `GET /api/attention`, and the run rail all read the same rows; no metrics stack is needed to see a failure.
- Each step run is bounded and idempotent from the store's point of view (append-only Bronze, replay-safe promotion), so a re-run is always safe to offer.
- The queue table and the ledger grow linearly with uploads × steps × generations; both are indexed by scope and state.

## What we refuse

Airflow, Prefect, Temporal, Celery or any second control plane (`checklist.md §0`: the Postgres queue carries all async work). Agent loops ("model thinks, calls a tool, thinks again"): structured outputs only, validated in code. Any step that lets a model write to the data plane.

## References

- `docs/blueprints/Analyst_worflow_and_DAGs/03_dag_background_worker_architecture.md` (the statement)
- `docs/blueprints/Analyst_worflow_and_DAGs/04_validation_and_implementation_plan.md` §2, §6, §16 (validation and the PR-2/PR-3 records)
- `docs/blueprints/structure.md` boundaries 1–8; `docs/blueprints/templates.md` §1.10 (step run)
- `backend/src/cinqflow/workflow/dag.py`, `workflow/store.py: StepLedger`, `workflow/rerun.py`, `queue/worker.py`, `intelligence/graphs/*`
