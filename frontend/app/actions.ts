"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { requireUser } from "@/lib/auth";
import { decideUpload, uploadFile } from "@/lib/api";

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

export async function submitDecision(
  _previous: DecisionState,
  form: FormData,
): Promise<DecisionState> {
  const uploadId = String(form.get("upload_id") ?? "");
  const decision = String(form.get("decision") ?? "");
  if (decision !== "approved" && decision !== "rejected") {
    return { error: "Choose approve or reject." };
  }

  // Whoever is actually signed in, not `decideUpload`'s fallback - a G1
  // decision is an audit record, and it must name the person who made it.
  const user = await requireUser();

  const { error } = await decideUpload(uploadId, decision, {
    approver: user.email,
    note: String(form.get("note") ?? "") || undefined,
  });
  if (error) {
    return { error };
  }

  revalidatePath(`/runs/${uploadId}/review`);
  revalidatePath("/data/intake");
  return {};
}
