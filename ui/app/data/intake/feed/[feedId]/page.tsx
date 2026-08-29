import Link from "next/link";
import { CitationChip } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { Status } from "@/components/Status";
import { attempt, isRefused } from "@/lib/api";
import type { Feed } from "@/lib/types";

/**
 * One feed. The destination a `feed:<id>@v<n>` citation opens.
 *
 * This is the payoff of making the citation vocabulary the routing primitive:
 * the agent's citation, the breadcrumb and this deep link are the same string,
 * and nobody wrote plumbing to connect them.
 */
export default async function FeedPage({
  params,
  searchParams,
}: {
  params: Promise<{ feedId: string }>;
  searchParams: Promise<{ version?: string }>;
}) {
  const { feedId } = await params;
  const { version } = await searchParams;
  const query = version ? `?version=${encodeURIComponent(version)}` : "";
  const feed = await attempt<Feed>(`/api/feeds/${encodeURIComponent(feedId)}${query}`);
  if (isRefused(feed)) return <RefusalNotice refusal={feed} />;

  return (
    <>
      <p className="note">
        <Link href="/data/intake">Data Intake</Link> / {feed.feed_id}
      </p>
      <h1>{feed.feed_id}</h1>
      <p className="lede">
        <Status word={feed.status} /> &nbsp;
        <CitationChip citationId={feed.citation_id} />
      </p>

      <div className="card">
        <dl className="kv">
          <dt>Domain</dt>
          <dd>{feed.domain}</dd>
          <dt>Source system</dt>
          <dd>{feed.source_system}</dd>
          <dt>File format</dt>
          <dd>{feed.file_format}</dd>
          <dt>Landing path</dt>
          <dd className="mono">{feed.landing_path}</dd>
          <dt>File-name pattern</dt>
          <dd className="mono">{feed.file_pattern}</dd>
          <dt>Schedule</dt>
          <dd className="mono">{feed.schedule_cron}</dd>
          <dt>Lifecycle state</dt>
          <dd>{feed.lifecycle_state}</dd>
        </dl>
      </div>

      <div className="card">
        <strong>Ask about this feed</strong>
        <p className="note">
          The compiled plan, the contract and the rules are explained by the Pipeline Insight
          Agent — every claim carrying a citation that opens the row it came from.
        </p>
        <Link
          className="cited"
          href={`/ai/ask?q=${encodeURIComponent(`what does the ${feed.feed_id} feed do?`)}`}
        >
          Explain this feed →
        </Link>
      </div>
    </>
  );
}
