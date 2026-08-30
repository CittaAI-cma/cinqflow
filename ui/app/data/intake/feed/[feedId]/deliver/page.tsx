import Link from "next/link";
import { DeliverForm } from "@/components/DeliverForm";
import { DeliveryOutcome, landedFrom } from "@/components/DeliveryOutcome";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";
import type { DeliverySource, Feed, Principal } from "@/lib/types";
import { deliverFile } from "../../../deliver/actions";

/**
 * CF-V1-E3-05 — one feed's delivery step.
 *
 * The same form and the same action as `/data/intake/deliver`, with the feed
 * LOCKED: here the feed is the address, not a question. Two doors into one
 * route rather than two routes, because the second implementation is where
 * the business date gets formatted differently or a refusal gets swallowed.
 */
export default async function DeliverPage({
  params,
  searchParams,
}: {
  params: Promise<{ feedId: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const { feedId } = await params;
  const result = await searchParams;
  const [feed, me] = await Promise.all([
    attempt<Feed>(`/api/feeds/${encodeURIComponent(feedId)}`),
    attempt<Principal>("/api/me"),
  ]);
  if (isRefused(feed)) return <RefusalNotice refusal={feed} />;

  const source = await attempt<DeliverySource>(
    `/api/feeds/${encodeURIComponent(feedId)}/deliveries/source`,
  );
  const mayDeliver = !isRefused(me) && me.permitted_actions.includes("edit_feed");
  const landed = landedFrom(result, feed.feed_id);

  return (
    <>
      <p className="note">
        <Link href="/data/intake">Data Intake</Link> /{" "}
        <Link href={`/data/intake/feed/${feed.feed_id}`}>{feed.feed_id}</Link> / Deliver a file
      </p>
      <h1>Upload a sample file</h1>
      <p className="lede">
        The first of the five steps. Everything after this reads the file you send: the schema is
        inferred from it, the mapping is checked against it, and the rules are tested on it.
      </p>

      {landed ? <DeliveryOutcome landed={landed} /> : null}

      {!isRefused(source) && !source.reachable ? (
        <div className="refusal">
          <strong>No delivery source is fitted</strong>
          <div className="note">{source.detail}</div>
        </div>
      ) : null}

      <div className="card">
        <DeliverForm
          action={deliverFile}
          feeds={[feed]}
          selected={feed.feed_id}
          locked
          returnTo={`/data/intake/feed/${feed.feed_id}/deliver`}
          today={new Date().toISOString().slice(0, 10)}
          mayDeliver={mayDeliver}
        />
      </div>

      <div className="card">
        <strong>Where it goes</strong>
        <p className="note">
          Into <span className="mono">{feed.landing_path}/incoming/&lt;business date&gt;/</span>,
          through the same landing controls, fingerprint check and registry entry as a file an
          SFTP poller fetches. This feed&rsquo;s file-name pattern is{" "}
          <span className="mono">{feed.file_pattern}</span>; a file that does not match it is not
          discarded — it is parked, registered and surfaced, with the reason. Deliver the same
          content twice and the second is skipped, because the fingerprint is already in the input
          registry.
        </p>
      </div>
    </>
  );
}
