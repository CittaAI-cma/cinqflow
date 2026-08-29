import { STATUS_WORDS, type StatusWord } from "@/lib/types";

/**
 * A status word, and only ever one of the seven.
 *
 * An unrecognised word renders VISIBLY WRONG rather than silently — a dialect
 * that looks fine on screen is a dialect that spreads. `tests/lexicon.spec.ts`
 * asserts none reaches a rendered surface.
 */
export function Status({ word }: { word: string }) {
  const known = (STATUS_WORDS as readonly string[]).includes(word);
  if (!known) {
    return (
      <span className="status uncited" data-word="unknown" title="not one of the seven">
        ⚠ {word}
      </span>
    );
  }
  return (
    <span className="status" data-word={word as StatusWord}>
      <span className="dot" />
      {word}
    </span>
  );
}
