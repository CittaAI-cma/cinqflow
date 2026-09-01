import { STATUS_WORDS, type StatusWord } from "@/lib/types";

/**
 * A status word, and only ever one of the seven.
 *
 * An unrecognised word renders VISIBLY WRONG rather than silently — a dialect
 * that looks fine on screen is a dialect that spreads. `tests/workspace.spec.ts`
 * asserts none reaches a rendered surface.
 *
 * Each word also carries a distinct MARK. Two reasons, and the second is the
 * real one:
 *
 *   · the dark theme gave Needs Attention and Missing the same hex, so two of
 *     the seven were indistinguishable — one means "an issue requires action",
 *     the other means "expected data has not arrived", and a screen that
 *     cannot tell them apart is a screen that cannot be acted on;
 *   · meaning carried by colour alone fails WCAG 1.4.1, and roughly one in
 *     twelve men cannot separate the red from the green reliably.
 *
 * The mark is aria-hidden: the word is already the accessible name, and a
 * screen reader announcing "triangle Needs Attention" is noise.
 */

/** Shape encodes the fact. Read the column in greyscale and it still works. */
const MARKS: Record<StatusWord, React.ReactElement> = {
  // hollow ring — nothing has happened yet
  Expected: <circle cx="6" cy="6" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.6" />,
  // filled — it exists
  Received: <circle cx="6" cy="6" r="5" fill="currentColor" />,
  // half-filled — mid-transition
  Processing: (
    <>
      <circle cx="6" cy="6" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path d="M6 1.5A4.5 4.5 0 0 1 6 10.5Z" fill="currentColor" />
    </>
  ),
  // check — done
  Completed: (
    <path
      d="M1.5 6.4 4.6 9.5 10.5 2.8"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  ),
  // diamond — a decision is pending
  "Needs Review": <path d="M6 .8 11.2 6 6 11.2.8 6Z" fill="currentColor" />,
  // triangle — the universal caution shape
  "Needs Attention": (
    <path d="M6 1 11.5 10.6H.5Z" fill="currentColor" strokeLinejoin="round" />
  ),
  // slashed ring — ABSENCE, visibly not the triangle
  Missing: (
    <>
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
    </>
  ),
};

export function Status({ word }: { word: string }) {
  const known = (STATUS_WORDS as readonly string[]).includes(word);
  if (!known) {
    return (
      <span className="status uncited" data-word="unknown" title="not one of the seven">
        {word}
      </span>
    );
  }
  const status = word as StatusWord;
  return (
    <span className="status" data-word={status}>
      <svg className="mark" viewBox="0 0 12 12" aria-hidden="true" focusable="false">
        {MARKS[status]}
      </svg>
      {status}
    </span>
  );
}
