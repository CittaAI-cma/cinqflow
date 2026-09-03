import type { MappingVersionDetail, PreviewResult, Run, UploadStatus } from "@/lib/api";

/** The console admits exactly seven status words. Anything else renders visibly wrong
 *  rather than silently passing through — see StatusWord's "unbound" fallback. */
export type StatusWord =
  | "Expected"
  | "Received"
  | "Processing"
  | "Completed"
  | "Needs Review"
  | "Needs Attention"
  | "Missing";

export function uploadStatusWord(status: UploadStatus): StatusWord | null {
  switch (status) {
    case "received":
      return "Received";
    case "profiling":
    case "profiled":
    case "interpreting":
    case "landing":
      return "Processing";
    case "interpreted":
      return "Needs Review";
    case "approved":
    case "rejected":
    case "landed":
      return "Completed";
    case "profile_failed":
    case "interpret_failed":
    case "land_failed":
      return "Needs Attention";
    default:
      return null;
  }
}

/** Whether polling `/uploads/{id}/progress` still has something new to learn.
 *  Every other status is one the server-rendered page already tells in full. */
export function isUploadInFlight(status: UploadStatus): boolean {
  return (
    status === "received" ||
    status === "profiling" ||
    status === "profiled" ||
    status === "interpreting"
  );
}

export function proposalStatusWord(status: "proposed" | "invalid"): StatusWord {
  return status === "proposed" ? "Needs Review" : "Needs Attention";
}

export function mappingStatusWord(status: MappingVersionDetail["status"]): StatusWord | null {
  switch (status) {
    case "draft":
      return "Processing";
    case "previewed":
      return "Needs Review";
    case "approved":
    case "superseded":
      return "Completed";
    default:
      return null;
  }
}

export function previewStatusWord(preview: PreviewResult | null): StatusWord {
  if (!preview) return "Expected";
  return preview.is_current ? "Completed" : "Needs Attention";
}

/** Whether polling `/batches/{id}/progress` for this run still has something
 *  new to learn. Mirrors `isUploadInFlight` for the batch/run axis. */
export function isRunInFlight(run: Run | null | undefined): boolean {
  return run?.state === "received" || run?.state === "in_progress";
}

export function runStatusWord(run: Run | null | undefined): StatusWord {
  if (!run) return "Expected";
  switch (run.state) {
    case "received":
      return "Received";
    case "in_progress":
      return "Processing";
    case "completed":
      return run.balanced === false ? "Needs Attention" : "Completed";
    case "failed":
      return "Needs Attention";
    default:
      return "Expected";
  }
}
