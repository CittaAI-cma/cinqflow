import Link from "next/link";
import { notFound } from "next/navigation";
import RunRail from "@/components/run/RunRail";
import { getUpload } from "@/lib/api";
import { canonicalStep } from "@/lib/runStep";

export const dynamic = "force-dynamic";

/** The run shell: the chrome every one of the seven step pages shares (the
 *  file's identity line and the seven-dot rail). `getUpload` is `cache()`-
 *  wrapped, so this fetch and each page's own `getUpload` call collapse into
 *  one request per render, not two. */
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
  const step = canonicalStep(upload.status);
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
      <RunRail uploadId={uploadId} step={step} adverse={adverse} />
      {children}
    </div>
  );
}
