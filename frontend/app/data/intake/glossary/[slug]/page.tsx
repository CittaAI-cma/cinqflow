import Link from "next/link";
import { CitationChip } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { RowsTable } from "@/components/RowsTable";
import { attempt, isRefused } from "@/lib/api";
import type { Rows } from "@/lib/types";

/**
 * One glossary term, by slug. The destination a `term:<slug>` citation opens.
 *
 * The lexical index is the K1 half of hybrid retrieval, arriving early —
 * "no chunking, no PHI-verify gate, no embedding" (core/retrieval), which is
 * why this reads as a definition lookup rather than a search result page.
 */
export default async function GlossaryPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const term = slug.replace(/-/g, " ");
  const query = new URLSearchParams({ query: term, limit: "1" });
  const result = await attempt<Rows>(`/api/tools/lookup_reference?${query}`);

  return (
    <>
      <p className="note">
        <Link href="/data/intake">Data Intake</Link> / glossary / {slug}
      </p>
      <h1>{term}</h1>
      <p className="lede">
        <CitationChip citationId={`term:${slug}`} />
      </p>

      {isRefused(result) ? <RefusalNotice refusal={result} /> : <RowsTable result={result} />}
    </>
  );
}
