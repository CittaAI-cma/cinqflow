import Link from "next/link";
import { CitationChip } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { attempt, isRefused } from "@/lib/api";
import type { Document } from "@/lib/types";

/**
 * One uploaded document, by document_id. The destination a `document:<id>`
 * citation opens (CF-V1-E16-04/E16-06) — a page fragment
 * (`document:<id>#p14`) scrolls to that page rather than opening a page of
 * its own, the same one-depth-level shape `runbook:<id>#step-3` already has.
 *
 * Read-only, deliberately: advancing a document's lifecycle (submit,
 * approve, publish) rides the SAME generic `/api/objects/knowledge_document/
 * {id}/...` routes every other governed object uses — there is no side
 * door, but there is also no dedicated button here yet. A steward acts on
 * it the way every other object type is acted on today, through the
 * governance API directly, until a shared "act on this object" panel
 * exists for every governed type rather than one built per screen.
 */
export default async function DocumentPage({
  params,
}: {
  params: Promise<{ documentId: string }>;
}) {
  const { documentId } = await params;
  const result = await attempt<Document>(`/api/documents/${encodeURIComponent(documentId)}`);

  return (
    <>
      <p className="note">
        <Link href="/data/intake">Data Intake</Link> / document {documentId}
      </p>
      <h1>{isRefused(result) ? documentId : result.filename}</h1>
      <p className="lede">
        <CitationChip citationId={`document:${documentId}`} />
      </p>

      {isRefused(result) ? (
        <RefusalNotice refusal={result} />
      ) : (
        <>
          <p className="note">
            v{result.version} · {result.lifecycle_state} · {result.media_type}
            {result.feed_id ? (
              <>
                {" "}
                · grounds{" "}
                <Link href={`/data/intake/feed/${result.feed_id}`}>{result.feed_id}</Link>
              </>
            ) : null}
          </p>

          {result.lifecycle_state !== "published" ? (
            <div className="card">
              <strong>Not yet grounding any agent</strong>
              <p className="note">
                A document&rsquo;s pages become cited knowledge only once it is Published — the
                same rule a runbook or a glossary term follows. This one is still{" "}
                <span className="mono">{result.lifecycle_state}</span>.
              </p>
            </div>
          ) : null}

          <h2>Pages</h2>
          {result.pages.map((page) => (
            <div className="card" id={`p${page.number}`} key={`${result.document_id}-p${page.number}`}>
              <strong>
                Page {page.number}
                {page.table_count > 0 ? (
                  <span className="note">
                    {" "}
                    · {page.table_count} table{page.table_count === 1 ? "" : "s"} kept whole
                  </span>
                ) : null}
              </strong>
              <pre className="mono wrap">{page.text}</pre>
            </div>
          ))}
        </>
      )}
    </>
  );
}
