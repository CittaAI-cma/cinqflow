"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { retryUpload } from "@/lib/api";
import { useToast } from "@/lib/useToast";

/** A one-shot retry with no live progress tracking, for a state this build
 *  has no timeline screen for yet (landing, from the read-only Review view —
 *  S3 isn't built in this phase). `RunProcessing` has its own bespoke retry
 *  that keeps polling afterward; this one only re-enqueues and says so,
 *  because the status transition happens in the background worker, not at
 *  the moment this call returns. */
export default function RetryButton({ uploadId, label }: { uploadId: string; label: string }) {
  const router = useRouter();
  const { push } = useToast();
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<"error" | "queued" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onClick() {
    setPending(true);
    setError(null);
    const response = await retryUpload(uploadId);
    setPending(false);
    if (response.error) {
      setResult("error");
      setError(response.error);
      push(response.error, "error");
      return;
    }
    setResult("queued");
    push("Retry queued.", "success");
    router.refresh();
  }

  return (
    <div className="run-processing-actions">
      <button type="button" className="btn-dark" onClick={onClick} disabled={pending}>
        {pending ? "Queuing…" : label}
      </button>
      {result === "queued" ? (
        <span className="meta">Queued. This build has no live landing progress yet — reload to check.</span>
      ) : null}
      {result === "error" ? <p className="alert error">{error}</p> : null}
    </div>
  );
}
