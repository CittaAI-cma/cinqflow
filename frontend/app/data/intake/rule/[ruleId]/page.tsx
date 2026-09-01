import Link from "next/link";
import { CitationChip } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { RowsTable } from "@/components/RowsTable";
import { attempt, isRefused } from "@/lib/api";
import type { Rows } from "@/lib/types";

/**
 * One DQ rule, by rule_id. The destination a `rule:<id>` citation opens.
 *
 * `lookup_reference` takes no feed_id — a rule_id resolves on its own,
 * lexically, against every feed's rules (an exact-term match outranks a
 * description match, so "DQ-002" finds the rule, not every mention of it).
 */
export default async function RulePage({ params }: { params: Promise<{ ruleId: string }> }) {
  const { ruleId } = await params;
  const query = new URLSearchParams({ query: ruleId, limit: "1" });
  const result = await attempt<Rows>(`/api/tools/lookup_reference?${query}`);

  return (
    <>
      <p className="note">
        <Link href="/data/intake">Data Intake</Link> / rule {ruleId}
      </p>
      <h1>{ruleId}</h1>
      <p className="lede">
        <CitationChip citationId={`rule:${ruleId}`} />
      </p>

      {isRefused(result) ? <RefusalNotice refusal={result} /> : <RowsTable result={result} />}

      <div className="card">
        <strong>Ask about this rule</strong>
        <p className="note">Which feeds run it, and at what severity, cited to the source.</p>
        <Link
          className="cited"
          href={`/ai/ask?q=${encodeURIComponent(`what does ${ruleId} check?`)}`}
        >
          Explain this rule →
        </Link>
      </div>
    </>
  );
}
