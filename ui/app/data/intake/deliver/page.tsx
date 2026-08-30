import Link from "next/link";
import { DeliverForm } from "@/components/DeliverForm";
import { DeliveryOutcome, landedFrom } from "@/components/DeliveryOutcome";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";
import type { DeliverySource, Feed, Principal } from "@/lib/types";
import { deliverFile } from "./actions";

/**
 * CF-V1-E3-05 — Data Intake's own door.
 *
 * The wizard's first step is "Upload sample", and until this page existed the
 * only way to reach it was to already know which feed you wanted, open it, and
 * find a link on its page. That is backwards for the case that actually
 * happens: somebody is holding a file a payer sent and needs to get it into
 * the platform. The feed is the QUESTION here, not the address.
 *
 * The feed's own page keeps its delivery step — same action, same form, same
 * outcome panel, with the feed locked because there it IS the address.
 */
export default async function DeliverToAFeed({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const result = await searchParams;
  const [feeds, me] = await Promise.all([
    attempt<Feed[]>("/api/feeds"),
    attempt<Principal>("/api/me"),
  ]);
  if (isRefused(feeds)) return <RefusalNotice refusal={feeds} />;

  const chosen = result.feed && feeds.some((feed) => feed.feed_id === result.feed) ? result.feed : "";
  const source = chosen
    ? await attempt<DeliverySource>(
        `/api/feeds/${encodeURIComponent(chosen)}/deliveries/source`,
      )
    : null;
  const mayDeliver = !isRefused(me) && me.permitted_actions.includes("edit_feed");
  const landed = landedFrom(result);

  return (
    <>
      <p className="note">
        <Link href="/data/intake">Data Intake</Link> / Deliver a file
      </p>
      <h1>Deliver a file</h1>
      <p className="lede">
        The first of the five steps. Everything after this reads the file you send: the schema is
        inferred from it, the mapping is checked against it, and the rules are tested on it.
      </p>

      {landed ? <DeliveryOutcome landed={landed} /> : null}

      {feeds.length === 0 ? (
        <div className="card empty">
          <span className="empty-title">There is no feed to deliver to yet</span>
          A file belongs to a feed — the feed is what decides where it lands and which pattern it
          is matched against. <Link href="/data/intake/new">Register one first</Link>.
        </div>
      ) : (
        <div className="card">
          <DeliverForm
            action={deliverFile}
            feeds={feeds}
            selected={chosen}
            returnTo="/data/intake/deliver"
            today={new Date().toISOString().slice(0, 10)}
            mayDeliver={mayDeliver}
          />
        </div>
      )}

      {source && !isRefused(source) && !source.reachable ? (
        <div className="refusal">
          <strong>No delivery source is fitted</strong>
          <div className="note">{source.detail}</div>
        </div>
      ) : null}

      <div className="card">
        <strong>What happens to it</strong>
        <p className="note">
          The upload is a CONNECTOR call, not a write. By the time landing controls see your file
          it is indistinguishable from one an SFTP poller fetched: same fingerprint check, same
          pattern match, same registry row, same four outcomes.
        </p>
        <dl className="kv">
          <dt>Accepted</dt>
          <dd>
            Moved to <span className="mono">processed/</span>, registered, and profiled — row
            counts, type readings, null counts and key candidates, all computed.
          </dd>
          <dt>Unexpected</dt>
          <dd>
            The name matched no pattern. Moved to <span className="mono">parked/</span> and
            registered anyway, with the closest feed named. Never discarded.
          </dd>
          <dt>Rejected</dt>
          <dd>
            A named pre-flight check declined it — empty, or far outside the size this feed
            usually carries. Moved to <span className="mono">rejected/</span>.
          </dd>
          <dt>Skipped</dt>
          <dd>
            The fingerprint is already in the input registry. Deliver the same content twice and
            the second is archived rather than loaded again.
          </dd>
        </dl>
      </div>
    </>
  );
}
