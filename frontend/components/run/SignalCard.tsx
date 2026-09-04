import type { Signal } from "@/lib/api";

const KIND_LABEL: Record<Signal["kind"], string> = { risk: "risk", unknown: "unknown" };

/** A risk or unknown, rendered through the same four-slot reasoning contract
 *  as the trust ladder's inferences and recommendations
 *  (docs/blueprints/analyst-forward-flow.md §1.2): what's true, why, how to
 *  check it on this screen, and what happens if it's accepted as-is. Sibling
 *  of `ClaimCard`, one card frame (`.claim`), so a signal reads as the same
 *  kind of thing as a claim rather than a lesser, bare sentence. */
export default function SignalCard({ signal }: { signal: Signal }) {
  return (
    <div className="claim">
      <div className="claim-head">
        <span className={`signal-kind ${signal.severity}`}>{KIND_LABEL[signal.kind]}</span>
      </div>
      <div className="claim-value">{signal.claim}</div>
      <p className="signal-basis">{signal.basis}</p>
      <div className="signal-meta">
        {signal.check ? (
          <span>
            <b>Check —</b> {signal.check}
          </span>
        ) : null}
        {signal.consequence ? (
          <span>
            <b>If accepted —</b> {signal.consequence}
          </span>
        ) : null}
      </div>
    </div>
  );
}
