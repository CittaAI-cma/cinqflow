import Link from "next/link";
import { RefusalNotice } from "@/components/Refusal";
import { Tag, type Tone } from "@/components/Tag";
import { EmptyState } from "@/components/ui/EmptyState";
import { MetricTile } from "@/components/ui/MetricTile";
import { attempt, isRefused } from "@/lib/api";

/**
 * CF-V1-E6-02 (W1-35/F6) — the acceptance rate per agent per week.
 *
 * `core.proposals.measure` already graded ONE proposal, and
 * `GET /api/proposals/{id}/acceptance` already exposed it — that route's own
 * docstring already called the acceptance rate per agent per week "THE
 * health metric." This is the first place the SUM across proposals has
 * anywhere to be seen.
 *
 * ONE PAGE, NOT ONE PER AGENT. `agent` is a query parameter read by
 * `GET /api/agents/{agent}/acceptance`, the same shape the review queue
 * already uses for `?agent=` — so a fifth R2 agent that starts writing
 * proposals tomorrow needs no new screen, only a new link below.
 */
type Acceptance = {
  total: number;
  accepted: number;
  corrected: number;
  rate: number;
  deterministic_total: number;
  deterministic_corrected: number;
  inferred_total: number;
  inferred_corrected: number;
  inferred_rate: number;
  additions: number;
  report: string;
};

type WeeklyAcceptance = {
  agent: string;
  week: string;
  proposal_count: number;
  acceptance: Acceptance;
};

const AGENT_LABEL: Record<string, string> = {
  "schema-inference": "Schema inference",
  "phi-detection": "PHI detection",
  "mapping-suggestion": "Mapping suggestion",
  "rule-authoring": "Rule authoring",
  "fingerprint-match": "Fingerprint match",
};

//: The R2 agents that write reviewable proposals today. A shortcut list for
//: the links below, not a constraint on the route — `?agent=` accepts any
//: name, including one this list has never heard of.
const KNOWN_AGENTS = Object.keys(AGENT_LABEL);

const DEFAULT_AGENT = "mapping-suggestion";

function rateTone(rate: number): Tone {
  // The same 90/70 split `core.reliability.Bands` illustrates elsewhere —
  // not imported (the frontend has no access to core), but not invented
  // fresh either, so a reviewer does not learn a second pair of thresholds.
  if (rate >= 0.9) return "good";
  if (rate >= 0.7) return "pending";
  return "bad";
}

export default async function AgentAcceptancePage({
  searchParams,
}: {
  searchParams: Promise<{ agent?: string }>;
}) {
  const { agent = DEFAULT_AGENT } = await searchParams;
  const result = await attempt<WeeklyAcceptance[]>(
    `/api/agents/${encodeURIComponent(agent)}/acceptance`,
  );

  const crumbs = (
    <p className="note">
      <Link href="/ai/observability">AI</Link> / acceptance
    </p>
  );

  if (isRefused(result)) {
    return (
      <>
        {crumbs}
        <h1>Agent Acceptance</h1>
        <RefusalNotice refusal={result} />
      </>
    );
  }

  // Oldest week first — the same order `weekly_acceptance` returns it in, so
  // a reviewer's eye moves left to right through time exactly as the API
  // meant it to, the same convention `core.reliability.trend()` uses.
  const latest = result.at(-1);
  const prior = result.length > 1 ? result[result.length - 2] : undefined;
  const trendPoints =
    latest && prior ? Math.round((latest.acceptance.rate - prior.acceptance.rate) * 100) : null;
  const decidedTotal = result.reduce((sum, week) => sum + week.proposal_count, 0);

  return (
    <>
      {crumbs}
      <h1>Agent Acceptance</h1>
      <p className="lede">
        How often a person accepted what {AGENT_LABEL[agent] ?? agent} proposed, summed by ISO
        week — not averaged, so one hard proposal cannot be outvoted by five easy ones.
      </p>

      <p className="note">
        {KNOWN_AGENTS.map((name, index) => (
          <span key={name}>
            {index > 0 ? " · " : ""}
            {name === agent ? (
              <strong>{AGENT_LABEL[name]}</strong>
            ) : (
              <Link href={`/ai/acceptance?agent=${encodeURIComponent(name)}`}>
                {AGENT_LABEL[name]}
              </Link>
            )}
          </span>
        ))}
      </p>

      {result.length === 0 ? (
        <EmptyState
          kind="recorded"
          what={`accepted proposals from ${AGENT_LABEL[agent] ?? agent}`}
        />
      ) : (
        <>
          <div className="grid">
            <MetricTile
              label="Latest week"
              value={latest ? `${Math.round(latest.acceptance.rate * 100)}%` : "—"}
              hint={latest ? `${latest.week} · ${latest.proposal_count} decided` : undefined}
            />
            <MetricTile
              label="Week over week"
              value={trendPoints === null ? "—" : `${trendPoints > 0 ? "+" : ""}${trendPoints}pp`}
              hint={prior ? `vs ${prior.week}` : "only one week recorded so far"}
            />
            <MetricTile
              label="Weeks recorded"
              value={result.length}
              hint={`${decidedTotal} decided proposal${decidedTotal === 1 ? "" : "s"} total`}
            />
          </div>

          <div className="card flush scroll">
            <table>
              <caption className="sr-only">
                {AGENT_LABEL[agent] ?? agent}&apos;s acceptance rate, oldest week first
              </caption>
              <thead>
                <tr>
                  <th scope="col">Week</th>
                  <th scope="col">Proposals</th>
                  <th scope="col">Accepted / Total</th>
                  <th scope="col">Rate</th>
                  <th scope="col">Deterministic</th>
                  <th scope="col">Inferred</th>
                </tr>
              </thead>
              <tbody>
                {result.map((week) => (
                  <tr className="row" key={week.week}>
                    <td className="mono">{week.week}</td>
                    <td className="num">{week.proposal_count}</td>
                    <td className="num mono">
                      {week.acceptance.accepted}/{week.acceptance.total}
                    </td>
                    <td>
                      <Tag tone={rateTone(week.acceptance.rate)}>
                        {Math.round(week.acceptance.rate * 100)}%
                      </Tag>
                    </td>
                    <td className="num mono">
                      {week.acceptance.deterministic_total -
                        week.acceptance.deterministic_corrected}
                      /{week.acceptance.deterministic_total}
                    </td>
                    <td className="num mono">{Math.round(week.acceptance.inferred_rate * 100)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card note">
            Summed across every proposal a person decided that week, never averaged — the same
            arithmetic <code>core.proposals.measure</code> already uses for one proposal, added
            up rather than re-invented for many.
          </div>
        </>
      )}
    </>
  );
}
