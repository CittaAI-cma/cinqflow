/** Badge — the one place a short piece of classified metadata gets its look.
 *
 *  Deliberately not a status word: `StatusWord` owns the seven-word run
 *  vocabulary and carries a shape-coded glyph so it survives greyscale. A
 *  badge is everything else that needs a chip — a tier name, a PHI marker, an
 *  ownership label, a count. Tones are semantic rather than colour-named
 *  (`origin` rather than `purple`) so the palette can move without a rename.
 *
 *  Server-safe: no hooks, no client boundary.
 */

export type BadgeTone =
  | "neutral"
  | "ok"
  | "danger"
  | "warn"
  | "info"
  /** Human-approved governance — a decision someone signed for. */
  | "governed"
  /** Machine-suggested and not yet confirmed by a person. */
  | "advisory";

export default function Badge({
  children,
  tone = "neutral",
  /** Rendered as `title` and, unlike a bare tooltip, also announced. */
  hint,
  mono = false,
  className = "",
}: {
  children: React.ReactNode;
  tone?: BadgeTone;
  hint?: string;
  mono?: boolean;
  className?: string;
}) {
  return (
    <span
      className={`badge badge-${tone}${mono ? " mono" : ""}${className ? ` ${className}` : ""}`}
      title={hint}
    >
      {children}
      {hint ? <span className="sr-only"> — {hint}</span> : null}
    </span>
  );
}
