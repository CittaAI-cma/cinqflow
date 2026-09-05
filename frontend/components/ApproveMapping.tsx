"use client";

import { useActionState, useState } from "react";
import { useFormStatus } from "react-dom";
import { approveVersion, type StudioState } from "@/app/mapping/actions";
import AnnounceOnMount, { announceOnSubmit } from "@/components/ui/AnnounceOnMount";
import GateBlockers from "@/components/run/GateBlockers";
import GateChecklist, { type ChecklistItem } from "@/components/run/GateChecklist";
import GateLocked from "@/components/run/GateLocked";
import type { GateStatus, PreviewResult } from "@/lib/api";

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
  editedCount = 0,
  canDecide,
  gate,
  basePath,
}: {
  feed: string;
  version: number;
  status: string;
  preview: PreviewResult | null;
  /** From the version's diff against what it was derived from - "how much of
   *  this is mine?" is the analyst's own question, so a version with edits
   *  gets an extra checklist item asking her to stand behind them. */
  editedCount?: number;
  /** `capabilities.can_decide_gates` from the server. Someone who may review
   *  but not decide sees the gate's frame with the reason, not the form -
   *  the API refuses them too (403); this mirrors it. */
  canDecide: boolean;
  /** Every reason the server will refuse, from `GET .../gate`. Null when the
   *  API is too old to answer; the gate then falls back to the preview's
   *  narrower `approvable`, which is what it used before this existed. */
  gate: GateStatus | null;
  /** The route this gate is on, so approving revalidates the surface the
   *  analyst is looking at and not only the durable `/mapping/{feed}` one. */
  basePath?: string;
}) {
  const [state, action] = useActionState<StudioState, FormData>(approveVersion, {});
  const [note, setNote] = useState("");
  const [checkedCount, setCheckedCount] = useState(0);
  const [totalCount, setTotalCount] = useState(0);
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

  if (!canDecide) return <GateLocked gate="G2" />;

  // The server's whole answer where it is available. `preview.approvable` is
  // only "is this preview current", which is one of four rules — trusting it
  // alone is what rendered an enabled button for a draft with an unmapped
  // required field, and then 409'd on the press.
  const approvable = gate ? gate.approvable : Boolean(preview?.approvable);
  const blockers = gate?.blockers ?? [];
  const failureCount =
    (preview?.aggregates.rows_with_failures ?? 0) +
    (preview?.aggregates.rows_quarantined ?? 0) +
    (preview?.aggregates.rows_rejected ?? 0);
  const items: ChecklistItem[] = [
    { id: "preview", text: "This preview reflects the current draft" },
    ...(editedCount > 0 ? [{ id: "edits", text: "The fields I edited look right" }] : []),
    ...(failureCount > 0
      ? [{ id: "failures", text: "The failures/quarantine counts are acceptable" }]
      : []),
  ];

  return (
    <>
      <h2>
        G2 approval <span className="meta">· the last gate before Silver Raw</span>
      </h2>

      {!approvable ? (
        blockers.length ? (
          <div className="card gate-box">
            <span className="panel-label">
              G2 is closed · {blockers.length} {blockers.length === 1 ? "reason" : "reasons"}
            </span>
            <GateBlockers blockers={blockers} />
          </div>
        ) : (
          <p className="alert warn">
            {preview
              ? `v${version} changed after this preview ran — preview again. G2 stays closed until the preview reflects the current draft.`
              : "No preview of this spec yet. G2 stays closed until the mapping has been seen running."}
          </p>
        )
      ) : (
        <form
          action={action}
          onSubmit={() => announceOnSubmit(storageKey, "Approved — promotion queued.")}
          className="gate-box"
        >
          <input type="hidden" name="feed" value={feed} />
          <input type="hidden" name="version" value={version} />
          {basePath ? <input type="hidden" name="base_path" value={basePath} /> : null}
          <input type="hidden" name="note" value={note} />
          <p className="gate-note">
            Approving freezes v{version} and queues the promotion of batch{" "}
            <span className="mono">{preview?.sample.batch_id}</span> to Silver Raw:{" "}
            <span className="mono">{preview?.aggregates.rows_ok}</span> of{" "}
            <span className="mono">{preview?.aggregates.rows_previewed}</span> previewed rows
            mapped cleanly, and <span className="mono">{failureCount}</span> would be quarantined
            with their reasons.
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
          <GateChecklist
            items={items}
            onChange={setNote}
            onProgress={(checked, total) => {
              setCheckedCount(checked);
              setTotalCount(total);
            }}
          />
          <div className="gate-actions">
            <span className="gate-progress meta">
              {checkedCount} of {totalCount} checked
            </span>
            <ApproveButton />
          </div>
          {state.error ? <p className="alert error">{state.error}</p> : null}
          {state.saved ? (
            <p className="alert ok">
              Approved. Promotion queued for batch <span className="mono">{state.batchId}</span> —{" "}
              <a href={`/batches/${state.batchId}`}>watch it promote</a>. That page follows the run
              to Silver Raw on its own.
            </p>
          ) : null}
        </form>
      )}
    </>
  );
}
