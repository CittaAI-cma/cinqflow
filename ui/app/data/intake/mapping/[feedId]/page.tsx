import Link from "next/link";
import { CitationChip } from "@/components/Cited";

/**
 * The `mapping:<feed>` destination — honestly unbuilt, not faked.
 *
 * Mapping authoring is Wave 1 (CF-V1-E6-*): no Wave-0 tool emits this
 * citation kind, and there is no governed mapping object yet to show. A page
 * that pretended otherwise would be worse than the 404 it replaces — a
 * malformed citation reads as evidence, and so does a fabricated one.
 */
export default async function MappingPage({
  params,
}: {
  params: Promise<{ feedId: string }>;
}) {
  const { feedId } = await params;

  return (
    <>
      <p className="note">
        <Link href="/data/intake">Data Intake</Link> /{" "}
        <Link href={`/data/intake/feed/${feedId}`}>{feedId}</Link> / mapping
      </p>
      <h1>{feedId} column mapping</h1>
      <p className="lede">
        <CitationChip citationId={`mapping:${feedId}`} />
      </p>
      <div className="card note">
        Column mapping is not a Wave-0 capability. The compiled plan already shows how this
        feed casts and maps its columns —{" "}
        <Link className="cited" href={`/data/intake/feed/${feedId}/plan`}>
          see the compiled plan
        </Link>
        . A dedicated mapping screen, with suggestion and review, arrives in Wave 1.
      </div>
    </>
  );
}
