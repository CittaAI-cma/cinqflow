import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import MappingPageBody from "@/components/MappingPageBody";
import { getUpload } from "@/lib/api";
import { canonicalStep, runHref } from "@/lib/runStep";

export const dynamic = "force-dynamic";

/** S5 - "Have I seen this mapping run, and do I own it?" Same content as the
 *  durable `/mapping/[feed]` studio (`MappingPageBody`) - the mapping version
 *  belongs to the feed, not this one upload, so nothing here forks it. What
 *  this route adds is the run flow's own chrome (the file identity line and
 *  rail from `RunShell`) and a way in that doesn't require already knowing
 *  the feed name.
 *
 *  Entered from S4's CTA, not derived from `canonicalStep` the way S1-S4 are -
 *  the control plane has no status field for "a mapping version exists"
 *  (docs/blueprints/analyst-forward-flow.md §2.1). Viewable once the upload
 *  has landed; a mapping version for the feed may already exist from a prior
 *  delivery, in which case this opens straight onto it. */
export default async function RunMappingPage({
  params,
  searchParams,
}: {
  params: Promise<{ uploadId: string }>;
  searchParams: Promise<{ v?: string; proposal?: string; limit?: string }>;
}) {
  const { uploadId } = await params;
  const { v, proposal, limit } = await searchParams;

  let detail;
  try {
    detail = await getUpload(uploadId);
  } catch {
    notFound();
  }
  const { upload } = detail;

  if (canonicalStep(upload.status) !== "bronze") {
    redirect(runHref(uploadId, canonicalStep(upload.status)));
  }

  return (
    <>
      <p className="meta">
        <Link href={runHref(uploadId, "bronze")}>← Bronze review</Link>
      </p>

      <h2 style={{ marginTop: 12 }}>
        Mapping &amp; preview <span className="meta">· gate G2</span>{" "}
        <span className="mono">{upload.feed}</span>
      </h2>

      <MappingPageBody
        feed={upload.feed}
        v={v}
        proposal={proposal}
        limit={limit}
        baseHref={`/runs/${encodeURIComponent(uploadId)}/mapping`}
      />
    </>
  );
}
