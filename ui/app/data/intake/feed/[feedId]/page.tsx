import { Fragment } from "react";
import Link from "next/link";
import { CitationChip } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { Status } from "@/components/Status";
import { attempt, isRefused } from "@/lib/api";
import type { Feed, FeedProfile, FeedSuspension } from "@/lib/types";

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

  // CF-V1-E3-04. A SECOND AXIS, not a lifecycle state: a paused feed is still
  // Published, which is why "which version was live in March" keeps answering.
  const suspension = await attempt<FeedSuspension>(
    `/api/feeds/${encodeURIComponent(feedId)}/suspension`,
  );

  // CF-V1-E3-05/CF-V1-E5-01. `GET /api/feeds/{id}/profiles` has existed since
  // the delivery step shipped, and until now nothing on this page — the ONE
  // place a person lands after delivering a sample and coming back later —
  // ever called it. The redirect from the delivery form carries a one-time
  // link to the profile it just made; navigate away and there was no way
  // back to it at all, for a feed with real, already-computed evidence
  // sitting in the database.
  const profiles = await attempt<FeedProfile[]>(
    `/api/feeds/${encodeURIComponent(feedId)}/profiles`,
  );

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

      {!isRefused(suspension) && suspension.is_paused ? (
        <div className="card">
          <strong>Paused — no new batch will start</strong>
          <p className="note">{suspension.explanation}</p>
          <p className="note">
            This feed is still {feed.lifecycle_state}. Pausing is an operational decision on a
            separate axis from the lifecycle, so lifting it needs no approver.
          </p>
        </div>
      ) : null}

      {/* CF-V1-E3-02. The SAME computation the submit button enforces — so
          this list can never show green while activation returns 403. */}
      {feed.readiness && !feed.readiness.is_ready ? (
        <div className="card">
          <strong>
            Not ready to activate — {feed.readiness.outstanding} thing(s) still missing
          </strong>
          <p className="note">
            You can keep saving as you go. What is blocked is asking somebody to review a feed
            nobody could operate yet.
          </p>
          <dl className="kv">
            {feed.readiness.items
              .filter((item) => !item.satisfied)
              .map((item) => (
                <Fragment key={item.key}>
                  <dt>{item.question}</dt>
                  <dd>
                    {item.why_it_matters}
                    <br />
                    <span className="note">To fix: {item.how_to_fix}</span>
                  </dd>
                </Fragment>
              ))}
          </dl>
        </div>
      ) : null}

      <div className="card">
        <strong>Who runs this feed</strong>
        {feed.operations.owners.length === 0 ? (
          <p className="note">Nobody is named yet.</p>
        ) : (
          <dl className="kv">
            {feed.operations.owners.map((owner) => (
              <Fragment key={owner.role}>
                <dt>{owner.role.replace("_", " ")}</dt>
                <dd>
                  {owner.display_name} <span className="note">({owner.subject})</span>
                </dd>
              </Fragment>
            ))}
          </dl>
        )}
      </div>

      <div className="card">
        <strong>When it is due, and what happens when it is not</strong>
        {feed.operations.service_level ? (
          <p className="note">
            Expected by {feed.operations.service_level.expected_by_local_time}{" "}
            {feed.operations.service_level.timezone} on{" "}
            {feed.operations.service_level.calendar.replace(/_/g, " ")}, with{" "}
            {feed.operations.service_level.grace_minutes} minutes of grace.
          </p>
        ) : (
          <p className="note">
            No arrival time is set, so this feed can never be Missing — only not-yet-arrived.
          </p>
        )}
        {feed.operations.alert_chain.length > 0 ? (
          <ol>
            {feed.operations.alert_chain.map((tier) => (
              <li key={tier.after_minutes}>
                after {tier.after_minutes} minutes — {tier.channel} to {tier.notify.join(", ")}
              </li>
            ))}
          </ol>
        ) : null}
      </div>

      {feed.operations.volume ? (
        <div className="card">
          <strong>What a normal delivery looks like</strong>
          <p className="note">
            {feed.operations.volume.typical_records !== null
              ? `Typically ${feed.operations.volume.typical_records.toLocaleString()} records, ±${feed.operations.volume.tolerance_percent}%.`
              : `Between ${feed.operations.volume.minimum_records ?? "?"} and ${feed.operations.volume.maximum_records ?? "?"} records.`}{" "}
            A delivery outside this is loadable and wrong, which is the failure nothing else
            catches.
          </p>
        </div>
      ) : null}

      {feed.operations.documents.length > 0 ? (
        <div className="card">
          <strong>Documents</strong>
          <ul>
            {feed.operations.documents.map((doc) => (
              <li key={doc.reference}>
                <a href={doc.reference}>{doc.label}</a>{" "}
                <span className="note">{doc.kind.replace(/_/g, " ")}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="card">
        <strong>Recent deliveries</strong>
        {isRefused(profiles) || profiles.length === 0 ? (
          <p className="note">
            Nothing delivered yet. Uploading a sample is the first of the five onboarding steps —
            everything after it reads the file you send.
          </p>
        ) : (
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th>File</th>
                  <th>Rows</th>
                  <th>Columns</th>
                  <th>Would load</th>
                  <th>Profiled</th>
                </tr>
              </thead>
              <tbody>
                {profiles.map((profile) => (
                  <tr className="row" key={profile.profile_id}>
                    <td>
                      <Link
                        className="cited"
                        href={`/data/intake/profile/${profile.profile_id}`}
                      >
                        {profile.source_key.split("/").pop()}
                      </Link>
                    </td>
                    <td className="num">{profile.structure.data_rows.toLocaleString()}</td>
                    <td className="num">{profile.structure.column_count}</td>
                    <td>{profile.would_load ? "Yes" : `No — ${profile.refusal?.reason ?? ""}`}</td>
                    <td>{new Date(profile.profiled_ts).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card">
        <strong>Deliver a file</strong>
        <p className="note">
          Send a sample the payer has actually delivered. It lands through the same controls as
          an SFTP poller&rsquo;s file — registered, fingerprinted and classified — and what comes
          back is the landing decision, not just &ldquo;uploaded&rdquo;.
        </p>
        <Link className="cited" href={`/data/intake/feed/${feed.feed_id}/deliver`}>
          Upload a sample file →
        </Link>
      </div>

      <div className="card">
        <strong>Column mapping</strong>
        <p className="note">
          Which canonical field each source column populates, and — just as importantly — which
          target fields nothing populates, with the reason somebody gave.
        </p>
        <Link className="cited" href={`/data/intake/mapping/${feed.feed_id}`}>
          Open the mapping →
        </Link>
      </div>

      <div className="card">
        <strong>Data-quality rules</strong>
        <p className="note">
          What each rule catches on the sampled delivery — tested, passed and failed counts, with
          the failing rows. Trust is built in the preview, not the prose.
        </p>
        <Link className="cited" href={`/data/intake/rules/${feed.feed_id}`}>
          Preview the rules →
        </Link>
      </div>

      <div className="card">
        <strong>History</strong>
        <p className="note">
          Every version is still here — &ldquo;which version was live in March?&rdquo; is one
          click, and so is what changed between any two of them.
        </p>
        <Link className="cited" href={`/data/intake/feed/${feed.feed_id}/history`}>
          Version history and comparison →
        </Link>
      </div>

      <div className="card">
        <strong>Start a new feed from this one</strong>
        <p className="note">
          A clone inherits the contract, the mappings and the rules — and none of the approval.
        </p>
        <Link className="cited" href={`/data/intake/feed/${feed.feed_id}/clone`}>
          Clone this feed →
        </Link>
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
