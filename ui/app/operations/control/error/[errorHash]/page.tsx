import Link from "next/link";
import { CitationChip } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { RowsTable } from "@/components/RowsTable";
import { attempt, isRefused } from "@/lib/api";
import type { Rows } from "@/lib/types";

/**
 * One error, by its hash. The destination an `error:<hash>` citation opens —
 * resolved with no batch_id in hand, the same way a `file:` fingerprint is.
 */
export default async function ErrorPage({
  params,
}: {
  params: Promise<{ errorHash: string }>;
}) {
  const { errorHash } = await params;
  const query = new URLSearchParams({ error_id_hash: errorHash });
  const result = await attempt<Rows>(`/api/tools/get_error_by_hash?${query}`);
  const batchId =
    !isRefused(result) && result.rows[0]
      ? (result.rows[0].batch_id as string | undefined)
      : undefined;

  return (
    <>
      <p className="note">
        <Link href="/operations/control">Control Operations</Link> / error {errorHash}
      </p>
      <h1>Error {errorHash}</h1>
      <p className="lede">
        <CitationChip citationId={`error:${errorHash}`} />
      </p>

      {isRefused(result) ? <RefusalNotice refusal={result} /> : <RowsTable result={result} />}

      {batchId && (
        <p className="note">
          <Link href={`/operations/control/batch/${batchId}?panel=errors`}>
            See the full error log for batch {batchId} →
          </Link>
        </p>
      )}
    </>
  );
}
