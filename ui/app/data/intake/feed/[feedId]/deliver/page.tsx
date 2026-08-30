import Link from "next/link";
import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { CitationChip } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused, upload, Refused } from "@/lib/api";
import type { Delivery, DeliverySource, Feed } from "@/lib/types";

/**
 * CF-V1-E3-05 — the wizard's first step, which until now named a capability
 * no route provided.
 *
 * The screen shows the LANDING DECISION, not "upload succeeded". Those are
 * different facts and the second one is nearly worthless: the bytes almost
 * always arrive, and what a BA needs to know is whether the platform accepted
 * them, and if not, which named check said no.
 */
export default async function DeliverPage({
  params,
  searchParams,
}: {
  params: Promise<{ feedId: string }>;
  searchParams: Promise<{ outcome?: string; headline?: string; next?: string; cite?: string }>;
}) {
  const { feedId } = await params;
  const result = await searchParams;
  const feed = await attempt<Feed>(`/api/feeds/${encodeURIComponent(feedId)}`);
  if (isRefused(feed)) return <RefusalNotice refusal={feed} />;

  const source = await attempt<DeliverySource>(
    `/api/feeds/${encodeURIComponent(feedId)}/deliveries/source`,
  );

  async function deliver(formData: FormData) {
    "use server";
    const file = formData.get("file");
    if (!(file instanceof File) || file.size === 0) {
      return redirectWith(feedId, {
        outcome: "REJECTED",
        headline: "Choose a file first — nothing was sent.",
        next: "",
      });
    }
    const body = new FormData();
    body.set("file", file);
    body.set("business_date", String(formData.get("business_date") ?? ""));
    const checksum = String(formData.get("checksum") ?? "").trim();
    if (checksum) body.set("checksum", checksum);

    try {
      const delivery = await upload<Delivery>(
        `/api/feeds/${encodeURIComponent(feedId)}/deliveries`,
        body,
      );
      revalidatePath(`/data/intake/feed/${feedId}`);
      return redirectWith(feedId, {
        outcome: delivery.outcome,
        headline: delivery.headline,
        next: delivery.next_step,
        cite: delivery.citation_id,
      });
    } catch (error) {
      // A refusal is a DECISION the server made. It is rendered, never
      // swallowed — the same rule every other screen here follows.
      const detail = error instanceof Refused ? error.message : String(error);
      return redirectWith(feedId, { outcome: "REJECTED", headline: detail, next: "" });
    }
  }

  const outcome = result.outcome;
  const accepted = outcome === "ACCEPTED";

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

      {outcome ? (
        <div className="card">
          <strong>{outcome}</strong>
          <p className="note">{result.headline}</p>
          {result.next ? <p className="note">{result.next}</p> : null}
          {result.cite ? (
            <p className="note">
              <CitationChip citationId={result.cite} />
            </p>
          ) : null}
          {accepted ? (
            <p className="note">
              <Link className="cited" href={`/data/intake/feed/${feed.feed_id}`}>
                Back to the feed →
              </Link>
            </p>
          ) : null}
        </div>
      ) : null}

      {!isRefused(source) && !source.reachable ? (
        <div className="card">
          <strong>No delivery source is available</strong>
          <p className="note">{source.detail}</p>
        </div>
      ) : null}

      <div className="card">
        <form action={deliver}>
          <p>
            <label htmlFor="file">The file the payer sent</label>
            <br />
            <input id="file" name="file" type="file" required />
          </p>
          <p className="note">
            It must match this feed&rsquo;s file-name pattern{" "}
            <span className="mono">{feed.file_pattern}</span>. A file that does not is not
            discarded — it is parked, registered and surfaced, with the reason.
          </p>
          <p>
            <label htmlFor="business_date">Business date</label>
            <br />
            <input
              id="business_date"
              name="business_date"
              type="date"
              required
              defaultValue={new Date().toISOString().slice(0, 10)}
            />
          </p>
          <p className="note">
            The month the data is ABOUT, not today. A roster for August delivered in September is
            August&rsquo;s, and only you know that.
          </p>
          <p>
            <label htmlFor="checksum">Checksum the sender declared (optional)</label>
            <br />
            <input id="checksum" name="checksum" type="text" placeholder="sha256-…" />
          </p>
          <p className="note">
            Supplied, it is checked before anything is written. A transfer that arrived damaged is
            the one thing refused outright rather than landed and rejected.
          </p>
          <button className="primary" type="submit">
            Deliver
          </button>
        </form>
      </div>

      <div className="card">
        <strong>Where it goes</strong>
        <p className="note">
          Into <span className="mono">{feed.landing_path}/incoming/&lt;business date&gt;/</span>,
          through the same landing controls, fingerprint check and registry entry as a file an
          SFTP poller fetches. Deliver the same content twice and the second is skipped — the
          fingerprint is already in the input registry.
        </p>
      </div>
    </>
  );
}

/**
 * Post-Redirect-Get, so the outcome survives a refresh without re-delivering.
 *
 * A refresh that re-POSTed would attempt the same file again — and the second
 * attempt is SKIPPED by fingerprint, which is correct behaviour reported as a
 * confusing screen. Better not to ask the question.
 */
function redirectWith(
  feedId: string,
  result: { outcome: string; headline: string; next: string; cite?: string },
): never {
  const query = new URLSearchParams({
    outcome: result.outcome,
    headline: result.headline,
    next: result.next,
    ...(result.cite ? { cite: result.cite } : {}),
  });
  redirect(`/data/intake/feed/${feedId}/deliver?${query.toString()}`);
}
