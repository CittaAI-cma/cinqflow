-- 001: the explicit step ledger (plan §6.1, PR-2).
--
-- One row per step per generation of one scope: an upload's
-- profile -> interpret -> gate_g1 -> land, a batch's analyze and promote, a feed
-- version's preview and gate_g2. workflow/dag.py declares the steps;
-- workflow/store.py's StepLedger writes the rows; queue/worker.py's run_once is
-- the one place a worker step is opened and closed, so no handler carries
-- ledger code of its own.
--
-- `generation` increments when a finished step runs again (a replay, or a PR-3
-- re-run); `attempts` counts the queue's own retries of one generation. A
-- `pending` row exists from the moment the message is enqueued (StepLedger.queued),
-- which is why `queued_ts` is here although the plan's sketch had no such column:
-- "waiting since" needs a time, and it is the honest distinction between
-- "queued, no worker has taken it" and "not reached".
CREATE TABLE {{workflow}}.step_run (
  step_run_id   UUID PRIMARY KEY,
  scope_kind    TEXT NOT NULL CHECK (scope_kind IN ('upload', 'batch', 'feed_version')),
  scope_id      TEXT NOT NULL,
  step_key      TEXT NOT NULL,
  generation    INT  NOT NULL DEFAULT 1,
  state         TEXT NOT NULL
                CHECK (state IN ('pending', 'running', 'done', 'failed', 'skipped')),
  attempts      INT  NOT NULL DEFAULT 0,
  message_id    UUID,                          -- the queue message that carries it
  artifact_type TEXT,                          -- profile | interpretation | approval | batch | ...
  artifact_id   TEXT,
  error         TEXT,                          -- failure text, refusal reason, or the rejection
  queued_ts     TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_ts    TIMESTAMPTZ,
  finished_ts   TIMESTAMPTZ,
  UNIQUE (scope_kind, scope_id, step_key, generation)
);

CREATE INDEX step_run_scope_idx ON {{workflow}}.step_run (scope_kind, scope_id);
CREATE INDEX step_run_state_idx ON {{workflow}}.step_run (state, queued_ts DESC);
