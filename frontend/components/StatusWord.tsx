import type { StatusWord as StatusWordType } from "@/lib/statusWords";

/** One shape per word, so the column survives greyscale and colorblind palettes alike. */
const ICONS: Record<StatusWordType, React.ReactNode> = {
  Expected: (
    <svg viewBox="0 0 12 12" aria-hidden="true">
      <circle cx="6" cy="6" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  ),
  Received: (
    <svg viewBox="0 0 12 12" aria-hidden="true">
      <circle cx="6" cy="6" r="5" fill="currentColor" />
    </svg>
  ),
  Processing: (
    <svg viewBox="0 0 12 12" aria-hidden="true">
      <circle cx="6" cy="6" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path d="M6 1.5A4.5 4.5 0 0 1 6 10.5Z" fill="currentColor" />
    </svg>
  ),
  Completed: (
    <svg viewBox="0 0 12 12" aria-hidden="true">
      <path
        d="M1.5 6.4 4.6 9.5 10.5 2.8"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.9"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  ),
  "Needs Review": (
    <svg viewBox="0 0 12 12" aria-hidden="true">
      <path d="M6 .8 11.2 6 6 11.2.8 6Z" fill="currentColor" />
    </svg>
  ),
  "Needs Attention": (
    <svg viewBox="0 0 12 12" aria-hidden="true">
      <path d="M6 1 11.5 10.6H.5Z" fill="currentColor" strokeLinejoin="round" />
    </svg>
  ),
  Missing: (
    <svg viewBox="0 0 12 12" aria-hidden="true">
      <circle
        cx="6"
        cy="6"
        r="4.6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeDasharray="2.2 1.6"
      />
      <path d="M3 9 9 3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  ),
};

/** Renders one of the seven admitted status words. Pass `null`/an unrecognised word to
 *  get the "unbound" fallback — a dialect that looks fine on screen is one that spreads. */
export default function StatusWord({
  word,
  raw,
}: {
  word: StatusWordType | null;
  raw?: string;
}) {
  if (!word) {
    return <span className="status unbound">{raw ?? "unknown"}</span>;
  }
  return (
    <span className="status" data-w={word}>
      {ICONS[word]}
      {word}
    </span>
  );
}
