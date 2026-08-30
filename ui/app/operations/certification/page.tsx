import Link from "next/link";
import { Cited } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { Tag } from "@/components/Tag";
import { EmptyState } from "@/components/ui/EmptyState";
import { attempt, isRefused } from "@/lib/api";
import { verdictTone } from "@/lib/certification";
import type { Batch, Certification, Feed } from "@/lib/types";

/**
 * CF-V2-E13-04 — which batches are certified, and what the evidence says.
 *
 * One row per feed's MOST RECENT batch, the same shape `Monitor` already
 * uses for "expected versus actual, per feed, per cycle" — certification is
 * the same question asked of the same anchor run, not a second inventory of
 * batches to maintain. There is no button anywhere on this screen that sets
 * a verdict: every one shown here was DERIVED, on this read, from retained
 * history, and re-deriving it later returns the same answer.
 */
export default async function CertificationOverview() {
  const feeds = await attempt<Feed[]>("/api/feeds");
  if (isRefused(feeds)) return <RefusalNotice refusal={feeds} />;

  const rows = await Promise.all(
    feeds.map(async (feed) => {
      const batches = await attempt<Batch[]>(
        `/api/batches?feed_id=${encodeURIComponent(feed.feed_id)}&limit=1`,
      );
      const latest = !isRefused(batches) ? batches[0] : undefined;
      if (!latest) return { feed, latest: undefined, certification: undefined };
      const certification = await attempt<Certification>(
        `/api/operations/batches/${encodeURIComponent(latest.batch_id)}/certification`,
      );
      return { feed, latest, certification: isRefused(certification) ? undefined : certification };
    }),
  );

  return (
    <>
      <h1>Certification</h1>
      <p className="lede">Which batches are certified, and what the evidence says.</p>

      {rows.every((r) => !r.latest) ? (
        <EmptyState kind="recorded" what="runs to certify" />
      ) : (
        <div className="card scroll">
          <table>
            <caption className="sr-only">The most recent batch per feed, and its certification</caption>
            <thead>
              <tr>
                <th scope="col">Feed</th>
                <th scope="col">Batch</th>
                <th scope="col">Verdict</th>
                <th scope="col">Checks</th>
                <th scope="col">Evidence</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ feed, latest, certification }) => (
                <tr className="row" key={feed.feed_id}>
                  <td>{feed.feed_id}</td>
                  <td>
                    {latest ? (
                      <Cited value={latest.batch_id} citationId={latest.citation_id} />
                    ) : (
                      <span className="note">no runs yet</span>
                    )}
                  </td>
                  <td>
                    {certification ? (
                      <Tag tone={verdictTone(certification.verdict)}>{certification.verdict}</Tag>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="num mono">
                    {certification
                      ? `${certification.checks.filter((c) => c.completed && c.passed).length}/${certification.checks.length} passed`
                      : "—"}
                  </td>
                  <td>
                    {latest ? (
                      <Link className="cited" href={`/operations/certification/batch/${latest.batch_id}`}>
                        See the evidence →
                      </Link>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
