"use client";

import Link from "next/link";
import { useEffect } from "react";

/** Route-level error boundary. Until this existed, a throw anywhere in a
 *  server component (an API timeout mid-render, a malformed payload) fell
 *  through to Next's own error screen — framework chrome that tells an analyst
 *  nothing about whether their data was affected.
 *
 *  The two things this must answer, in order:
 *   1. Is my data safe? For every screen behind this boundary the answer is
 *      yes, and it is not a platitude: reads are what failed, and every write
 *      path in this app is a queued job with its own transaction, so a screen
 *      that could not render never half-applied anything.
 *   2. What do I do now? Retry re-runs the failed render without a full
 *      reload, which is usually enough for a transient control-plane blip.
 */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // The digest is the only handle on the server-side stack, which is
    // deliberately not shipped to the browser.
    console.error("route error", error.digest ?? "", error);
  }, [error]);

  return (
    <div className="card u-rise-in" style={{ marginTop: 18, maxWidth: 720 }} role="alert">
      <span className="panel-label">Something went wrong</span>
      <h2 style={{ margin: "8px 0 6px", fontSize: 19 }}>This screen could not be loaded.</h2>
      <p className="meta" style={{ maxWidth: "62ch" }}>
        Reading the control plane failed. Nothing was written and no run was
        started or changed by this — every write in this platform is a queued
        job, so a screen that fails to render leaves the pipeline exactly as it
        was.
      </p>

      {error.digest ? (
        <p className="meta" style={{ marginTop: 10 }}>
          Reference <span className="mono">{error.digest}</span> — quote this when reporting it.
        </p>
      ) : null}

      <div className="run-processing-actions" style={{ marginTop: 14 }}>
        <button type="button" className="btn-dark" onClick={reset}>
          Try again
        </button>
        <Link href="/data/intake" className="btn-outline">
          Back to ingestion
        </Link>
      </div>
    </div>
  );
}
