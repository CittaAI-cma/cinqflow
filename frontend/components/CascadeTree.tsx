import type { BatchError } from "@/lib/types";

/**
 * Root cause first, its consequences folded underneath — plate 4.7's
 * `CascadeTree`, sized for a table cell rather than a page.
 *
 * "Three errors logged; two are consequences of the first" is the story's own
 * sentence (CF-V2-E12-04), and `core.operations.monitor.separate_cascade` is
 * what makes it true — this component only renders what that clustering
 * already decided. It never re-derives which error is the root: `rootCause`
 * and `consequences` arrive pre-separated from the incident evidence.
 */
export function CascadeTree({
  rootCause,
  consequences,
}: {
  rootCause: BatchError | null;
  consequences: BatchError[];
}) {
  if (!rootCause) {
    return <span className="note">no error recorded for this batch</span>;
  }
  return (
    <div className="stack">
      <div>
        <strong>{rootCause.category}</strong> — {rootCause.message}
      </div>
      {consequences.length > 0 ? (
        <details>
          <summary>
            {consequences.length} consequence{consequences.length === 1 ? "" : "s"}
          </summary>
          <ul className="tree-list">
            {consequences.map((error) => (
              <li key={error.error_id_hash} className="note">
                {error.category} — {error.message}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
