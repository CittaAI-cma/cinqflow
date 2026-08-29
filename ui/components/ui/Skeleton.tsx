/**
 * Not-loaded-yet, told apart from nothing-here.
 *
 * Before this, a screen mid-fetch and a screen with no data looked identical,
 * so a slow control-plane query read as "no runs today" — the most expensive
 * possible misreading on an operations screen.
 */
export function Skeleton({ width = "100%", height }: { width?: string; height?: string }) {
  return <span className="skeleton" style={{ display: "block", width, height }} aria-hidden />;
}

export function TableSkeleton({ rows = 5, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="card flush" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading…</span>
      <table>
        <tbody>
          {Array.from({ length: rows }, (_, r) => (
            <tr key={r}>
              {Array.from({ length: cols }, (_, c) => (
                <td key={c}>
                  <Skeleton width={c === 0 ? "60%" : "80%"} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
