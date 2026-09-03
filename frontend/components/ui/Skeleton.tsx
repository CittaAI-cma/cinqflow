/** Shimmer block for loading states. Server-safe (no hooks) so `loading.tsx`
 *  files can render it directly while the real server component fetches. */
export function Skeleton({
  width,
  height = 14,
  radius = 4,
  className,
  style,
}: {
  width?: number | string;
  height?: number | string;
  radius?: number;
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <span
      className={`skeleton${className ? ` ${className}` : ""}`}
      style={{ width, height, borderRadius: radius, ...style }}
      aria-hidden="true"
    />
  );
}

/** A stack of shimmer lines, one per entry in `width` (or `lines` copies of
 *  the same width). Use for paragraph/label-shaped placeholders. */
export function SkeletonText({
  lines = 1,
  width = "100%",
}: {
  lines?: number;
  width?: string | string[];
}) {
  const widths = Array.isArray(width) ? width : Array.from({ length: lines }, () => width);
  return (
    <div className="skeleton-lines">
      {widths.map((w, i) => (
        <Skeleton key={i} height={12} width={w} />
      ))}
    </div>
  );
}

/** Wraps a whole route-level skeleton so the wait is *announced*, not just
 *  drawn. Every `Skeleton` is `aria-hidden` (shimmer bars are noise to a
 *  screen reader), which left an assistive-technology user with silence
 *  between navigating and the content arriving — no shape, no spinner, no
 *  message. `aria-busy` marks the region as in-flight and the `role="status"`
 *  line gives it something to say.
 *
 *  `label` should name what is loading, not just "Loading" — on a run screen
 *  the difference between "Loading the run" and "Loading the mapping studio"
 *  is the only navigational feedback a non-visual user gets. */
export function SkeletonScreen({
  label,
  children,
  className = "grid",
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={className} aria-busy="true">
      <span role="status" className="sr-only">
        {label}
      </span>
      {children}
    </div>
  );
}

/** A `.card`-shaped placeholder: a label-sized line, then a few body lines. */
export function SkeletonCard({ lines = 3 }: { lines?: number }) {
  return (
    <div className="card">
      <Skeleton height={10} width={90} style={{ marginBottom: 12 }} />
      <SkeletonText lines={lines} width={["100%", "88%", "62%"].slice(0, lines)} />
    </div>
  );
}

/** A `.dt`-shaped placeholder table, so a loading list keeps its column
 *  rhythm instead of collapsing to a spinner. */
export function SkeletonTable({ columns = 6, rows = 8 }: { columns?: number; rows?: number }) {
  return (
    <div className="dt-wrap">
      <table className="dt dt-ruled">
        <thead>
          <tr>
            {Array.from({ length: columns }).map((_, c) => (
              <th key={c}>
                <Skeleton height={9} width="55%" />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }).map((_, r) => (
            <tr key={r}>
              {Array.from({ length: columns }).map((_, c) => (
                <td key={c}>
                  <Skeleton height={12} width={c === 0 ? "82%" : "48%"} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
