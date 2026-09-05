import { notFound, redirect } from "next/navigation";
import { getUpload } from "@/lib/api";
import { loadRunSteps, resolveCanonical } from "@/lib/runProgress";
import { runHref } from "@/lib/runStep";

export const dynamic = "force-dynamic";

/** This route is kept alive as a stable, step-independent link into a run —
 *  the register, lineage chain and old bookmarks all point here — but it is
 *  no longer a page of its own. It always forwards to the step the control
 *  plane's own state proves the run is at. */
export default async function UploadRedirectPage({
  params,
}: {
  params: Promise<{ uploadId: string }>;
}) {
  const { uploadId } = await params;

  let detail;
  try {
    detail = await getUpload(uploadId);
  } catch {
    notFound();
  }

  const steps = await loadRunSteps(uploadId);
  redirect(runHref(uploadId, resolveCanonical(steps, detail.upload.status)));
}
