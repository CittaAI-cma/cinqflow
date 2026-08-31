import { Cited } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { Status } from "@/components/Status";
import { EmptyState } from "@/components/ui/EmptyState";
import { attempt, isRefused } from "@/lib/api";
import type { Governed, WorkQueue } from "@/lib/types";

/**
 * CF-V1-E11-01 — one lifecycle engine, proven by having one screen for
 * everything it holds. `awaiting_my_review` never contains the caller's own
 * work — the queue does not offer what the engine would refuse — so this
 * page needs no client-side filtering to keep that true; the server already
 * enforced it the same way `core.lifecycle`'s own author-approves-own
 * refusal does everywhere else.
 *
 * "What a person must decide, oldest first" is the destination's own
 * `answers` line (`core.navigation`) — so both tables sort that way, not by
 * whatever order the ten object types happen to iterate in.
 */

// Every governed type that opens a real page today. A type absent from this
// map still renders — as its bare `type:id@vN` — rather than as a dead link,
// the same "no citation kind is a dead end without a page behind it" rule
// `lib/citations.ts` follows for the agent's own citations.
const CITATION_KIND: Record<string, string> = {
  feed: "feed",
  contract: "contract",
  mapping: "mapping",
  dq_rule: "rule",
  glossary_term: "term",
  runbook: "runbook",
  knowledge_document: "document",
};

function citationFor(item: Governed): string {
  const kind = CITATION_KIND[item.object_type] ?? item.object_type;
  return `${kind}:${item.object_id}@v${item.version}`;
}

export default async function WorkQueuePage() {
  const queue = await attempt<WorkQueue>("/api/work-queue");

  if (isRefused(queue)) {
    return (
      <>
        <h1>Work Queue</h1>
        <RefusalNotice refusal={queue} />
      </>
    );
  }

  const review = [...queue.awaiting_my_review].sort((a, b) => a.created_ts.localeCompare(b.created_ts));
  const submitted = [...queue.my_submissions].sort((a, b) => a.created_ts.localeCompare(b.created_ts));

  return (
    <>
      <h1>Work Queue</h1>
      <p className="lede">What a person must decide, oldest first.</p>

      <h2>Awaiting my review</h2>
      {review.length === 0 ? (
        <EmptyState kind="recorded" what="items awaiting your review" />
      ) : (
        <QueueTable rows={review} />
      )}

      <h2>My submissions</h2>
      {submitted.length === 0 ? (
        <EmptyState kind="recorded" what="submissions of yours in flight" />
      ) : (
        <QueueTable rows={submitted} />
      )}
    </>
  );
}

function QueueTable({ rows }: { rows: Governed[] }) {
  return (
    <div className="card flush scroll">
      <table>
        <caption className="sr-only">Governed objects, oldest first</caption>
        <thead>
          <tr>
            <th scope="col">Type</th>
            <th scope="col">Object</th>
            <th scope="col">Version</th>
            <th scope="col">State</th>
            <th scope="col">Created by</th>
            <th scope="col">Created</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((item) => (
            <tr className="row" key={`${item.object_type}:${item.object_id}@v${item.version}`}>
              <td>{item.object_type.replace(/_/g, " ")}</td>
              <td>
                <Cited value={item.object_id} citationId={citationFor(item)} />
              </td>
              <td className="num mono">{item.version}</td>
              <td>
                <Status word={item.status} />
              </td>
              <td>{item.created_by_name || item.created_by_subject}</td>
              <td className="num mono">{item.created_ts.slice(0, 19).replace("T", " ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
