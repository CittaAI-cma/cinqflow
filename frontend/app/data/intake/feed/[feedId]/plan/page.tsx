import Link from "next/link";
import { CitationChip } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { RowsTable } from "@/components/RowsTable";
import { attempt, isRefused } from "@/lib/api";
import type { Rows } from "@/lib/types";

/**
 * The compiled plan. The destination a `plan:<feed>@v<n>` citation opens —
 * the IR the engine will actually run, step by step: read, validate,
 * land_bronze, cast, map, evaluate_rules, resolve_identity, load, reconcile.
 */
export default async function PlanPage({
  params,
  searchParams,
}: {
  params: Promise<{ feedId: string }>;
  searchParams: Promise<{ version?: string }>;
}) {
  const { feedId } = await params;
  const { version } = await searchParams;
  const query = new URLSearchParams({ feed_id: feedId });
  if (version) query.set("version", version);

  const result = await attempt<Rows>(`/api/tools/get_compiled_plan?${query}`);

  return (
    <>
      <p className="note">
        <Link href="/data/intake">Data Intake</Link> /{" "}
        <Link href={`/data/intake/feed/${feedId}`}>{feedId}</Link> / plan
      </p>
      <h1>
        {feedId} <span className="note">compiled plan{version ? ` @v${version}` : ""}</span>
      </h1>
      <p className="lede">
        <CitationChip citationId={`plan:${feedId}${version ? `@v${version}` : ""}`} />
      </p>

      {isRefused(result) ? (
        <RefusalNotice refusal={result} />
      ) : (
        <>
          <RowsTable result={result} />
          <div className="card note">
            This is the artifact the engine runs, the agent explains, and the eval harness grades
            against — one plan, three jobs.
          </div>
        </>
      )}
    </>
  );
}
