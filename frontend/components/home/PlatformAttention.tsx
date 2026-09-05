import Link from "next/link";
import RerunStepButton from "@/components/home/RerunStepButton";
import StatusWord from "@/components/StatusWord";
import Timestamp from "@/components/ui/Timestamp";
import { getAttention, type AttentionStep } from "@/lib/api";
import { RERUN_LOCKED_REASON } from "@/lib/persona";
import { rerunSourceFor } from "@/lib/rerun";
import { uploadStatusWord } from "@/lib/statusWords";

function where(step: AttentionStep): React.ReactNode {
  const label = step.filename ?? step.batch_id ?? step.scope_id;
  return step.href ? <Link href={step.href}>{label}</Link> : <span className="mono">{label}</span>;
}

/** The Data Platform home (PR-4): one operational view of what is failing,
 *  what the queue gave up on, what is in flight, and each feed's latest state
 *  with the adverse ones first - all from `GET /api/attention`, which reads the
 *  step ledger and the queue; nothing is stored for this page. A failed step
 *  offers Re-run here, the same confirmation and Server Action as on the run
 *  screens, when the caller holds `can_rerun_steps`. A rejected gate is a
 *  decision, not a failure, and does not appear. */
export default async function PlatformAttention({ canRerun }: { canRerun: boolean }) {
  const attention = await getAttention().catch(() => null);
  if (!attention) {
    return (
      <section className="home-section">
        <p className="meta">The control plane did not answer, so nothing can be shown here.</p>
      </section>
    );
  }

  const { failed_steps: failed, in_flight_steps: inFlight, dead_messages: dead, feeds } = attention;
  const depth = attention.queue_depth;
  const busyTopics = Object.entries(depth).filter(([k, v]) => k !== "pending_total" && v > 0);
  const attentionCount = failed.length + dead.length;

  return (
    <section className="home-section">
      <p className="home-lede">
        {attentionCount === 0
          ? "Nothing needs attention."
          : `${attentionCount} thing${attentionCount === 1 ? "" : "s"} need${attentionCount === 1 ? "s" : ""} attention.`}
        <span className="meta home-lede-meta">
          {" "}
          · {inFlight.length} step{inFlight.length === 1 ? "" : "s"} in flight · {depth.pending_total}{" "}
          queued
          {busyTopics.length ? ` (${busyTopics.map(([k, v]) => `${k} ${v}`).join(", ")})` : ""}
        </span>
      </p>

      <div className="card" style={{ padding: 0 }}>
        <div className="home-card-head">
          <span className="panel-label">Needs attention</span>
          {!canRerun && failed.length ? <span className="meta">{RERUN_LOCKED_REASON}</span> : null}
        </div>
        <div className="dt-wrap">
          <table className="dt">
            <thead>
              <tr>
                <th>Feed</th>
                <th>Where</th>
                <th>Step</th>
                <th>Error</th>
                <th className="right">Attempts</th>
                <th>When</th>
                {canRerun ? <th /> : null}
              </tr>
            </thead>
            <tbody>
              {failed.length === 0 && dead.length === 0 ? (
                <tr>
                  <td colSpan={canRerun ? 7 : 6} className="dt-empty">
                    No failed steps and no dead-letter messages.
                  </td>
                </tr>
              ) : null}
              {failed.map((step) => {
                const source = rerunSourceFor(step.scope_kind, step.scope_id);
                return (
                  <tr key={step.step_run_id}>
                    <td className="mono">{step.feed ?? "—"}</td>
                    <td>{where(step)}</td>
                    <td>
                      {step.label}
                      {step.generation > 1 ? (
                        <span className="meta"> · run {step.generation}</span>
                      ) : null}
                    </td>
                    <td className="mono small error">{step.error ?? "—"}</td>
                    <td className="right">{step.attempts}</td>
                    <td>
                      <Timestamp
                        value={step.finished_ts ?? step.queued_ts}
                        withSeconds={false}
                      />
                    </td>
                    {canRerun ? (
                      <td>
                        {source ? (
                          <RerunStepButton
                            source={source}
                            stepKey={step.step_key}
                            label={step.label}
                            audit={`${step.scope_kind} ${step.scope_id} · generation ${step.generation} → ${step.generation + 1}`}
                          />
                        ) : null}
                      </td>
                    ) : null}
                  </tr>
                );
              })}
              {dead.map((message) => (
                <tr key={message.message_id}>
                  <td className="mono">{String(message.payload.feed ?? "—")}</td>
                  <td className="mono small">
                    {String(message.payload.upload_id ?? message.payload.batch_id ?? message.dedupe_key)}
                  </td>
                  <td>
                    <span className="tag danger">dead letter</span>{" "}
                    <span className="mono small">{message.topic}</span>
                  </td>
                  <td className="mono small error">{message.last_error ?? "—"}</td>
                  <td className="right">{message.attempts}</td>
                  <td>
                    <Timestamp value={message.enqueued_ts} withSeconds={false} />
                  </td>
                  {canRerun ? (
                    <td className="meta small">
                      re-run the step from its run screen
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="home-grid">
        <div className="card">
          <span className="panel-label">In flight</span>
          {inFlight.length === 0 ? (
            <p className="meta" style={{ marginTop: 8 }}>
              Nothing queued or running.
            </p>
          ) : (
            <ul className="plain home-recent">
              {inFlight.map((step) => (
                <li key={step.step_run_id}>
                  <span
                    className={`run-step-dot ${step.state === "running" ? "current" : "pending queued"}`}
                    aria-hidden="true"
                  />{" "}
                  {step.label} <span className="meta">{step.state}</span> · {where(step)}{" "}
                  <span className="mono meta">{step.feed ?? ""}</span> ·{" "}
                  <Timestamp value={step.started_ts ?? step.queued_ts} withSeconds={false} className="meta" />
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="card">
          <span className="panel-label">Feeds by health</span>
          {feeds.length === 0 ? (
            <p className="meta" style={{ marginTop: 8 }}>
              No feeds have delivered yet.
            </p>
          ) : (
            <ul className="plain home-recent">
              {feeds.map((feed) => (
                <li key={feed.feed} className={feed.adverse ? "adverse" : undefined}>
                  <Link href={`/data/intake/${encodeURIComponent(feed.feed)}`} className="mono">
                    {feed.feed}
                  </Link>{" "}
                  <StatusWord word={uploadStatusWord(feed.status)} raw={feed.status} />{" "}
                  <Link href={`/uploads/${encodeURIComponent(feed.upload_id)}`} className="meta">
                    {feed.filename}
                  </Link>{" "}
                  <Timestamp value={feed.created_ts} withSeconds={false} className="meta" />
                  {feed.error ? <div className="mono small error">{feed.error}</div> : null}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}
