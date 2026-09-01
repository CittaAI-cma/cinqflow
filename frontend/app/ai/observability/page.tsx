import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";
import type { AgentAction, Budget } from "@/lib/types";

/**
 * LLM Observability — every run, its cost against cap, and its REFUSALS.
 *
 * This ships in Wave 0 because the gateway produces budgets, prompt hashes and
 * refusal events from day one, and because "if it is not on a screen, it is not
 * being governed". The refusals column is the point: a governed AI layer that
 * only shows what the agent was allowed to do cannot answer "what did it try?"
 */
export default async function Observability() {
  const budget = await attempt<Budget>("/api/llm-budget");
  const actions = await attempt<AgentAction[]>("/api/agent-actions?limit=200");
  if (isRefused(budget)) return <RefusalNotice refusal={budget} />;
  if (isRefused(actions)) return <RefusalNotice refusal={actions} />;

  const spent = Number(budget.spent_today_usd);
  const cap = Number(budget.daily_cap_usd);

  return (
    <>
      <h1>LLM Observability</h1>
      <p className="lede">
        Every agent run, its cost against cap, its grounding, and every refusal.
      </p>

      <div className="grid">
        <div className="card">
          <div className="note">Spent today · {budget.agent}</div>
          <div className="big">${budget.spent_today_usd}</div>
          <div className="note">
            of ${budget.daily_cap_usd} daily cap ({cap > 0 ? Math.round((spent / cap) * 100) : 0}%)
            · ${budget.per_run_cap_usd} per run
          </div>
        </div>
        <div className="card">
          <div className="note">Runs today</div>
          <div className="big">{budget.runs_today}</div>
        </div>
        <div className="card">
          <div className="note">Refusals today</div>
          <div className="big">{budget.refusals_today}</div>
          <div className="note">a refusal is a control working, not a failure</div>
        </div>
        <div className="card">
          <div className="note">Uncited claims blocked</div>
          <div className="big">{budget.uncited_claims_blocked}</div>
          <div className="note">claims citing rows no tool returned</div>
        </div>
      </div>

      <h2>Agent actions</h2>
      <div className="card scroll">
        <table>
          <thead>
            <tr>
              <th>When</th>
              <th>Run</th>
              <th>Action</th>
              <th>Outcome</th>
              <th>Model</th>
              <th>Prompt</th>
              <th>Tokens</th>
              <th>Cost</th>
              <th>Risk</th>
            </tr>
          </thead>
          <tbody>
            {actions.map((row, index) => (
              <tr className="row" key={index} data-outcome={row.outcome}>
                <td className="mono">{row.occurred_ts.slice(0, 19).replace("T", " ")}</td>
                <td className="mono">{row.run_id}</td>
                <td className="mono">{row.action}</td>
                <td className={row.is_refusal ? "uncited" : undefined}>{row.outcome}</td>
                <td className="mono">{row.model_version || row.model || "—"}</td>
                <td className="mono" title={row.prompt_hash}>
                  {row.prompt_ref || "—"}
                </td>
                <td className="mono">
                  {row.prompt_tokens + row.completion_tokens || "—"}
                </td>
                <td className="mono">${row.cost_usd}</td>
                <td className="mono">{row.risk_class}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card note">
        Every model call carries a prompt hash, a model version, a cost and a caller — 100% of
        them, because those fields are part of the gateway&apos;s return value rather than
        something a caller is trusted to record.
      </div>
    </>
  );
}
