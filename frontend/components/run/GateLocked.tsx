import { GATE_LOCKED_REASON } from "@/lib/persona";

/** What a gate looks like to someone who may review but not decide. Same frame
 *  as the live gate (`.gate-box`) so the screen keeps its shape; the reason is
 *  stated, never a greyed-out button with no explanation - the console's
 *  standing rule for anything a caller cannot do. The API enforces this too
 *  (`require_capability("can_decide_gates")`); this only mirrors it. */
export default function GateLocked({ gate }: { gate: "G1" | "G2" }) {
  return (
    <div className="card gate-box">
      <span className="panel-label">Decision — {gate}</span>
      <p className="alert warn" style={{ marginTop: 8 }}>
        {GATE_LOCKED_REASON}
      </p>
    </div>
  );
}
