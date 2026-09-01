import Link from "next/link";
import { CitationChip } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { RowsTable } from "@/components/RowsTable";
import { attempt, isRefused } from "@/lib/api";
import type { Rows } from "@/lib/types";

/**
 * One landed file, by its content fingerprint. The destination a `file:<hash>`
 * citation opens — resolved with no feed_id in hand.
 */
export default async function LandedFilePage({
  params,
}: {
  params: Promise<{ fingerprint: string }>;
}) {
  const { fingerprint } = await params;
  const query = new URLSearchParams({ fingerprint });
  const result = await attempt<Rows>(`/api/tools/get_file_by_fingerprint?${query}`);
  const feedId =
    !isRefused(result) && result.rows[0] ? (result.rows[0].feed_id as string | undefined) : undefined;

  return (
    <>
      <p className="note">
        <Link href="/data/explorer">Data Explorer</Link> / file {fingerprint}
      </p>
      <h1>{fingerprint}</h1>
      <p className="lede">
        <CitationChip citationId={`file:${fingerprint}`} />
      </p>

      {isRefused(result) ? <RefusalNotice refusal={result} /> : <RowsTable result={result} />}

      {feedId && (
        <p className="note">
          <Link href={`/data/intake/feed/${feedId}`}>See the {feedId} feed →</Link>
        </p>
      )}
    </>
  );
}
