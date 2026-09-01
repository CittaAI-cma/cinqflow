import { Cited } from "@/components/Cited";

/**
 * A figure, its label, and where it came from.
 *
 * Replaces the `.card > .note + .big` composition that five pages had each
 * built by hand. The reason it is a component rather than a class: it takes a
 * `citationId`, so a headline number that nobody can trace renders MARKED —
 * the same rule `<Cited>` enforces for numbers inside tables, applied to the
 * numbers a user actually reads first.
 */
export function MetricTile({
  label,
  value,
  citationId,
  hint,
  tone,
}: {
  label: string;
  value: React.ReactNode;
  citationId?: string | null;
  hint?: string;
  /** Draws the eye when the figure is the reason someone opened the screen. */
  tone?: "attention";
}) {
  return (
    <div className="card tile">
      <span className="label">{label}</span>
      <span
        className="big"
        style={tone === "attention" ? { color: "var(--st-needs-attention)" } : undefined}
      >
        {citationId ? <Cited value={value} citationId={citationId} /> : value}
      </span>
      {hint && <span className="note">{hint}</span>}
    </div>
  );
}
