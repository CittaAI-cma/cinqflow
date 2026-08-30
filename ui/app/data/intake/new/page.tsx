import Link from "next/link";
import { RegisterFeedForm } from "@/components/RegisterFeedForm";
import { attempt, isRefused } from "@/lib/api";
import type { Principal } from "@/lib/types";
import { registerFeed } from "./actions";

/**
 * CF-V0-E3-01 — step 0 of the five-step wizard, which nothing before this
 * page produced: "Upload sample" needs a feed to upload TO, and until this
 * existed the only way to get one was `POST /api/feeds` from a terminal.
 *
 * Created as a DRAFT, always. Everything after this screen — approve the
 * inferred schema, map fields, define rules, publish — reads the feed_id
 * this saves; nothing here is executable on its own.
 */
export default async function RegisterNewFeed({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const result = await searchParams;
  const me = await attempt<Principal>("/api/me");
  const mayRegister = !isRefused(me) && me.permitted_actions.includes("create_feed");
  const outcome = result.outcome;

  return (
    <>
      <p className="note">
        <Link href="/data/intake">Data Intake</Link> / Register a new feed
      </p>
      <h1>Register a new feed</h1>
      <p className="lede">
        Six fields, plus a real sample name. Saved as a Draft — nothing here is executable until
        it is published through the four steps after this one.
      </p>

      {outcome ? (
        <div className="card outcome" data-outcome={outcome}>
          <strong className="outcome-word">{outcome}</strong>
          <p>{result.headline}</p>
          {outcome === "CREATED" && result.feed ? (
            <p className="inline">
              <Link className="cited" href={`/data/intake/feed/${result.feed}`}>
                Open {result.feed} →
              </Link>
              <Link className="cited" href={`/data/intake/feed/${result.feed}/deliver`}>
                Upload its first sample →
              </Link>
            </p>
          ) : null}
        </div>
      ) : null}

      <div className="card">
        <RegisterFeedForm action={registerFeed} mayRegister={mayRegister} />
      </div>
    </>
  );
}
