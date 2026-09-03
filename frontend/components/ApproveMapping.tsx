"use client";

import { useActionState } from "react";
import { useFormStatus } from "react-dom";
import { approveVersion, type StudioState } from "@/app/mapping/actions";
import AnnounceOnMount, { announceOnSubmit } from "@/components/ui/AnnounceOnMount";
import type { PreviewResult } from "@/lib/api";

function ApproveButton() {
  const { pending } = useFormStatus();
  return (
    <button type="submit" disabled={pending}>
      {pending ? "Recording…" : "Approve — G2 → Silver Raw"}
    </button>
  );
}

/** G2. Deliberately shows what is about to happen before it can be agreed to:
 *  the gate exists so nobody approves a mapping they have not seen run. */
export default function ApproveMapping({
  feed,
  version,
  status,
  preview,
}: {
  feed: string;
  version: number;
  status: string;
  preview: PreviewResult | null;
}) {
  const [state, action] = useActionState<StudioState, FormData>(approveVersion, {});
  const storageKey = `g2:${feed}:${version}`;

  if (status === "approved") {
    return (
      <>
        <AnnounceOnMount storageKey={storageKey} />
        <h2>
          G2 approval <span className="meta">· this version is authoritative</span>
        </h2>
        <div className="card grid">
          <span className="meta">
            v{version} is approved and frozen. Its promotion writes Silver Raw for the batch it
            was approved against; re-running it rebuilds that batch and nothing else.
          </span>
          {preview?.sample ? (
            <span className="meta">
              Batch <span className="mono">{preview.sample.batch_id}</span> ·{" "}
              <a href={`/batches/${preview.sample.batch_id}`}>see what was written</a>
            </span>
          ) : null}
        </div>
      </>
    );
  }

  if (status === "superseded") {
    return (
      <>
        <h2>G2 approval</h2>
        <p className="empty">
          v{version} was superseded by a later approved version. It cannot be approved again.
        </p>
      </>
    );
  }

  const approvable = Boolean(preview?.approvable);

  return (
    <>
      <h2>
        G2 approval <span className="meta">· the last gate before Silver Raw</span>
      </h2>

      {!approvable ? (
        <p className="alert warn">
          {preview
            ? `v${version} changed after this preview ran — preview again. G2 stays closed until the preview reflects the current draft.`
            : "No preview of this spec yet. G2 stays closed until the mapping has been seen running."}
        </p>
      ) : (
        <form
          action={action}
          onSubmit={() => announceOnSubmit(storageKey, "Approved — promotion queued.")}
          className="gate-box"
        >
          <input type="hidden" name="feed" value={feed} />
          <input type="hidden" name="version" value={version} />
          <p className="gate-note">
            Approving freezes v{version} and queues the promotion of batch{" "}
            <span className="mono">{preview?.sample.batch_id}</span> to Silver Raw:{" "}
            <span className="mono">{preview?.aggregates.rows_ok}</span> of{" "}
            <span className="mono">{preview?.aggregates.rows_previewed}</span> previewed rows
            mapped cleanly, and{" "}
            <span className="mono">
              {(preview?.aggregates.rows_with_failures ?? 0) +
                (preview?.aggregates.rows_quarantined ?? 0) +
                (preview?.aggregates.rows_rejected ?? 0)}
            </span>{" "}
            would be quarantined with their reasons.
            {preview?.sample_is_partial ? (
              <>
                {" "}
                The preview covered{" "}
                <span className="mono">{preview.sample.rows.toLocaleString()}</span> of{" "}
                <span className="mono">{preview.sample.rows_in_batch.toLocaleString()}</span> rows
                in the batch — the promotion runs over all of them.
              </>
            ) : null}
          </p>
          <div style={{ marginBottom: 12 }}>
            <label htmlFor="g2-note">Note (recorded with the decision)</label>
            <input id="g2-note" name="note" type="text" placeholder="what you checked" />
          </div>
          <div className="gate-actions">
            <ApproveButton />
          </div>
          {state.error ? <p className="alert error">{state.error}</p> : null}
          {state.saved ? (
            <p className="alert ok">
              Approved. Promotion queued for batch <span className="mono">{state.batchId}</span>{" "}
              — run <span className="mono">make worker</span> and reload.
            </p>
          ) : null}
        </form>
      )}
    </>
  );
}
