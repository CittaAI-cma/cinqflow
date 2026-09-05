/** The value map's text form — `M=male, F=female` — and its parse.
 *
 *  One parser, shared by the save action and the studio's echo, for the same
 *  reason the server publishes its validation dependencies rather than letting
 *  the client reimplement them: a second copy drifts, and the analyst finds out
 *  by way of a mapping that ran differently from what the box showed.
 *
 *  The parse is deliberately forgiving of whitespace and deliberately strict
 *  about halves. A fragment missing either side cannot become a mapping - there
 *  is no code and no value to write - so it is dropped. What changes here is
 *  that it is dropped *out loud*: `dropped` carries every fragment that did not
 *  survive, so the row can say so instead of the analyst discovering it in a
 *  preview whose codes silently passed through unmapped. */
export type ParsedValueMap = {
  /** In the order they were typed, later duplicates winning - which is what
   *  `Record` assignment does, made visible rather than implied. */
  pairs: [string, string][];
  /** Fragments that carried no `=`, or nothing on one side of it. */
  dropped: string[];
  /** Codes typed more than once. The last one wins; the analyst should know. */
  duplicates: string[];
};

export function parseValueMap(raw: string): ParsedValueMap {
  const pairs: [string, string][] = [];
  const dropped: string[] = [];
  const duplicates: string[] = [];
  const seen = new Map<string, number>();

  for (const fragment of raw.split(",")) {
    if (!fragment.trim()) continue; // a trailing comma is not a mistake worth naming
    const [key, value] = fragment.split("=").map((part) => part?.trim());
    if (!key || !value) {
      dropped.push(fragment.trim());
      continue;
    }
    const at = seen.get(key);
    if (at === undefined) {
      seen.set(key, pairs.length);
      pairs.push([key, value]);
    } else {
      duplicates.push(key);
      pairs[at] = [key, value];
    }
  }
  return { pairs, dropped, duplicates };
}

/** What the save writes: the pairs as the record `MappingField.value_map` holds. */
export function valueMapRecord(raw: string): Record<string, string> {
  return Object.fromEntries(parseValueMap(raw).pairs);
}

/** The record back as text, for the input's initial value. */
export function formatValueMap(map: Record<string, string>): string {
  return Object.entries(map)
    .map(([code, value]) => `${code}=${value}`)
    .join(", ");
}
