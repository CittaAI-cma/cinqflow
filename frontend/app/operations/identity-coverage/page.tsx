import { RefusalNotice } from "@/components/Refusal";
import { Tag } from "@/components/Tag";
import { EmptyState } from "@/components/ui/EmptyState";
import { attempt, isRefused } from "@/lib/api";
import type { CoverageSnapshot, ParityCheck } from "@/lib/types";

/**
 * CF-V3-E9-04 — daily identity accounting and coverage telemetry.
 *
 * "CINQFLOW de-risks this decision with daily parity evidence; it does not
 * make it." — 08-open/00-open-questions.md, Q16. There is no button on this
 * screen that decides the OurID cutover, and none that writes to the legacy
 * estate — `legacy_readonly` has no write verb for this screen to call even
 * by mistake. This page is the evidence a human weighs, nothing more.
 *
 * COVERAGE NEVER APPEARS WITHOUT ITS DENOMINATOR. Every percentage here sits
 * beside the `total` it is a share of — "leadership sees Fidelis at 99.8%"
 * is meaningless without knowing 99.8% of WHAT, and a documented don't in
 * the story is reporting one without the other.
 *
 * REGRESSIONS ARE PER SOURCE, NEVER ROLLED UP. A payer sending bad
 * demographics becomes visible only if its row is never averaged into
 * everyone else's — this page renders one card and one history table per
 * source, the same discipline the exception queue's health cards keep.
 */

const HISTORY_DAYS = 30;

export default async function IdentityCoverage() {
  const sources = await attempt<string[]>("/api/identity/telemetry/sources");
  if (isRefused(sources)) return <RefusalNotice refusal={sources} />;

  const [coverageBySource, parityBySource] = await Promise.all([
    Promise.all(
      sources.map((source) =>
        attempt<CoverageSnapshot[]>(
          `/api/identity/telemetry/coverage/${encodeURIComponent(source)}?days=${HISTORY_DAYS}`,
        ),
      ),
    ),
    Promise.all(
      sources.map((source) =>
        attempt<ParityCheck[]>(
          `/api/identity/telemetry/parity/${encodeURIComponent(source)}?days=${HISTORY_DAYS}`,
        ),
      ),
    ),
  ]);

  return (
    <>
      <h1>Identity Coverage &amp; Parity</h1>
      <p className="lede">
        What share of records carry Verato&rsquo;s LinkId and the legacy OurId, per source,
        trending over time — the evidence the OurID cutover decision is made against, not the
        decision itself.
      </p>

      {sources.length === 0 ? (
        <EmptyState kind="recorded" what="identity coverage telemetry" />
      ) : (
        <div className="grid" style={{ marginBottom: "var(--s-4)" }}>
          {sources.map((source, index) => {
            const history = coverageBySource[index];
            const parity = parityBySource[index];
            const latest = !isRefused(history) && history.length > 0 ? history[0] : null;
            const latestParity =
              !isRefused(parity) && parity.length > 0 ? parity[0] : null;
            return (
              <div className="card" key={source}>
                <h3 style={{ marginBottom: "var(--s-2)" }}>{source}</h3>
                {latest ? (
                  <dl className="kv">
                    <dt>Both keys</dt>
                    <dd className="num mono">
                      {latest.both_coverage_pct}%{" "}
                      <span className="note">of {latest.total.toLocaleString()}</span>
                    </dd>
                    <dt>LinkId</dt>
                    <dd className="num mono">{latest.link_id_coverage_pct}%</dd>
                    <dt>OurId</dt>
                    <dd className="num mono">{latest.our_id_coverage_pct}%</dd>
                    {latest.is_regression ? (
                      <>
                        <dt>Regression</dt>
                        <dd>
                          <Tag tone="bad">-{latest.drop_points} pts overnight</Tag>
                        </dd>
                      </>
                    ) : null}
                  </dl>
                ) : (
                  <p className="empty">No coverage recorded yet.</p>
                )}
                {latestParity ? (
                  <p className="note" style={{ marginTop: "var(--s-2)" }}>
                    Parity: {latestParity.match_rate_pct}% match ({latestParity.matched} of{" "}
                    {latestParity.checked} checked against the legacy estate, read-only)
                  </p>
                ) : null}
              </div>
            );
          })}
        </div>
      )}

      {sources.map((source, index) => {
        const history = coverageBySource[index];
        if (isRefused(history) || history.length === 0) return null;
        return (
          <div className="card flush scroll" key={source} style={{ marginBottom: "var(--s-3)" }}>
            <table>
              <caption>
                {source} — coverage trend, newest first ({HISTORY_DAYS} days)
              </caption>
              <thead>
                <tr>
                  <th scope="col">Date</th>
                  <th scope="col">Total</th>
                  <th scope="col">LinkId</th>
                  <th scope="col">OurId</th>
                  <th scope="col">Both</th>
                  <th scope="col">Flag</th>
                </tr>
              </thead>
              <tbody>
                {history.map((row) => (
                  <tr className="row" key={row.business_date}>
                    <td className="mono">{row.business_date}</td>
                    <td className="num mono">{row.total.toLocaleString()}</td>
                    <td className="num mono">{row.link_id_coverage_pct}%</td>
                    <td className="num mono">{row.our_id_coverage_pct}%</td>
                    <td className="num mono">{row.both_coverage_pct}%</td>
                    <td>
                      {row.is_regression ? (
                        <Tag tone="bad">-{row.drop_points} pts</Tag>
                      ) : (
                        <span className="note">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
    </>
  );
}
