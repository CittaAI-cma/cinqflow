import Link from "next/link";
import { notFound } from "next/navigation";
import RunRail from "@/components/run/RunRail";
import { getUpload } from "@/lib/api";
import { loadRunSteps, resolveCanonical } from "@/lib/runProgress";

export const dynamic = "force-dynamic";

/** The run shell: the chrome every one of the seven step pages shares (the
 *  file's identity line and the seven-dot rail). `getUpload` and
 *  `loadRunSteps` are both `cache()`-wrapped, so this layout and the step
 *  page it wraps cost one request each per render, not two. The rail's dots
 *  and the canonical step both come from the step ledger; the upload's
 *  status is the fallback for a run the ledger has nothing about. */
export default async function RunLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ uploadId: string }>;
}) {
  const { uploadId } = await params;

  let detail;
  try {
    detail = await getUpload(uploadId);
  } catch {
    notFound();
  }

  const { upload } = detail;
  const steps = await loadRunSteps(uploadId);
  const step = resolveCanonical(steps, upload.status);
  const adverse = upload.status === "rejected" || upload.status.endsWith("_failed");

  return (
    <div className="run-shell">
      <p className="meta">
        <Link href={`/data/intake/${encodeURIComponent(upload.feed)}`}>← {upload.feed}</Link>
      </p>
      <div className="run-head">
        <h2 className="run-head-title">{upload.filename}</h2>
        <p className="run-head-meta mono">
          {upload.source_system}/{upload.domain} · business date {upload.business_date}
        </p>
      </div>
      <RunRail uploadId={uploadId} step={step} adverse={adverse} steps={steps} />
      {children}
    </div>
  );
}
