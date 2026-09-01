import Link from "next/link";
import { redirect } from "next/navigation";
import { CitationChip } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { api, isRefused, attempt } from "@/lib/api";
import type { Ask, Tool } from "@/lib/types";

/**
 * Ask CINQFLOW — conversation, canvas, and HOW I GOT THERE.
 *
 * Three things this screen makes visible that most AI surfaces hide:
 *   · the TRACE, with per-node latency, so an answer is not a black box;
 *   · the COST of the run, against a cap that actually binds;
 *   · what it could NOT answer, named, rather than padded into prose.
 *
 * An unanswered list that is longer than the claims list is a good outcome,
 * not a failure — it means the platform refused to exceed its grounding.
 */
async function ask(formData: FormData) {
  "use server";
  const question = String(formData.get("question") ?? "").trim();
  if (question) redirect(`/ai/ask?q=${encodeURIComponent(question)}`);
}

export default async function AskPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string }>;
}) {
  const { q } = await searchParams;
  const tools = await attempt<Tool[]>("/api/tools");

  let answer: Ask | null = null;
  let refusal = null;
  if (q) {
    const result = await attempt<Ask>("/api/ask", {
      method: "POST",
      body: JSON.stringify({ question: q }),
    });
    if (isRefused(result)) refusal = result;
    else answer = result;
  }

  return (
    <>
      <h1>Ask CINQFLOW</h1>
      <p className="lede">
        Explain this feed, this plan, this run. Every claim carries a citation you can open.
      </p>

      <form className="ask card" action={ask}>
        <input
          name="question"
          defaultValue={q ?? ""}
          placeholder="why did batch 8842 lose 180 rows?"
          data-ask-input
        />
        <button className="primary" type="submit">
          Ask
        </button>
      </form>

      {refusal && <RefusalNotice refusal={refusal} />}

      {answer && (
        <>
          {answer.refused ? (
            <div className="refusal" data-refusal>
              <strong>Refused</strong>
              <div className="note">{answer.refusal}</div>
            </div>
          ) : (
            <div className="card" data-answer>
              {answer.claims.length === 0 ? (
                <p>
                  <strong>Needs your input.</strong> There is nothing recorded that answers
                  this yet.
                </p>
              ) : (
                answer.claims.map((claim, index) => (
                  <p key={index} data-claim>
                    {claim.text}{" "}
                    {claim.citation_ids.map((citation) => (
                      <CitationChip key={citation} citationId={citation} />
                    ))}
                  </p>
                ))
              )}
              <p className="note">confidence: {answer.confidence}</p>
            </div>
          )}

          {answer.unanswered.length > 0 && (
            <div className="card" data-unanswered>
              <strong>What this answer does not cover</strong>
              <ul className="note">
                {answer.unanswered.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="card">
            <strong>How I got there</strong>
            <table>
              <thead>
                <tr>
                  <th>Node</th>
                  <th>Latency</th>
                </tr>
              </thead>
              <tbody>
                {answer.trace.map((step) => (
                  <tr key={step.node}>
                    <td className="mono">{step.node}</td>
                    <td className="mono">{step.duration_ms} ms</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="note">
              tools called: {answer.tools_called.join(", ") || "none"} · run cost $
              {answer.cost_usd} · run {answer.run_id}
            </p>
          </div>
        </>
      )}

      <h2>What this agent may call</h2>
      <p className="note">
        {isRefused(tools) ? "Certified, read-only, RBAC-scoped tools" : `${tools.length} certified, read-only, RBAC-scoped tools`}. It never writes SQL, and no write
        tool is on its whitelist at any confidence.
      </p>
      <div className="card scroll">
        <table>
          <thead>
            <tr>
              <th>Tool</th>
              <th>Answers</th>
              <th>Cites</th>
            </tr>
          </thead>
          <tbody>
            {!isRefused(tools) &&
              tools.map((tool) => (
                <tr key={tool.name}>
                  <td className="mono">{tool.name}</td>
                  <td>{tool.answers}</td>
                  <td className="note">{tool.cites.join(", ")}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>

      <p className="note">
        Cost, grounding and refusals are on a screen:{" "}
        <Link className="cited" href="/ai/observability">
          LLM Observability
        </Link>
        .
      </p>
    </>
  );
}
