import Link from "next/link";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";
import type { Feed } from "@/lib/types";

const TABS = [
  { key: "landing", label: "Landing Zone", note: "Files as they arrived, untouched." },
  { key: "bronze", label: "Bronze", note: "An immutable copy of the source." },
  { key: "silver_raw", label: "Silver Raw", note: "Typed, mapped, rule-evaluated." },
] as const;

/**
 * Data Explorer — what data we have, and where it is.
 *
 * Note what this screen does NOT show: rows. Wave 0 exposes the layers' SHAPE
 * and their counts, because no certified tool can emit a member-level row and
 * the UI reads the same tools the agent does. Row-level query over the data
 * layers, with masking underneath it, is CF-V4-E14-04.
 */
export default async function Explorer({
  searchParams,
}: {
  searchParams: Promise<{ tab?: string }>;
}) {
  const { tab = "landing" } = await searchParams;
  const feeds = await attempt<Feed[]>("/api/feeds");
  if (isRefused(feeds)) return <RefusalNotice refusal={feeds} />;
  const current = TABS.find((t) => t.key === tab) ?? TABS[0];

  return (
    <>
      <h1>Data Explorer</h1>
      <p className="lede">What data do we have, and where is it.</p>

      <div className="tabs">
        {TABS.map((t) => (
          <Link
            key={t.key}
            href={`/data/explorer?tab=${t.key}`}
            aria-current={t.key === current.key ? "page" : undefined}
            data-tab={t.key}
          >
            {t.label}
          </Link>
        ))}
      </div>

      <div className="card">
        <strong>{current.label}</strong>
        <p className="note">{current.note}</p>
        {current.key === "bronze" && (
          <p>
            Bronze is <strong>append-only at the database layer</strong> — an{" "}
            <span className="mono">UPDATE</span> or <span className="mono">DELETE</span> is
            refused by a trigger, not by a convention. That is what makes &ldquo;the original is
            untouched&rdquo; a property of the database rather than a promise.
          </p>
        )}
        {current.key === "silver_raw" && (
          <p>
            Silver Raw is the Wave-0 terminus. Identity resolution (gate G4) and Silver ODS are
            Wave 3; their schemas are provisioned and empty.
          </p>
        )}
        {current.key === "landing" && (
          <p>
            Every arriving file is registered — including unexpected ones, which are parked and
            surfaced, never ignored. Open a run in{" "}
            <Link className="cited" href="/operations/control">
              Control Operations
            </Link>{" "}
            to see its input registry.
          </p>
        )}
      </div>

      <h2>Feeds in this layer</h2>
      <div className="card scroll">
        <table>
          <thead>
            <tr>
              <th>Feed</th>
              <th>Domain</th>
              <th>Landing path</th>
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
                <td className="mono">{feed.landing_path}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <strong>Row-level data is not available in Wave 0</strong>
        <p className="note">
          No certified tool can emit a member-level row, in any environment — including the ones
          holding synthetic data, because a tool that is safe only because the data is synthetic
          is not a safe tool. Natural-language query over the data layers is CF-V4-E14-04.
        </p>
      </div>
    </>
  );
}
