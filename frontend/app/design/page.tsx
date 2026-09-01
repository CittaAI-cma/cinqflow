import { CitationChip, Cited } from "@/components/Cited";
import { RefusalNotice } from "@/components/Refusal";
import { Status } from "@/components/Status";
import { EmptyState } from "@/components/ui/EmptyState";
import { MetricTile } from "@/components/ui/MetricTile";
import { Skeleton, TableSkeleton } from "@/components/ui/Skeleton";
import { Refused } from "@/lib/api";
import { STATUS_WORDS } from "@/lib/types";

/**
 * The living reference — every component, in every state, on one page.
 *
 * Reads NO platform data: it is a mirror for the design system, not a screen.
 * Two jobs, and the second is the one that pays for it:
 *
 *   · a reviewer can check the seven status marks are distinguishable in
 *     greyscale, side by side, in one glance;
 *   · a component with no state on this page is a component whose empty,
 *     loading and refused states nobody has designed — which is how "static
 *     UI, no UX" happened the first time.
 */
export const metadata = { title: "Design system · CINQFLOW" };

const TOKENS = {
  Neutrals: ["--bg", "--surface-2", "--surface-3", "--line", "--line-strong", "--ink", "--ink-2", "--ink-3"],
  Status: [
    "--st-expected",
    "--st-received",
    "--st-processing",
    "--st-completed",
    "--st-needs-review",
    "--st-needs-attention",
    "--st-missing",
  ],
  Semantic: ["--accent", "--cite", "--focus", "--danger"],
};

function Swatch({ token }: { token: string }) {
  return (
    <div className="inline">
      <span
        style={{
          background: `var(${token})`,
          width: "var(--s-5)",
          height: "var(--s-5)",
          borderRadius: "var(--radius-sm)",
          border: "1px solid var(--line)",
          display: "inline-block",
        }}
      />
      <code className="mono">{token}</code>
    </div>
  );
}

export default function DesignSystem() {
  return (
    <>
      <h1>Design system</h1>
      <p className="lede">
        Every component, in every state. This page reads no platform data — it is a mirror for
        the system, not a screen.
      </p>

      <h2>The seven status words</h2>
      <p className="note">
        Word, shape and colour. Read this column with the colour removed and it still works —
        which is the point: two of the seven used to share a hex.
      </p>
      <div className="card">
        <div className="stack">
          {STATUS_WORDS.map((word) => (
            <Status key={word} word={word} />
          ))}
        </div>
      </div>

      <h2>An eighth word fails loudly</h2>
      <p className="note">
        A dialect that looks fine on screen is a dialect that spreads. This is what
        <span className="mono"> {'<Status word="Success" />'} </span> renders.
      </p>
      <div className="card">
        <Status word="Success" />
      </div>

      <h2>The citation layer</h2>
      <div className="card stack">
        <div>
          <Cited value="8,842 rows" citationId="batch:8842" /> — a figure that opens the row it
          came from.
        </div>
        <div>
          <CitationChip citationId="recon:8842#DQ-002" />
          <CitationChip citationId="feed:fidelis-downstate-roster" />
        </div>
        <div>
          <Cited value="1,204 members" /> — an uncited figure renders marked. Uncited claims are
          a defect class, so they must be visible in review rather than plausible.
        </div>
      </div>

      <h2>Metric tiles</h2>
      <div className="grid">
        <MetricTile label="Feeds published" value={12} citationId="feed:fidelis-downstate-roster" />
        <MetricTile label="Runs in view" value={66} />
        <MetricTile label="Needing attention" value={3} tone="attention" hint="ranked by harm" />
      </div>

      <h2>Nothing here — and which nothing</h2>
      <p className="note">
        Three different facts. Collapsing them into one grey sentence makes a user guess which
        one they are looking at.
      </p>
      <EmptyState kind="recorded" what="reconciliation" />
      <EmptyState kind="scope" what="this run" />
      <EmptyState kind="wave" what="Mapping &amp; Rules" />

      <h2>Not loaded yet</h2>
      <p className="note">
        Distinct from nothing-here. A slow query that reads as “no runs today” is the most
        expensive misreading available on an operations screen.
      </p>
      <TableSkeleton rows={3} cols={4} />
      <div className="card stack">
        <Skeleton width="40%" />
        <Skeleton width="70%" />
      </div>

      <h2>Refusals</h2>
      <p className="note">“You may not do that” is an answer, not a crash.</p>
      <div className="stack">
        <RefusalNotice refusal={new Refused(403, "not permitted: read_only may not edit_feed")} />
        <RefusalNotice refusal={new Refused(404, "no such feed, or not one you can reach")} />
      </div>

      <h2>Controls</h2>
      <div className="card inline">
        <button className="primary">Primary</button>
        <button>Secondary</button>
        <button disabled>Disabled</button>
        <input placeholder="An input" aria-label="An input" />
      </div>

      <h2>Tokens</h2>
      {Object.entries(TOKENS).map(([group, tokens]) => (
        <div key={group}>
          <h3>{group}</h3>
          <div className="card grid">
            {tokens.map((token) => (
              <Swatch key={token} token={token} />
            ))}
          </div>
        </div>
      ))}

      <h2>Type scale</h2>
      <p className="note">
        Named by token, not by size. The step is the contract; its value is free to change.
      </p>
      <div className="card stack">
        <span style={{ fontSize: "var(--t-xl)" }}>--t-xl · page title</span>
        <span style={{ fontSize: "var(--t-lg)" }}>--t-lg</span>
        <span style={{ fontSize: "var(--t-md)" }}>--t-md · section</span>
        <span style={{ fontSize: "var(--t-base)" }}>--t-base · body</span>
        <span style={{ fontSize: "var(--t-sm)" }}>--t-sm · note</span>
        <span style={{ fontSize: "var(--t-xs)" }}>--t-xs · label</span>
        <span className="mono">a41f9c2e · 0O1lI — the mono face disambiguates these</span>
      </div>
    </>
  );
}
