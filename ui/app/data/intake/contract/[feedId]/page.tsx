import Link from "next/link";
import { CitationChip } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { RowsTable } from "@/components/RowsTable";
import { attempt, isRefused } from "@/lib/api";
import type { Rows } from "@/lib/types";

/**
 * One schema contract. The destination a `contract:<feed>@v<n>` citation
 * opens — served by the SAME certified tool (`get_schema_contract`) the
 * agent calls, through the generic `/api/tools/{name}` route.
 */
export default async function ContractPage({
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

  const result = await attempt<Rows>(`/api/tools/get_schema_contract?${query}`);

  return (
    <>
      <p className="note">
        <Link href="/data/intake">Data Intake</Link> /{" "}
        <Link href={`/data/intake/feed/${feedId}`}>{feedId}</Link> / contract
      </p>
      <h1>
        {feedId} <span className="note">schema contract{version ? ` @v${version}` : ""}</span>
      </h1>
      <p className="lede">
        <CitationChip citationId={`contract:${feedId}${version ? `@v${version}` : ""}`} />
      </p>

      {isRefused(result) ? <RefusalNotice refusal={result} /> : <RowsTable result={result} />}

      <div className="card">
        <strong>Ask about this contract</strong>
        <p className="note">
          Which columns are enforced, and at what severity — explained with a citation on every
          claim.
        </p>
        <Link
          className="cited"
          href={`/ai/ask?q=${encodeURIComponent(`what does the ${feedId} contract require?`)}`}
        >
          Explain this contract →
        </Link>
      </div>
    </>
  );
}
