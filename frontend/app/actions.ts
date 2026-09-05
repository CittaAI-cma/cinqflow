"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { authMutate, requireUser } from "@/lib/auth";
import { uploadFile } from "@/lib/api";
import type { RerunSource } from "@/lib/rerun";

export interface UploadState {
  error?: string;
}

export interface DecisionState {
  error?: string;
}

export async function submitUpload(
  _previous: UploadState,
  form: FormData,
): Promise<UploadState> {
  const file = form.get("file");
  if (!(file instanceof File) || file.size === 0) {
    return { error: "Choose a CSV or XLSX file to upload." };
  }

  const { uploadId, error } = await uploadFile(form);
  if (error || !uploadId) {
    return { error: error ?? "Upload failed." };
  }

  revalidatePath("/data/intake");
  // A freshly created upload is always `received` — canonically "processing"
  // (lib/runStep.ts) — so this can redirect straight there without an extra
  // fetch just to ask the control plane something it already told us.
  redirect(`/runs/${uploadId}/processing`);
}

/** G1. The API records whoever holds the session as the approver and refuses
 *  anyone without `can_decide_gates` (403 → the reason, in the analyst's
 *  words, via `authMutate`'s error text). No approver travels in the body
 *  any more — identity is the token's, not the form's. */
export async function submitDecision(
  _previous: DecisionState,
  form: FormData,
): Promise<DecisionState> {
  const uploadId = String(form.get("upload_id") ?? "");
  const decision = String(form.get("decision") ?? "");
  if (decision !== "approved" && decision !== "rejected") {
    return { error: "Choose approve or reject." };
  }

  // Defence in depth: middleware already gates the route on a session, and the
  // API is the real boundary - this just fails fast with a redirect if the
  // session evaporated between render and click.
  await requireUser();

  const path = decision === "approved" ? "approve" : "reject";
  const { error } = await authMutate<{ status: string }>(`/api/uploads/${uploadId}/${path}`, {
    method: "POST",
    body: JSON.stringify({ note: String(form.get("note") ?? "") || null }),
  });
  if (error) {
    return { error };
  }

  revalidatePath(`/runs/${uploadId}/review`);
  revalidatePath("/data/intake");
  return {};
}

/** Re-enqueues the work a `*_failed` upload failed at. A Server Action, not a
 *  browser call: `/retry` is capability-gated (`can_rerun_steps`) and only the
 *  Next.js server holds the bearer token. Returns the error text rather than
 *  throwing, so `RetryButton`/`RunProcessing` can show it inline. */
export async function submitRetry(uploadId: string): Promise<{ error?: string }> {
  const { error } = await authMutate<{ status: string; queued: string }>(
    `/api/uploads/${uploadId}/retry`,
    { method: "POST" },
  );
  if (error) return { error };
  revalidatePath(`/runs/${uploadId}/processing`);
  revalidatePath(`/runs/${uploadId}/review`);
  return {};
}

/** PR-3: queue a new generation of one step. Capability-gated at the API
 *  (`can_rerun_steps`) and refused with a reason when the step is already
 *  queued, still running, a gate, or when the queue will retry it on its own -
 *  `authMutate` returns that reason (`message — hint`) for the dialog to show. */
export async function submitRerun(
  source: RerunSource,
  stepKey: string,
): Promise<{ error?: string; generation?: number }> {
  const path =
    source.kind === "upload"
      ? `/api/uploads/${source.uploadId}/steps/${stepKey}/rerun`
      : source.kind === "batch"
        ? `/api/batches/${source.batchId}/steps/${stepKey}/rerun`
        : `/api/feeds/${encodeURIComponent(source.feed)}/mapping-versions/${source.version}/steps/${stepKey}/rerun`;
  const { data, error } = await authMutate<{ generation: number }>(path, { method: "POST" });
  if (error) return { error };
  return { generation: data?.generation };
}
