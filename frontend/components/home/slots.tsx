import Link from "next/link";
import { Cited } from "@/components/Cited";
import { Status } from "@/components/Status";
import { EmptyState } from "@/components/ui/EmptyState";
import { MetricTile } from "@/components/ui/MetricTile";
import { attempt, isRefused } from "@/lib/api";
import { batchesForFeeds, byHarm, byRecency } from "@/lib/queries";
import type { AgentAction, AuditEntry, Batch, Feed } from "@/lib/types";

/**
 * The home slots. One component per question a persona opens the screen with.
 *
 * Which of these render, and in what order, is decided by core/persona.py and
 * arrives on /api/me. This file knows how to draw a slot; it does not know who
 * gets it. That split is what keeps ADR-0020's merge rule enforceable: a slot
 * cannot quietly change a word or a depth for one role, because it does not
 * know which role it is drawing for.
 *
 * Each slot renders its BODY only. The heading and the one-line subtitle are
 * rendered once by app/page.tsx from the server-ranked list, because the first
 * slot's title IS the page title — and a component that renders its own <h2>
 * would duplicate the <h1> above it.
 */

function RunsTable({ batches, caption }: { batches: Batch[]; caption: string }) {
  return (
    <div className="card flush scroll">
      <table>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Batch</th>
            <th scope="col">Feed</th>
            <th scope="col">Business date</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {batches.map((batch) => (
            <tr className="row" key={batch.batch_id}>
              <td>
                <Cited value={batch.batch_id} citationId={batch.citation_id} />
              </td>
              <td>{batch.feed_id}</td>
              <td className="num mono">{batch.business_date}</td>
              <td>
                <Status word={batch.status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** What needs me, ranked by downstream harm. The engineer's first ten seconds. */
export function NeedsYou({ batches }: { batches: Batch[] }) {
  const urgent = batches
    .filter((b) => ["Needs Attention", "Missing", "Needs Review"].includes(b.status))
    .sort(byHarm);

  return urgent.length === 0 ? (
    <EmptyState kind="recorded" what="anything needing attention" />
  ) : (
    <RunsTable batches={urgent} caption="Runs needing attention, ranked by harm" />
  );
}

/** What arrived, most recent first — for someone who cannot act on harm. */
export function Arrived({ batches }: { batches: Batch[] }) {
  const recent = [...batches].sort(byRecency).slice(0, 10);
  return recent.length === 0 ? (
    <EmptyState kind="recorded" what="arrivals" />
  ) : (
    <RunsTable batches={recent} caption="Recent arrivals" />
  );
}

export function Runs({ batches }: { batches: Batch[] }) {
  return batches.length === 0 ? (
    <EmptyState
      kind="recorded"
      what="runs"
      action={
        <span className="note">
          Run <span className="mono">cinqflow simulate</span> to place a file — the demo places
          no files by hand.
        </span>
      }
    />
  ) : (
    <RunsTable batches={[...batches].sort(byHarm)} caption="All runs in view" />
  );
}

export function Feeds({ feeds }: { feeds: Feed[] }) {
  return feeds.length === 0 ? (
    <EmptyState kind="recorded" what="feeds" />
  ) : (
    <div className="card flush scroll">
      <table>
        <caption className="sr-only">Registered feeds</caption>
        <thead>
          <tr>
            <th scope="col">Feed</th>
            <th scope="col">Domain</th>
            <th scope="col">Version</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {feeds.map((feed) => (
            <tr className="row" key={feed.feed_id}>
              <td>
                <Link className="cited" href={feed.route}>
                  {feed.feed_id}
                </Link>
              </td>
              <td>{feed.domain}</td>
              <td className="num mono">
                <Cited value={`v${feed.version}`} citationId={feed.citation_id} />
              </td>
              <td>
                <Status word={feed.status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The fastest route from "I have a question" to an answer with citations —
 *  the first of the four crossings the product exists for. */
export function AskShortcut() {
  return (
    <div className="card">
      <form className="ask" action="/ai/ask">
        <input
          name="q"
          placeholder="why did batch 8842 lose rows?"
          aria-label="Ask CINQFLOW a question"
        />
        <button className="primary" type="submit">
          Ask
        </button>
      </form>
    </div>
  );
}

/** Governance evidence, not a hidden log: if it is not on a screen, it is not
 *  being governed. */
export async function RefusalsToday() {
  const actions = await attempt<AgentAction[]>("/api/agent-actions?limit=200");
  const rows = isRefused(actions) ? [] : actions.filter((a) => a.is_refusal);

  return (
    <>
      <div className="grid">
        <MetricTile
          label="Refusals in view"
          value={rows.length}
          tone={rows.length > 0 ? "attention" : undefined}
        />
        <MetricTile label="Agent runs in view" value={isRefused(actions) ? "—" : actions.length} />
      </div>
      {rows.length === 0 ? (
        <EmptyState kind="recorded" what="refusals" />
      ) : (
        <div className="card flush scroll">
          <table>
            <caption className="sr-only">Refused agent actions</caption>
            <thead>
              <tr>
                <th scope="col">Run</th>
                <th scope="col">Agent</th>
                <th scope="col">Action</th>
                <th scope="col">Outcome</th>
                <th scope="col">Risk</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 10).map((row) => (
                <tr className="row" key={row.run_id} data-outcome={row.outcome}>
                  <td className="mono">{row.run_id.slice(0, 12)}</td>
                  <td>{row.agent}</td>
                  <td className="mono">{row.action}</td>
                  <td className="mono">{row.outcome}</td>
                  <td className="mono">{row.risk_class}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/** Who changed what, and who was denied — the administrator's actual job. */
export async function AccessChanges() {
  const entries = await attempt<AuditEntry[]>("/api/audit?limit=100");
  const rows = isRefused(entries) ? [] : entries;
  const denials = rows.filter((entry) => entry.action.startsWith("denied:"));

  return (
    <>
      <div className="grid">
        <MetricTile label="Entries in view" value={rows.length} />
        <MetricTile
          label="Denied attempts"
          value={denials.length}
          tone={denials.length > 0 ? "attention" : undefined}
        />
      </div>
      {rows.length === 0 ? (
        <EmptyState kind="recorded" what="audit entries" />
      ) : (
        <div className="card flush scroll">
          <table>
            <caption className="sr-only">Recent audit entries</caption>
            <thead>
              <tr>
                <th scope="col">When</th>
                <th scope="col">Actor</th>
                <th scope="col">Action</th>
                <th scope="col">Object</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 10).map((entry, index) => (
                <tr className="row" key={index} data-action={entry.action}>
                  <td className="num mono">{entry.occurred_ts.slice(0, 19).replace("T", " ")}</td>
                  <td>{entry.actor_subject}</td>
                  <td className="mono">{entry.action}</td>
                  <td className="mono">{entry.object_id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/** Fetch once, share across every slot that needs it. */
export async function loadHomeData() {
  const feeds = await attempt<Feed[]>("/api/feeds");
  if (isRefused(feeds)) return { feeds: [] as Feed[], batches: [] as Batch[], refusal: feeds };
  const batches = await batchesForFeeds(feeds, 10);
  return { feeds, batches, refusal: null };
}
